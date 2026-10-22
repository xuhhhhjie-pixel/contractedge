"""Audit RW-CAND-130 using its public requirement, declared inputs and raw renders.

Run before independent hidden scoring. No evaluator or GT files are accessed.
"""
import hashlib
import json
from pathlib import Path
import sys

import yaml


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def jobs(path):
    return [doc for doc in yaml.safe_load_all(path.read_text()) if isinstance(doc, dict)
            and doc.get('kind') == 'Job'
            and any(doc.get('metadata', {}).get('name', '').endswith(suffix)
                    for suffix in ('cluster-gateway-tls-secret-create', 'cluster-gateway-tls-secret-patch'))]


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    trace = read(root / 'repair.trace.json')
    assert trace['case_id'] == 'RW-CAND-130'
    assert trace['status'] == 'PUBLIC_REVIEW_COMPLETE'
    assert read(root / 'implementation-manifest.json')['unchanged']
    last = root / 'rounds' / str(trace['rounds'][-1]['round_id'])
    feedback = read(last / 'feedback.json')
    assert feedback['route'] == 'STOP' and feedback['semantic_outcome'] == 'PUBLIC_REVIEW_PASS'
    assert not feedback['pending_obligations'] and not read(last / 'plan.json')['open_obligations']
    checks = read(last / 'public-review.json')['checks']
    assert checks and all(c['status'] == 'PASS' for c in checks)
    method = read(root / 'method_input.json')
    values = next(yaml.safe_load(a['content']) for a in method['declared_artifacts']
                  if a['path'].endswith('/trigger-values.yaml'))
    raw = last / 'public/raw'
    trigger, control = jobs(raw / 'trigger.stdout'), jobs(raw / 'control.stdout')
    assert len(trigger) == 2 and not control
    fields = ['nodeSelector', 'tolerations', 'affinity']
    comparisons = [{'job': job['metadata']['name'], 'field': field,
                    'matches_public_input': job['spec']['template']['spec'].get(field) == values[field]}
                   for job in trigger for field in fields]
    assert len(comparisons) == 6 and all(c['matches_public_input'] for c in comparisons)
    patch = root / 'candidate.patch'
    assert sha(patch) == trace['candidate']['patch_sha256'] and patch.stat().st_size > 0
    result = {'schema_version': 'ce.single_public_acceptance.v1', 'case_id': trace['case_id'],
              'status': 'PASS', 'source': 'PUBLIC_REQUIREMENT_AND_DECLARED_INPUTS_ONLY',
              'trigger_target_jobs': len(trigger), 'scheduling_comparisons': comparisons,
              'control_target_jobs': len(control), 'pending_obligations': [],
              'public_review_checks': [{'id': c['id'], 'status': c['status']} for c in checks],
              'raw_sha256': {name: sha(raw / name) for name in ('trigger.stdout', 'control.stdout')},
              'patch_sha256': sha(patch)}
    selection = {'case_id': trace['case_id'], 'patch_sha256': sha(patch),
                 'trace_sha256': sha(root / 'repair.trace.json'),
                 'broken_front_trace_sha256': sha(root / 'rounds/0/front.trace.json'),
                 'selection_rule': 'Fully closed public run; fixed before independent hidden scoring',
                 'hidden_evaluation_used_for_selection': False}
    for name, body in [('public-acceptance-audit.json', result), ('candidate-selection.json', selection)]:
        with (root / name).open('x') as stream:
            stream.write(json.dumps(body, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
