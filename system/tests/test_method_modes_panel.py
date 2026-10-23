"""Actual mode routes, source provenance, fixed profiles and legacy equivalence."""
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contractedge.contracts.repair_intent import RequirementOnlyIntent
from contractedge.contracts.validation import canonical_digest
from contractedge.development import public_demo
from contractedge.development.method_panel import (PanelProvider, panel_config, run_cell)
from contractedge.development.method_panel_fixtures import (CASES, MODES, PROBE_ID, PanelRunner,
    case_definition, freeze_panel_case, enforce_requirement_profile, synthetic_edits)
from contractedge.experiments.modes import (public_feedback, source_input, direct_plan_request,
    method_mode, repair_intent, audit_mode_calls, generate_nonrelation)
from contractedge.execution.consumer import ConsumerContext, BudgetPort
from contractedge.execution.consumers.direct import ModifyOnlyConsumer
from contractedge.execution.literal import PlanRejected
from contractedge.model.provider import ModelResponse
from contractedge.model.budget import BudgetedProvider
from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot


@pytest.mark.parametrize('mode', MODES)
def test_production_mode_dispatches_only_its_own_real_stages(tmp_path, mode):
    row = run_cell('config-binding/base', mode, tmp_path / mode)
    assert row['status'] == 'PUBLIC_REVIEW_COMPLETE'
    assert row['broken_target_fail_proven']
    assert row['removed_stage_calls_zero']
    assert row['intent'] == row['expected_intent']
    assert set(row['candidate_checks'].values()) == {'PASS'}
    expected = {'full': (1, 1, 0, 1), 'without_relation': (0, 0, 1, 1),
        'without_changeset': (1, 0, 0, 1), 'direct': (0, 0, 0, 1)}[mode]
    assert tuple(row['stage_counts'][key] for key in ('relation', 'changeset', 'direct_plan', 'execution')) == expected
    root = tmp_path / mode / 'repair'
    front = json.loads((root / 'rounds/0/front.trace.json').read_text())
    item = json.loads((root / 'rounds/0/plan.json').read_text())
    if mode in {'direct', 'without_changeset'}:
        assert item['writes'] == []
        assert json.loads((root / 'rounds/0/consumer-input.json').read_text())['operation_coverage'].startswith('NOT_APPLICABLE')
    if mode != 'full':
        assert front['changeset'] is None
    if mode in {'direct', 'without_relation'}:
        assert front['diagnosis'] is None
    if mode == 'direct':
        assert set(item['model_authored_input']) == {'requirement'}


def test_feedback_projection_rejects_semantic_source_pollution_and_preserves_raw_facts():
    raw = {'id': 'OBS-a', 'snapshot_id': 's', 'excerpt': 'public output',
        'content_digest': hashlib.sha256(b'public output').hexdigest()}
    tainted = {'from_snapshot': 's0', 'to_snapshot': 's', 'build_state': 'PASSED', 'public_observations': [raw],
        'diagnosis': {'mark': 'FORBIDDEN_OLD_DIAGNOSIS_937'},
        'coordination_links': [{'relation_refs': ['FORBIDDEN_OLD_RELATION_938']}],
        'retained_constraints': [{'statement': 'FORBIDDEN_OLD_PLAN_939'}],
        'rationale': 'FORBIDDEN_OLD_ATTRIBUTION_940'}
    projected = public_feedback(tainted)
    assert projected['public_observations'] == [raw]
    assert 'FORBIDDEN_' not in json.dumps(projected)
    tainted['public_observations'][0]['snapshot_id'] = 'stale'
    with pytest.raises(PlanRejected, match='stale or altered'):
        public_feedback(tainted)


def test_direct_plan_final_schema_and_correction_receive_no_old_full_products(tmp_path):
    source = tmp_path / 'src'; source.mkdir(); (source / 'app.py').write_text('name = "default"\n')
    method = SimpleNamespace(requirement_text='Keep the relation field named changeset as public data.',
        input_digest='a' * 64, declared_artifacts=(), case_id='public')
    tainted = {'from_snapshot': 's0', 'to_snapshot': 's1', 'build_state': 'PASSED', 'public_observations': [],
        'diagnosis': 'FORBIDDEN_RELATION_FROM_FULL', 'retained_constraints': ['FORBIDDEN_P_FROM_FULL']}
    inputs = source_input(method, source, {'allowed_paths': ['app.py']}, feedback=tainted)
    request = direct_plan_request(inputs, panel_config('without_relation'))
    serialized = json.dumps(request.messages) + json.dumps(request.output_schema) + json.dumps(request.metadata)
    assert 'FORBIDDEN_' not in serialized
    assert 'relation field named changeset' in serialized  # no keyword stripping
    assert request.output_schema['additionalProperties'] is False
    assert request.metadata['semantic_input_stages'] == []
    assert 'diagnosis' not in json.loads(request.messages[1]['content'])


