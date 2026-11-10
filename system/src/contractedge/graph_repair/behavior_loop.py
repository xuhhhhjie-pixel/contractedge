"""Host-side orchestration of the behaviour method around ContractGraphAgent.

prepare():       one budgeted proposal call for requirements from the task text, before the first step.
after_action():  after an executed action: refresh offered tests, at most one check-plan proposal when new
                 tests appear, run at most `behavior_checks_per_tick` checks, return a short note.
before_submit(): run a few pending checks, return a one-time reminder for unseen failures, else None.
finish():        write the summary bound to the version actually left in the work tree.

Proposal calls use the agent's own model object (same transport, usage ledger and clock) with a
separate message list; they never enter the executor's conversation. Checks run through the
environment directly, so a check never triggers another tick. Any state error disables the
mechanism for the rest of the run and the executor continues as without it.
"""
from __future__ import annotations

import json
import posixpath
import re
import shlex
import time
from pathlib import Path

from contractedge.graph_repair import behavior
from contractedge.graph_repair import behavior_checks as checks
from contractedge.graph_repair import behavior_feedback as feedback
from contractedge.graph_repair.checkpoint import parse, tree_script

INDEX = '/ce-state/behavior.index'
MAX_TASK, MAX_FILE, MAX_READ = 12000, 6000, 200_000
TEST_FILE = re.compile(r'(?<![\w.-])((?:/testbed/)?[\w./-]*_test\.go)\b')
REQUIREMENT_FILE = re.compile(r'^requirement_file:\s*([\w./-]+)\s*$', re.M)   # named by the method task text
SHOWN = 5

REQUIREMENTS = """You extract behaviour requirements for a bug fix from public material only.
Reply with one JSON object and nothing else:
{"requirements": [{"kind": "target" or "preserve", "condition": "...", "expected": "...",
  "source_refs": [{"source": "task", "quote": "<8-400 characters copied exactly from the task text>"}]}]}
When a quote comes from the requirement file shown, use {"source": "file", "path": "<its path>", "quote": "..."}.
Rules:
- One observable behaviour per item. Different fields, outputs, conditions or triggering/non-triggering cases are
  separate items.
- "target": behaviour the fix must produce. "preserve": existing behaviour the text says must keep working.
- Copy each quote verbatim. Do not infer requirements from file names, guess hidden tests, or add expectations the
  text does not state. Leave out anything you are unsure of.
- At most 8 items. An empty list is a valid answer."""

CHECK_PLAN = """You map behaviour requirements to existing public Go tests.
Reply with one JSON object and nothing else:
{"checks": [{"obligations": ["O1"], "scenario": "short name", "package": "./dir", "run": "^TestName$"}],
 "requirements": [{"kind": "target" or "preserve", "condition": "...", "expected": "...",
   "source_refs": [{"source": "file", "path": "<test file path shown below>", "quote": "<copied exactly>"}]}]}
Rules:
- Use only the packages and test names listed. A test fits a requirement only if it exercises the requirement's
  condition and asserts its expected behaviour; a similar name is not enough.
- "run" is a Go test name pattern such as ^TestName$ or ^TestName$/^subtest$; no other commands.
- "requirements" may add behaviour stated in the test file shown (with an exact quote from it); otherwise leave it empty.
- Empty lists are valid answers."""


def _json_object(text: str) -> dict | None:
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', (text or '').strip())
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _rel(path: str) -> str:
    path = path[len('/testbed/'):] if path.startswith('/testbed/') else path
    return posixpath.normpath(path[2:] if path.startswith('./') else path)


def _package(path: str) -> str:
    directory = posixpath.dirname(_rel(path))
    return './' + directory if directory else '.'


