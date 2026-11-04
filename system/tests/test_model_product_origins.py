"""Final artifacts and diagnosis decisions name their actual producing call."""
from dataclasses import replace
import json

import pytest

from contractedge.model.budget import BudgetedProvider, recorded_token_charge
from contractedge.model.provider import ModelRequest, ModelResponse
from contractedge.orchestration.recorder import ProviderJournal, RunRecorder
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.state import RepairEngine
from contractedge.source.snapshot import compute_snapshot
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import PlanningGapProvider, front_fixture


def read(path):
    return json.loads(path.read_text())


class FlowProvider(PipelineFixtureProvider):
    def __init__(self, scenario):
        super().__init__()
        self.scenario = scenario
        self.counts = {'diagnosis': 0, 'planning': 0}
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        kind = ('diagnosis' if request.prompt_version.startswith('diagnosis.') else
                'planning' if request.prompt_version.startswith('changeset.') else None)
        if kind is None:
            return ModelResponse('{}', {'fixture': 'prior-call'})
        self.counts[kind] += 1
        index = self.counts[kind]
        if self.scenario == kind + '_unknown':
            raise TimeoutError('Controlled response unknown')
        if self.scenario == kind + '_invalid' or self.scenario == kind + '_format' and index == 1:
            return ModelResponse('{}', {'finish_reason': 'stop'})
        if (self.scenario == kind + '_truncated' and index == 1 or
                self.scenario == 'extension_truncated' and kind == 'diagnosis' and index == 2):
            return ModelResponse('{"status":', {'finish_reason': 'length', 'usage': {
                'completion_tokens': request.max_output_tokens,
                'completion_tokens_details': {'reasoning_tokens': request.max_output_tokens}}})
        response = super().complete_json(request)
        if (self.scenario in {'diagnosis_extension', 'extension_truncated'}
                and kind == 'diagnosis' and index == 1):
            payload = json.loads(response.content)
            payload.update(status='EVIDENCE_GAP', evidence_gaps=[{'id': 'G1',
                'summary': 'Check optional template inventory',
                'locator_hint': {'path': 'templates/optional-serviceaccount.yaml'}}])
            return ModelResponse(json.dumps(payload), {})
        return response


def run_flow(tmp_path, scenario, *, offset=False):
    source = tmp_path / 'source'
    source.mkdir()
    value, _ = front_fixture(source)
    provider = PlanningGapProvider() if scenario == 'planning_extension' else FlowProvider(scenario)
    provider._snapshot_digests = compute_snapshot(source).file_digests
    counter = lambda text: len(text.split())
    config = {'model': {'model': 'fixture'}, 'run_id': 'origin-fixture',
        'max_context_tokens': 24000, 'max_output_tokens': 1024, 'max_total_tokens': 100000,
        'max_model_calls': 8, 'max_elapsed_seconds': 30, 'max_format_retries': 1,
        'planning_truncation_retry': 'disable_thinking'}
    if scenario.endswith('_invalid'):
        config['max_format_retries'] = 0
    previous = BudgetedProvider(FlowProvider('normal'), counter,
        max_tokens=100000, max_seconds=30, max_calls=8)
    if offset:
        previous.complete_json(ModelRequest('fixture', 'older-front', (), 8))
    recorder = RunRecorder(tmp_path / 'journal-root', run_id=config['run_id'], input_digest=value['input_digest'])
    journal = ProviderJournal(recorder, prior_calls=previous.calls)
    budget = BudgetedProvider(provider, counter, max_tokens=100000, max_seconds=30,
        max_calls=8, on_update=journal.update)
    budget.prior_calls = len(previous.calls)
    budget.reserved_tokens = sum(recorded_token_charge(call) for call in previous.calls)
    journal.attach(budget)
    journal.engine = RepairEngine(recorder, value['initial_snapshot_id'], config)
    if offset:
        # A prior segment plus another stage in the active shared budget means
        # neither absolute nor segment-local indices start at this front.
        if scenario == 'planning_extension':
            original = provider.complete_json
            provider.complete_json = lambda request: (ModelResponse('{}', {})
                if request.prompt_version == 'earlier-shared-stage' else original(request))
        budget.complete_json(ModelRequest('fixture', 'earlier-shared-stage', (), 8))
    first = journal.first_call_index + len(budget.calls)
    trace = run_front_half(value, source, budget, config, counter, journal=journal)
    return trace, budget, journal, first


def final_receipt(journal, stage):
    return journal.recorder._read(journal.latest[stage].relative_path)


def linked_call(journal, reference):
    reference = journal.recorder.verify_call(reference)
    rows = journal.recorder._read(reference.ledger_segment_path)
    return rows[reference.absolute_call_index - journal.first_call_index]


