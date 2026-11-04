"""Delivery admission rejects incomplete evidence without dispatching a model."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


def load():
    path = Path(__file__).resolve().parents[1] / 'scripts/assess_formal_readiness.py'
    assert path.is_file(), 'The final admission aggregator is missing'
    spec = importlib.util.spec_from_file_location('formal_admission_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reports(tmp_path, module):
    identity = dict(source_sha256='a' * 64, panel_sha256='b' * 64,
                    candidate_config_sha256='c' * 64, data_files_sha256='d' * 64)
    checker = tmp_path / 'checker.py'
    checker.write_text('deterministic evidence checker\n')
    proof = tmp_path / 'actual.log'
    proof.write_text('completed, finite fixture evidence\n')
    refs = {}
    for gate, names in module.REQUIRED_CHECKS.items():
        report = dict(schema_version='ce.formal_gate_evidence.v1', gate=gate,
                      identity=identity, executed_at='2026-10-27T08:00:00+00:00',
                      checker=module.reference(checker), inputs=[module.reference(proof)],
                      checks=[dict(id=name, status='PASS', evidence=[module.reference(proof)])
                              for name in names])
        path = tmp_path / (gate + '.json')
        path.write_text(json.dumps(report))
        refs[gate] = module.reference(path)
    return identity, refs


def test_all_required_reports_validate_with_exact_bindings(tmp_path):
    module = load(); identity, refs = reports(tmp_path, module)
    result = module.check_gate_reports(refs, identity, tmp_path)
    assert set(result) == {'G' + str(i) for i in range(7)}
    assert all(row['status'] == 'PASS' for row in result.values())


@pytest.mark.parametrize('mutation', ['missing-report', 'forged-digest', 'old-source',
    'old-config', 'old-panel', 'old-data', 'not-run', 'unknown', 'missing-check',
    'duplicate-check', 'boolean-only', 'missing-proof', 'mutated-proof',
    'missing-checker', 'mutated-checker', 'naive-time', 'wrong-gate', 'symlink'])
def test_missing_stale_unrun_or_unbound_report_cannot_open_admission(tmp_path, mutation):
    module = load(); identity, refs = reports(tmp_path, module)
    path = tmp_path / 'G2.json'; value = json.loads(path.read_text())
    if mutation == 'missing-report': refs.pop('G2')
    elif mutation == 'forged-digest': refs['G2']['sha256'] = '0' * 64
    elif mutation.startswith('old-'):
        field = {'old-source': 'source_sha256', 'old-config': 'candidate_config_sha256',
                 'old-panel': 'panel_sha256', 'old-data': 'data_files_sha256'}[mutation]
        value['identity'][field] = 'f' * 64
    elif mutation in {'not-run', 'unknown'}: value['checks'][0]['status'] = mutation.upper().replace('-', '_')
    elif mutation == 'missing-check': value['checks'].pop()
    elif mutation == 'duplicate-check': value['checks'].append(copy.deepcopy(value['checks'][0]))
    elif mutation == 'boolean-only': value = {'status': 'PASS', 'formal_ready': True}
    elif mutation == 'missing-proof': value['checks'][0]['evidence'] = []
    elif mutation == 'mutated-proof': (tmp_path / 'actual.log').write_text('changed')
    elif mutation == 'missing-checker': value.pop('checker')
    elif mutation == 'mutated-checker': (tmp_path / 'checker.py').write_text('changed')
    elif mutation == 'naive-time': value['executed_at'] = '2026-10-27T08:00:00'
    elif mutation == 'wrong-gate': value['gate'] = 'G1'
    elif mutation == 'symlink':
        alias = tmp_path / 'alias.json'; alias.symlink_to(path)
        refs['G2']['path'] = str(alias)
    path.write_text(json.dumps(value))
    if mutation not in {'missing-report', 'forged-digest', 'symlink'}:
        refs['G2'] = module.reference(path)
    result = module.check_gate_reports(refs, identity, tmp_path)
    assert result['G2']['status'] == 'FAIL'
    assert result['G2']['errors']


def test_manifest_input_failure_is_a_machine_readable_rejection(tmp_path):
    module = load(); manifest = tmp_path / 'input.json'
    manifest.write_text(json.dumps({'schema_version': 'ce.formal_admission_manifest.v1'}))
    result = module.assess(manifest)
    assert result['formal_ready'] is False and result['status'] == 'NOT_READY'
    assert result['errors'] and result['new_model_http_calls'] == 0
    output = tmp_path / 'result.json'
    command = [sys.executable, str(Path(module.__file__)), '--manifest', str(manifest), '--output', str(output)]
    child = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert child.returncode == 1 and json.loads(output.read_text())['formal_ready'] is False


def test_actual_panel_adapter_rejects_duplicate_cached_results(tmp_path):
    module = load()
    from tests.test_fixed_panel_driver import prepared, load as driver
    panel, path, slot, transport = prepared(tmp_path)
    row = driver().run_slot(path, slot['slot_id'], tmp_path / 'unused-key', transport=transport)
    records_path = Path(panel['output_root']) / 'records.json'
    records_path.write_text(json.dumps([row, row]))
    with pytest.raises(ValueError, match='Duplicate'):
        module.check_panel(path)


@pytest.mark.parametrize('mutation', ['historical-source', 'fake-public-pass', 'public-fail', 'partial'])
def test_actual_terminal_evidence_prevents_false_admission(tmp_path, mutation):
    module = load()
    from tests.test_fixed_panel_driver import prepared, load as driver
    panel, path, slot, transport = prepared(tmp_path)
    row = driver().run_slot(path, slot['slot_id'], tmp_path / 'unused-key', transport=transport)
    if mutation == 'historical-source':
        receipt = Path(row['evidence_files']['receipt']['path'])
        value = json.loads(receipt.read_text()); value['identity']['method_files'] = {'other.py': '0' * 64}
        receipt.write_text(json.dumps(value))
    elif mutation == 'fake-public-pass':
        trace = Path(row['evidence_files']['trace']['path']); value = json.loads(trace.read_text())
        value['last_feedback']['semantic_outcome'] = 'UNKNOWN'
        trace.write_text(json.dumps(value))
    elif mutation == 'public-fail':
        trace = Path(row['evidence_files']['trace']['path']); value = json.loads(trace.read_text())
        value['last_feedback']['semantic_outcome'] = 'PUBLIC_REVIEW_FAIL'
        trace.write_text(json.dumps(value)); row['public_decision'] = 'FAIL'
    if mutation != 'partial':
        # Rehash the outer refs, so the existing terminal verifier must check
        # the deeper source, exported patch and independent score identities.
        for ref in row['evidence_files'].values(): ref['sha256'] = module.sha(Path(ref['path']))
        (Path(panel['output_root']) / 'records.json').write_text(json.dumps([row]))
    if mutation in {'historical-source', 'fake-public-pass', 'public-fail'}:
        with pytest.raises(ValueError): module.check_panel(path)
    else:
        result = module.check_panel(path)
        assert result['complete'] is False and result['formal_effect_pass'] is False
        assert result['closed_slots'] == 1 and result['expected_slots'] == 46


def test_genuine_negative_public_and_independent_result_stays_a_valid_failure(tmp_path):
    module = load()
    from tests.test_fixed_panel_driver import prepared, load as driver
    from scripts.fixed_panel import digest
    panel, path, slot, transport = prepared(tmp_path)
    tool = Path(panel['benchmark_root']) / 'tools/score_case.py'
    tool.write_text(tool.read_text().replace("'status':'PASS'", "'status':'SEMANTIC_FAIL'") + '\nraise SystemExit(1)\n')
    panel['execution_environment']['public_tool_files'] = digest({'score_case.py': module.sha(tool)})
    path.write_text(json.dumps(panel))
    original_dispatch = transport.dispatch
    def dispatch(command, log):
        code = original_dispatch(command, log)
        trace = Path(panel['output_root']) / 'methods' / slot['slot_id'] / slot['case_id'] / 'repair.trace.json'
        value = json.loads(trace.read_text()); value['last_feedback']['semantic_outcome'] = 'PUBLIC_REVIEW_FAIL'
        trace.write_text(json.dumps(value))
        return code
    transport.dispatch = dispatch
    row = driver().run_slot(path, slot['slot_id'], tmp_path / 'unused-key', transport=transport)
    assert row['status'] == 'SEMANTIC_FAIL' and row['public_decision'] == 'FAIL'
    result = module.check_panel(path)
    assert result['closed_slots'] == 1 and result['formal_effect_pass'] is False


def test_complete_finite_panel_and_bound_gate_reports_open_only_matching_delivery(tmp_path):
    """Real files, finite public fixture scoring, no model/benchmark efficacy claim."""
    module = load()
    from tests.test_fixed_panel_driver import prepared, load as driver, MethodTransport
    from scripts.fixed_panel import build_schedule, digest
    panel, panel_path, _, _ = prepared(tmp_path)
    original_path = Path(panel['original_registration']['path'])
    original = json.loads(original_path.read_text())
    cases = ['RW-CAND-130', 'R3-KPT-4673', 'R3-KOPF-726', 'R4-PROVIDER-HELM-408',
             'R4-ACK-CLOUDWATCH-2985', 'RW-ALERTMANAGER-4705', 'RW-CERTMANAGER-9116']
    rename = {r['case_id']: c for r, c in zip(original['A'], cases)}
    original['A'] = [{'case_id': c} for c in cases]
    original['key_repeat_cases'] = [cases[2], cases[5]]
    seeds = panel['seeds']
    panel['seeds'] = {cases[1]: seeds['A2'], cases[5]: seeds['A3']}
    original['recovery_seed_paths'] = {c: v['path'] for c, v in panel['seeds'].items()}
    original['recovery_seed_calibration'] = {c: {'seed_sha256': v['sha256']} for c, v in panel['seeds'].items()}
    original_path.write_text(json.dumps(original)); panel['original_registration'] = module.reference(original_path)
    benchmark = Path(panel['benchmark_root'])
    for name in ['public/manifest.jsonl', 'partition/member_groups.jsonl']:
        path = benchmark / name
        rows = [json.loads(s) for s in path.read_text().splitlines()]
        for row in rows: row['case_id'] = rename.get(row['case_id'], row['case_id'])
        path.write_text('\n'.join(json.dumps(r) for r in rows)); panel['data_files'][name] = module.sha(path)
    panel['slots'] = build_schedule(original, panel['panel_id'])
    panel_path.write_text(json.dumps(panel))
    run = driver()
    for group in ['A', 'repeat', 'recovery', 'B']:
        for slot in [s for s in panel['slots'] if s['group'] == group]:
            candidate = not (slot['group'] == 'A' and slot['case_id'] == cases[0] and slot['variant'] == 'baseline')
            transport = MethodTransport(panel, panel_path, slot, candidate=candidate)
            run.run_slot(panel_path, slot['slot_id'], tmp_path / 'unused-key', transport=transport)
    root = Path(panel['variants']['candidate']['root'])
    fixture = root / 'finite-fixture.txt'; fixture.write_text('bounded test fixture')
    tool = root / 'finite-tool.py'; tool.write_text('finite checker')
    config_file = root / 'candidate-config.json'
    config_file.write_bytes(Path(panel['variants']['candidate']['config']).read_bytes())
    configuration = {'stage_configuration_files': {config_file.name: module.sha(config_file)}}
    fixture_panel = [fixture.name]; tool_map = {tool.name: module.sha(tool)}
    snapshot = dict(schema_version='ce.development_snapshot.v1', formal_ready=False,
                    development_snapshot_id='finite-admission-unit-test',
                    files=panel['variants']['candidate']['source_files'],
                    source_sha256=panel['variants']['candidate']['source_sha256'],
                    configuration=configuration, configuration_sha256=digest(configuration),
                    fixture_panel=fixture_panel, fixture_panel_sha256=digest(fixture_panel),
                    fixture_files={fixture.name: module.sha(fixture)},
                    tooling_files=tool_map, tooling_files_sha256=digest(tool_map))
    snapshot_path = root / 'snapshot.json'; snapshot_path.write_text(json.dumps(snapshot))
    identity, refs = reports(tmp_path, module)
    identity.update(source_sha256=snapshot['source_sha256'], panel_sha256=module.sha(panel_path),
                    candidate_config_sha256=panel['variants']['candidate']['config_sha256'],
                    data_files_sha256=digest(panel['data_files']))
    for gate in refs:
        path = tmp_path / (gate + '.json'); value = json.loads(path.read_text())
        value['identity'] = identity; path.write_text(json.dumps(value)); refs[gate] = module.reference(path)
    manifest = dict(schema_version='ce.formal_admission_manifest.v1', system_root=str(root),
                    snapshot=module.reference(snapshot_path), panel=module.reference(panel_path),
                    records=module.reference(Path(panel['output_root']) / 'records.json'), gates=refs)
    path = tmp_path / 'admission.json'; path.write_text(json.dumps(manifest))
    result = module.assess(path)
    assert result['formal_ready'] is True, json.dumps(result, indent=2)
    assert result['panel']['closed_slots'] == 46 and result['new_model_http_calls'] == 0
    refs.pop('G6'); path.write_text(json.dumps(manifest))
    assert module.assess(path)['formal_ready'] is False
