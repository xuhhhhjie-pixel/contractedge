"""ContractGraphAgent: mini-swe-agent DefaultAgent with two overridden methods.

execute_action: automatic checkpoints, failure attribution, the 85% wall-clock forced submission.
has_finished:   the submission marker triggers `ce-submit --check`; unmet items go back to the model;
                two identical unmet results in a row force submission of the best candidate.
The step loop, parsing, templates and model calls are DefaultAgent's.
"""
from __future__ import annotations

import calendar
import json
import re
import subprocess
import time
from pathlib import Path

from minisweagent.agents.default import (DefaultAgent, ExecutionTimeoutError, NonTerminatingException,
                                         Submitted)

from contractedge.graph_repair import attribution
from contractedge.graph_repair.checkpoint import checkpoint_script, parse, score
from contractedge.graph_repair.config import Variant

MARKERS = ('MINI_SWE_AGENT_FINAL_OUTPUT', 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
PLAIN_PUBLIC = re.compile(r'^\s*(?:cd\s+\S+\s*&&\s*)?ce-public(?:\s+[\w.:/-]+)*\s*(?:2>&1)?\s*$')
CHECKED_COMMAND = re.compile(r'(?<![\w./-])(?:ce-public|ce-run|ce-probe\s+run|go\s+(?:build|test|vet)|make\b|helm\s+'
                             r'(?:template|lint)|kustomize\s+build|kubectl\s+kustomize|npm\s+(?:test|run)|pytest|cargo\s+(?:build|test))')
SUBMIT_JSON = re.compile(r'^CE_SUBMIT_CHECK_JSON=(\{.*\})\s*$', re.M)


class SubmitBlocked(NonTerminatingException):
    """The stop rule is not met yet; the message lists what is missing."""


class ContractGraphAgent(DefaultAgent):
    def __init__(self, model, env, *, ce_variant: Variant, ce_state: Path, ce_wire_usage: Path | None = None,
                 ce_tool_timeout: int = 600, ce_baseline_tree: str | None = None, **kwargs):
        super().__init__(model, env, **kwargs)
        self.ce = ce_variant
        self.ce_state = Path(ce_state)
        self.ce_wire = Path(ce_wire_usage) if ce_wire_usage else None
        self.ce_tool_timeout = ce_tool_timeout
        self.ce_baseline_tree = ce_baseline_tree
        self.ce_started: float | None = None
        self.ce_steps = 0
        self.ce_probe_calls = 0
        self.ce_last_tree: str | None = ce_baseline_tree
        self.ce_checkpoints: list[dict] = []
        self.ce_last_unmet: str | None = None
        self.ce_forced: dict | None = None
        self.ce_force_declined = False
        self.ce_seen_files: set[str] = set()

    # ------------------------------------------------------------ bookkeeping
    def _log(self, event: str, **value) -> None:
        self.ce_state.mkdir(parents=True, exist_ok=True)
        with (self.ce_state / 'agent-events.jsonl').open('a') as stream:
            stream.write(json.dumps({'event': event, 'at': time.time(), 'step': self.ce_steps,
                                     'elapsed': round(self.elapsed(), 1), **value}, default=str) + '\n')

    def _write(self, name: str, value) -> None:
        self.ce_state.mkdir(parents=True, exist_ok=True)
        tmp = self.ce_state / (name + '.tmp')
        tmp.write_text(json.dumps(value, indent=1, sort_keys=True))
        tmp.replace(self.ce_state / name)

    def _read(self, name: str, default):
        try:
            return json.loads((self.ce_state / name).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def started(self) -> float:
        """Same clock as the supervisor: the first request_started in the wire ledger."""
        if self.ce_started is None and self.ce_wire and self.ce_wire.exists():
            for line in self.ce_wire.read_text().splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get('phase') == 'request_started':
                    self.ce_started = calendar.timegm(time.strptime(row['request_started_utc'], '%Y-%m-%dT%H:%M:%SZ'))
                    break
        if self.ce_started is None:
            self.ce_started = time.time()
        return self.ce_started

    def elapsed(self) -> float:
        return time.time() - self.started()

    def token_pressure(self) -> bool:
        """True when the transport's cumulative token/request budget cannot cover two more requests.

        The baseline transport stops a run (BUDGET_EXHAUSTED, no submission) once the remaining token
        budget cannot reserve the next request; the stop rule submits the best candidate before that.
        """
        if not self.ce_wire or not self.ce_wire.exists():
            return False
        try:
            from deepseek_transport import MAX_REQUESTS, MAX_TOTAL_TOKENS
        except ImportError:
            MAX_REQUESTS, MAX_TOTAL_TOKENS = 300, 5_000_000
        finished, started = [], 0
        for line in self.ce_wire.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            started += row.get('phase') == 'request_started'
            if row.get('phase') == 'request_finished':
                finished.append(row.get('usage', {}).get('total_tokens', 0))
        if not finished:
            return False
        used, last = sum(finished), finished[-1]
        return used + 2 * (last + 16384) >= MAX_TOTAL_TOKENS or started + 2 >= MAX_REQUESTS

    def remaining(self) -> float:
        return self.ce.wall_seconds - (time.time() - self.started())

    def _run(self, command: str, timeout: int) -> dict:
        try:
            return self.env.execute(command, timeout=max(5, int(timeout)))
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            out = exc.output.decode('utf-8', 'replace') if isinstance(getattr(exc, 'output', None), bytes) else ''
            return {'output': out, 'returncode': 124, 'timed_out': True}

    # ------------------------------------------------------------ checkpoints
    def _checkpoint(self, step_command: str) -> str | None:
        n = len(self.ce_checkpoints) + 1
        # Only commits and moves a ref when the tree differs from the last checkpoint.
        result = self._run(checkpoint_script(n, self.ce_last_tree), 90)
        tree, commit = parse(result['output'], 'TREE'), parse(result['output'], 'COMMIT')
        if not tree:
            self._log('checkpoint_failed', output=result['output'][-400:])
            return None
        if commit:
            row = {'n': n, 'commit': commit, 'tree': tree, 'step': self.ce_steps, 'at': time.time(),
                   'changed': tree != self.ce_baseline_tree, 'after': step_command[:200]}
            self.ce_checkpoints.append(row)
            self._write('checkpoints.json', self.ce_checkpoints)
            self._log('checkpoint', n=n, tree=tree)
        self.ce_last_tree = tree
        return tree

    def _auto_graph(self, previous: str | None, tree: str) -> str:
        """First edit of a file: seed nodes, the consumed declaration and same-declaration consumers."""
        listed = self._run(f'cd /testbed && git -c safe.directory=* diff --name-only {previous or self.ce_baseline_tree} {tree}', 30)
        files = [f for f in listed.get('output', '').split() if f and f not in self.ce_seen_files]
        if not files or listed.get('returncode') != 0:
            return ''
        self.ce_seen_files.update(files)
        quoted = ' '.join("'" + f.replace("'", "'\\''") + "'" for f in files[:3])
        result = self._run(f'cd /testbed && ce-graph auto --files {quoted}', 90)
        text = result.get('output', '').strip()
        self._log('auto_graph', files=files[:3], returncode=result.get('returncode'), chars=len(text))
        return text if result.get('returncode') == 0 and text.startswith('[ce-auto]') else ''

    def _record_public(self, tree: str, command: str, output: dict) -> None:
        if tree and PLAIN_PUBLIC.match(command) and not output.get('timed_out'):
            results = self._read('results.json', {})
            results.setdefault(tree, {})['public'] = {'exit_code': output.get('returncode'), 'by': 'agent'}
            self._write('results.json', results)

    def _best(self) -> dict | None:
        rows = [c for c in self.ce_checkpoints if c.get('changed')]
        if not rows:
            return None
        results = self._read('results.json', {})
        return max(rows, key=lambda c: (score(c['tree'], results), c['n']))

    def _force_submit(self, reason: str):
        """Restore the best candidate (when there is one) and submit it."""
        current = self.ce_last_tree
        if self.ce.checkpoint:
            best = self._best()
            if best is None and (current is None or current == self.ce_baseline_tree):
                # Nothing to submit yet; checked again at the next step.
                if not self.ce_force_declined:
                    self._log('force_declined', reason=reason, why='no change to submit')
                self.ce_force_declined = True
                return
            if best is not None and best['tree'] != current:
                restored = self._run(f'ce-rollback {best["n"]}', 120)
                self._log('force_rollback', checkpoint=best['n'], output=restored['output'][-300:])
        self.ce_forced = {'reason': reason, 'tree': current, 'at_elapsed': round(self.elapsed(), 1)}
        self._log('forced_submission', **self.ce_forced)
        raise Submitted(f'FORCED_SUBMISSION ({reason}): best candidate submitted by the stop rule.\n')

    # ------------------------------------------------------------ overrides
    def execute_action(self, action: dict) -> dict:
        if not self.ce.any_mechanism:
            return super().execute_action(action)     # live-equivalent: exactly DefaultAgent
        command = action['action']
        self.ce_steps += 1
        if 'ce-probe' in command:
            self.ce_probe_calls += 1
        self._write('budget.json', {'elapsed': round(self.elapsed(), 1), 'n_calls': self.model.n_calls,
                                    'probe_calls': self.ce_probe_calls, 'steps': self.ce_steps})
        if self.ce.stop_rule and time.time() - self.started() >= self.ce.force_fraction * self.ce.wall_seconds:
            self._force_submit(f'WALL_{int(self.ce.force_fraction * 100)}')
        if self.ce.stop_rule and self.token_pressure():
            self._force_submit('TOKEN_BUDGET')
        timeout = None
        if self.ce.stop_rule:
            # Leave room to restore and export before the supervisor's wall-clock stop.
            limit = self.remaining() - 90
            default = self.ce_tool_timeout if CHECKED_COMMAND.search(command) or 'ce-' in command else \
                getattr(self.env.config, 'timeout', 120)
            if limit < default:
                timeout = max(15, int(limit))
        try:
            output = self.env.execute(command, timeout=timeout) if timeout else self.env.execute(command)
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            text = exc.output.decode('utf-8', errors='replace') if getattr(exc, 'output', None) else ''
            if self.ce.checkpoint:
                self._checkpoint(command)
            raise ExecutionTimeoutError(self.render_template(self.config.timeout_template, action=action, output=text))
        if (self.ce_state / 'calibration-in-progress.json').exists():
            self._log('resume_calibration', output=self._run('ce-rollback --resume-calibration', 120)['output'][-300:])
        previous = self.ce_last_tree
        tree = self._checkpoint(command) if self.ce.checkpoint else None
        if tree:
            self._record_public(tree, command, output)
            if tree != previous and self.ce.graph and self.ce.linkers:
                note = self._auto_graph(previous, tree)
                if note:
                    output = {**output, 'output': output.get('output', '') + '\n' + note}
        if self.ce.attribution and output.get('returncode', 0) != 0 and CHECKED_COMMAND.search(command):
            rows = attribution.attribute(output.get('output', ''), self._read('graph.json', None),
                                         self._read('probes.json', []))
            if rows:
                note = attribution.render(rows)
                output = {**output, 'output': output.get('output', '') + '\n' + note}
                self._log('attribution', rows=attribution.to_json(rows))
        self.has_finished(output)
        return output | {'action': command}

    def has_finished(self, output: dict):
        lines = output.get('output', '').lstrip().splitlines(keepends=True)
        marked = bool(lines) and lines[0].strip() in MARKERS and output.get('returncode', 0) == 0
        if not marked or not self.ce.stop_rule:
            return super().has_finished(output)
        submission = ''.join(lines[1:])
        result = self._run('ce-submit --check --json', max(60, min(self.remaining() - 60, 2 * self.ce_tool_timeout)))
        match = SUBMIT_JSON.search(result.get('output', ''))
        if not match:
            # A broken check must not trap the run: submit and keep the evidence.
            self._log('submit_check_unavailable', output=result.get('output', '')[-600:])
            raise Submitted(submission)
        value = json.loads(match.group(1))
        self._log('submit_check', passed=value['passed'], unmet=value['unmet'])
        if value['passed']:
            raise Submitted(submission)
        signature = json.dumps(sorted((u['kind'], u['id']) for u in value['unmet']))
        if signature == self.ce_last_unmet:
            self._force_submit('REPEATED_UNMET')
        self.ce_last_unmet = signature
        items = '\n'.join(f"- [{u['kind']}] {u['id']}: {u['detail']}" for u in value['unmet'][:20])
        notes = '\n'.join(value.get('notes', []))
        raise SubmitBlocked(
            'Submission not accepted yet: the stop rule found unmet items.\n' + items +
            (('\nNotes:\n' + notes) if notes else '') +
            '\nFix them and submit again. If an item cannot be met, explain why in your reasoning; submitting again '
            'with the same unmet items submits the best candidate as is.')
