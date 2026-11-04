"""Actual feedback/revision stage admission retains adopted state on the wire."""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.feedback import make_feedback
from contractedge.orchestration.recorder import ProviderJournal, RunRecorder
from contractedge.orchestration.repair import revise_plan
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.semantic_delivery import SemanticDeliveryError, install_monitor
from tests.test_stage_delivery_admission import config, ready_front
from tests.test_stage_material_boundary import walk


class DispatchReached(RuntimeError):
    pass


class WireCapture:
    def __init__(self, edit):
        self.edit, self.sent, self.changed = edit, [], False

    def prepare_request(self, request):
        messages = json.loads(json.dumps(request.messages))
        before = json.dumps(messages, sort_keys=True)
        messages = self.edit(messages)
        self.changed |= json.dumps(messages, sort_keys=True) != before
        body = json.dumps({'messages': messages}).encode()
        return SimpleNamespace(body=body, metadata={}, overhead='', output_schema=None,
            wire_schema=None, audit_record=lambda: {'body_sha256': hashlib.sha256(body).hexdigest()})

    def complete_prepared_json(self, request, prepared):
        self.sent.append(replace(request, messages=tuple(json.loads(prepared.body)['messages'])))
        raise DispatchReached('Offline captured stage request; no real model')


def monitored(tmp_path, trace, edit):
    port = WireCapture(edit)
    journal = ProviderJournal(RunRecorder(tmp_path, run_id=trace['run_id'], input_digest=trace['input_digest']))
    budget = BudgetedProvider(port, len, max_tokens=2000000, max_seconds=30, max_calls=8,
                              on_update=journal.update)
    journal.attach(budget)
    install_monitor(budget, journal)
    return port, budget, journal


def current_feedback(trace):
    return make_feedback(case_id=trace['case_id'], run_id=trace['run_id'], input_digest=trace['input_digest'],
        round_id=0, from_snapshot=trace['snapshot_id'], to_snapshot=trace['snapshot_id'],
        patch_digest='b' * 64, execution_status='EXECUTION_PASS',
        observations=[row for row in trace['diagnosis']['evidence_refs'] if row['kind'] == 'PUBLIC_OBSERVATION'],
        plan=trace['changeset'], review={'decision': 'UNKNOWN', 'checks': [], 'failure_kind': 'UNKNOWN'})


@pytest.mark.parametrize('remove_feedback', [False, True])
def test_feedback_rediagnosis_actual_wire_requires_complete_feedback(tmp_path, remove_feedback):
    method, source, trace = ready_front(tmp_path)
    feedback = current_feedback(trace)
    def mutate(messages):
        def edit(value):
            if remove_feedback and isinstance(value.get('execution_feedback'), dict):
                value['execution_feedback'].pop('rationale', None)
        return walk(messages, edit)
    port, budget, journal = monitored(tmp_path / 'rediagnosis', trace, mutate)
    result = run_front_half(method, source if method.source_root == '.' else source.parent,
        budget, {**config(), 'run_id': trace['run_id']}, len,
        execution_feedback=feedback, public_observations=tuple(feedback['public_observations']), journal=journal)
    if remove_feedback:
        assert port.changed
        assert result.status == 'EVIDENCE_DELIVERY_INCOMPLETE', result.errors
        assert port.sent == budget.calls == []
    else:
        assert len(port.sent) == len(budget.calls) == 1
        assert result.status != 'EVIDENCE_DELIVERY_INCOMPLETE', result.errors


@pytest.mark.parametrize('fault', ['none', 'feedback', 'rejected_trial', 'current_plan'])
def test_plan_revision_adopted_materials_on_actual_wire(tmp_path, fault):
    from tests.test_execution_runtime_reasoning import runtime_attempt
    method, source, trace = ready_front(tmp_path)
    feedback = current_feedback(trace)
    historical = tmp_path / 'historical'
    historical.mkdir()
    rejected = runtime_attempt(historical, {'snapshot_id': trace['snapshot_id']})['rejected_trial']
    def mutate(messages):
        if fault == 'current_plan':
            return [row for row in messages if row['role'] != 'assistant']
        def edit(value):
            if fault == 'feedback' and value.get('schema_version') == feedback['schema_version']:
                if value.get('to_snapshot') == feedback['to_snapshot']:
                    value.pop('rationale', None)
            if fault == 'rejected_trial' and isinstance(value.get('rejected_trial'), dict):
                value['rejected_trial'].pop('decision', None)
        return walk(messages, edit)
    port, budget, _ = monitored(tmp_path / 'revision', trace, mutate)
    with pytest.raises(DispatchReached if fault == 'none' else SemanticDeliveryError):
        revise_plan(trace, method, source, feedback, budget, len, config(), rejected_trial=rejected)
    if fault == 'none':
        assert len(port.sent) == len(budget.calls) == 1
    else:
        assert port.changed
        assert port.sent == budget.calls == []


