"""Validate every frozen public launch route without candidates or builds.

Uses synthetic workspace metadata, not a claim that source preparation or
native execution was repeated for all cases. Frozen launcher identities are
verified by the package's own route-only command.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from contractedge.dataset.benchmark import read_members, release_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.benchmark_root.resolve(strict=True)
    identity = release_identity(root)
    records = []
    with tempfile.TemporaryDirectory(prefix='ce150-route-audit-') as temporary:
        base = Path(temporary)
        for row in read_members(root / 'public'):
            work = base / row['case_id']; work.mkdir()
            (work / 'source').mkdir(); (work / 'inputs').mkdir()
            (work / 'workspace.json').write_text(json.dumps({'case_id': row['case_id'],
                'source_root': str(work / 'source'), 'inputs_root': str(work / 'inputs'),
                'source_sha256': row['source_sha256'], 'git_initialized': False,
                'private_materials_loaded': False}) + '\n')
            run = subprocess.run([sys.executable, str(root / 'tools/launch_public_case.py'),
                '--public-root', str(root / 'public'), '--workspace', str(work),
                '--output', str(base / (row['case_id'] + '-unused-output'))],
                capture_output=True, text=True, timeout=60)
            record = {'case_id': row['case_id'], 'primary_class': row['primary_class'],
                      'returncode': run.returncode, 'task_manifest_sha256': row['task_manifest_sha256']}
            if run.returncode:
                record['error'] = run.stderr[-2000:]
            else:
                record['route'] = json.loads(run.stdout)
            records.append(record)
    errors = [r for r in records if r['returncode']]
    report = {'schema_version': 'ce.public_route_audit.v1', **identity,
              'status': 'PASS' if not errors else 'FAIL', 'verified': len(records) - len(errors),
              'errors': errors, 'records': records, 'synthetic_workspace_metadata': True,
              'candidate_executions': 0, 'model_calls': 0, 'private_materials_loaded': False,
              'scope': 'All selected public route/launcher pins only; not all-case source extraction or runtime portability.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ['status', 'selected_cases', 'verified', 'candidate_executions']}))
    return 0 if not errors else 1


if __name__ == '__main__':
    raise SystemExit(main())