@pytest.mark.parametrize('offset', [False, True])
@pytest.mark.parametrize('scenario,diagnosis_index,planning_index', [
    ('normal', 0, 1), ('diagnosis_truncated', 1, 2), ('planning_truncated', 0, 2),
    ('diagnosis_format', 1, 2), ('planning_format', 0, 2),
    ('diagnosis_extension', 1, 2), ('planning_extension', 2, 3),
    ('extension_truncated', 2, 3),
])
def test_final_artifact_origin_is_the_successful_response_in_every_front_flow(
        tmp_path, scenario, diagnosis_index, planning_index, offset):
    trace, budget, journal, first = run_flow(tmp_path, scenario, offset=offset)
    assert trace.status == 'READY', trace.errors
    for stage, relative_index, value in [('diagnosis', diagnosis_index, trace.diagnosis),
                                         ('planning', planning_index, trace.changeset)]:
        receipt = final_receipt(journal, stage)
        assert [ref['absolute_call_index'] for ref in receipt['call_refs']] == [first + relative_index]
        call = linked_call(journal, receipt['call_refs'][0])
        assert call['dispatch_state'] == 'response_known'
        assert call['response_metadata'].get('finish_reason') != 'length'
        payload = json.loads(call['response_content'])
        assert payload['status'] == value['status']
        field = 'violations' if stage == 'diagnosis' else 'changes'
        assert payload[field] == value[field]
        output = receipt['output_refs'][0]
        assert journal.recorder._read(output['relative_path']) == value
    decisions = [read(path) for path in sorted(journal.recorder.root.glob('journal/transitions/revision-*.json'))]
    diagnosis_decision = [row for row in decisions if row['result']['stage'] == 'diagnosis'][-1]
    assert [ref['absolute_call_index'] for ref in diagnosis_decision['result']['call_refs']] == [first + diagnosis_index]
    assert diagnosis_decision['result']['call_refs'] == final_receipt(journal, 'diagnosis')['call_refs']
    # Stage attribution never adds or changes facts in the canonical ledger.
    sealed = journal.recorder._read(journal.recorder.seal_ledger(segment_index=journal.segment_index).artifact_ref.relative_path)
    assert sealed == json.loads(json.dumps(budget.calls))
    assert all('stage' not in row and 'product_origin' not in row for row in budget.calls)


@pytest.mark.parametrize('scenario', ['diagnosis_invalid', 'diagnosis_unknown', 'planning_unknown'])
def test_absent_packet_has_no_invented_origin_and_keeps_prior_packet_origin(tmp_path, scenario):
    trace, budget, journal, first = run_flow(tmp_path, scenario, offset=True)
    assert trace.status in {'INVALID_OUTPUT', 'INFRASTRUCTURE_ERROR'}, trace.errors
    for stage, value in [('diagnosis', trace.diagnosis), ('planning', trace.changeset)]:
        receipt = final_receipt(journal, stage)
        if value is None:
            assert receipt['status'] == 'NOT_PRODUCED'
            assert receipt['call_refs'] == [] and receipt['output_refs'] == []
        else:
            assert stage == 'diagnosis' and scenario == 'planning_unknown'
            assert [ref['absolute_call_index'] for ref in receipt['call_refs']] == [first]
            assert linked_call(journal, receipt['call_refs'][0])['dispatch_state'] == 'response_known'
    if scenario.endswith('_unknown'):
        assert budget.calls[-1]['dispatch_state'] == 'response_unknown'
        assert budget.reserved_tokens == sum(recorded_token_charge(row) for row in budget.calls) + 8


def test_identical_repeated_front_output_links_to_its_own_dispatch(tmp_path):
    trace, budget, journal, first = run_flow(tmp_path, 'normal')
    previous = final_receipt(journal, 'diagnosis')['call_refs']
    source = tmp_path / 'source'
    # The persisted front input is authoritative and avoids rebuilding a
    # semantically equivalent input with a different identity.
    diagnosis_receipt = final_receipt(journal, 'diagnosis')
    method_ref = next(ref for ref in diagnosis_receipt['input_refs'] if ref['kind'] == 'METHOD_INPUT')
    value = journal.recorder._read(method_ref['relative_path'])
    config_ref = next(ref for ref in diagnosis_receipt['input_refs'] if ref['kind'] == 'FRONT_CONFIG')
    config = journal.recorder._read(config_ref['relative_path'])
    repeated = run_front_half(value, source, budget, config,
        budget.token_counter, journal=journal, attempt_id='second-front')
    assert repeated.status == 'READY', repeated.errors
    assert repeated.diagnosis == trace.diagnosis and repeated.changeset == trace.changeset
    final = final_receipt(journal, 'diagnosis')['call_refs']
    assert [ref['absolute_call_index'] for ref in final] == [first + 2]
    assert final != previous
    assert linked_call(journal, previous[0])['response_content'] == linked_call(journal, final[0])['response_content']


def test_changed_product_cannot_borrow_a_completed_response_origin(tmp_path):
    trace, _, journal, _ = run_flow(tmp_path, 'normal')
    dispatch = journal.dispatches[0][1]
    call = linked_call(journal, dispatch.call_refs[0])
    response = journal.completed_response(ModelResponse(call['response_content'], call['response_metadata']), dispatch)
    origin = response.produced(trace.diagnosis)
    changed = {**trace.diagnosis, 'status': 'EVIDENCE_GAP'}
    with pytest.raises(ValueError, match='differs from its bound response product'):
        journal.final_front(replace(trace, diagnosis=changed), attempt_id='altered',
                            products={'diagnosis': origin})
    with pytest.raises(ValueError, match='canonical dispatch facts'):
        journal.completed_response(ModelResponse('different response', {}), dispatch)