class BehaviorLoop:
    def __init__(self, variant, *, model, run, directory: Path, remaining, baseline_tree: str | None,
                 env_identity: str, log):
        self.v = variant
        self.model = model
        self.run = run                      # run(command, timeout) -> {'output', 'returncode', ...}
        self.store = behavior.Store(directory)
        self.logs = Path(directory) / 'check-logs'
        self.remaining = remaining
        self.baseline = baseline_tree
        self.env = env_identity
        self.agent_log = log
        self.disabled: str | None = None
        self.sources: behavior.Sources | None = None
        self.intro: str | None = None

    # ------------------------------------------------------------ plumbing
    def _event(self, event: str, **value) -> None:
        self.store.log(event, **value)
        self.agent_log('behavior_' + event, **{k: v for k, v in value.items() if k in
                                                ('status', 'purpose', 'result', 'check', 'reason', 'added')})

    def _load(self) -> dict | None:
        if self.disabled:
            return None
        try:
            return self.store.load()
        except behavior.StateError as exc:
            self.disabled = f'STATE_ERROR: {exc}'
            self._event('state_error', reason=str(exc))
            return None

    def _save(self, snapshot: dict) -> None:
        try:
            self.store.save(snapshot)
        except OSError as exc:
            self.disabled = f'STATE_ERROR: {exc}'
            self._event('state_error', reason=str(exc))

    def _tree(self) -> str | None:
        return parse(self.run(tree_script(INDEX), 60).get('output', ''), 'TREE')

    def _read_file(self, path: str) -> str | None:
        quoted = shlex.quote(path)
        result = self.run(f'test -f {quoted} && head -c {MAX_READ} -- {quoted} 2>/dev/null', 30)
        return result.get('output', '') if result.get('returncode') == 0 else None

    def _room(self, seconds: int) -> bool:
        return self.remaining() - self.v.behavior_reserve_seconds >= seconds

    # ------------------------------------------------------------ proposals
    def propose(self, snapshot: dict, purpose: str, context: str) -> dict:
        counters = snapshot['counters']
        if counters.get('proposals', 0) >= self.v.behavior_max_proposals or not self._room(self.v.behavior_proposal_timeout):
            self._event('proposal', purpose=purpose, status='BUDGET')
            return {'status': 'BUDGET'}
        counters['proposals'] = counters.get('proposals', 0) + 1
        system = REQUIREMENTS if purpose == 'requirements' else CHECK_PLAN
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': context}]
        began = time.monotonic()
        try:
            response = self.model.query(messages, timeout=self.v.behavior_proposal_timeout)
        except Exception as exc:          # transport errors must not stop the executor
            self._event('proposal', purpose=purpose, status='ERROR', reason=f'{type(exc).__name__}: {exc}'[:300],
                        seconds=round(time.monotonic() - began, 1))
            return {'status': 'ERROR'}
        content = response.get('content', '') if isinstance(response, dict) else ''
        value = _json_object(content)
        status = 'PARSE_ERROR' if value is None else 'OK' if any(value.get(k) for k in ('requirements', 'checks')) \
            else 'EMPTY'
        self._event('proposal', purpose=purpose, status=status, chars=len(content),
                    seconds=round(time.monotonic() - began, 1), raw=content[:4000])
        return {'status': status, 'value': value or {}}

    # ------------------------------------------------------------ phases
    def prepare(self, task: str) -> dict:
        self.sources = behavior.Sources(task, self._read_file)
        snapshot = self._load()
        if snapshot is None:
            return {'status': self.disabled}
        context = 'Task text:\n<<<\n' + task[:MAX_TASK] + '\n>>>'
        named = REQUIREMENT_FILE.search(task)
        path = behavior.Sources.resolve('/inputs/' + named.group(1)) if named else None
        if path and path.startswith('/inputs/'):
            text = self.sources.text(path)
            if text:
                context += f'\n\nRequirement file {path}:\n<<<\n' + text[:MAX_TASK] + '\n>>>'
                self._event('requirement_file', status='READ', reason=path)
        result = self.propose(snapshot, 'requirements', context)
        delta = None
        if result['status'] == 'OK':
            delta = behavior.apply_proposals(snapshot, result['value'].get('requirements'), self.sources, 'task')
            self._event('requirements', added=delta['added'], delta=delta)
        self._save(snapshot)
        self.intro = self._intro(snapshot)
        return {'status': result['status'], 'delta': delta}

    def _intro(self, snapshot: dict) -> str | None:
        grounded = behavior.active(snapshot)
        unresolved = [o['id'] for o in snapshot['obligations'].values() if o['grounding'] == 'UNRESOLVED']
        if not grounded and not unresolved:
            return None
        lines = ['[ce-behavior] Behaviour requirements from the public task material (quotes checked against the source):']
        for ob in grounded[:SHOWN]:
            lines.append(f"- {ob['id']} [{ob['kind']}] when {feedback._short(ob['condition'], 100)}: expected "
                         f"{feedback._short(ob['expected'], 120)}  (\"{feedback._short(ob['source_refs'][0]['quote'], 90)}\")")
        if len(grounded) > SHOWN:
            lines.append(f'  ... {len(grounded) - SHOWN} more kept by the executor.')
        if unresolved:
            lines.append(f"Unresolved (no checked source, not used): {', '.join(unresolved)}.")
        if self.v.behavior_feedback:
            lines.append('Matching existing tests are run automatically after edits.')
        return '\n'.join(lines)

    def _offer(self, snapshot: dict, packages) -> list[str]:
        """List top-level Test functions in packages not offered yet; return packages that have tests."""
        fresh = [p for p in sorted(set(packages)) if p not in snapshot['packages']][:10]
        found = []
        for package in fresh:
            directory = '/testbed' if package == '.' else '/testbed/' + package[2:]
            result = self.run(f"cd {shlex.quote(directory)} 2>/dev/null && grep -h -o -E '^func Test[A-Za-z0-9_]*' "
                              f"-- *_test.go 2>/dev/null | head -200", 30)
            names = sorted({line[len('func '):] for line in result.get('output', '').split('\n')
                            if line.startswith('func Test')})
            snapshot['packages'][package] = names
            if names:
                found.append(package)
        return found

    def _changed_packages(self, tree: str) -> list[str]:
        if not self.baseline:
            return []
        result = self.run(f'cd /testbed && git -c safe.directory=* diff --name-only {self.baseline} {tree}', 30)
        if result.get('returncode') != 0:
            return []
        return [_package(f) for f in result.get('output', '').split() if f.endswith('.go')]

    def _plan_checks(self, snapshot: dict, new_packages: list[str], test_file: str | None) -> list[str]:
        grounded = behavior.active(snapshot)
        unchecked = [o for o in grounded if not any(snapshot['checks'].get(c, {}).get('status') == 'ACTIVE'
                                                     for c in o['check_ids'])]
        if not (unchecked or test_file) or not (new_packages or test_file):
            return []
        offered = {p: names for p, names in snapshot['packages'].items() if names}
        context = ['Requirements:'] + ([f"- {o['id']} [{o['kind']}] when {o['condition']}: expected {o['expected']}"
                                        for o in grounded] or ['- none yet'])
        context += ['', 'Packages and their test functions (module root is the repository root):']
        context += [f"- {p}: {', '.join(names[:40])}" for p, names in list(offered.items())[:10]]
        if test_file:
            text = self.sources.text('/testbed/' + test_file) if self.sources else None
            if text:
                context += ['', f'Test file /testbed/{test_file} (first {MAX_FILE} characters):', text[:MAX_FILE]]
        result = self.propose(snapshot, 'check_plan', '\n'.join(context))
        if result['status'] != 'OK':
            return []
        value = result['value']
        if value.get('requirements') and self.sources:
            delta = behavior.apply_proposals(snapshot, value['requirements'], self.sources, 'test-file')
            self._event('requirements', added=delta['added'], delta=delta)
        delta = checks.plan(snapshot, value.get('checks'), offered, self.v.behavior_check_timeout)
        self._event('check_plan', added=delta['added'], delta=delta)
        return delta['added']

    def _run_checks(self, snapshot: dict, tree: str, limit: int, show: bool) -> list[str]:
        notes = []
        for check in checks.pending(snapshot, tree, self.env)[:max(0, limit)]:
            counters = snapshot['counters']
            if counters.get('check_runs', 0) >= self.v.behavior_max_check_runs or not self._room(check['timeout']):
                self._event('check_skipped', check=check['id'], reason='budget')
                break
            evidence = checks.execute(check, snapshot, self.run, self._tree, self.env, self.logs)
            bundle = feedback.reduce(snapshot, evidence, tree, self.baseline, show=show)
            self._event('check', check=check['id'], result=evidence['result'], evidence=evidence['id'],
                        actions=bundle['next_actions'])
            if bundle['text']:
                notes.append(bundle['text'])
        return notes

    def after_action(self, command: str, output: dict, previous: str | None, tree: str | None) -> str:
        if self.disabled or not tree:
            return ''
        snapshot = self._load()
        if snapshot is None:
            return ''
        notes = []
        if self.intro:
            notes.append(self.intro)
            self.intro = None
        packages, test_file = [], None
        if previous and tree != previous:
            stale = feedback.stale(snapshot, previous, tree)
            if stale:
                notes.append(f"[ce-behavior] Earlier results for {', '.join(stale)} no longer apply after this edit.")
            packages += self._changed_packages(tree)
        if output.get('returncode', 0) == 0:
            for match in TEST_FILE.findall(command):
                rel = _rel(match)
                if rel not in snapshot['test_files'] and behavior.Sources.resolve(rel):
                    snapshot['test_files'].append(rel)
                    test_file = test_file or rel
                    packages.append(_package(rel))
        if self.v.behavior_feedback:              # requirements-only runs neither plan nor run checks
            new_packages = self._offer(snapshot, packages)
            self._plan_checks(snapshot, new_packages, test_file)
            notes += self._run_checks(snapshot, tree, self.v.behavior_checks_per_tick, show=True)
        self._save(snapshot)
        return '\n'.join(notes)

    def before_submit(self, tree: str | None) -> str | None:
        if self.disabled or not tree or not self.v.behavior_feedback:
            return None
        snapshot = self._load()
        if snapshot is None:
            return None
        self._run_checks(snapshot, tree, self.v.behavior_submit_checks, show=False)
        reminder = feedback.submit_reminder(snapshot, tree)
        self._save(snapshot)
        if reminder:
            self._event('submit_reminder', status='SHOWN')
        return reminder

    def rank(self, tree: str) -> tuple:
        snapshot = None if self.disabled else self._load()
        return feedback.rank(snapshot, tree) if snapshot else (0, 0)

    def finish(self, exit_status: str) -> dict | None:
        snapshot = None if self.disabled else self._load()
        tree = self._tree()
        value = feedback.summary(snapshot, tree) if snapshot else {'schema': 'contractedge.behavior_summary.v1',
                                                                   'submitted_identity': tree}
        value.update(exit_status=exit_status, disabled=self.disabled)
        try:
            self.store.dir.mkdir(parents=True, exist_ok=True)
            (self.store.dir / 'behavior-summary.json').write_text(json.dumps(value, indent=1, ensure_ascii=False))
        except OSError:
            pass
        return value
