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

import base64
import json
import posixpath
import re
import shlex
import time
from pathlib import Path

from contractedge.graph_repair import behavior
from contractedge.graph_repair import behavior_checks as checks
from contractedge.graph_repair import behavior_examples as examples
from contractedge.graph_repair import behavior_feedback as feedback
from contractedge.graph_repair.checkpoint import parse, tree_script

INDEX = '/ce-state/behavior.index'
MAX_TASK, MAX_FILE, MAX_READ = 12000, 6000, 200_000
# A named test file; not a glob such as `! -name '*_test.go'` (a real run offered '_test.go' from one).
TEST_FILE = re.compile(r'(?<![\w.*/-])((?:/testbed/)?(?:[\w.-]+/)*\w[\w.-]*_test\.go)\b')
REQUIREMENT_FILE = re.compile(r'^requirement_file:\s*([\w./-]+)\s*$', re.M)   # named by the method task text
SHOWN = 5
# Public fixtures offered to the requirements proposal: small text files under /inputs only.
MAX_FIXTURE, MAX_FIXTURE_FILES, MAX_FIXTURES_TOTAL = 8192, 6, 12000
FIXTURE_SUFFIXES = ('.json', '.yaml', '.yml', '.txt', '.md', '.toml', '.ini', '.env', '.properties', '.csv', '.tf', '.hcl')
INPUT_METADATA = {'task.yaml', 'environment.json'}          # run description, not behaviour

REQUIREMENTS = """You extract behaviour requirements for a bug fix from public material only.
Reply with one JSON object and nothing else:
{"requirements": [{"kind": "target" or "preserve", "condition": "...", "expected": "...",
  "source_refs": [{"source": "task", "quote": "<8-400 characters copied exactly from the task text>"}]}]}
When a quote comes from a file shown, use {"source": "file", "path": "<its path>", "quote": "..."}.
Rules:
- One observable behaviour per item. Different fields, outputs, conditions or triggering/non-triggering cases are
  separate items.
- "target": behaviour the fix must produce. "preserve": existing behaviour the text says must keep working.
- Public input files (fixtures) may give concrete cases: the input and the value it must produce. State such a case
  with its concrete values and quote the fixture lines. A case the material marks as a control or as not affected
  by the bug is "preserve".
- Copy each quote verbatim. Do not infer requirements from file names, guess hidden tests, or add expectations the
  text does not state. Leave out anything you are unsure of.
- At most 8 items. An empty list is a valid answer."""

