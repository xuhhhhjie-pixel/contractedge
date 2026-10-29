"""Bind existing static, implementation and operational checks to exact v068.

No model or scoring dispatch. G4/G5 are intentionally not emitted here.
"""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

SYSTEM = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path[:0] = [str(SYSTEM / 'src'), str(SYSTEM / 'scripts')]
from assess_formal_readiness import reference, sha, digest, read, REQUIRED_CHECKS
from fixed_panel import verify_panel
from verify_development_snapshot import verify_snapshot

PANEL = Path('/home/xhj/contractedge-fixed46-official-v068-20261028/panel.json')
FREEZE = SYSTEM / 'docs/development/quote-binding-v068-20261028.snapshot.json'


def main():
    panel = verify_panel(PANEL)
    freeze = read(FREEZE)
    assert verify_snapshot(SYSTEM, freeze, require_current_bindings=True)['status'] == 'PASS'
    identity = dict(source_sha256=freeze['source_sha256'], panel_sha256=sha(PANEL),
        candidate_config_sha256=panel['variants']['candidate']['config_sha256'], data_files_sha256=digest(panel['data_files']))
    assert identity['source_sha256'] == panel['variants']['candidate']['source_sha256']

    def emit(name, proofs, scope):
        assert set(proofs) == set(REQUIRED_CHECKS[name])
        value = dict(schema_version='ce.formal_gate_evidence.v1', gate=name, identity=identity,
            executed_at=datetime.now(timezone.utc).isoformat(), checker=reference(__file__),
            inputs=[reference(p) for p in dict.fromkeys(p for paths in proofs.values() for p in paths)],
            checks=[dict(id=k, status='PASS', evidence=[reference(p) for p in paths]) for k, paths in proofs.items()], scope=scope)
        with (OUT / ('V068_' + name + '_EVIDENCE.json')).open('x') as f:
            json.dump(value, f, indent=2); f.write('\n')

    data_path = OUT / 'V068_STATIC_DATASET_ADMISSION.json'
    data = read(data_path)
    assert data['status'] == 'PASS' and data['verified_cases'] == 150 and not data['errors']
    assert data['candidate_executions'] == data['hidden_behavior_scores_run'] == data['new_model_http_calls'] == 0
    assert data['dataset']['release_entry_sha256'] == sha(Path(panel['benchmark_root']) / 'RELEASE_ENTRY.json')
    emit('G0', {k: [data_path, OUT / 'V068_LOCAL_IMAGE_PREFLIGHT.json', OUT / 'V068_PUBLIC_ROUTES.json'] for k in REQUIRED_CHECKS['G0']}, data['scope'])

    full_path = OUT / 'V068_FULL_REGRESSION.json'
    full = read(full_path)
    assert full['status'] == 'PASS' and full['source_sha256'] == identity['source_sha256'] and full['exit_code'] == 0
    assert full['log_sha256'] == sha(OUT / 'full-v068.log')
    previous_mapping = SYSTEM / 'outputs/planning-helper-contracts-20261027/IMPLEMENTATION_COVERAGE_v1.json'
    mapping = {k: {name: item[name] for name in ['requirements', 'source', 'tests', 'flag']}
        for k, item in read(previous_mapping)['mechanisms'].items()}
    mapping['helper_prerequisites']['tests'] += ['test_precondition_establishment.py', 'test_precondition_quote_feedback.py']
    mapping['feedback_unknown_reasons']['tests'] += ['test_behavior_plan_reassessment.py', 'test_runtime_plan_reassessment.py', 'test_go_comment_noop.py']
    mapping['feedback_unknown_reasons']['source'] += ['orchestration/repair.py', 'execution/code_change.py']
    config = read(panel['variants']['candidate']['config'])
    for name, item in mapping.items():
        assert config[item['flag']] is True
        item['source_evidence'] = []
        for relative in item['source']:
            path = SYSTEM / 'src/contractedge' / relative
            assert sha(path) == freeze['files']['contractedge/' + relative]
            item['source_evidence'].append(reference(path))
        item['test_evidence'] = []
        for filename in dict.fromkeys(item['tests']):
            path = SYSTEM / 'tests' / filename
            assert sha(path) == freeze['fixture_files']['tests/' + filename]
            nodes = [n.name for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')]
            assert nodes
            item['test_evidence'].append(dict(file=reference(path), test_definitions=nodes,
                validation_scope='Bound full-suite fixture, not a proof of arbitrary model correctness'))
    coverage = dict(schema_version='ce.implementation_evidence_map.v1', identity=identity,
        original_design=reference(SYSTEM / 'docs/reports/2026-10-20-final-optimization-design.md'),
        reliability_design=reference(SYSTEM / 'docs/reports/2026-10-20-reliability-closure-design.md'),
        prior_requirement_mapping=reference(previous_mapping), qualification=reference(full_path), mechanisms=mapping,
        review_scope='Author source/test mapping and verified full-suite evidence; not independent whole-branch review or a model repair-rate certificate', formal_ready=False)
    coverage_path = OUT / 'V068_IMPLEMENTATION_COVERAGE.json'
    with coverage_path.open('x') as f: json.dump(coverage, f, indent=2); f.write('\n')
    emit('G1', {k: [coverage_path, full_path] + [SYSTEM / 'tests' / p for p in item['tests']] for k, item in mapping.items()}, coverage['review_scope'])

    operational_path = OUT / 'V068_CLEAN_CLONE_REAL_ROUNDTRIP.json'
    op = read(operational_path)
    assert op['status'] == op['independent_status'] == 'PASS' and op['public_status'] == 'EXECUTION_PASS'
    assert op['source_sha256'] == identity['source_sha256'] and op['new_model_http_calls'] == 0
    assert op['source_swap_real_process_exit'] == 73 and op['source_recovery']['status'] == 'ROLLED_BACK'
    assert op['source_recovery']['restored_snapshot'] == op['sealed_input']['original_snapshot']
    assert not op['source_recovery']['candidate_exported'] and not op['source_recovery']['budget_reset']
    assert op['completed_score_finalize_twice'] and op['score_dispatches'] == 1
    clone_path = OUT / 'V068_CLEAN_CLONE_VERIFICATION.json'
    clone = read(clone_path)
    assert clone['status'] == 'PASS' and clone['source_sha256'] == identity['source_sha256']
    emit('G6', {'clean_clone_real_roundtrip': [operational_path, clone_path],
        'durable_crash_recovery': [operational_path, OUT / 'clean_clone_roundtrip_v068.py', full_path],
        'resource_and_drift_checks': [data_path, clone_path, OUT / 'V068_ACTUAL_ISOLATION.json'],
        'declared_serial_execution': [PANEL, OUT / 'run_panel_v068.py', SYSTEM / 'scripts/run_fixed_panel.py']},
        op['scope'] + ' Uses a sealed current-panel candidate; no extra live repair. Concurrent unrelated ledger activity does not belong to this credential-free replay.')
    print(json.dumps(dict(status='STATIC_IMPLEMENTATION_OPERATIONAL_EVIDENCE_BOUND', gates=['G0', 'G1', 'G6'], identity=identity, formal_ready=False)))


if __name__ == '__main__':
    main()