def test_actual_direct_plan_format_correction_does_not_copy_forbidden_feedback_products(tmp_path):
    (tmp_path / 'app.py').write_text('value=0\n')
    method = SimpleNamespace(requirement_text='Set value to one; keep public relation wording.',
        input_digest='b' * 64, declared_artifacts=(), case_id='public')
    requests = []
    class Provider:
        def complete_json(self, request):
            requests.append(request)
            body = json.loads(request.messages[1]['content'])
            if len(requests) == 1:
                return ModelResponse('{"steps":[]}', {})
            return ModelResponse(json.dumps({'schema_version': 'ce.direct_plan.v1',
                'snapshot_id': body['snapshot_id'], 'input_digest': body['input_digest'],
                'goal': 'Set value, preserve relation wording.',
                'steps': [{'id': 'own', 'path': 'app.py', 'operation': 'MODIFY',
                    'intent': 'Keep legal relation wording.', 'depends_on': []}]}), {})
    config = {**panel_config('without_relation'), 'max_format_retries': 1}
    budget = BudgetedProvider(Provider(), public_demo.count_tokens, max_tokens=500000, max_seconds=30, max_calls=6)
    trace = generate_nonrelation('without_relation', method, tmp_path, {'allowed_paths': ['app.py']},
        budget, public_demo.count_tokens, config, feedback={'from_snapshot': 's0', 'to_snapshot': 's1',
            'build_state': 'PASSED', 'public_observations': [], 'coordination_links': ['FORBIDDEN_FULL_RELATION_991'],
            'retained_constraints': ['FORBIDDEN_FULL_PLAN_992']})
    assert len(requests) == 2
    assert requests[0].output_schema == requests[1].output_schema
    assert 'FORBIDDEN_FULL_' not in json.dumps(budget.calls)
    assert trace['diagnosis'] is None and trace['changeset'] is None
    assert 'relation wording' in trace['direct_plan']['steps'][0]['intent']


def test_actual_direct_execution_feedback_and_own_correction_keep_provenance(tmp_path):
    (tmp_path / 'app.py').write_text('value=0\n'); snapshot = compute_snapshot(tmp_path)
    consumer = ModifyOnlyConsumer(); requests = []
    projected = public_feedback({'from_snapshot': 's0', 'to_snapshot': snapshot.snapshot_id,
        'build_state': 'PASSED', 'public_observations': [], 'diagnosis': 'FORBIDDEN_OLD_E_993',
        'coordination_links': ['FORBIDDEN_OLD_LINK_994'], 'retained_constraints': ['FORBIDDEN_OLD_P_995']})
    context = ConsumerContext(tmp_path, {'allowed_paths': ['app.py']}, panel_config('direct'),
        'Set value to one.', feedback=projected, previous_attempt={
            'consumer_id': consumer.ID, 'intent_kind': 'REQUIREMENT_ONLY',
            'model_response': 'OWN_DIRECT_DRAFT_996', 'validation_error': 'own draft invalid'})
    item = consumer.prepare(RequirementOnlyIntent(snapshot.snapshot_id, 'a' * 64), context)
    accepted = consumer.accept(item, snapshot, context.policy)
    class Provider:
        def complete_json(self, request):
            requests.append(request)
            return ModelResponse(json.dumps({'replacements': [{'key': 'own', 'file': 'app.py',
                'before': 'value=0', 'after': 'value=1'}]}), {})
    receipt = consumer.execute(accepted, BudgetPort(Provider(), public_demo.count_tokens, context.config))
    assert receipt['status'] == 'APPLIED'
    value = json.dumps(requests[0].messages) + json.dumps(requests[0].output_schema)
    assert 'FORBIDDEN_OLD_' not in value and 'OWN_DIRECT_DRAFT_996' in value
    payload = json.loads(requests[0].messages[1]['content'])
    assert payload['repair_input']['writes'] == []
    assert set(payload['repair_input']['model_authored_input']) == {'requirement'}
    assert payload['public_feedback']['semantic_attribution'] == 'UNKNOWN'


