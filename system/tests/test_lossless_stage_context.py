"""Regression boundaries for complete selected material across actual requests."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import hashlib
import json

import pytest

from contractedge.execution.consumer import execution_review_mapping
from contractedge.model.budget import BudgetExceeded
from contractedge.execution.topomen import ModelPlanExecutor, PlanRejected
from contractedge.execution.topomen import apply_edits
from contractedge.orchestration.feedback import feedback_prompt_view, historical_trial_prompt_view
from contractedge.orchestration.runner import required_planning_source_support
from contractedge.retrieval.candidates import build_candidate_set
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot
from tests.test_execution_support_windows import fixture, request
from tests.test_failure_tasks import predicate_record
from tests.test_production_request_admission import NativePort


def test_initial_selection_reaches_planning_without_gap_or_model_citation(tmp_path):
    (tmp_path / 'worker.py').write_text('def helper(value):\n    return value\n')
    snapshot = compute_snapshot(tmp_path)
    units = parse_source_units('worker.py', (tmp_path / 'worker.py').read_text(), snapshot.snapshot_id)
    selected = build_candidate_set('helper', None, units, [], top_n=12,
        max_context_tokens=10000, token_counter=len)
    selected = replace(selected, required_endpoint_ids=(), extension_used=False)
    records = required_planning_source_support(
        SimpleNamespace(declared_artifacts=(), public_evidence=()), selected, snapshot.snapshot_id)
    assert selected.candidates
    assert {r['id'] for r in records} == {'SRC-' + c['id'] for c in selected.candidates}
    assert all(r['delivery_required'] for r in records)
    assert [r['excerpt'] for r in records] == [c['text'] for c in selected.candidates]


def test_feedback_keeps_body_when_matching_record_is_not_in_this_request():
    record = predicate_record()
    original = dict(to_snapshot='TRIAL', public_observations=[record])
    before = deepcopy(original)
    view = feedback_prompt_view(original)
    assert view['public_observations'] == [record]
    assert original == before


def test_current_observation_does_not_replace_historical_same_local_id():
    historical = predicate_record(snapshot='OLD')
    current = predicate_record(snapshot='CURRENT')
    view = feedback_prompt_view(dict(to_snapshot='OLD', public_observations=[historical]),
        supplied_observations=[current])
    assert view['public_observations'] == [historical]
    assert 'public_observation_record_ref' not in view['public_observation_refs'][0]


def test_rejected_trial_keeps_full_quotes_obligations_and_reason():
    row = predicate_record()
    value = dict(to_snapshot='TRIAL', public_observations=[row], rationale='reason ' * 1000,
        pending_obligations=[dict(id='P1', statement='Still required')],
        public_check_results=[dict(id='P1', status='FAIL', rationale='details ' * 1000,
            evidence=[dict(observation_id=row['id'], quote=row['excerpt'])])])
    view = historical_trial_prompt_view(value, canonical_text='Still required')
    assert all(view[k] == v for k, v in value.items())


def test_review_receives_complete_edit_beyond_old_character_cap():
    edit = dict(old_text='before\n' * 4000, new_text='after\n' * 4000)
    result = {'from_snapshot': 'before', 'to_snapshot': 'after',
              'semantic_edit_trace': [dict(change_id='C1', literal_edits=[edit])]}
    mapping = execution_review_mapping(result)
    assert mapping[0]['literal_edits'][0] == {**edit, 'truncated': False}
    with pytest.raises(BudgetExceeded, match='context'):
        execution_review_mapping(result, max_characters=10)


@pytest.mark.parametrize('required', [False, True])
def test_selected_support_cannot_disappear_at_local_limit_before_dispatch(tmp_path, required):
    plan, config, _ = fixture(tmp_path, required=required)
    config['max_execution_support_bytes'] = 1
    provider = NativePort()
    with pytest.raises(BudgetExceeded, match='read-only support budget'):
        ModelPlanExecutor(provider, len, config).execute(plan, tmp_path,
            {'allowed_paths': ['worker.*']}, requirement='Preserve input identity')
    assert provider.dispatches == []


def test_selected_optional_support_cannot_be_popped_to_fit_final_request(tmp_path):
    plan, config, record = fixture(tmp_path, required=False)
    initial, body = request(tmp_path, plan, config)
    assert record in body['read_only_source_support']
    config['max_context_tokens'] = sum(len(m['content']) for m in initial.messages) - 1
    with pytest.raises(BudgetExceeded, match='full context budget'):
        request(tmp_path, plan, config)


def test_historical_source_body_remains_historical_after_current_reread(tmp_path):
    plan, config, record = fixture(tmp_path, historical=True)
    _, body = request(tmp_path, plan, config)
    historical = body['accepted_plan']['coordination_context']['source_support'][0]
    assert historical == record
    current = body['read_only_source_support'][0]
    assert current['excerpt'] == record['excerpt']
    assert current['snapshot_id'] == plan['snapshot_id'] != record['snapshot_id']


@pytest.mark.parametrize('explicit_precondition', [False, True])
def test_edited_selected_target_can_continue_without_asserting_old_precondition(tmp_path, explicit_precondition):
    from contractedge.orchestration.repair import rebind_execution_plan
    plan, config, record = fixture(tmp_path)
    lines = (tmp_path / 'worker.py').read_text().splitlines(keepends=True)
    excerpt = ''.join(lines[204:207])
    record.update(excerpt=excerpt, content_digest=hashlib.sha256(excerpt.encode()).hexdigest(),
        locator={'path': 'worker.py', 'start_line': 205, 'end_line': 207})
    if explicit_precondition:
        plan['precondition_review'] = [dict(source_evidence_ref=record['id'], disposition='REQUIRED')]
    applied = apply_edits(plan, tmp_path, {'allowed_paths': ['worker.*']}, {'edits': [dict(
        change_id='C1', path='worker.py', operation='MODIFY', old_text='return required_helper(value)',
        new_text='return required_helper(value + 1)')]})
    rebound = rebind_execution_plan(plan, tmp_path, {'route': 'EXECUTE'}, prior_execution=applied)
    if explicit_precondition:
        with pytest.raises(PlanRejected, match='source was not delivered'):
            request(tmp_path, rebound, config)
        return
    _, body = request(tmp_path, rebound, config)
    current = body['current_target_files'][0]
    assert current['content'] == (tmp_path / 'worker.py').read_text()
    assert 'windowed_from_current_target' not in current
    assert body['accepted_plan']['coordination_context']['source_support'][0] == record
    provider = NativePort()
    with pytest.raises(BudgetExceeded, match='context budget'):
        ModelPlanExecutor(provider, len, {**config, 'max_context_tokens': 1}).execute(
            rebound, tmp_path, {'allowed_paths': ['worker.*']}, requirement='Preserve input identity')
    assert provider.dispatches == []
