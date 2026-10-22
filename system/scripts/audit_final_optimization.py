"""Assemble fixed-version acceptance evidence; never launches or selects repairs.

Run only after the preselected panel, seeded continuation and independent
scoring finish. Private test details are not read; only frozen score statuses.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from contractedge.orchestration.batch import _method_digest
from contractedge.contracts.validation import canonical_digest

OUT = ROOT / 'outputs/final-optimization-20261020'
PANEL_BATCHES = [
    ('deepseek-final-regression-v6-20261020', 'deepseek-independent-final-regression-v6-score-20261020'),
    ('deepseek-final-transfer-v2-20261020', 'deepseek-independent-final-transfer-v2-score-20261020'),
]
RECOVERY = ('deepseek-final-seeded-recovery-v2-20261020',
            'deepseek-independent-final-seeded-recovery-v2-score-20261020')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def batch_records(batch_name, score_name):
    batch = ROOT / 'outputs' / batch_name
    score_root = ROOT / 'outputs' / score_name
    state, score = read(batch / 'batch.json'), read(score_root / 'cohort.json')
    method = read(OUT / 'candidate-method-v0.5.5.json')
    config = ROOT / method['configuration']
    assert sha(config) == method['configuration_sha256'], 'Configuration drift'
    assert state['identity']['method_code_digest'] == _method_digest(), 'Mixed method version'
    assert state['identity']['config_digest'] == canonical_digest(read(config)), 'Mixed configuration'
    assert read(batch / 'experiment-config.json') == read(config), 'Recorded configuration differs'
    assert state['identity']['public_manifest_sha256'] == '469d2aea9ee8d7d9b00cddcbc7cdbb2816eb27b7b23f3cde92ec3032848f5492'
    assert state['identity']['native_topomen']['source_sha256'] == read(config)['topomen_source_sha256']
    assert state['identity']['public_tools']['sha256'] == '28bd18c5251328ae102b6d418ef24bd2cd61586754ef50d56d453fb2c4e0aad4'
    assert read(batch / 'implementation-manifest.json')['unchanged'], 'Source drift during run'
    assert state['case_count'] == score['case_count'] == 150
    assert score['pending_count'] == 0, 'Scoring incomplete'
    assert score['batch_sha256'] == sha(batch / 'batch.json'), 'Batch changed after scoring'
    scored = {r['case_id']: r for r in score['records']}
    assert len(scored) == len(state['records']) == 150
    assert set(scored) == {r['case_id'] for r in state['records']}
    rows = []
    for record in state['records']:
        if record['status'] == 'NOT_RUN':
            continue
        assert record['status'] != 'RUNNING', 'Active case'
        row = {'case_id': record['case_id'], 'method_status': record['status'],
               'batch': str(batch.relative_to(ROOT)),
               'batch_record_error': record.get('error'),
               'independent_status': scored[record['case_id']]['status']}
        trace = read(batch / record['trace']) if record.get('trace') else {}
        if record.get('trace'):
            assert sha(batch / record['trace']) == record['trace_sha256']
        candidate = record.get('candidate') or {}
        if candidate.get('patch_bytes'):
            assert sha(candidate['patch_path']) == candidate['patch_sha256']
            assert scored[record['case_id']]['patch_sha256'] == candidate['patch_sha256']
        feedback = trace.get('last_feedback') or {}
        row.update(patch_bytes=candidate.get('patch_bytes', 0),
                   patch_sha256=candidate.get('patch_sha256'),
                   semantic_outcome=feedback.get('semantic_outcome'), route=feedback.get('route'),
                   pending_obligations=len(feedback.get('pending_obligations', [])),
                   rounds=len(trace.get('rounds', [])), errors=trace.get('errors', []),
                   elapsed_seconds=trace.get('budget', {}).get('elapsed_seconds'))
        directory = batch / 'cases' / record['case_id']
        calls = read(directory / 'model_calls.json') if (directory / 'model_calls.json').exists() else []
        row['model_calls'] = len(calls)
        row['model_usage_tokens'] = sum(c.get('response_metadata', {}).get('usage', {}).get('total_tokens', 0)
                                       for c in calls)
        observations = list(directory.glob('rounds/*/public/observation.json'))
        row['public_runs'] = []
        for path in observations:
            report = read(path)
            row['public_runs'].append({'path': str(path.relative_to(ROOT)),
                'status': report['status'], 'method_probe_status': report.get('method_probe_status'),
                'typed_observation_counts': dict(Counter(o.get('evidence_kind') for o in report['observations'])),
                'registered_adapter_objects': sum(bool(o.get('adapter_id')) for o in report['observations'])})
        row['front_statuses'] = [read(p)['status'] for p in sorted(directory.glob('rounds/*/front.trace.json'))]
        row['feedback_routes'] = [read(p)['route'] for p in sorted(directory.glob('rounds/*/feedback.json'))]
        rows.append(row)
    return rows


def main():
    rows = [r for pair in PANEL_BATCHES for r in batch_records(*pair)]
    selected = [r['case_id'] for r in read(OUT / 'development-panel.json')['cases']]
    assert Counter(r['case_id'] for r in rows) == Counter(selected), 'Panel replaced or missing cases'
    rows.sort(key=lambda r: selected.index(r['case_id']))
    natural = next(r for r in rows if r['case_id'] == 'R3-KPT-4673')
    natural_root = ROOT / natural['batch'] / 'cases/R3-KPT-4673'
    if natural['rounds'] >= 2:
        # The spec accepts either natural or seeded recovery. Keep natural
        # continuation within its original run; never count it twice as a pass.
        recovery = [dict(natural)]
        first = read(natural_root / 'rounds/0/feedback.json')
        proof = read(OUT / 'core-decision-proof-kpt-recovery-v0.5.5.json')
        recovery[0].update(scope='NATURAL_SAME_RUN_RECOVERY_NOT_FIRST_PASS',
            initial_route=first['route'], used_public_feedback=proof['next_execution_prompt_contains_feedback'],
            failed_initial_checks=proof['failed_checks'])
    else:
        recovery = batch_records(*RECOVERY)
        assert len(recovery) == 1 and recovery[0]['case_id'] == 'R3-KPT-4673'
        recovery_root = ROOT / recovery[0]['batch'] / 'cases/R3-KPT-4673'
        provenance = read(recovery_root / 'provenance.json')
        first = read(recovery_root / 'seed-feedback.json')
        recovery[0].update(scope=provenance['scope'], initial_route=first['route'],
                           used_public_feedback=(recovery_root / 'continuation-plan.json').exists(),
                           seed_patch_sha256=provenance['seed_sha256'])
        probe_states = {}
        for stage in ('seed-public', 'final-public'):
            path = recovery_root / stage / 'observation.json'
            if path.exists():
                observed = read(path)
                probe_states[stage] = {o['behavior_check']['id']: o['behavior_check']['status']
                    for o in observed['observations'] if o.get('behavior_check')}
        recovery[0]['probe_states'] = probe_states
        proof = read(OUT / 'core-decision-proof-kpt-recovery-v0.5.5.json')
        recovery[0]['failed_checks_reached_execution_prompt'] = proof['failed_ids_in_execution_prompt']
    recovery[0]['effect_acceptance_passed'] = (recovery[0]['independent_status'] == 'PASS'
        and recovery[0]['used_public_feedback'] and recovery[0]['initial_route'] == 'EXECUTE')
    counts = dict(Counter(r['independent_status'] for r in rows))
    metrics = {
        'panel_size': len(rows), 'independent_passes': counts.get('PASS', 0),
        'independent_status_counts': counts,
        'candidate_count': sum(r['patch_bytes'] > 0 for r in rows),
        'public_pass_independent_nonpass': [r['case_id'] for r in rows
            if r['semantic_outcome'] == 'PUBLIC_REVIEW_PASS' and r['independent_status'] != 'PASS'],
        'independent_pass_public_unknown': [r['case_id'] for r in rows
            if r['independent_status'] == 'PASS' and r['semantic_outcome'] == 'UNKNOWN'],
        'model_calls': sum(r['model_calls'] for r in rows),
        'single_semantic_round_independent_passes': sum(r['rounds'] == 1 and r['independent_status'] == 'PASS' for r in rows),
        'no_candidate_count': sum(r['patch_bytes'] == 0 for r in rows),
        'public_review_passes': sum(r['semantic_outcome'] == 'PUBLIC_REVIEW_PASS' for r in rows),
        'public_unknown_with_candidate': sum(r['patch_bytes'] > 0 and r['semantic_outcome'] == 'UNKNOWN' for r in rows),
    }
    report = {'schema_version': 'ce.final_optimization_acceptance.v1', 'version': '0.5.5',
        'method_code_digest': _method_digest(), 'panel': rows, 'metrics': metrics, 'recovery': recovery,
        'engineering_delivery_complete': True,
        'recovery_effect_acceptance_passed': recovery[0]['effect_acceptance_passed'],
        'repair_rate_improvement_established': False,
        'formal_ready': False,
        'formal150_started': False, 'ablation_started': False,
        'interpretation': 'Fixed exposed development panel; no best-of merging or generalization claim. Recovery passes are not additional panel passes or first-pass successes.'}
    (OUT / 'final-validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'metrics': metrics, 'recovery': recovery}, ensure_ascii=False))


if __name__ == '__main__':
    main()
