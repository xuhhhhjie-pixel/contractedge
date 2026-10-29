"""Parent-only static150 identity/resources audit; no repair or behavior scoring."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import os
import shutil
import sys

SYSTEM = Path('/home/xhj/contractedge-alert-closure-20261028/system')
BENCH = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
OUT = SYSTEM / 'outputs/alert-repeat-closure-20261028'
sys.path[:0] = [str(SYSTEM / 'src'), str(SYSTEM / 'scripts')]
from contractedge.dataset.benchmark import release_identity, read_members, public_task, execution_contract, confined, editable_policy
from assess_formal_readiness import reference, sha

identity = release_identity(BENCH)
public = read_members(BENCH / 'public')
evaluation = [json.loads(s) for s in (BENCH / 'evaluation/manifest.jsonl').read_text().splitlines() if s.strip()]
partition = [json.loads(s) for s in (BENCH / 'partition/member_groups.jsonl').read_text().splitlines() if s.strip()]
entry = json.loads((BENCH / 'RELEASE_ENTRY.json').read_text())
assert len(public) == len(evaluation) == len(partition) == 150
ids = {r['case_id'] for r in public}
assert ids == {r['case_id'] for r in evaluation} == {r['case_id'] for r in partition}
assert len(ids) == 150 and len({r['case_id'] for r in evaluation}) == 150
assert sha(BENCH / 'evaluation/manifest.jsonl') == entry['evaluation_manifest_sha256']
by_id = {r['case_id']: r for r in evaluation}
prepared_path = Path('/home/xhj/contractedge-clean-delivery-official-v068-20261028-public150/preparation.json')
if not prepared_path.is_file():
    prepared_path = Path('/home/xhj/contractedge-clean-delivery-official-v068-20261028-public150/preparation.json')
prepared = json.loads(prepared_path.read_text())
assert prepared['status'] == 'PASS' and prepared['prepared'] == 150 and not prepared['errors']
assert prepared['public_manifest_sha256'] == identity['public_manifest_sha256']
assert {r['case_id'] for r in prepared['records']} == ids
prepared_cases = {r['case_id']: r for r in prepared['records']}
cache = {}; errors = []; rows = []; max_cpus = 0; max_memory = 0


def verify(path, expected):
    stat = path.stat(); key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if key not in cache: cache[key] = sha(path)
    if cache[key] != expected: raise ValueError('Asset hash mismatch')


for row in public:
    case = row['case_id']
    try:
        _, directory, task, manifest = public_task(BENCH / 'public', case)
        env = execution_contract(directory, task, manifest)
        assert editable_policy(task, env)['allowed_paths']
        assert prepared_cases[case]['source_sha256'] == row['source_sha256']
        assert prepared_cases[case]['task_manifest_sha256'] == row['task_manifest_sha256']
        for name, expected in manifest['file_sha256'].items():
            verify(confined(directory, name), expected)
        private = by_id[case]
        assert row['primary_class'] == private['primary_class']
        # Hash-only management verification. No GT/adapter body is parsed,
        # sent to a model or included in this report.
        for name in ['adapter', 'gt', 'runner_entry']:
            if name in private: verify(confined(BENCH / 'evaluation', private[name]), private[name + '_sha256'])
        resources = env.get('resources', {})
        max_cpus = max(max_cpus, resources.get('cpus', 0))
        max_memory = max(max_memory, resources.get('memory_bytes', 0))
        rows.append(dict(case_id=case, task_manifest_sha256=row['task_manifest_sha256'],
            source_sha256=row['source_sha256'], public_assets_verified=len(manifest['file_sha256']),
            private_registry_hashes_verified=True, route='FROZEN_PUBLIC_LAUNCHER'))
    except (OSError, ValueError, KeyError, AssertionError) as exc:
        errors.append({'case_id': case, 'error_type': type(exc).__name__, 'reason': str(exc)})

images_path = OUT / 'V068_LOCAL_IMAGE_PREFLIGHT.json'; images = json.loads(images_path.read_text())
routes_path = OUT / 'V068_PUBLIC_ROUTES.json'; routes = json.loads(routes_path.read_text())
assert routes['status'] == 'PASS' and routes['verified'] == 150
assert routes['public_manifest_sha256'] == identity['public_manifest_sha256']
memory = {s.split(':', 1)[0]: int(s.split()[1]) * 1024 for s in Path('/proc/meminfo').read_text().splitlines() if ':' in s and s.split()[1].isdigit()}
runtime = Path('/run/user/' + str(os.getuid()))
limits = {'cpus': len(os.sched_getaffinity(0)), 'available_memory_bytes': memory['MemAvailable'],
    'work_disk_free_bytes': shutil.disk_usage(SYSTEM).free, 'user_runtime_free_bytes': shutil.disk_usage(runtime).free,
    'platform': os.uname().sysname + '/' + os.uname().machine}
# Serial execution: published maximum container2CPU/6GiB plus the special
# host tmpfs path's20GiB available memory/4GiB tmpfs and cold-build24GiB disk.
resource_ok = (limits['cpus'] >= max_cpus and limits['available_memory_bytes'] >= max(max_memory, 20 * 1024**3)
    and limits['work_disk_free_bytes'] >= 24 * 1024**3 and limits['user_runtime_free_bytes'] >= 4 * 1024**3
    and limits['platform'] == 'Linux/x86_64' and images['available'] == images['image_count'] == 20)
if not resource_ok: errors.append({'reason': 'Current serial host resource or pinned local image admission failed'})
report = dict(schema_version='ce.parent_static_dataset_admission.v1', status='PASS' if not errors else 'FAIL',
    checked_at=datetime.now(timezone.utc).isoformat(), checker=reference(__file__), dataset=identity,
    public_evaluator_partition_members=150, partition_counts=dict(Counter(r.get('candidate_split', r.get('split')) for r in partition)),
    verified_cases=len(rows), distinct_asset_contents_checked=len(cache), errors=errors,
    proof=[reference(prepared_path), reference(images_path), reference(routes_path)],
    environment=limits, resource_requirements={'max_container_cpus': max_cpus, 'max_container_memory_bytes': max_memory,
        'minimum_work_disk_bytes': 24 * 1024**3, 'minimum_available_host_memory_bytes': 20 * 1024**3,
        'minimum_user_runtime_free_bytes': 4 * 1024**3},
    records=rows, new_model_http_calls=0, candidate_executions=0, hidden_behavior_scores_run=0,
    private_bodies_parsed=False, formal_ready=False,
    scope='Zero-model static management preflight of the current150 unified release: actual public bytes, hash-only private entry pins, current public routes and local images/resources. No all-case build/runtime/repair-success claim. Recheck resources before formal launch.')
with (OUT / 'V068_STATIC_DATASET_ADMISSION.json').open('x') as stream: json.dump(report, stream, indent=2); stream.write('\n')
print(json.dumps({k: report[k] for k in ['status', 'verified_cases', 'distinct_asset_contents_checked', 'errors', 'environment']}))
raise SystemExit(0 if report['status'] == 'PASS' else 1)