EXAMPLES = """
Also list the concrete cases the public material gives (for example the trigger and control cases of a fixture):
add "examples": [{"name": "<case name, e.g. trigger>", "kind": "target" or "preserve",
  "input": "<what the case feeds the code, in a few words>",
  "fields": [{"name": "<short_snake_case>", "meaning": "<what this value is>", "expected": <value or null>}],
  "source_refs": [<quotes of the case, as above>]}]
- A field is one observable output of the production code for that case (a returned identifier, whether an update
  or error happens, a rendered value ...). Give "expected" only when the public material states that exact value
  for that output; otherwise null. A case the material marks as a control is "preserve".
- A case may be a fixture record or a situation the task text describes (a condition and what must then happen);
  list both kinds.
- A field is an OUTPUT the production code gives for the case; never list the case's own input values as fields.
- List every output the material says the case must produce, not only the one the bug title names.
- When the code converts a value in two directions (for example a name derived from stored state, and an identifier
  rebuilt from that name), make each direction its own field. If the material shows the stored record, the rebuilt
  identifier must equal the stored one: give that stored value as "expected".
- At most 6 examples; leave the list empty when the material has no concrete case."""

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
                 env_identity: str, log, gold: dict | None = None):
        self.v = variant
        # Ceiling experiments only: examples and a driver given from outside (e.g. built from the hidden
        # evaluator of a development case) replace the extracted ones. Never set in a comparison group.
        self.gold = gold
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
        # Requirements are asked once (prepare); the limit is for check plans, so an early empty plan
        # cannot use up the budget a later, relevant test would need (real run R4-PROVIDER-UPJET-AWS-2117).
        counters = snapshot['counters']
        over = purpose == 'check_plan' and counters.get('check_plans', 0) >= self.v.behavior_max_proposals
        if over or not self._room(self.v.behavior_proposal_timeout):
            self._event('proposal', purpose=purpose, status='BUDGET')
            return {'status': 'BUDGET'}
        counters['proposals'] = counters.get('proposals', 0) + 1
        if purpose == 'check_plan':
            counters['check_plans'] = counters.get('check_plans', 0) + 1
        system = (REQUIREMENTS + (EXAMPLES if self.v.behavior_examples else '')) if purpose == 'requirements' \
            else CHECK_PLAN
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': context}]
        began = time.monotonic()
        for attempt in range(2):
            try:
                response = self.model.query(messages, timeout=self.v.behavior_proposal_timeout)
            except Exception as exc:          # transport errors must not stop the executor
                self._event('proposal', purpose=purpose, status='ERROR', reason=f'{type(exc).__name__}: {exc}'[:300],
                            seconds=round(time.monotonic() - began, 1))
                return {'status': 'ERROR'}
            content = response.get('content', '') if isinstance(response, dict) else ''
            value = _json_object(content)
            # Real runs: DeepSeek sometimes answers with an empty content (its native tool-call markup); ask once more.
            if value is not None or attempt or not self._room(self.v.behavior_proposal_timeout):
                break
            self._event('proposal', purpose=purpose, status='RETRY', chars=len(content), raw=content[:1000],
                        seconds=round(time.monotonic() - began, 1))
            counters['proposals'] += 1
            messages += [{'role': 'assistant', 'content': content or '(empty)'},
                         {'role': 'user', 'content': 'Your reply was empty or not a JSON object. Reply again with the '
                                                     'JSON object only, as plain text, no tool calls.'}]
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
        fixtures = self._fixtures(skip={path})
        for fixture, text in fixtures:
            context += f'\n\nPublic input file {fixture}:\n<<<\n' + text + '\n>>>'
        if fixtures:
            self._event('input_files', status='READ', reason=', '.join(f for f, _ in fixtures))
        result = self.propose(snapshot, 'requirements', context)
        delta = None
        if result['status'] == 'OK':
            delta = behavior.apply_proposals(snapshot, result['value'].get('requirements'), self.sources, 'task')
            self._event('requirements', added=delta['added'], delta=delta)
        if self.v.behavior_examples and self.gold:
            self._install_gold(snapshot)
        elif self.v.behavior_examples and result['status'] in ('OK', 'EMPTY'):
            found = examples.apply_examples(snapshot, result['value'].get('examples'), self.sources)
            self._event('examples', added=found['added'], delta=found)
            if found['added'] and self.v.behavior_driver_helper:
                self._save(snapshot)
                self._setup_examples(snapshot, task, fixtures)
        if self.v.behavior_examples and snapshot.get('examples') and not (snapshot.get('driver') or {}).get('hash'):
            self._put(f'/testbed/{examples.DIR}/skeleton.go.txt', examples.skeleton(snapshot))
        self._save(snapshot)
        self.intro = self._intro(snapshot)
        return {'status': result['status'], 'delta': delta}

    # ------------------------------------------------------------ examples
    def _setup_examples(self, snapshot: dict, task: str, fixtures) -> None:
        """Write the driver with the separate helper, keep the source untouched, calibrate on the original version."""
        if self.remaining() < self.v.behavior_driver_min_remaining:
            self._event('driver', status='BUDGET')
            return
        before = self._tree()
        if before is None:
            self._event('driver', status='SKIPPED', reason='source identity unreadable; helper edits could not be checked')
            return
        writer = examples.DriverWriter(self.model, self.run, self.v.behavior_driver_steps,
                                       lambda: self.remaining() < self.v.behavior_driver_min_remaining - 300,
                                       self.store.dir / 'driver' / 'transcript.json')
        outcome = writer.write(examples.brief(snapshot, task, fixtures), lambda e, **k: self._event(e, **k))
        if self._tree() != before:
            # The helper may only write under the ignored examples directory; undo anything else.
            self.run('cd /testbed && git -c safe.directory=* checkout -q -- . && git -c safe.directory=* clean -fdq', 60)
            restored = self._tree() == before
            self._event('driver_reverted_source', status='OK' if restored else 'FAILED')
            if not restored:
                self.disabled = 'STATE_ERROR: the driver helper changed the source and it could not be restored'
                return
        snapshot['driver'] = {'status': outcome['status'], 'steps': outcome['steps'], 'by': 'helper'}
        if not examples.driver_hash(self._read_file):
            self._event('driver', status='NO_DRIVER', reason=outcome['status'])
            return
        self._calibrate(snapshot, examples.driver_hash(self._read_file))

    def _install_gold(self, snapshot: dict) -> None:
        snapshot['examples'] = {name: {'name': name, 'kind': ex['kind'], 'input': ex.get('input', ''),
                                       'fields': ex['fields'], 'source_refs': [], 'baseline': {}, 'usable': {}}
                                for name, ex in self.gold['examples'].items()}
        if not self.gold.get('driver'):
            # Diagnostic level: given examples, the executor writes the driver as in the method.
            self._event('examples', status='GOLD_EXAMPLES', added=sorted(snapshot['examples']))
            return
        for name, text in self.gold['driver'].items():
            self._put(f'/testbed/{examples.DIR}/{name}', text)
        snapshot['driver'] = {'by': 'gold'}
        self._event('examples', status='GOLD', added=sorted(snapshot['examples']))
        self._calibrate(snapshot, examples.driver_hash(self._read_file))

    def _put(self, path: str, text: str) -> None:
        data = shlex.quote(base64.b64encode(text.encode()).decode())
        self.run(f'mkdir -p {shlex.quote(posixpath.dirname(path))} && printf %s {data} | base64 -d > {shlex.quote(path)}', 60)

    def _calibrate(self, snapshot: dict, digest: str) -> dict:
        """Run the driver on the original version and keep the fields that can speak about the fix."""
        driver = snapshot.setdefault('driver', {'by': 'executor'})
        driver.update(hash=digest, calibrations=driver.get('calibrations', 0) + 1)
        examples.save_driver_copy(self._read_file, self.store.dir / 'driver' / f"v{driver['calibrations']}")
        result = self._observe(snapshot, 'BASELINE', cold=True, baseline=True)
        report = examples.calibrate(snapshot, result['observed'])
        if result['renamed']:
            report['renamed'] = result['renamed']
        if any(d['why'] == 'not observed by the driver' for d in report['dropped']):
            report['output_tail'] = examples.output_tail(result['output'])
        driver.update(calibration=report, active=bool(report['usable']))
        self._event('driver', status='CALIBRATED' if report['usable'] else 'REJECTED', reason=json.dumps(report)[:600])
        return report

    def _sync_driver(self, snapshot: dict) -> str:
        """Calibrate a driver the executor wrote or changed; return a note about it."""
        if not self.v.behavior_examples or not snapshot.get('examples') or snapshot.get('examples_abandoned'):
            return ''
        digest = examples.driver_hash(self._read_file)
        driver = snapshot.get('driver') or {}
        if not digest or digest == driver.get('hash'):
            return ''
        if driver.get('calibrations', 0) >= self.v.behavior_max_calibrations or \
                not self._room(self.v.behavior_example_timeout):
            driver.update(hash=digest, active=False)
            self._event('driver', status='BUDGET')
            return '[ce-behavior] The example driver changed, but no calibration budget is left; examples are not checked.'
        report = self._calibrate(snapshot, digest)
        lines = ['[ce-behavior] Example driver calibrated on a copy of the original version: '
                 f"{len(report['usable'])} field(s) usable."]
        lines += [f"- read {a} as {b}" for a, b in list(report.get('renamed', {}).items())[:8]]
        lines += [f"- dropped {d['example']}.{d['field']}: {d['why']}" for d in report['dropped'][:8]]
        if report.get('output_tail'):
            lines += ['Driver output on the original version (last lines):', report['output_tail']]
        if not report['usable']:
            lines.append('No field can be checked; fix the driver (it must call the production code) and run it again.')
        return '\n'.join(lines)

    def _abandon(self, snapshot: dict) -> str:
        """Do no harm: with no calibrated driver after the given share of the wall time, stop the examples.

        Real run UPJET-AWS-2182: the executor spent its time on a driver that never calibrated and submitted
        nothing; the examples must not cost the fix itself."""
        driver = snapshot.get('driver') or {}
        if not (self.v.behavior_examples and snapshot.get('examples')) or snapshot.get('examples_abandoned') \
                or driver.get('active') or (self.gold or {}).get('driver'):
            return ''
        used = 1 - self.remaining() / self.v.wall_seconds
        if used < self.v.behavior_driver_abandon_fraction:
            return ''
        snapshot['examples_abandoned'] = round(used, 2)
        self._event('driver', status='ABANDONED', reason=f'{used:.0%} of the wall time used, no calibrated driver')
        return (f'[ce-behavior] No example driver has been calibrated and {used:.0%} of the time is used. Stop working on '
                'the driver and spend the remaining time on the fix; the examples are no longer checked.')

    def _observe(self, snapshot: dict, tree: str | None, cold: bool = False, baseline: bool = False) -> dict:
        # Real runs (L2 diagnostic): drivers appeared after 858-1071 s, when a fixed 900 s calibration no longer
        # fitted; cap the timeout by the remaining time instead of refusing.
        timeout = max(self.v.behavior_example_timeout, self.v.behavior_calibration_timeout if cold else 0)
        timeout = max(self.v.behavior_example_timeout // 2,
                      min(timeout, int(self.remaining() - self.v.behavior_reserve_seconds - 60)))
        began = time.monotonic()
        result = self.run(examples.run_command(self.baseline if baseline else None), timeout)
        observed, renamed = examples.resolve_names(examples.parse(result.get('output', '')), snapshot)
        n = len(snapshot.setdefault('example_runs', [])) + 1
        log = self.logs / f'X{n}.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.get('output', ''))
        row = {'id': f'X{n}', 'tree': tree, 'driver': snapshot.get('driver', {}).get('hash'),
               'seconds': round(time.monotonic() - began, 1), 'returncode': result.get('returncode'),
               'observed': [[k[0], k[1], v] for k, v in observed.items()], 'log_ref': str(log)}
        snapshot['example_runs'].append(row)
        counters = snapshot['counters']
        counters['example_runs'] = counters.get('example_runs', 0) + 1
        counters['example_seconds'] = round(counters.get('example_seconds', 0) + row['seconds'], 1)
        return {'row': row, 'observed': observed, 'renamed': renamed, 'output': result.get('output', '')}

    def _examples_active(self, snapshot: dict) -> bool:
        driver = snapshot.get('driver') or {}
        return bool(self.v.behavior_examples and driver.get('active')
                    and examples.driver_hash(self._read_file) == driver.get('hash'))

    def _example_rows(self, snapshot: dict, tree: str) -> list[dict] | None:
        """Judged rows for `tree`, running the driver when this tree has not been observed yet."""
        if not self._examples_active(snapshot):
            return None
        latest = next((r for r in reversed(snapshot.get('example_runs', [])) if r['tree'] == tree), None)
        if latest is None:
            if not self._room(self.v.behavior_example_timeout):
                return None
            observed = self._observe(snapshot, tree)['observed']
        else:
            observed = {(a, b): v for a, b, v in latest['observed']}
        return examples.judge(snapshot, observed)

    @staticmethod
    def _example_signature(tree: str, rows: list[dict]) -> str:
        return json.dumps([tree, [(r['example'], r['field'], r['status'], r['observed']) for r in rows]])

    def _example_note(self, snapshot: dict, tree: str, rows: list[dict], show: bool) -> str:
        signature = self._example_signature(tree, rows)
        shown = snapshot.setdefault('examples_shown', [])
        if not show or signature in shown:
            return ''
        shown.append(signature)
        return examples.render(rows, '[ce-behavior] Example check (public fixture cases, driver '
                                     f'/testbed/{examples.DIR}) on the current version:')

    def _fixtures(self, skip) -> list[tuple[str, str]]:
        """Small text files among the public inputs (fixtures), besides the requirement file and run metadata.

        In the real UPJET-AWS-2117 run the trigger and control cases were only in /inputs/inputs; the
        executor found them after 17 steps and the requirements, built from requirement.md, lacked them."""
        found = self.run(f"find /inputs -type f -size -{MAX_FIXTURE}c 2>/dev/null | sort | head -50", 30)
        out, total = [], 0
        for line in found.get('output', '').splitlines():
            path = behavior.Sources.resolve(line.strip())
            if not path or not path.startswith('/inputs/') or path in skip or posixpath.basename(path) in INPUT_METADATA \
                    or not path.endswith(FIXTURE_SUFFIXES):
                continue
            text = self.sources.text(path) if self.sources else None
            if not text or '\0' in text or total + len(text) > MAX_FIXTURES_TOTAL or len(out) >= MAX_FIXTURE_FILES:
                continue
            out.append((path, text))
            total += len(text)
        return out

    def _intro(self, snapshot: dict) -> str | None:
        grounded = behavior.active(snapshot)
        unresolved = [o['id'] for o in snapshot['obligations'].values() if o['grounding'] == 'UNRESOLVED']
        if not grounded and not unresolved and not (self.v.behavior_examples and snapshot.get('examples')):
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
        if self.v.behavior_examples and snapshot.get('examples'):
            lines.append('Example cases from the public material (expected values are copied from it and checked by the '
                         'executor, not by you):')
            lines.append(self._example_lines(snapshot))
            if not self._examples_active(snapshot):
                lines.append(examples.EXECUTOR_PROTOCOL.format(dir=examples.DIR))
        return '\n'.join(lines)

    def _example_lines(self, snapshot: dict) -> str:
        lines = []
        for ex in snapshot.get('examples', {}).values():
            lines.append(f"- {ex['name']} [{ex['kind']}] input: {examples._short(ex['input'] or 'see its source', 160)}")
            for fname, spec in ex['fields'].items():
                if ex['usable'] and not ex['usable'].get(fname):
                    continue
                want = spec['expected'] if spec['mode'] == 'LITERAL' else (
                    'different from the original version' if ex['kind'] == 'target' else 'same as the original version')
                base = f"; original version gives {examples._short(ex['baseline'][fname])}" if fname in ex['baseline'] else ''
                lines.append(f"    {fname} ({examples._short(spec['meaning'] or 'value', 80)}): expected "
                             f"{examples._short(want)}{base}")
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
        # Asking again about the same tests for the same open requirements gives the same answer.
        shown = sorted(f'{p}:{n}' for p, names in list(offered.items())[:10] for n in names[:40])
        seen = snapshot.setdefault('planned', {'tests': [], 'obligations': []})
        if not test_file and set(shown) <= set(seen['tests']) and \
                {o['id'] for o in unchecked} <= set(seen['obligations']):
            self._event('check_plan', status='SKIPPED', reason='nothing new to map')
            return []
        seen['tests'] = sorted(set(seen['tests']) | set(shown))
        seen['obligations'] = sorted(set(seen['obligations']) | {o['id'] for o in unchecked})
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
            timeout = checks.timeout_for(check, snapshot, self.v.behavior_cold_check_timeout)
            if counters.get('check_runs', 0) >= self.v.behavior_max_check_runs or not self._room(timeout):
                self._event('check_skipped', check=check['id'], reason='budget')
                break
            evidence = checks.execute(check, snapshot, self.run, self._tree, self.env, self.logs, timeout)
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
        abandoned = self._abandon(snapshot)
        if abandoned:
            notes.append(abandoned)
        calibrated = self._sync_driver(snapshot)
        if calibrated:
            notes.append(calibrated)
        if (calibrated or (previous and tree != previous)) and self._examples_active(snapshot):
            rows = self._example_rows(snapshot, tree)
            if rows is not None:
                self._event('examples_run', result=json.dumps([[r['example'], r['field'], r['status']] for r in rows]))
                note = self._example_note(snapshot, tree, rows, show=True)
                if note:
                    notes.append(note)
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
        self._sync_driver(snapshot)
        rows = self._example_rows(snapshot, tree)
        bad = examples.failing(rows or [])
        reminded = snapshot.setdefault('examples_reminded', [])
        if snapshot.get('examples') and self.v.behavior_examples and rows is None and 'NO_DRIVER' not in reminded \
                and not snapshot.get('examples_abandoned'):
            # Examples from the public material exist but nothing checks them yet: say so once.
            reminded.append('NO_DRIVER')
            text = ('[ce-behavior] Before submitting: the public material gives concrete example cases, but no '
                    'calibrated driver checks them yet:\n' + self._example_lines(snapshot) +
                    '\nWrite the driver described in the method notes and run it, or submit again to submit as is '
                    '(this reminder is shown once).')
            reminder = (reminder + '\n' + text) if reminder else text
        key = self._example_signature(tree, rows or [])
        if bad and key not in reminded and key not in snapshot.get('examples_shown', []):
            # Like test failures: an example failure on this version that was not shown blocks submission once.
            reminded.append(key)
            text = examples.render(rows, '[ce-behavior] Before submitting: the example check on this version:')
            reminder = (reminder + '\n' + text) if reminder else \
                text + '\nFix them, or submit again to submit as is (this reminder is shown once).'
        self._save(snapshot)
        if reminder:
            self._event('submit_reminder', status='SHOWN')
        return reminder

    def rank(self, tree: str) -> tuple:
        snapshot = None if self.disabled else self._load()
        if not snapshot:
            return (0, 0, 0)
        latest = next((r for r in reversed(snapshot.get('example_runs', [])) if r['tree'] == tree), None)
        rows = examples.judge(snapshot, {(a, b): v for a, b, v in latest['observed']}) if latest else []
        met = sum(r['status'] in ('PASS', 'CHANGED') for r in rows) - len(examples.failing(rows))
        return (met, *feedback.rank(snapshot, tree))

    def finish(self, exit_status: str) -> dict | None:
        snapshot = None if self.disabled else self._load()
        tree = self._tree()
        value = feedback.summary(snapshot, tree) if snapshot else {'schema': 'contractedge.behavior_summary.v1',
                                                                   'submitted_identity': tree}
        if snapshot and snapshot.get('examples'):
            latest = next((r for r in reversed(snapshot.get('example_runs', [])) if r['tree'] == tree), None)
            value['examples'] = {'driver': snapshot.get('driver'), 'defined': snapshot['examples'],
                                 'submitted': examples.judge(snapshot, {(a, b): v for a, b, v in latest['observed']})
                                 if latest else None}
        value.update(exit_status=exit_status, disabled=self.disabled)
        try:
            self.store.dir.mkdir(parents=True, exist_ok=True)
            (self.store.dir / 'behavior-summary.json').write_text(json.dumps(value, indent=1, ensure_ascii=False))
        except OSError:
            pass
        return value
