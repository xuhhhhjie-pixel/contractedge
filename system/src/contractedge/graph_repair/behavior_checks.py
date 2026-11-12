"""Check tasks for behaviour obligations, one execution path, and the `go test -json` adapter.

A check is an existing public test selected by package and `-run` pattern; the command is built
here from a structured runner spec, never from model text. Evidence is written only from the
adapter's reading of an actual run and is bound to the source tree before and after the run,
the check definition and the runtime identity. Exit codes alone, grep matches, unselected tests,
build or environment failures are never behaviour evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from pathlib import Path

from contractedge.graph_repair import behavior

ADAPTER = 'go_test_json'
RESULTS = ('PASS', 'SEMANTIC_FAIL', 'BUILD_FAIL', 'INFRA_ERROR', 'TIMEOUT', 'INVALID_CHECK', 'UNCLASSIFIED')
PACKAGE = re.compile(r'^\.(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$')
RUN = re.compile(r'^[A-Za-z0-9_^$|()\[\].*+?/-]{1,200}$')
INFRA = re.compile(r'read-only file system|no space left on device|missing go\.sum entry|cannot find module providing|'
                   r'dial tcp|proxy\.golang\.org|go: downloading|permission denied|failed to initialize build cache|'
                   r'GOCACHE is not defined|exec: "go": executable file not found')
LAYOUT = re.compile(r'go\.mod file not found|directory prefix \S+ does not contain main module|'
                    r'is not in std|no Go files in|matched no packages|cannot find package')


RUNNER = 'go-test-json/3'
# The public runtime has a read-only root and no network. Cases ship their modules under
# /work/.contractedge-deps (gomod, setup/go.sum) and the published build points Go at them; the
# check uses the same setup, read from the case, and never writes the module files it is given.
# The runtime's own GOCACHE is a tmpfs dropped after every ce-run, so each check rebuilt the package
# (about 250 s on provider-upjet-aws). Checks keep their build cache in the work tree's ignored
# dependency directory instead (same run, content-addressed by Go; about 5 s once warm). It is not
# part of the exported patch (/.contractedge-deps/ is excluded from git).
SETUP = ('cd /work && export GOTOOLCHAIN=local && M= && '
         'if [ -d .contractedge-deps ] && mkdir -p .contractedge-deps/gocache 2>/dev/null; then '
         'export GOCACHE=/work/.contractedge-deps/gocache; fi && '
         'if [ -d .contractedge-deps/gomod ]; then export GOMODCACHE=/work/.contractedge-deps/gomod GOPROXY=off '
         'GOSUMDB=off; fi && if [ -f .contractedge-deps/setup/go.sum ]; then cp go.mod /tmp/ce-check.go.mod && '
         'cp .contractedge-deps/setup/go.sum /tmp/ce-check.go.sum && M="-mod=readonly -modfile=/tmp/ce-check.go.mod"; fi && ')


def def_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps({'adapter': ADAPTER, 'runner': spec, 'runner_version': RUNNER},
                                     sort_keys=True).encode()).hexdigest()


def command(spec: dict) -> str:
    """Shell for the method container; the test runs through ce-run in the public runtime (/work)."""
    inner = SETUP + f"exec go test -json -count=1 $M -run {shlex.quote(spec['run'])} {shlex.quote(spec['package'])}"
    return 'ce-run /bin/sh -c ' + shlex.quote(inner)


# ---------------------------------------------------------------- planning
def _top(name: str) -> str:
    return name.split('/', 1)[0]


def validate(item, snapshot: dict, offered: dict) -> tuple[dict | None, str]:
    """Check one proposed check against grounded obligations and the tests actually offered.

    `offered` maps package ('./pkg/x') -> list of top-level test function names found in it.
    """
    if not isinstance(item, dict):
        return None, 'not an object'
    if set(item) - {'obligations', 'scenario', 'package', 'run'}:
        return None, 'only obligations, scenario, package and run are accepted (no free commands)'
    ids = item.get('obligations')
    if not isinstance(ids, list) or not ids:
        return None, 'obligations are required'
    for oid in ids:
        ob = snapshot['obligations'].get(oid)
        if ob is None or ob['grounding'] != 'GROUNDED':
            return None, f'{oid} is not a grounded obligation'
    package, pattern = item.get('package', ''), item.get('run', '')
    if not isinstance(package, str) or not PACKAGE.match(package) or '/..' in package:
        return None, f'package {package!r} must look like ./dir/subdir'
    if package not in offered:
        return None, f'package {package} was not offered'
    if not isinstance(pattern, str) or not RUN.match(pattern):
        return None, f'run pattern {pattern!r} has characters outside a test-name regular expression'
    try:
        top = re.compile(pattern.split('/', 1)[0])
    except re.error as exc:
        return None, f'run pattern does not compile: {exc}'
    if not any(top.search(name) for name in offered[package]):
        return None, f'run pattern matches none of the tests offered in {package}'
    scenario = behavior._norm(item.get('scenario') or pattern)[:200]
    return {'obligations': ids, 'scenario': scenario, 'spec': {'kind': 'go_test', 'package': package, 'run': pattern}}, ''


def plan(snapshot: dict, proposals, offered: dict, timeout: int) -> dict:
    """Register proposed checks; a check already registered with the same definition gains the new links."""
    delta = {'added': [], 'linked': [], 'rejected': []}
    checks = snapshot['checks']
    by_hash = {c['def_hash']: c for c in checks.values()}
    for item in proposals if isinstance(proposals, list) else []:
        value, why = validate(item, snapshot, offered)
        if value is None:
            delta['rejected'].append({'item': item, 'reason': why})
            continue
        digest = def_hash(value['spec'])
        check = by_hash.get(digest)
        if check is None:
            cid = f'C{1 + max((int(k[1:]) for k in checks), default=0)}'
            check = {'id': cid, 'obligation_refs': [], 'scenario': value['scenario'], 'runner_spec': value['spec'],
                     'adapter': ADAPTER, 'def_hash': digest, 'timeout': int(timeout), 'status': 'ACTIVE',
                     'baseline_result': None}
            checks[cid] = by_hash[digest] = check
            delta['added'].append(cid)
        for oid in value['obligations']:
            ob = snapshot['obligations'][oid]
            if not any(r['id'] == oid and r['version'] == ob['version'] for r in check['obligation_refs']):
                check['obligation_refs'] = [r for r in check['obligation_refs'] if r['id'] != oid] + \
                    [{'id': oid, 'version': ob['version']}]
                delta['linked'].append([check['id'], oid])
            if check['id'] not in ob['check_ids']:
                ob['check_ids'].append(check['id'])
    return delta


def live_refs(snapshot: dict, check: dict) -> list[dict]:
    """Links that still speak for a grounded obligation at its current version."""
    out = []
    for ref in check['obligation_refs']:
        ob = snapshot['obligations'].get(ref['id'])
        if ob and ob['grounding'] == 'GROUNDED' and ob['version'] == ref['version']:
            out.append(ref)
    return out


def has_evidence(snapshot: dict, check: dict, tree: str, env: str) -> bool:
    return any(e['check_id'] == check['id'] and e['identity']['def_hash'] == check['def_hash']
               and e['identity']['tree_before'] == tree and e['identity']['env'] == env for e in snapshot['evidence'])


def pending(snapshot: dict, tree: str, env: str) -> list[dict]:
    """Active checks with live links and no evidence for this tree; failing ones first, then never run."""
    rows = []
    for check in snapshot['checks'].values():
        refs = live_refs(snapshot, check)
        if check['status'] != 'ACTIVE' or not refs or has_evidence(snapshot, check, tree, env):
            continue
        statuses = [row.get('status') for ref in refs
                    for row in [snapshot['obligations'][ref['id']]['outcomes'].get(check['scenario'], {})]]
        rank = 0 if 'FAIL_AT_SNAPSHOT' in statuses else 1 if not any(statuses) else 2
        rows.append((rank, int(check['id'][1:]), check))
    return [c for _, _, c in sorted(rows, key=lambda r: r[:2])]


# ---------------------------------------------------------------- adapter
def classify(output: str, returncode: int, timed_out: bool = False) -> dict:
    """Read `go test -json` output. Unknown shapes are UNCLASSIFIED, never guessed from the exit code."""
    value = {'result': 'UNCLASSIFIED', 'tests': {}, 'failing': [], 'excerpt': '', 'detail': ''}
    if timed_out or returncode == 124:
        return {**value, 'result': 'TIMEOUT', 'detail': 'the check timed out'}
    if returncode in (125, 126, 127):
        return {**value, 'result': 'INFRA_ERROR', 'detail': f'runner exit {returncode}: ' + output.strip()[-200:]}
    events, plain = [], []
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            plain.append(line)
            continue
        if isinstance(row, dict) and 'Action' in row:
            events.append(row)
        else:
            plain.append(line)
    tests, outputs = {}, {}
    for row in events:
        name = row.get('Test')
        if name and row['Action'] in ('pass', 'fail', 'skip'):
            tests[name] = row['Action']
        if name and row['Action'] == 'output':
            outputs.setdefault(name, []).append(row.get('Output', ''))
    texts = ' '.join(plain + [row.get('Output', '') for row in events])
    build_failed = any(row['Action'] == 'build-fail' for row in events) or \
        re.search(r'\[(?:build|setup) failed\]', texts) is not None
    value['tests'] = tests
    if INFRA.search(texts) and (build_failed or not tests):
        return {**value, 'result': 'INFRA_ERROR', 'detail': INFRA.search(texts).group(0)}
    if LAYOUT.search(texts) and not tests:
        return {**value, 'result': 'INVALID_CHECK', 'detail': LAYOUT.search(texts).group(0)}
    if build_failed:
        lines = [l.strip() for l in plain + [r.get('Output', '') for r in events if r['Action'] == 'build-output']
                 if re.search(r'\.go:\d+', l)]
        return {**value, 'result': 'BUILD_FAIL', 'detail': 'the package does not build',
                'excerpt': '\n'.join(lines[:8])[:600]}
    failing = sorted(n for n, a in tests.items() if a == 'fail')
    if failing:
        lines = []
        for name in failing:
            for text in outputs.get(name, []):
                text = text.rstrip()
                if text.strip() and not re.match(r'^\s*(?:=== |--- |FAIL|PASS|ok\s)', text):
                    lines.append(f'{name}: {text.strip()}')
        return {**value, 'result': 'SEMANTIC_FAIL', 'failing': failing, 'excerpt': '\n'.join(lines[:12])[:900],
                'detail': f'{len(failing)} failing test(s)'}
    ran = [n for n, a in tests.items() if a == 'pass']
    if not events and not ran:
        return {**value, 'detail': f'exit {returncode} without go test -json events'}
    if not ran:
        why = 'only skipped tests' if tests else 'no test was selected'
        return {**value, 'result': 'INVALID_CHECK', 'detail': why}
    if returncode == 0:
        return {**value, 'result': 'PASS', 'detail': f'{len(ran)} test(s) passed'}
    return {**value, 'detail': f'exit {returncode} without a failing test'}


# ---------------------------------------------------------------- execution
COLD_RESULTS = ('TIMEOUT', 'INFRA_ERROR', 'UNCLASSIFIED')     # the package build may not have finished


def timeout_for(check: dict, snapshot: dict, cold_timeout: int) -> int:
    """The first check of a package builds it from scratch; later ones reuse the build cache."""
    warm = snapshot.get('warm_packages', [])
    return check['timeout'] if check['runner_spec']['package'] in warm else max(check['timeout'], int(cold_timeout))


def execute(check: dict, snapshot: dict, run, tree, env: str, log_dir: Path, timeout: int | None = None) -> dict:
    """The only way evidence is produced: run, read with the adapter, bind to identities.

    `run(command, timeout)` executes in the method container and returns mini-swe-agent's dict;
    `tree()` returns the current source tree id (or None when it cannot be read).
    """
    before = tree()
    began = time.monotonic()
    result = run(command(check['runner_spec']), timeout or check['timeout'])
    seconds = round(time.monotonic() - began, 1)
    after = tree()
    reading = classify(result.get('output', ''), result.get('returncode', 0), bool(result.get('timed_out')))
    n = 1 + len(snapshot['evidence'])
    log = Path(log_dir) / f'E{n}-{check["id"]}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(result.get('output', ''))
    if before is None or after is None:
        reading = {**reading, 'result': 'UNCLASSIFIED', 'detail': 'source identity could not be read'}
    elif before != after:
        reading = {**reading, 'result': 'INVALID_CHECK', 'detail': 'the source tree changed while the check ran'}
    refs = live_refs(snapshot, check)
    evidence = {'id': f'E{n}', 'check_id': check['id'], 'scenario': check['scenario'],
                'obligation_versions': {r['id']: r['version'] for r in refs},
                'identity': {'tree_before': before, 'tree_after': after, 'def_hash': check['def_hash'], 'env': env},
                'result': reading['result'], 'tests': reading['tests'], 'failing': reading['failing'],
                'excerpt': reading['excerpt'], 'detail': reading['detail'], 'returncode': result.get('returncode'),
                'log_ref': str(log), 'duration': seconds}
    snapshot['evidence'].append(evidence)
    counters = snapshot['counters']
    counters['check_runs'] = counters.get('check_runs', 0) + 1
    counters['check_seconds'] = round(counters.get('check_seconds', 0) + seconds, 1)
    package = check['runner_spec']['package']
    if reading['result'] not in COLD_RESULTS and package not in snapshot.setdefault('warm_packages', []):
        snapshot['warm_packages'].append(package)
    if reading['result'] == 'INVALID_CHECK' and before == after:
        check['status'] = 'INVALID'           # same definition would be invalid again
        check['invalid_reason'] = reading['detail']
    return evidence
