"""Graph-repair experiment scheduler: prepare cases, create run slots, generate, score, account.

Runs in the experiment root (copied there with job_worker.py). Every model run is a once-only
slot `attempts-<TAG>/<method>` with a fresh copy of the baseline source; nothing is resampled.
Hidden scores are read only after a run is frozen and only into results/, never by a method.

  python3 graph_eval.py runs PHASE         # write runs-PHASE.json from the frozen lists
  python3 graph_eval.py daemon PHASE       # durable loop; safe to restart (adopts live jobs)
  python3 graph_eval.py status
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

E = Path(__file__).resolve().parent
sys.path.insert(0, str(E))
from custody import read, save, sha, verify_candidate  # noqa: E402

CREDENTIALS = '/home/zyw/.config/contractedge-deepseek-official/api.env'
GIB = 2 ** 30
LIVE, GRAPH = 'live-swe-agent', 'contractedge-graph'
GROUPS = {'A': (GRAPH, 'full'), 'B': (LIVE, None), 'C': (GRAPH, 'no-probes'), 'D': (GRAPH, 'no-closure'),
          'E': (GRAPH, 'no-stop-rule'), 'F': (GRAPH, 'submit-only'), 'Z': (GRAPH, 'live-equivalent')}
REPORT_KEYS = ('case_id', 'tag', 'group', 'rep', 'method', 'variant', 'phase')


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def log(event, **value):
    with (E / 'events.jsonl').open('a') as f:
        f.write(json.dumps({'utc': utc(), 'event': event, **value}) + '\n')


def settings():
    return read(E / 'SETTINGS.json')


# ---------------------------------------------------------------- run lists
def runs_for(phase: str) -> list[dict]:
    lists = read(E / 'freeze/EVALUATION_LISTS.json')
    plan = {c['case_id'] for c in read(E / 'BATCH_PLAN.json')['cases']}
    out = []

    def add(cases, group, reps):
        for rep in range(1, reps + 1):
            for cid in cases:
                method, variant = GROUPS[group]
                out.append({'case_id': cid, 'group': group, 'rep': rep, 'method': method, 'variant': variant,
                            'tag': f'{phase}-{group}{rep}', 'phase': phase})
    s = settings()['phases'][phase]
    for group, spec in s['groups'].items():
        cases = []
        for name in spec['lists']:
            cases += lists[name] if name in lists else [name]
        cases = [c for c in dict.fromkeys(cases) if c in plan and c not in spec.get('exclude', [])]
        add(cases, group, spec['reps'])
    # Interleave: for each case, alternate groups and repetitions so A and B run side by side in time.
    order = {g: i for i, g in enumerate(s['groups'])}
    cases = list(dict.fromkeys(r['case_id'] for r in out))
    out.sort(key=lambda r: (cases.index(r['case_id']), r['rep'], order[r['group']]))
    return out


# ---------------------------------------------------------------- jobs
def job_dir(key):
    return E / 'jobs' / key


def launch(key, argv, env, timeout):
    folder = job_dir(key)
    folder.mkdir(parents=True)
    spec = {'argv': [str(a) for a in argv], 'env': {k: str(v) for k, v in env.items()}, 'timeout_seconds': timeout}
    save(folder / 'REQUEST.json', spec)
    subprocess.Popen([sys.executable, str(E / 'job_worker.py'), str(folder / 'REQUEST.json')], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    log('LAUNCHED', job=key)


def alive(folder):
    try:
        meta = read(folder / 'STARTED.json')
        return Path('/proc', str(meta['pid']), 'stat').read_text().split()[21] == meta['start_ticks']
    except (FileNotFoundError, ProcessLookupError, KeyError):
        return False


def job_state(key):
    folder = job_dir(key)
    if not folder.exists():
        return 'NEW'
    if (folder / 'DONE.json').exists():
        return 'DONE'
    if (folder / 'STARTED.json').exists() and not alive(folder):
        return 'LOST'
    return 'RUNNING'


def base_env():
    env = {'PATH': str(E / 'bin') + ':' + os.environ['PATH'], 'PYTHONPATH': str(E / 'controller') + ':' + str(E),
           'PYTHONDONTWRITEBYTECODE': '1', 'HOME': os.environ['HOME']}
    return env


# ---------------------------------------------------------------- prepare / slots
def case_root(cid):
    return E / 'cases' / cid


def prepared(cid):
    return (case_root(cid) / 'QUALIFIED.json').exists()


def start_prepare(cid):
    launch('prepare-' + cid, [E / 'bin/python3', E / 'prepare_case150.py', cid], base_env(), 24 * 3600)


def make_slot(run):
    cid, tag, method = run['case_id'], run['tag'], run['method']
    active = case_root(cid) / 'active-case'
    slot_root = active / ('attempts-' + tag)
    out = slot_root / method
    if (slot_root / 'SLOT_FROZEN.json').exists():
        return
    assert not out.exists(), 'Inspect an incomplete slot instead of overwriting it'
    template = read(active / 'attempts' / method / 'profile.json')
    task = (active / 'attempts' / method / 'task.txt').read_bytes()
    record = read(active / 'baseline/workspace.json')
    config = read(active / 'case-config.json')
    workspace = active / 'workspaces' / (tag + '-' + method)
    source = workspace / 'source'
    shutil.copytree(record['source_root'], source, symlinks=True)
    for mount in template.get('public_dependency_mounts', []):
        target = Path(str(mount['target']).replace('/testbed/', str(source) + '/', 1).replace('/work/', str(source) + '/', 1))
        if target.is_relative_to(source):
            target.mkdir(parents=True, exist_ok=True)
    (source / '.git/info').mkdir(exist_ok=True)
    with (source / '.git/info/exclude').open('a') as exclude:
        exclude.write('\n/.contractedge-deps/\n/.ce75-public-logs/\n')
    for path in [source, *source.rglob('*')]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o666 | (0o111 if path.is_dir() else 0))
    mounts = []
    for i, mount in enumerate(template.get('public_dependency_mounts', [])):
        if mount.get('writable'):
            copy = active / 'method-public-deps' / (tag + '-' + method) / str(i)
            shutil.copytree(mount['source'], copy, symlinks=True)
            mount = {**mount, 'source': str(copy)}
        mounts.append(mount)
    short = config['case_short_id']
    socket = Path('/run/user') / str(os.getuid()) / 'cep5-public' / (short + '-' + tag + '-' + method)
    socket.mkdir(parents=True)
    socket.chmod(0o755)
    out.mkdir(parents=True)
    (out / 'public-logs').mkdir()
    (out / 'task.txt').write_bytes(task)
    profile = {**template, 'source_root': str(source), 'task_path': str(out / 'task.txt'),
               'public_logs': str(out / 'public-logs'), 'public_socket_dir': str(socket), 'public_dependency_mounts': mounts,
               'public_container_prefix': short + '-' + tag + '-' + method + '-public-'}
    if template.get('special_public_contract'):
        scratch = Path('/run/user') / str(os.getuid()) / 'ce75o-public-scratch' / short / (tag + '-' + method)
        scratch.mkdir(parents=True)
        scratch.chmod(0o700)
        profile['special_scratch_root'] = str(scratch)
    (out / 'profile.json').write_text(json.dumps(profile, indent=2))
    save(workspace / 'workspace.json', {**record, 'source_root': str(source), 'inputs_root': template['inputs_root']})
    assert sha(out / 'task.txt') == read(active / 'EXECUTION_PROTOCOL_FROZEN.json')['task_prompts'][method]
    save(slot_root / 'SLOT_FROZEN.json', {'case_id': cid, 'tag': tag, 'created_utc': utc(), 'new_model_requests': 0,
                                          'profiles': {method: sha(out / 'profile.json')},
                                          'task_prompts': {method: sha(out / 'task.txt')},
                                          'variants': {method: run['variant']}, 'group': run['group'], 'rep': run['rep'],
                                          'ce_tools_manifest_sha256': sha(E / 'ce-tools/MANIFEST.json'),
                                          'source_copied_from': record['source_root']})
    log('SLOT_CREATED', case_id=cid, tag=tag, method=method)


def start_generate(run):
    env = {**base_env(), 'CE_RUN_ROOT': str(case_root(run['case_id'])), 'CE_RUN_VERSION': run['tag'],
           'CE_PREFLIGHT_CREDENTIAL_FILE': CREDENTIALS}
    if run['variant']:
        env['CE_GRAPH_VARIANT'] = run['variant']
    launch(gen_key(run), [E / 'bin/python3', E / 'controller/run_remote_method.py', run['method']], env, 4800)


def gen_key(run):
    return f"generate-{run['case_id']}-{run['tag']}-{run['method']}"


def score_key(run):
    return f"score-{run['case_id']}-{run['tag']}-{run['method']}"


def attempt_dir(run):
    return case_root(run['case_id']) / 'active-case' / ('attempts-' + run['tag']) / run['method']


def archive_paths(run):
    folder = case_root(run['case_id']) / 'active-case' / ('archives-' + run['tag'])
    return folder / (run['method'] + '.tar.gz'), folder / (run['method'] + '.ARCHIVE.json')


def verify_budget_stopped(archive, receipt, method, cid):
    """custody.verify_candidate without its 'no in-flight request' assertion.

    A wall-clock stop can leave the last request unsettled; that run is a budget-exhausted,
    unsubmitted (diagnostic) candidate. Every byte and identity check is kept.
    """
    import hashlib
    import tarfile
    pin = read(receipt)
    digest = sha(archive)
    assert digest == pin['sha256'] and pin['root'] == method
    with tarfile.open(archive, 'r:gz') as t:
        members = {m.name: m for m in t.getmembers() if m.isfile()}
        for name, m in members.items():
            expected = pin['manifest'][str(Path(name).relative_to(method))]
            assert m.size == expected['bytes'] and hashlib.sha256(t.extractfile(m).read()).hexdigest() == expected['sha256'], name
        frozen = json.loads(t.extractfile(members[method + '/CANDIDATE_FROZEN.json']).read())
        patch_sha = hashlib.sha256(t.extractfile(members[method + '/candidate.patch']).read()).hexdigest()
    assert frozen['case_id'] == cid and frozen['method'] == method and frozen['patch_sha256'] == patch_sha
    assert frozen['requests_failed'] == 0 and frozen['execution_status'] == 'BUDGET_EXHAUSTED'
    return {'case_id': cid, 'method': method, 'archive_sha256': digest, 'patch_sha256': patch_sha, 'frozen': frozen,
            'requests_outcome_unknown': frozen['requests_outcome_unknown']}


class InfraInterrupted(RuntimeError):
    """The job-level safety timeout stopped the run before its supervisor finished (host overload)."""


def account_interrupted(run, out) -> str:
    """Bill the requests an interrupted run made; the slot itself is not scored (no partial evidence)."""
    sys.path.insert(0, str(E / 'controller'))
    from billing import build as build_billing
    cost, tokens, requests = None, 0, 0
    if (out / 'wire/usage.jsonl').exists():
        build_billing(out / 'wire/usage.jsonl', E / 'controller/pricing.json', out)
        billing = read(out / 'billing_summary.json')
        cost = billing.get('calculated_cost')
        rows = [json.loads(l) for l in (out / 'wire/usage.jsonl').read_text().splitlines() if l.strip()]
        tokens = sum(r.get('usage', {}).get('total_tokens', 0) for r in rows if r.get('phase') == 'request_finished')
        requests = sum(r.get('phase') == 'request_started' for r in rows)
    ledger_add({**{k: run[k] for k in ('case_id', 'tag', 'method', 'variant', 'group', 'phase')}, 'cost_cny': cost,
                'known_total_tokens': tokens, 'requests': requests, 'unresolved_requests': None,
                'collected_utc': utc() + ' INFRA_INTERRUPTED'})
    return f'job safety timeout during host overload after {requests} requests; cost {cost} accounted; one infrastructure re-run allowed'


def collect_generation(run):
    out = attempt_dir(run)
    done = read(job_dir(gen_key(run)) / 'DONE.json')
    if done.get('interrupted') and not (out / 'SUPERVISOR_RESULT.json').exists():
        raise InfraInterrupted(account_interrupted(run, out))
    worker_log = (job_dir(gen_key(run)) / 'worker.log').read_text(errors='replace')
    if not (out / 'EXECUTION_OUTCOME.json').exists() and (out / 'SUPERVISOR_RESULT.json').exists() \
            and 'request_settlement.py' in worker_log and 'AssertionError' in worker_log:
        # Model run ended; only the zero-model tail failed (shared API user id). No resampling.
        subprocess.run([sys.executable, str(E / 'complete_slot.py'), run['case_id'], run['tag'], run['method']],
                       env={**os.environ, **base_env()}, check=True, capture_output=True, timeout=1800)
        log('SLOT_TAIL_COMPLETED', **{k: run[k] for k in REPORT_KEYS})
    outcome = read(out / 'EXECUTION_OUTCOME.json')
    billing = read(out / 'billing_summary.json') if (out / 'billing_summary.json').exists() else {}
    archive, receipt = archive_paths(run)
    unknown = outcome.get('requests_outcome_unknown', 0)
    if unknown and outcome['execution_status'] == 'BUDGET_EXHAUSTED':
        identity = verify_budget_stopped(archive, receipt, run['method'], run['case_id'])
    else:
        identity = verify_candidate(archive, receipt, run['method'], run['case_id'])
    row = {**{k: run[k] for k in REPORT_KEYS}, 'job_returncode': done['returncode'], 'interrupted': done['interrupted'],
           'execution_status': outcome['execution_status'], 'requests': outcome['requests_finished'],
           'requests_failed': outcome['requests_failed'], 'requests_unknown': outcome['requests_outcome_unknown'],
           'known_total_tokens': identity['frozen']['known_total_tokens'], 'cost_cny': billing.get('calculated_cost'),
           'unresolved_requests': billing.get('unresolved_requests'), 'requests_outcome_unknown': unknown, 'patch_sha256': identity['patch_sha256'],
           'archive_sha256': identity['archive_sha256'], 'supervisor_stop_reason': outcome.get('supervisor_stop_reason'),
           'collected_utc': utc()}
    save(E / 'generation' / run['case_id'] / (run['tag'] + '-' + run['method'] + '.json'), row)
    ledger_add(row)
    log('GENERATION_COLLECTED', **{k: run[k] for k in REPORT_KEYS}, status=row['execution_status'], cost=row['cost_cny'])
    return row


def start_score(run):
    archive, receipt = archive_paths(run)
    out = E / 'scoring-private' / run['case_id'] / (run['tag'] + '-' + run['method'])
    env = {k: v for k, v in base_env().items()}
    env.update(PYTHONPATH=str(E / 'controller') + ':' + str(E / 'zero-model-guard'), CE_NO_MODEL='1',
               CE_ZERO_MODEL_ATTEMPTS=str(E / 'evidence/score-model-attempts.jsonl'),
               DOCKER_HOST=read(case_root(run['case_id']) / 'runtime/READY.json')['socket'])
    with __import__('tarfile').open(archive) as t:
        patch = t.extractfile(run['method'] + '/candidate.patch').read()
    if len(patch) > 4 * 1024 * 1024:
        out.mkdir(parents=True)
        save(out / 'SCORE_RECEIPT.json', {'case_id': run['case_id'], 'method': run['method'], 'score': 'INVALID_PATCH',
                                          'reason': 'patch exceeds 4 MiB (published validator limit)', 'scorer_exit_code': 1,
                                          'end_to_end_success': False, 'runtime_executed': False})
        save(job_dir(score_key(run)) / 'DONE.json', {'returncode': 0, 'static': True, 'interrupted': False, 'error': None})
        return
    launch(score_key(run), [E / 'bin/python3', E / 'controller/score_with_telemetry.py', '--case-root',
                            case_root(run['case_id']) / 'active-case', '--archive', archive, '--receipt', receipt,
                            '--method', run['method'], '--output', out], env, 4 * 3600)


def collect_score(run):
    out = E / 'scoring-private' / run['case_id'] / (run['tag'] + '-' + run['method'])
    receipt = read(out / 'SCORE_RECEIPT.json') if (out / 'SCORE_RECEIPT.json').exists() else {}
    score = read(out / 'score/score.json') if (out / 'score/score.json').exists() else {}
    gen = read(E / 'generation' / run['case_id'] / (run['tag'] + '-' + run['method'] + '.json'))
    row = {**gen, 'score': receipt.get('score'), 'scorer_exit_code': receipt.get('scorer_exit_code'),
           'end_to_end_success': receipt.get('end_to_end_success'), 'score_reason': score.get('reason_code') or receipt.get('reason'),
           'scored_utc': utc()}
    save(E / 'results' / run['case_id'] / (run['tag'] + '-' + run['method'] + '.json'), row)
    log('SCORED', **{k: run[k] for k in REPORT_KEYS}, infrastructure=bool(receipt))
    return row


def cleanup_case(cid, runs):
    """Remove per-slot source copies once every slot of this phase is scored; evidence stays."""
    active = case_root(cid) / 'active-case'
    removed = []
    for run in runs:
        for path in [active / 'workspaces' / (run['tag'] + '-' + run['method']), active / 'export-work' / (run['tag'] + '-' + run['method']),
                     active / 'method-public-deps' / (run['tag'] + '-' + run['method'])]:
            if path.exists() and path.resolve().is_relative_to(active.resolve()):
                try:
                    shutil.rmtree(path)
                except PermissionError:
                    remove_in_container(cid, path)   # files written under container sub-uids
                removed.append(str(path))
    log('CASE_SLOTS_CLEANED', case_id=cid, removed=len(removed))


EXPERIMENT_START = '2026-11-08T16:30:00Z'   # images tagged after this were loaded by this experiment
RELEASE_TREES = ['workspaces', 'baseline', 'qualification-pristine-export-copy', 'package', 'public-deps',
                 'method-public-deps', 'runtime-subset', 'export-work']


def loaded_after_start(stamp: str) -> bool:
    """Docker LastTagTime (RFC 3339, any offset, nanoseconds) after this experiment started."""
    m = re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)$', stamp or '')
    if not m:
        return False
    when = datetime.datetime.fromisoformat(m.group(1) + ('+00:00' if m.group(2) == 'Z' else m.group(2)))
    return when > datetime.datetime.fromisoformat(EXPERIMENT_START.replace('Z', '+00:00'))


def release_case(cid):
    """After a case's last phase: drop reproducible trees and this experiment's runtime image.

    Run evidence (attempts-*, archives-*, smoke, preflight-archives, evidence, JSON records) stays.
    The trees are re-creatable from the hard-linked release shards by preparing the case again.
    """
    active = case_root(cid) / 'active-case'
    removed = []
    for name in RELEASE_TREES:
        path = active / name
        if path.exists() and not path.is_symlink():
            try:
                shutil.rmtree(path)
            except PermissionError:
                remove_in_container(cid, path)
            removed.append(name)
    for path in [active / 'transport.tar.gz', case_root(cid) / 'transport.tar.gz']:
        if path.exists():
            path.unlink()
            removed.append(path.name)
    image = None
    published = active / 'evidence/PUBLISHED_IMAGE.json'
    if published.exists():
        ready = read(case_root(cid) / 'runtime/READY.json')
        env = {**os.environ, 'DOCKER_HOST': ready['socket']}
        value = read(published)
        tagged = value.get('Metadata', {}).get('LastTagTime', '') or ''
        users = subprocess.run([ready['docker'], 'ps', '-a', '-q', '--filter', 'ancestor=' + value['Id']], env=env,
                               capture_output=True, text=True).stdout.split()
        others = [c for c in read(E / 'BATCH_PLAN.json')['cases'] if c['case_id'] != cid and prepared(c['case_id'])
                  and not (E / 'status' / f'RELEASED-{c["case_id"]}.json').exists()
                  and (case_root(c['case_id']) / 'active-case/evidence/PUBLISHED_IMAGE.json').exists()
                  and read(case_root(c['case_id']) / 'active-case/evidence/PUBLISHED_IMAGE.json')['Id'] == value['Id']]
        mine = loaded_after_start(tagged)
        if mine and not users and not others:
            done = subprocess.run([ready['docker'], 'image', 'rm', value['Id']], env=env, capture_output=True, text=True)
            image = {'id': value['Id'], 'removed': done.returncode == 0, 'stderr': done.stderr[-300:]}
        else:
            image = {'id': value['Id'], 'removed': False, 'kept_because': 'in use or shared' if mine
                     else 'loaded before this experiment (or unknown tag time)', 'last_tag_time': tagged}
    save(E / 'status' / f'RELEASED-{cid}.json', {'utc': utc(), 'removed': removed, 'image': image,
                                                 'evidence_kept': True})
    log('CASE_RELEASED', case_id=cid, removed=removed, image=image)


def remove_in_container(cid, path):
    """Delete a slot copy through the rootless daemon: container root owns the mapped sub-uids."""
    active = case_root(cid) / 'active-case'
    assert path.resolve().is_relative_to(active.resolve()) and path.name and not path.is_symlink()
    ready = read(case_root(cid) / 'runtime/READY.json')
    image = read(active / 'case-config.json')['method_images'][LIVE]
    subprocess.run([ready['docker'], 'run', '--rm', '--pull=never', '--network=none', '--entrypoint', '/bin/sh',
                    '-v', f'{path.parent}:/w', image, '-c', f'rm -rf -- "/w/{path.name}"'],
                   env={**os.environ, 'DOCKER_HOST': ready['socket']}, check=True, capture_output=True, timeout=900)
    if path.exists():
        shutil.rmtree(path)


# ---------------------------------------------------------------- money
def ledger():
    path = E / 'money-ledger.json'
    return read(path) if path.exists() else {'schema': 'ce.graph_repair.money_ledger.v1', 'runs': []}


def ledger_add(row):
    value = ledger()
    value['runs'].append({k: row[k] for k in ('case_id', 'tag', 'method', 'variant', 'group', 'phase', 'cost_cny',
                                              'known_total_tokens', 'requests', 'unresolved_requests', 'collected_utc')})
    save(E / 'money-ledger.json', value)


def spent():
    return sum((Decimal(str(r['cost_cny'] or 0)) for r in ledger()['runs']), Decimal(0))


def latest_balance():
    """Newest account balance snapshot taken by a run supervisor (amount only, no credentials)."""
    snapshots = sorted(E.glob('cases/*/active-case/attempts-*/*/balance-after.json'), key=lambda p: p.stat().st_mtime)
    for path in reversed(snapshots):
        value = read(path)
        for row in value.get('balance_infos', []):
            if row.get('currency') == 'CNY':
                return Decimal(row['total_balance'])
    return None


def money_ok(inflight):
    s = settings()
    cap = Decimal(s['spend_cap_cny'])
    reserve = Decimal(s['reserve_per_run_cny']) * (inflight + 1)
    balance = latest_balance()
    if balance is not None and balance - reserve < Decimal(s.get('min_balance_cny', '15')):
        return False, f'account balance {balance} minus reserve {reserve} below floor'
    return spent() + reserve <= cap, f'spent {spent()} + reserve {reserve} vs cap {cap}'


def resources_ok(active_generations):
    mem = {l.split(':')[0]: int(l.split()[1]) * 1024 for l in Path('/proc/meminfo').read_text().splitlines()}
    free = shutil.disk_usage(E).free
    s = settings()
    if free < s['min_free_disk_gib'] * GIB:
        return False, 'DISK'
    if mem['MemAvailable'] < (s['min_free_memory_gib'] + s['memory_per_run_gib']) * GIB:
        return False, 'MEMORY'
    if active_generations >= s['max_parallel_runs']:
        return False, 'RUN_SLOTS'
    return True, 'OK'


# ---------------------------------------------------------------- daemon
def issue(key, subject, exc):
    save(E / 'issues' / (key + '.json'), {'utc': utc(), 'subject': subject, 'error': f'{type(exc).__name__}: {str(exc)[:600]}'})
    log('ISSUE_RECORDED', key=key)


def has_issue(key):
    return (E / 'issues' / (key + '.json')).exists()


def deferred(run):
    """Not started and in a group deferred for budget (priority A/B -> F -> E -> C/D)."""
    s = settings()
    only = s.get('allow_only')     # small pilot: restrict new launches to a frozen subset
    outside = bool(only) and (run['group'] not in only['groups'] or run['rep'] not in only['reps']
                              or run['case_id'] not in only['cases'])
    return (run['group'] in s.get('defer_groups', []) or outside) and job_state(gen_key(run)) == 'NEW'


def terminal(run):
    """Scored, deferred for budget, or stopped for a recorded reason (failed preparation, evidence)."""
    return ((E / 'results' / run['case_id'] / (run['tag'] + '-' + run['method'] + '.json')).exists() or deferred(run)
            or has_issue('prepare-' + run['case_id']) or has_issue(gen_key(run)) or has_issue(score_key(run)))


def daemon(phase):
    lock = (E / f'DAEMON-{phase}.lock').open('a')
    import fcntl
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    runs = read(E / f'runs-{phase}.json')['runs']
    log('DAEMON_STARTED', phase=phase, runs=len(runs))
    halt = None
    while True:
        if (E / 'PAUSE').exists() or (E / f'PAUSE-{phase}').exists():
            status(phase, runs, 'PAUSED', halt)
            time.sleep(20)
            continue
        states = {}
        for run in runs:
            g, s_ = job_state(gen_key(run)), job_state(score_key(run))
            if g == 'LOST' or s_ == 'LOST':
                halt = {'reason': 'LOST_WORKER_NO_RESAMPLING', 'run': run}
            states[run['tag'], run['case_id'], run['method']] = (g, s_)
        if halt:
            save(E / f'status/HALT-{phase}.json', halt)
            status(phase, runs, 'HALTED', halt)
            return 2
        # Collect finished work.
        for run in runs:
            gstate, sstate = states[run['tag'], run['case_id'], run['method']]
            gen_file = E / 'generation' / run['case_id'] / (run['tag'] + '-' + run['method'] + '.json')
            if gstate == 'DONE' and not gen_file.exists() and not has_issue(gen_key(run)):
                try:
                    collect_generation(run)
                except InfraInterrupted as exc:
                    issue(gen_key(run), {**run, 'infra': True}, exc)
                except Exception as exc:  # recorded per run; no resampling, the run stays unscored
                    issue(gen_key(run), run, exc)
            res_file = E / 'results' / run['case_id'] / (run['tag'] + '-' + run['method'] + '.json')
            if sstate == 'DONE' and not res_file.exists() and not has_issue(score_key(run)):
                try:
                    collect_score(run)
                except Exception as exc:
                    issue(score_key(run), run, exc)
        # Too many unexplained runs means something systematic: stop paid work for review.
        unexplained = [p for p in (E / 'issues').glob('generate-*.json') if not read(p)['subject'].get('infra')]
        if len(unexplained) >= settings().get('max_generation_issues', 3):
            halt = {'reason': 'TOO_MANY_GENERATION_ISSUES', 'issues': sorted(p.name for p in (E / 'issues').glob('generate-*.json'))}
            continue
        jobs = [p.name for p in (E / 'jobs').iterdir() if job_state(p.name) == 'RUNNING']
        gens = sum(j.startswith('generate-') for j in jobs)
        scores = sum(j.startswith('score-') for j in jobs)
        preps = sum(j.startswith('prepare-') for j in jobs)
        # Scoring first.
        for run in runs:
            gen_file = E / 'generation' / run['case_id'] / (run['tag'] + '-' + run['method'] + '.json')
            if gen_file.exists() and job_state(score_key(run)) == 'NEW' and scores < settings()['max_parallel_scores']:
                start_score(run)
                scores += 1
        # Cleanup cases whose runs in this phase are all scored.
        for cid in dict.fromkeys(r['case_id'] for r in runs):
            mine = [r for r in runs if r['case_id'] == cid]
            marker = E / 'status' / f'CLEANED-{phase}-{cid}.json'
            if not marker.exists() and all(terminal(r) for r in mine):
                try:
                    cleanup_case(cid, mine)
                    save(marker, {'utc': utc()})
                    infra = [q for q in (E / 'issues').glob(f'generate-{cid}-*.json') if read(q)['subject'].get('infra')]
                    # Cases with an infrastructure-interrupted run keep their sources for the one re-run.
                    if settings()['phases'][phase].get('release_cases') and cid not in settings().get('keep_cases', []) \
                            and not infra:
                        release_case(cid)
                except Exception as exc:     # leftover copies cost disk, never the run evidence
                    issue(f'cleanup-{phase}-{cid}', {'case_id': cid}, exc)
                    save(marker, {'utc': utc(), 'cleanup_failed': True})
        # Generations, in order; prepare cases ahead of need.
        wait = None
        nogen = (E / f'NOGEN-{phase}').exists()      # collect and score started runs only
        pending = [] if nogen else [r for r in runs if job_state(gen_key(r)) == 'NEW' and not deferred(r) and not has_issue('prepare-' + r['case_id'])]
        live_cases = {r['case_id'] for r in runs if job_state(gen_key(r)) == 'RUNNING'}
        for run in pending:
            cid = run['case_id']
            if not prepared(cid):
                busy = (E / 'cases' / cid).exists() and not (E / 'jobs' / ('prepare-' + cid)).exists()
                if job_state('prepare-' + cid) == 'NEW' and preps == 0 and not busy and resources_ok(0)[0]:
                    start_prepare(cid)
                    preps += 1
                elif job_state('prepare-' + cid) == 'DONE' and not prepared(cid):
                    issue('prepare-' + cid, {'case_id': cid}, RuntimeError('preparation failed; runs of this case skipped'))
                continue
            if cid in live_cases and not settings().get('allow_same_case_parallel'):
                continue
            ok, why = resources_ok(gens)
            money, detail = money_ok(gens)
            if not ok or not money:
                wait = why if not ok else 'MONEY: ' + detail
                break
            make_slot(run)
            start_generate(run)
            gens += 1
            live_cases.add(cid)
        considered = [r for r in runs if not nogen or job_state(gen_key(r)) != 'NEW']
        done = all(terminal(r) for r in considered) and not any(
            job_state(p.name) == 'RUNNING' for p in (E / 'jobs').iterdir() if p.name.startswith(('generate-', 'score-')))
        status(phase, runs, 'COMPLETED' if done else 'RUNNING', halt, wait)
        if done:
            log('PHASE_COMPLETED', phase=phase)
            return 0
        time.sleep(10)


def status(phase, runs, state, halt=None, wait=None):
    gen = sum((E / 'generation' / r['case_id'] / (r['tag'] + '-' + r['method'] + '.json')).exists() for r in runs)
    scored = sum((E / 'results' / r['case_id'] / (r['tag'] + '-' + r['method'] + '.json')).exists() for r in runs)
    save(E / 'status' / f'STATUS-{phase}.json', {'utc': utc(), 'state': state, 'runs': len(runs), 'generated': gen,
                                                 'scored': scored, 'spent_cny': str(spent()), 'halt': halt, 'wait': wait,
                                                 'running': [p.name for p in (E / 'jobs').iterdir() if job_state(p.name) == 'RUNNING']})


def main():
    command = sys.argv[1]
    if command == 'runs':
        phase = sys.argv[2]
        runs = runs_for(phase)
        path = E / f'runs-{phase}.json'
        assert not path.exists(), 'Run list already frozen'
        save(path, {'phase': phase, 'frozen_utc': utc(), 'settings_sha256': sha(E / 'SETTINGS.json'), 'runs': runs})
        print(json.dumps({'phase': phase, 'runs': len(runs), 'cases': len({r['case_id'] for r in runs})}))
    elif command == 'daemon':
        raise SystemExit(daemon(sys.argv[2]))
    elif command == 'release':
        busy = {p.name.split('-', 1)[1] for p in (E / 'jobs').iterdir() if job_state(p.name) == 'RUNNING'}
        for cid in sys.argv[2:]:
            if prepared(cid) and not any(cid in b for b in busy) and not (E / 'status' / f'RELEASED-{cid}.json').exists():
                release_case(cid)
    elif command == 'status':
        for p in sorted((E / 'status').glob('STATUS-*.json')):
            print(p.name, p.read_text())


if __name__ == '__main__':
    main()