@pytest.mark.parametrize('recovery,fault', [
    ('format', 'none'), ('truncation', 'none'),
    ('format', 'draft'), ('truncation', 'draft'), ('format', 'validation_feedback'),
    ('format', 'requirement'), ('truncation', 'requirement')])
def test_planning_recovery_keeps_its_adopted_draft_and_planning_material_contract(
        tmp_path, recovery, fault):
    from tests.test_stage_delivery_admission import prepared_case, RecordingPipeline

    _, runner, method, source, workspace = prepared_case(tmp_path)
    observations = runner.observe(tmp_path / 'prepared', tmp_path / 'broken')['observations']
    feedback_copies_removed = []
    def mutate(messages):
        if fault == 'requirement' and any(message['role'] == 'assistant' for message in messages):
            from tests.test_original_requirement_delivery import remove_exact_text
            return walk(messages, lambda value: remove_exact_text(value, method.requirement_text))
        if fault == 'draft':
            return [row for row in messages if row['role'] != 'assistant']
        if fault == 'validation_feedback':
            def edit(value):
                if value.get('validation_feedback'):
                    feedback_copies_removed.append(value.pop('validation_feedback'))
            return walk(messages, edit)
        return messages
    capture = WireCapture(mutate)
    class Pipeline(RecordingPipeline):
        planning_calls = 0

        def prepare_request(self, request):
            return capture.prepare_request(request)

        def complete_prepared_json(self, request, prepared):
            actual = replace(request, messages=tuple(json.loads(prepared.body)['messages']))
            capture.sent.append(actual)
            if actual.prompt_version.startswith('changeset.'):
                self.planning_calls += 1
                if self.planning_calls == 1:
                    metadata = ({'finish_reason': 'length', 'usage': {'completion_tokens': 1024,
                        'completion_tokens_details': {'reasoning_tokens': 900}}}
                        if recovery == 'truncation' else {})
                    return ModelResponse('{"status":', metadata)
            return super().complete_json(actual)

    port = Pipeline(source)
    directory = tmp_path / 'planning'
    journal = ProviderJournal(RunRecorder(directory, run_id='planning-boundary', input_digest=method.input_digest))
    budget = BudgetedProvider(port, len, max_tokens=2000000, max_seconds=30, max_calls=8,
                              on_update=journal.update)
    journal.attach(budget)
    monitor = install_monitor(budget, journal)
    frames, original_scope = [], monitor.scope
    def scope(context):
        frames.append(context)
        return original_scope(context)
    monitor.scope = scope
    result = run_front_half(method, workspace, budget, {**config(), 'run_id': 'planning-boundary',
        'max_format_retries': 1, 'planning_truncation_retry': 'disable_thinking'}, len,
        public_observations=tuple(observations), journal=journal)
    assert len(frames) == 3, result.errors
    # A recovery of the planning request must retain the planning requirements,
    # even when the orchestration event name does not start with CHANGESET.
    assert frames[2]['subjects'] == frames[1]['subjects']
    if fault != 'none':
        assert capture.changed
        if fault == 'validation_feedback':
            # The main planning payload and correction wrapper each carry the
            # same current errors; remove both so neither copy can hide loss.
            assert len(feedback_copies_removed) == 2
            assert feedback_copies_removed[0] == feedback_copies_removed[1]
        assert result.status == 'EVIDENCE_DELIVERY_INCOMPLETE', result.errors
        assert len(capture.sent) == len(budget.calls) == 2
    else:
        assert result.status in {'READY', 'READY_PARTIAL'}, result.errors
        assert len(capture.sent) == len(budget.calls) == 3
        assert capture.sent[-1].messages[-2]['content'] == '{"status":'
        if recovery == 'truncation':
            assert capture.sent[-1].thinking == 'disabled'
            assert result.model_calls[-1]['stage'] == 'PLANNING_TRUNCATION_FORMAT_RETRY'
