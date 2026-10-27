"""Bind static, implementation and isolation evidence to the same paid freeze."""
import ast
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

SYSTEM = Path('/home/xhj/contractedge-planning-branch-continuity-20261026/system')
OUT = SYSTEM / 'outputs/planning-helper-contracts-20261027'
WORK = SYSTEM / '.superpowers/sdd/2026-10-27-planning-helper-contracts'
PANEL = Path('/home/xhj/contractedge-fixed46-source060-20261027/panel.json')
sys.path.insert(0, str(SYSTEM / 'scripts'))
from assess_formal_readiness import reference, sha, digest, read
from fixed_panel import verify_panel, verified_records

panel = verify_panel(PANEL); records = verified_records(PANEL, panel)
identity = dict(source_sha256=panel['variants']['candidate']['source_sha256'], panel_sha256=sha(PANEL),
    candidate_config_sha256=panel['variants']['candidate']['config_sha256'], data_files_sha256=digest(panel['data_files']))
snapshot = read(SYSTEM / 'docs/development/ordering-source-v060-20261027-v1.snapshot.json')
assert snapshot['files'] == panel['variants']['candidate']['source_files']
qpath = OUT / 'ORDERING_SOURCE_QUALIFICATION.json'; q = read(qpath)
assert q['source_sha256'] == identity['source_sha256'] and q['full_exit_code'] == 0
assert q['full_log_sha256'] == sha(OUT / 'full-ordering-source-final.log')
stamp = datetime.now(timezone.utc).isoformat()
for name in ['dataset_static_admission.py', 'finish_gate_evidence_complete.py', 'collect_delivery_evidence.py']:
    dest = OUT / name
    if dest.exists(): assert dest.read_bytes() == (WORK / name).read_bytes()
    else: shutil.copy2(WORK / name, dest)


def gate(key, checks, inputs, scope):
    report = dict(schema_version='ce.formal_gate_evidence.v1', gate=key, identity=identity,
        executed_at=stamp, checker=reference(OUT / 'finish_gate_evidence_complete.py'),
        inputs=[reference(p) for p in inputs],
        checks=[dict(id=name, status='PASS', evidence=[reference(p) for p in proof]) for name, proof in checks], scope=scope)
    path = OUT / (key + '_DELIVERY_EVIDENCE_v1.json')
    if path.exists():
        assert key == 'G0' and read(path)['identity'] == identity, 'Existing completed report cannot be replaced'
        path = OUT / (key + '_DELIVERY_EVIDENCE_v2.json')
    with path.open('x') as f: json.dump(report, f, indent=2); f.write('\n')
    return reference(path)


data_path = OUT / 'G0_STATIC_DATASET_ADMISSION.json'; data = read(data_path)
assert data['status'] == 'PASS' and data['verified_cases'] == 150 and not data['errors']
assert data['candidate_executions'] == data['hidden_behavior_scores_run'] == data['new_model_http_calls'] == 0
benchmark = Path(panel['benchmark_root'])
assert data['dataset']['release_entry_sha256'] == sha(benchmark / 'RELEASE_ENTRY.json')
g0 = gate('G0', [(name, [data_path]) for name in ['membership150', 'public_evaluation_mapping',
    'archive_grader_partition_integrity', 'release_layout_mapping', 'resource_preflight150']],
    [data_path, OUT / 'G0_LOCAL_IMAGE_PREFLIGHT.json', OUT / 'G0_CURRENT_PUBLIC_ROUTES.json'], data['scope'])

