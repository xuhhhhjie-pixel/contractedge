"""Executable examples: concrete cases from public material, observed through a driver program.

An example names a scenario (e.g. the fixture's "trigger" or "control" case), whether it is a
target or a preserve case, and the fields whose values the fixed code must produce. A field's
expectation is either
- LITERAL: a value copied from public material and verified to occur there verbatim; the program
  compares the observed value with it; or
- DIFF: no public value; a target field must differ from what the original version produces and a
  preserve field must stay equal to it.
Expected values are held here and never come from the driver. The driver prints one line per observation:
    CE_OBSERVE {"example": "<name>", "field": "<field>", "value": <json>}
By default the executor writes the driver (it reads the code anyway; real runs showed a separate helper
starting from scratch could not find the code paths in 16 steps); optionally a separate budgeted helper
loop writes it before the first step. Whenever the driver appears or changes it is calibrated on a copy
of the ORIGINAL version (git archive of the baseline tree under the ignored dependency directory, built
with -trimpath so both copies share one build cache): every field must be observed, a LITERAL target
must not already hold there (a hard-coded answer would) and a LITERAL preserve must hold. Fields that
fail calibration are dropped. Known gap: a DIFF target field the driver hard-codes stays UNCHANGED and
reads as "not met" (one submit block).

Driver aids (2026-11-12 real runs): a skeleton with the observation helper and one line per field is placed
next to the driver; near-miss names are read as the defined ones when exactly one fits (a driver printed
"trigger-subnet-association" for "trigger" and every field read as unobserved); when fields are not
observed the last lines of the driver output go back with the note (a build error and a panic were shown
to the executor only as "not observed").
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from pathlib import Path

from contractedge.graph_repair import behavior

DIR = '.contractedge-deps/ce-examples'           # inside /work and /testbed, excluded from git
BASE = '/work/.contractedge-deps/ce-base'         # copy of the original version for calibration
OBSERVE = re.compile(r'^CE_OBSERVE (\{.*\})\s*$', re.M)
NAME = re.compile(r'^[A-Za-z0-9_.-]{1,60}$')
MAX_EXAMPLES, MAX_FIELDS = 6, 6


def _name(value) -> str:
    # Extraction runs proposed names such as "subnet association trigger"; a strict pattern dropped the whole
    # example (UPJET-AWS-2117, -2182). Normalise instead of rejecting.
    return re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value or '')).strip('-.')[:60]
RUNNER = 'examples/1'

DRIVER_PROMPT = """You write a small observation driver for a bug-fix benchmark. You do not fix the bug.
The repository is at /testbed (a Go module). Write one Go file that exercises the PRODUCTION code with the
inputs of each example below and prints, for every listed field, exactly one line:
CE_OBSERVE {"example": "<example name>", "field": "<field name>", "value": <the observed value as JSON>}
Rules:
- Call the real production functions/configuration that decide the behaviour; never compute or hard-code the
  answer yourself, and never copy values that the code is supposed to produce.
- Do not edit any file under /testbed except your driver. Put it in exactly one of these places:
  /testbed/{dir}/main.go             (package main; can use exported identifiers of the module), or
  /testbed/{dir}/driver_test.go      (package of the code under test, with func TestCEExampleDriver(t *testing.T);
                                      use this when you need unexported identifiers) and then write the package
                                      directory (like ./config) on one line to /testbed/{dir}/package.txt
- If a value is an error, print its message string; print null for "no error" or "nothing happened".
- The program runs offline in the repository's public Go toolchain. Run it with:  ce-example-run
  (it builds and runs your driver the way the checks will and shows its output; the first build is slow).