@pytest.mark.parametrize('mode', ['without_relation', 'without_changeset', 'direct'])
def test_forbidden_trace_products_cannot_be_relabelled_as_an_ablation(tmp_path, mode):
    (tmp_path / 'app.py').write_text('value=0\n'); snapshot = compute_snapshot(tmp_path)
    trace = {'snapshot_id': snapshot.snapshot_id, 'input_digest': 'd' * 64,
        'diagnosis': {'FORBIDDEN_FULL_RELATION': 1}, 'changeset': {'FORBIDDEN_FULL_PLAN': 1},
        'direct_plan': {'steps': []}}
    method = SimpleNamespace(input_digest='d' * 64)
    with pytest.raises(PlanRejected, match='forbidden'):
        repair_intent(mode, trace, method, tmp_path)


def test_direct_correction_checks_mode_provenance_again_at_final_dispatch(tmp_path):
    (tmp_path / 'app.py').write_text('value=0\n'); snapshot = compute_snapshot(tmp_path)
    consumer = ModifyOnlyConsumer(); context = ConsumerContext(tmp_path, {'allowed_paths': ['app.py']},
        panel_config('direct'), 'Set value to one.')
    item = consumer.prepare(RequirementOnlyIntent(snapshot.snapshot_id, 'a' * 64), context)
    accepted = consumer.accept(item, snapshot, context.policy)
    item = replace(item, context=replace(context, previous_attempt={'consumer_id': consumer.ID,
        'intent_kind': 'SEMANTIC_CHANGESET', 'model_response': 'FORBIDDEN_FULL_CORRECTION_MARKER'}))
    accepted = replace(accepted, execution_input=item)
    provider = SimpleNamespace(complete_json=lambda request: pytest.fail('must reject before dispatch'))
    with pytest.raises(PlanRejected, match='another consumer or semantic mode'):
        consumer.execute(accepted, BudgetPort(provider, public_demo.count_tokens, context.config))


@pytest.mark.parametrize('corruption', ['drop_check', 'stale', 'alter_spec'])
def test_fixed_public_profile_cannot_be_weakened_by_a_model_plan(tmp_path, corruption):
    public = freeze_panel_case('config-binding/base', tmp_path)
    runner = PanelRunner(public, 'config-binding/base'); workspace = tmp_path / 'workspace'
    runner.prepare(runner.case, workspace, tmp_path / 'prepare')
    observed = runner.observe(workspace, tmp_path / 'observe')
    assert enforce_requirement_profile({'decision': 'PASS'}, observed)['decision'] == 'FAIL'
    bad = copy.deepcopy(observed)
    if corruption == 'drop_check':
        bad['requirement_profile']['checks'].pop()
    elif corruption == 'stale':
        bad['snapshot_id'] = 'stale'
    else:
        bad['requirement_profile']['spec_digest'] = '0' * 64
    review = enforce_requirement_profile({'decision': 'PASS'}, bad)
    assert review['requirement_profile_valid'] is False
    assert review['decision'] == 'UNKNOWN'


def test_default_full_matches_explicit_full_request_bytes(tmp_path):
    requests = []
    for explicit in (False, True):
        root = tmp_path / str(explicit); root.mkdir()
        public = freeze_panel_case('config-binding/base', root)
        runner = PanelRunner(public, 'config-binding/base'); provider = PanelProvider('config-binding/base', 'full', runner)
        config = panel_config('full'); config['run_id'] = 'd2-legacy-full-equivalence'
        if not explicit:
            config.pop('method_mode')
        report = repair_case(public, root / 'unused-tools', runner.case, root / 'repair', provider,
            public_demo.count_tokens, config, public_runner=runner)
        assert report['status'] == 'PUBLIC_REVIEW_COMPLETE'
        requests.append(provider.requests)
    assert requests[0] == requests[1]


def test_method_mode_is_independent_of_execution_backend():
    for mode in MODES:
        assert method_mode({'method_mode': mode, 'execution_backend': 'topomen_agent'}) == mode
    assert method_mode({}) == 'full'
    with pytest.raises(ValueError, match='method_mode'):
        method_mode({'method_mode': 'made-up'})


def test_separate_feedback_control_preserves_removed_stage_zero_calls(tmp_path):
    from contractedge.development.method_panel import run_feedback_study
    rows = run_feedback_study(tmp_path / 'feedback')
    for row in rows:
        assert row['round_count'] == 2 and row['removed_stage_calls_zero']
        assert row['round_profiles'][0]['primary'] == 'PASS'
        assert row['round_profiles'][0]['secondary'] == 'FAIL'
        assert set(row['round_profiles'][1].values()) == {'PASS'}
        assert row['status'] == 'PUBLIC_REVIEW_COMPLETE'


@pytest.mark.parametrize('fault', ['target-fail', 'target-unknown', 'preservation-fail',
    'preservation-unknown', 'missing-target', 'missing-preservation', 'incomplete-status',
    'duplicate-pair', 'incumbent-export'])