mapping = {
    'obligation_lineage': {
        'requirements': 'Original work B + explicit CONTINUE/REANCHOR/SPLIT/MERGE, immutable history/residual/disputed interpretation; not automatic natural-language equivalence',
        'source': ['requirement/normalize.py', 'requirement/lineage.py'],
        'tests': ['test_explicit_obligation_lineage.py', 'test_obligation_lineage.py', 'test_lineage_request_continuity.py'],
        'flag': 'obligation_lineage_enabled'},
    'operation_fact_consistency': {
        'requirements': 'Original work A + fact/operation/outcome distinction, complete JSON/resource/scenario typing, registered public producer and expected exception scope',
        'source': ['execution/observations.py', 'execution/public.py', 'diagnosis/operation_consistency.py', 'execution/support.py'],
        'tests': ['test_structured_observation_adapters.py', 'test_operation_consistency.py', 'test_operation_failure_routing.py', 'test_observation_selection.py'],
        'flag': 'operation_consistency_enabled'},
    'helper_prerequisites': {
        'requirements': 'Original work C: actual planning/execution source prerequisites, review/open coverage, finite helper initialization/use/cleanup checks and limited public probes; unsupported syntax remains UNKNOWN',
        'source': ['execution/helper_context.py', 'execution/preconditions.py', 'changeset/validate.py', 'orchestration/runner.py'],
        'tests': ['test_helper_preconditions.py', 'test_plan_precondition_coverage.py', 'test_planning_helper_contracts.py', 'test_execution_helper_context.py'],
        'flag': 'helper_preconditions_enabled'},
    'bounded_gap_delivery': {
        'requirements': 'Original work C: public entry/config/producer/consumer evidence, bounded missing roles and actual serialized delivery/current snapshots; no complete call-graph claim',
        'source': ['source/entrypoints.py', 'source/units.py', 'retrieval/gap_delivery.py', 'orchestration/runner.py'],
        'tests': ['test_gap_delivery.py', 'test_probe_source_entrypoints.py', 'test_ready_support_consistency.py'],
        'flag': 'gap_role_delivery_enabled'},
    'candidate_preservation': {
        'requirements': 'Original work C/D + influence links, trial promotion, protected necessary checks, verified snapshot rollback/export consistency, same-program public comparisons',
        'source': ['orchestration/candidate_guard.py', 'orchestration/trial.py', 'orchestration/repair.py', 'orchestration/feedback.py'],
        'tests': ['test_candidate_regression_guard.py', 'test_preservation_impact.py', 'test_pending_preservation_guard.py', 'test_preservation_identity_feedback.py'],
        'flag': 'candidate_guard_enabled'},
    'feedback_unknown_reasons': {
        'requirements': 'Original work D/E: necessary vs sufficient/related checks, scoped UNKNOWN, actionable failure facts/current source vs rejected snapshot, finite calibrated KPT/Alert probes and bounded revision context',
        'source': ['execution/support.py', 'execution/review.py', 'orchestration/feedback.py', 'execution/probe_specs.py', 'execution/probes.py'],
        'tests': ['test_check_coverage.py', 'test_failure_tasks.py', 'test_method_behavior_probes.py', 'test_temporal_probe_dispatch.py', 'test_temporal_response_bounds.py', 'test_necessary_coverage_review.py'],
        'flag': 'requirement_traceability_enabled'},
}
config = read(panel['variants']['candidate']['config'])
for key, item in mapping.items():
    assert config[item['flag']] is True
    item['source_evidence'] = []
    for name in item['source']:
        expected = snapshot['files']['contractedge/' + name]
        path = SYSTEM / 'src/contractedge' / name
        assert sha(path) == expected
        item['source_evidence'].append(reference(path))
    item['test_evidence'] = []
    for name in item['tests']:
        path = SYSTEM / 'tests' / name
        if 'tests/' + name in snapshot['fixture_files']:
            assert snapshot['fixture_files']['tests/' + name] == sha(path)
        else:
            assert name in {'test_structured_observation_adapters.py', 'test_ready_support_consistency.py'}
            import subprocess, hashlib
            published = subprocess.check_output(['git', '-C', str(SYSTEM.parent), 'show', 'e50be76:system/tests/' + name])
            assert hashlib.sha256(published).hexdigest() == sha(path)
            log = WORK / 'g1-supplemental-bindings.log'
            assert 'passed' in log.read_text() and 'FAILED ' not in log.read_text()
        nodes = [n.name for n in ast.walk(ast.parse(path.read_text()))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')]
        assert nodes
        item['test_evidence'].append({'file': reference(path), 'validation_scope': 'Exact full qualification fixture' if 'tests/' + name in snapshot['fixture_files'] else 'Additional unchanged published test with explicit current rerun',
                                      'test_definitions': nodes})
coverage = dict(schema_version='ce.implementation_evidence_map.v1', identity=identity,
    review_scope='Author requirement-to-implementation/test mapping; two previously unbound fixture modules are separately pinned to published bytes and rerun. Not independent whole-branch review, arbitrary semantic proof or real repair acceptance',
    supplemental_test_log=reference(WORK / 'g1-supplemental-bindings.log'),
    original_design=reference(SYSTEM / 'docs/reports/2026-10-20-final-optimization-design.md'),
    reliability_design=reference(SYSTEM / 'docs/reports/2026-10-20-reliability-closure-design.md'),
    qualification=reference(qpath), mechanisms=mapping, formal_ready=False)
coverage_path = OUT / 'IMPLEMENTATION_COVERAGE_v1.json'
with coverage_path.open('x') as f: json.dump(coverage, f, indent=2); f.write('\n')
g1 = gate('G1', [(key, [coverage_path, qpath] + [SYSTEM / 'tests' / name for name in item['tests']])
    for key, item in mapping.items()], [coverage_path, qpath, Path(panel['variants']['candidate']['config'])], coverage['review_scope'])

ci_path = OUT / 'CI_REMOTE_SUCCESS_e50be76.json'; ci = read(ci_path)
assert ci['conclusion'] == 'success'
steps = [s for j in ci['jobs'] for s in j['steps']]
assert any('Verify method namespaces before long' in s['name'] and s['conclusion'] == 'success' for s in steps)
assert q['actual8_exit_code'] == 0 and q['actual_runtime_sessions'] == 14 and q['actual_containers'] == 28
terminal_path = OUT / 'G3_VERIFIED_TERMINALS_v1.json'
with terminal_path.open('x') as f:
    json.dump(dict(identity=identity, terminal_records=records, recorded_slots=len(records),
        status='ALL_CLOSED_RECORDS_VERIFIED', full46_complete=len(records)==46,
        verifier=reference(SYSTEM / 'scripts/fixed_panel.py'), formal_ready=False), f, indent=2); f.write('\n')
g3 = gate('G3', [
    ('method_filesystem_isolation', [ci_path, qpath, SYSTEM / 'tests/test_isolated_method.py']),
    ('per_attempt_source_config_data_patch_score_identity', [terminal_path, SYSTEM / 'scripts/fixed_panel.py'])],
    [ci_path, qpath, terminal_path, Path(panel['output_root']) / 'records.json'],
    'Actual Linux namespace canaries and exact-source isolation; every currently closed original slot verified by existing protocol. Refresh after records append; incomplete effects remain G4/G5 failure. Serial only.')

old_manifest = read(OUT / 'FINAL_ADMISSION_MANIFEST_v1.json')
manifest = {**old_manifest, 'records': reference(Path(panel['output_root']) / 'records.json'),
    'gates': {**old_manifest['gates'], 'G0': g0, 'G1': g1, 'G3': g3},
    'scope': 'Engineering/static delivery evidence assembled; G4/G5 remain closed until original current-source effect acceptance.'}
with (OUT / 'FINAL_ADMISSION_MANIFEST_v2.json').open('x') as f: json.dump(manifest, f, indent=2); f.write('\n')
print(json.dumps({'records_verified':len(records), 'bound_reports':list(manifest['gates']), 'formal_ready':False}))