- Each reply: a THOUGHT, then exactly one ```bash block with one command. When the driver prints every listed
  field, reply with the command:  echo CE_DRIVER_DONE"""

EXECUTOR_PROTOCOL = """Write an observation driver EARLY: as soon as you know which production code decides these values, before
you edit it. It shows what the code gives now, and every later edit is checked against the expected values:
  /testbed/{dir}/main.go          package main, using exported identifiers of the module (start from
                                  /testbed/{dir}/skeleton.go.txt: it prints every field; replace each nil), or
  /testbed/{dir}/driver_test.go   package of the code under test with func TestCEExampleDriver(t *testing.T),
                                  plus the package directory (like ./config) on one line in /testbed/{dir}/package.txt
It must call the PRODUCTION code with each example's input and print, per field, one line
  CE_OBSERVE {{"example": "<name>", "field": "<field>", "value": <observed value as JSON>}}
(an error as its message string; null for none). Never hard-code results. Run it with `ce-example-run`. When it
appears or changes, the executor runs it on a copy of the original version (target values must differ there, control
values must hold) and then after each of your edits, and reports observed against expected. Files in /testbed/{dir} are
not part of your patch."""


def setup(root: str) -> str:
    """Go environment for driver runs in the public runtime, rooted at /work or the baseline copy.

    Same module setup as the checks (behavior_checks.SETUP) with absolute dependency paths, plus -trimpath
    so the build cache of /work also serves the baseline copy."""
    deps = '/work/.contractedge-deps'
    return (f'cd {root} && export GOTOOLCHAIN=local GOFLAGS=-trimpath && M= && '
            f'if mkdir -p {deps}/gocache 2>/dev/null; then export GOCACHE={deps}/gocache; fi && '
            f'if [ -d {deps}/gomod ]; then export GOMODCACHE={deps}/gomod GOPROXY=off GOSUMDB=off; fi && '
            f'if [ -f {deps}/setup/go.sum ]; then cp go.mod /tmp/ce-x.go.mod && cp {deps}/setup/go.sum /tmp/ce-x.go.sum && '
            f'M="-mod=readonly -modfile=/tmp/ce-x.go.mod"; fi && ')


def _canon(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(',', ':'))


JSON_PAIR = re.compile(r'"([^"]{1,80})"\s*:\s*"([^"]*)"')
YAML_PAIR = re.compile(r'(?:^|\s)([A-Za-z_][\w./-]{0,80}):\s+([^\s",{}\[\]]+)')


def _stated(refs: list[dict]) -> dict:
    """Key -> values the example's own quoted lines give (JSON or YAML pairs; a key's last path segment)."""
    stated = {}
    for ref in refs:
        quote = ref.get('quote', '')
        for key, value in JSON_PAIR.findall(quote) + YAML_PAIR.findall(quote):
            stated.setdefault(_key(key.rsplit('/', 1)[-1]), set()).add(value)
    return stated


def apply_examples(snapshot: dict, proposals, sources: behavior.Sources) -> dict:
    """Register proposed examples; LITERAL values must occur verbatim in the task text or a public file read."""
    delta = {'added': [], 'rejected': []}
    corpus = [behavior._norm(sources.task)] + [behavior._norm(t) for t in sources.cache.values() if t]
    examples = snapshot.setdefault('examples', {})
    for item in (proposals if isinstance(proposals, list) else [])[:MAX_EXAMPLES]:
        if not isinstance(item, dict):
            continue
        name, kind = _name(item.get('name')), item.get('kind')
        if not NAME.match(name) or kind not in behavior.KINDS or name in examples:
            delta['rejected'].append({'item': item, 'reason': 'name must be short and new; kind target or preserve'})
            continue
        refs, why = behavior._verified(item.get('source_refs'), sources)
        if not refs:
            delta['rejected'].append({'item': item, 'reason': '; '.join(why)})
            continue
        fields, stated = {}, _stated(refs)
        for field in (item.get('fields') if isinstance(item.get('fields'), list) else [])[:MAX_FIELDS]:
            fname = _name(field.get('name')) if isinstance(field, dict) else ''
            if not NAME.match(fname):
                continue
            expected = field.get('expected')
            # Real run UPJET-AWS-2117: the trigger's quoted record says "external_name": "vpce-.../subnet-...", the
            # proposal expected the state id (which also occurs in the material); the executor then "fixed" the code
            # towards the wrong value. A value that contradicts the same key in the case's own quotes is not used.
            conflict = stated.get(_key(fname))
            if expected is not None and conflict and _canon(expected) not in conflict:
                fields[fname] = {'mode': 'DIFF', 'expected': None,
                                 'note': f'expected value contradicts the quoted record ({fname}: '
                                         f'{_short(sorted(conflict)[0], 80)})'}
            elif expected is not None and any(behavior._norm(_canon(expected)) in text for text in corpus):
                fields[fname] = {'mode': 'LITERAL', 'expected': _canon(expected)}
            else:
                fields[fname] = {'mode': 'DIFF', 'expected': None,
                                 **({'note': 'expected value not found verbatim in public material'}
                                    if expected is not None else {})}
            fields[fname]['meaning'] = behavior._norm(field.get('meaning', ''))[:200]
        if not fields:
            delta['rejected'].append({'item': item, 'reason': 'no usable field'})
            continue
        examples[name] = {'name': name, 'kind': kind, 'input': behavior._norm(item.get('input', ''))[:400],
                          'fields': fields, 'source_refs': refs, 'baseline': {}, 'usable': {}}
        delta['added'].append(name)
    return delta


# ---------------------------------------------------------------- driver
def run_command(baseline: str | None = None) -> str:
    """Build and run the driver through the public runtime and remove any copy it placed.

    With `baseline` (a tree or commit id) the driver runs against a copy of that version, made once."""
    root = '/work'
    prep = ''
    if baseline:
        if not re.fullmatch(r'[0-9a-f]{7,64}', baseline):
            raise ValueError('baseline must be a git object id')
        root = BASE
        prep = (f'if [ ! -f {BASE}/.ce-ready ]; then rm -rf {BASE} && mkdir -p {BASE} && '
                f'git -C /work -c safe.directory=* archive {baseline} | tar -x -C {BASE} && touch {BASE}/.ce-ready; fi && ')
    # The driver runs from inside the module it observes, so the baseline copy gets its own copy of it.
    driver = f'D=/work/{DIR}; ' if not baseline else \
        f'rm -rf {BASE}/{DIR} && mkdir -p {BASE}/{DIR} && cp /work/{DIR}/* {BASE}/{DIR}/ && D={BASE}/{DIR}; '
    inner = prep + setup(root) + driver + (
        f'if [ -f $D/package.txt ]; then P=$(head -n1 $D/package.txt); '
        f'case "$P" in ./*) ;; *) echo CE_DRIVER_ERROR bad package; exit 3;; esac; '
        f'T="$P/zz_ce_example_driver_test.go"; cp $D/driver_test.go "$T" && trap \'rm -f "$T"\' EXIT && '
        f'go test -count=1 $M -run "^TestCEExampleDriver$" -v "$P"; '
        f'else go run $M $D/main.go; fi')
    return 'ce-run /bin/sh -c ' + shlex.quote(inner)


def _key(name: str) -> str:
    return re.sub(r'[^a-z0-9]', '', name.lower())


def resolve_names(observed: dict, snapshot: dict) -> tuple[dict, dict]:
    """Read near-miss example and field names as the defined ones when exactly one defined name fits.

    An example name fits when its letters and digits equal, start with or contain a defined name's; a field
    name only when they are equal. Exact names win. Returns the observations and the renamings applied."""
    defined = snapshot.get('examples', {})
    out, renamed = {}, {}
    for (example, field), value in observed.items():
        name = example
        if example not in defined:
            k = _key(example)
            fits = [d for d in defined if _key(d) and (k == _key(d) or k.startswith(_key(d)) or _key(d) in k)]
            if len(fits) == 1:
                name = fits[0]
        fname = field
        if name in defined and field not in defined[name]['fields']:
            fits = [f for f in defined[name]['fields'] if _key(f) == _key(field)]
            if len(fits) == 1:
                fname = fits[0]
        if (name, fname) != (example, field):
            if (name, fname) in observed:
                continue
            renamed[f'{example}.{field}'] = f'{name}.{fname}'
        out[(name, fname)] = value
    return out, renamed


def output_tail(output: str, n: int = 12) -> str:
    lines = [line[:200] for line in (output or '').splitlines() if line.strip()]
    return '\n'.join(lines[-n:])


SKELETON_HEAD = r"""package main

// Example driver skeleton: copy to main.go, call the PRODUCTION code with each case's input and pass
// what it returns instead of nil (an error as err.Error()). Keep the example and field names.
import (
	"encoding/json"
	"fmt"
)

func ceObserve(example, field string, value any) {
	e, _ := json.Marshal(example)
	f, _ := json.Marshal(field)
	v, err := json.Marshal(value)
	if err != nil {
		v, _ = json.Marshal(fmt.Sprint(value))
	}
	fmt.Printf("CE_OBSERVE {\"example\": %s, \"field\": %s, \"value\": %s}\n", e, f, v)
}

func main() {
"""


def skeleton(snapshot: dict) -> str:
    """A Go main package that prints every field once; the executor replaces each nil with the production call."""
    calls = []
    for ex in snapshot.get('examples', {}).values():
        calls.append(f"\t// example {ex['name']!r} [{ex['kind']}]: input: {_short(ex['input'] or 'see its source', 160)}")
        for fname, spec in ex['fields'].items():
            calls.append(f"\tceObserve({json.dumps(ex['name'])}, {json.dumps(fname)}, nil) "
                         f"// TODO {_short(spec['meaning'] or 'value', 100)}")
    return SKELETON_HEAD + '\n'.join(calls) + '\n}\n'


def parse(output: str) -> dict:
    observed = {}
    for match in OBSERVE.finditer(output or ''):
        try:
            row = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get('example'), str) and isinstance(row.get('field'), str):
            observed[(row['example'], row['field'])] = _canon(row.get('value'))
    return observed


def driver_hash(read) -> str | None:
    texts = [read(f'/testbed/{DIR}/{n}') for n in ('main.go', 'driver_test.go', 'package.txt')]
    if not any(texts):
        return None
    return hashlib.sha256(json.dumps(texts).encode()).hexdigest()


def calibrate(snapshot: dict, observed: dict) -> dict:
    """Bind baseline observations; keep only fields that can speak about the fix."""
    report = {'usable': [], 'dropped': []}
    for ex in snapshot.get('examples', {}).values():
        for fname, spec in ex['fields'].items():
            key = (ex['name'], fname)
            if key not in observed:
                ok, why = False, 'not observed by the driver'
            else:
                ex['baseline'][fname] = observed[key]
                if spec['mode'] == 'LITERAL' and ex['kind'] == 'target':
                    ok, why = observed[key] != spec['expected'], 'already equal to the expected value on the original version'
                elif spec['mode'] == 'LITERAL':
                    ok, why = observed[key] == spec['expected'], 'differs from the expected value on the original version'
                else:
                    ok, why = True, ''
            ex['usable'][fname] = ok
            (report['usable'] if ok else report['dropped']).append({'example': ex['name'], 'field': fname,
                                                                    **({} if ok else {'why': why})})
    return report


def judge(snapshot: dict, observed: dict) -> list[dict]:
    rows = []
    for ex in snapshot.get('examples', {}).values():
        for fname, spec in ex['fields'].items():
            if not ex['usable'].get(fname):
                continue
            value = observed.get((ex['name'], fname))
            base = ex['baseline'].get(fname)
            if value is None:
                status, want = 'MISSING', spec['expected'] or ('≠ ' + base if ex['kind'] == 'target' else base)
            elif spec['mode'] == 'LITERAL':
                status, want = ('PASS' if value == spec['expected'] else 'FAIL'), spec['expected']
            elif ex['kind'] == 'target':
                status, want = ('CHANGED' if value != base else 'UNCHANGED'), '≠ ' + base
            else:
                status, want = ('PASS' if value == base else 'REGRESSED'), base
            rows.append({'example': ex['name'], 'kind': ex['kind'], 'field': fname, 'mode': spec['mode'],
                         'status': status, 'observed': value, 'expected': want})
    return rows


def failing(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r['status'] in ('FAIL', 'UNCHANGED', 'REGRESSED', 'MISSING')]


def render(rows: list[dict], header: str) -> str:
    lines = [header]
    for r in rows:
        mark = 'ok' if r['status'] in ('PASS', 'CHANGED') else 'NOT MET'
        lines.append(f"- {r['example']} [{r['kind']}] {r['field']}: {r['status']} ({mark}); observed "
                     f"{_short(r['observed'])}, expected {_short(r['expected'])}")
    if failing(rows):
        lines.append('Next: REPAIR - make the failing fields produce the expected values (they come from the public '
                     'task material; the driver only observes the production code). UNCHANGED means the target '
                     'case still behaves as on the original version; REGRESSED means a case that must keep working '
                     'changed.')
    return '\n'.join(lines)


def _short(value, n: int = 90) -> str:
    text = 'none' if value is None else str(value)
    return text if len(text) <= n else text[:n - 1] + '…'


class DriverWriter:
    """A short, separate tool loop that writes the driver; its messages never enter the executor's."""

    ACTION = re.compile(r'```bash\s*\n(.*?)\n\s*```', re.S)
    # DeepSeek's native tool-call markup (DSML), seen in real runs instead of a fenced block.
    DSML = re.compile(r'parameter name="command"[^>]*>(.*?)</[^<>]*parameter>', re.S)
    MAX_FORMAT_ERRORS = 6

    def __init__(self, model, run, steps: int, deadline, transcript: Path | None = None):
        self.model, self.run, self.steps, self.deadline = model, run, steps, deadline
        self.transcript = transcript

    def _command(self, content: str) -> str | None:
        actions = self.ACTION.findall(content) or self.DSML.findall(content)
        return actions[0].strip() if len(actions) == 1 and actions[0].strip() else None

    def write(self, brief: str, log) -> dict:
        messages = [{'role': 'system', 'content': DRIVER_PROMPT.replace('{dir}', DIR)},
                    {'role': 'user', 'content': brief}]
        steps = errors = 0
        status = 'STEPS'
        try:
            while steps < self.steps and errors < self.MAX_FORMAT_ERRORS:
                if self.deadline():
                    status = 'BUDGET'
                    break
                try:
                    reply = self.model.query(messages)
                except Exception as exc:
                    log('driver_error', reason=f'{type(exc).__name__}: {exc}'[:300])
                    status = 'ERROR'
                    break
                content = reply.get('content', '') if isinstance(reply, dict) else ''
                messages.append({'role': 'assistant', 'content': content})
                command = self._command(content)
                if command is None:
                    errors += 1
                    messages.append({'role': 'user', 'content': 'Reply with a THOUGHT and exactly one ```bash block '
                                                                'containing one command, as plain text.'})
                    continue
                if command.splitlines()[0].strip() == 'echo CE_DRIVER_DONE':
                    status = 'DONE'
                    break
                if command == 'ce-example-run':
                    command = run_command()
                steps += 1
                began = time.monotonic()
                result = self.run(command, 600 if 'ce-run' in command else 120)
                text = (result.get('output') or '')[-6000:]
                log('driver_step', step=steps, seconds=round(time.monotonic() - began, 1), returncode=result.get('returncode'))
                messages.append({'role': 'user', 'content': f"<returncode>{result.get('returncode')}</returncode>\n"
                                                            f"<output>\n{text}\n</output>"})
            else:
                status = 'FORMAT' if errors >= self.MAX_FORMAT_ERRORS else 'STEPS'
        finally:
            if self.transcript:
                self.transcript.parent.mkdir(parents=True, exist_ok=True)
                self.transcript.write_text(json.dumps(messages, indent=1, ensure_ascii=False))
        return {'status': status, 'steps': steps, 'format_errors': errors}


def brief(snapshot: dict, task: str, fixtures: list[tuple[str, str]]) -> str:
    """What the driver writer sees: the task, the fixtures, example names and fields - not which value is expected."""
    lines = ['Task text:', task[:6000], '']
    for path, text in fixtures:
        lines += [f'Public input file {path}:', text[:4000], '']
    lines.append('Examples to observe (print one CE_OBSERVE line per field):')
    for ex in snapshot.get('examples', {}).values():
        fields = ', '.join(f"{f} ({spec['meaning'] or 'value'})" for f, spec in ex['fields'].items())
        lines.append(f"- example {ex['name']!r}: input: {ex['input'] or 'see the quoted source'}; fields: {fields}")
    return '\n'.join(lines)


def save_driver_copy(read, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('main.go', 'driver_test.go', 'package.txt'):
        text = read(f'/testbed/{DIR}/{name}')
        if text:
            (directory / name).write_text(text)