def test_panel_exit_gate_keeps_failed_cells_and_rejects_false_synthetic_success(tmp_path, monkeypatch, fault):
    """Harness control only: no candidate, Docker, provider or paid ledger is run."""
    import contractedge.development.method_panel as panel

    def cell(case_id, mode, output, **kwargs):
        case = case_definition(case_id)
        row = {'fixture_id': case_id, 'method_mode': mode, 'status': 'PUBLIC_REVIEW_COMPLETE',
            'intent': panel.INTENTS[mode], 'expected_intent': panel.INTENTS[mode],
            'candidate_export': {'snapshot_id': 'fixed-synthetic-candidate'},
            'broken_target_fail_proven': True, 'removed_stage_calls_zero': True,
            'broken_isolation_complete': True, 'candidate_isolation_complete': True,
            'stage_counts': {},
            'target_checks': {key: 'PASS' for key in case['repair_item_ids']},
            'preservation_checks': {key: 'PASS' for key in case['preservation_item_ids']}}
        for key in ('raw_requirement_digest', 'method_input_digest', 'source_archive_sha256',
            'initial_snapshot_id', 'public_profile_digest', 'consumer', 'shared_resource_config_digest'):
            row[key] = case_id
        if (case_id, mode) == ('config-binding/base', 'direct'):
            if fault.startswith('target-'): row['target_checks'][case['repair_item_ids'][0]] = fault[7:].upper()
            elif fault.startswith('preservation-'): row['preservation_checks'][case['preservation_item_ids'][0]] = fault[13:].upper()
            elif fault == 'missing-target': row['target_checks'] = {}
            elif fault == 'missing-preservation': row['preservation_checks'] = {}
            elif fault == 'incomplete-status': row['status'] = 'UNKNOWN'
            elif fault == 'duplicate-pair': row['method_mode'] = 'full'
            elif fault == 'incumbent-export':
                row['candidate_export'] = {'snapshot_id': row['initial_snapshot_id']}
                row['target_checks'][case['repair_item_ids'][0]] = 'FAIL'
        return row

    monkeypatch.setattr(panel, 'run_cell', cell)
    output = tmp_path / 'panel'
    code = panel.run_panel(output, runtime_config={})
    manifest = json.loads((output / 'manifest.json').read_text())
    assert len(manifest['cells']) == manifest['cell_count'] == 24
    assert code == 1, 'A failed fixed synthetic fixture must not be certified by exported incumbent/candidate'
    assert manifest['acceptance']['status'] == 'FAIL'
    assert manifest['acceptance']['failed_cells']
    assert any(r['fixture_id'] == 'config-binding/base' and (r['method_mode'] == 'direct' or fault == 'duplicate-pair')
        for r in manifest['cells']), 'Original failing row must be retained'


@pytest.mark.parametrize('subset', [False, True])
def test_panel_exit_gate_accepts_only_the_complete_expected_synthetic_cells(tmp_path, monkeypatch, subset):
    """Positive harness control also covers the explicit one-cell CLI selection."""
    import contractedge.development.method_panel as panel

    def cell(case_id, mode, output, **kwargs):
        case = case_definition(case_id)
        row = {'fixture_id': case_id, 'method_mode': mode, 'status': 'PUBLIC_REVIEW_COMPLETE',
            'intent': panel.INTENTS[mode], 'expected_intent': panel.INTENTS[mode],
            'candidate_export': {'snapshot_id': 'fixed-synthetic-candidate'},
            'broken_target_fail_proven': True, 'removed_stage_calls_zero': True,
            'broken_isolation_complete': True, 'candidate_isolation_complete': True,
            'stage_counts': {},
            'target_checks': {key: 'PASS' for key in case['repair_item_ids']},
            'preservation_checks': {key: 'PASS' for key in case['preservation_item_ids']}}
        for key in ('raw_requirement_digest', 'method_input_digest', 'source_archive_sha256',
            'initial_snapshot_id', 'public_profile_digest', 'consumer', 'shared_resource_config_digest'):
            row[key] = case_id
        return row

    monkeypatch.setattr(panel, 'run_cell', cell)
    output = tmp_path / 'panel'
    kwargs = {'case_id': 'config-binding/base', 'method_mode': 'direct'} if subset else {}
    assert panel.run_panel(output, runtime_config={}, **kwargs) == 0
    manifest = json.loads((output / 'manifest.json').read_text())
    assert manifest['acceptance']['status'] == 'PASS'
    assert manifest['acceptance']['expected_cell_count'] == (1 if subset else 24)
    assert manifest['acceptance']['failed_cells'] == []
