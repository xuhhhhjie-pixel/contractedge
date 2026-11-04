"""Synthetic status/routing counterexamples; these are not real model outcomes."""
from copy import deepcopy
import hashlib
import json

import pytest

from contractedge.contracts.models import ContractError
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_diagnosis
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import _format_validation_feedback, run_front_half
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_diagnosis, valid_method_input
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import front_fixture
from tests.test_run_budget import run_fixture


@pytest.mark.parametrize('supported', [False, True])
def test_model_gap_status_requires_an_explicit_gap_without_mutating_the_packet(supported):
    body = valid_diagnosis()
    body.update(status='EVIDENCE_GAP', unverified_outcomes=['O1'])
    if not supported:
        body['violations'] = []
        body['outcome_coverage'][0].update(repair_supported=[], unresolved=['O1'])
    before = deepcopy(body)
    with pytest.raises(ContractError) as caught:
        validate_diagnosis(body, parse_method_input(valid_method_input()), None)
    assert caught.value.code == 'STATUS_INCONSISTENT'
    assert caught.value.path == 'evidence_gaps'
    assert body == before


def test_existing_correction_feedback_delivers_the_actual_empty_gap_error():
    body = valid_diagnosis()
    body.update(status='EVIDENCE_GAP', unverified_outcomes=['O1'])
    with pytest.raises(ContractError) as caught:
        validate_diagnosis(body, parse_method_input(valid_method_input()), None)
    feedback = _format_validation_feedback(caught.value, body)
    assert 'Original validation: ' + str(caught.value) in feedback
    assert 'at least one concrete blocking evidence_gaps entry with a locator_hint' in feedback
    assert 'unverified future outcomes alone' in feedback
    assert 'Preserve supported local violations and genuine gaps' in feedback
    assert 'READY must satisfy its full validation' in feedback


class StatusSequenceProvider(PipelineFixtureProvider):
    """Scripted public fixture: choose status explicitly, without a remote model."""

    def __init__(self, statuses):
        super().__init__()
        self.statuses = statuses
        self.requests = []
        self.responses = []
        self.diagnosis_count = 0

    def complete_json(self, request):
        self.requests.append(request)
        response = super().complete_json(request)
        if not request.prompt_version.startswith('diagnosis.'):
            return response
        mode = self.statuses[self.diagnosis_count]
        self.diagnosis_count += 1
        body = json.loads(response.content)
        body['unverified_outcomes'] = ['O1']
        if mode not in {'ready', 'unsupported_ready', 'invalid_support'}:
            body['status'] = 'EVIDENCE_GAP'
        if mode == 'concrete_gap':
            body['evidence_gaps'] = [dict(id='G1', summary='Locate the optional account template.',
                locator_hint={'path': 'templates/optional-serviceaccount.yaml', 'search_term': 'ServiceAccount'})]
        if mode == 'invalid_support':
            body['violations'][0]['support_basis'] = 'OBSERVED_ONLY'
        if mode == 'unsupported_ready':
            body['units'].append(dict(id='U3', role='unlocated output', kind='output',
                semantic_role_class='GENERATED_STATE', existence='UNKNOWN', locator_refs=[],
                requirement_refs=['O1']))
            body['expected_relations'].append(dict(body['expected_relations'][0], id='E2',
                source_unit_ids=['U2'], target_unit_ids=['U3'], support_refs=[]))
        self.responses.append(deepcopy(body))
        return ModelResponse(json.dumps(body), {'provider': 'synthetic-status-sequence'})


def test_one_failed_correction_stops_as_invalid_output_and_does_not_plan(tmp_path):
    provider = StatusSequenceProvider(['empty_gap', 'empty_gap'])
    trace = run_fixture(tmp_path, provider, lambda text: len(text.split()), max_format_retries=1)
    assert trace.status == 'INVALID_OUTPUT', trace.errors
    assert [call['stage'] for call in trace.model_calls] == ['DIAGNOSIS', 'DIAGNOSIS_FORMAT_RETRY']
    assert trace.diagnosis is None and trace.changeset is None
    assert trace.structural_validation == 'FAIL'
    assert any('STATUS_INCONSISTENT at evidence_gaps' in error for error in trace.errors)
    assert all(body['status'] == 'EVIDENCE_GAP' and not body['evidence_gaps'] for body in provider.responses)
    retry = provider.requests[1]
    assert json.loads(retry.messages[-2]['content']) == provider.responses[0]
    assert 'Original validation: STATUS_INCONSISTENT at evidence_gaps' in retry.messages[-1]['content']


@pytest.mark.parametrize('config,expected', [
    ({'max_format_retries': 0}, 'INVALID_OUTPUT'),
    ({'max_format_retries': 1, 'max_model_calls': 1}, 'BUDGET_EXHAUSTED'),
])
def test_new_validation_uses_existing_correction_and_call_limits(tmp_path, config, expected):
    provider = StatusSequenceProvider(['empty_gap'])
    trace = run_fixture(tmp_path, provider, lambda text: len(text.split()), **config)
    assert trace.status == expected, trace.errors
    assert [call['stage'] for call in trace.model_calls] == ['DIAGNOSIS']
    assert trace.diagnosis is None and trace.changeset is None


def test_model_can_correct_to_ready_while_future_outcome_stays_unverified(tmp_path):
    provider = StatusSequenceProvider(['empty_gap', 'ready'])
    trace = run_fixture(tmp_path, provider, lambda text: len(text.split()), max_format_retries=1)
    assert trace.status == 'READY', trace.errors
    assert [call['stage'] for call in trace.model_calls] == [
        'DIAGNOSIS', 'DIAGNOSIS_FORMAT_RETRY', 'SEMANTIC_CHANGESET']
    assert provider.responses[0]['status'] == 'EVIDENCE_GAP'
    assert provider.responses[1]['status'] == 'READY'
    assert trace.diagnosis['status'] == 'READY'
    assert trace.diagnosis['unverified_outcomes'] == ['O1']
    assert trace.diagnosis['violations'][0]['causal_status'] == 'SUPPORTED'


def test_correction_cannot_escape_full_ready_validation_by_only_changing_status(tmp_path):
    provider = StatusSequenceProvider(['empty_gap', 'unsupported_ready'])
    trace = run_fixture(tmp_path, provider, lambda text: len(text.split()), max_format_retries=1)
    assert trace.status == 'INVALID_OUTPUT', trace.errors
    assert [call['stage'] for call in trace.model_calls] == ['DIAGNOSIS', 'DIAGNOSIS_FORMAT_RETRY']
    assert any('PRODUCTION_PATH_INCOMPLETE' in error for error in trace.errors)
    assert trace.diagnosis is None and trace.changeset is None
    assert provider.responses[1]['status'] == 'READY'


def test_empty_gap_after_another_error_does_not_reset_the_existing_correction_allowance(tmp_path):
    provider = StatusSequenceProvider(['invalid_support', 'empty_gap'])
    trace = run_fixture(tmp_path, provider, lambda text: len(text.split()), max_format_retries=1)
    assert trace.status == 'INVALID_OUTPUT', trace.errors
    assert [call['stage'] for call in trace.model_calls] == ['DIAGNOSIS', 'DIAGNOSIS_FORMAT_RETRY']
    assert 'CAUSAL_CLAIM_UNSUPPORTED' in trace.model_calls[0]['validation_error']
    assert any('STATUS_INCONSISTENT at evidence_gaps' in error for error in trace.errors)
    assert trace.diagnosis is None and trace.changeset is None


@pytest.mark.parametrize('extension_status', ['ready', 'concrete_gap'])
def test_correction_to_real_gap_uses_the_existing_single_extension(tmp_path, extension_status):
    method, source = front_fixture(tmp_path)
    optional = tmp_path / 'templates/optional-serviceaccount.yaml'
    optional.write_text('apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: optional\n')
    method['initial_snapshot_id'] = compute_snapshot(tmp_path).snapshot_id
    method['public_evidence'][0]['snapshot_id'] = method['initial_snapshot_id']
    method.pop('input_digest')
    method['input_digest'] = canonical_digest(method)
    provider = StatusSequenceProvider(['empty_gap', 'concrete_gap', extension_status])
    provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(source.read_bytes()).hexdigest()}
    trace = run_front_half(method, tmp_path, provider,
        dict(model={'model': 'fixture'}, top_n=12, max_context_tokens=40000,
             max_output_tokens=1024, max_total_tokens=160000, max_format_retries=1),
        lambda text: len(text.split()))
    expected_stages = ['DIAGNOSIS', 'DIAGNOSIS_FORMAT_RETRY', 'DIAGNOSIS_EXTENSION']
    if extension_status == 'ready':
        expected_stages.append('SEMANTIC_CHANGESET')
    assert [call['stage'] for call in trace.model_calls] == expected_stages
    assert trace.status == ('READY' if extension_status == 'ready' else 'EVIDENCE_GAP'), trace.errors
    assert sum(bool(row.get('extension_used')) for row in trace.retrieval_rounds) == 1
    extension_body = json.loads(provider.requests[2].messages[1]['content'])
    assert any(record['kind'] == 'SOURCE_SPAN'
               and record['locator']['path'] == 'templates/optional-serviceaccount.yaml'
               for record in extension_body['evidence_catalog'])
    assert trace.diagnosis['violations'][0]['causal_status'] == 'SUPPORTED'
    assert trace.diagnosis['unverified_outcomes'] == ['O1']
    if extension_status == 'concrete_gap':
        assert trace.diagnosis['evidence_gaps'][0]['id'] == 'G1'
        assert trace.changeset is None


def test_zero_candidates_remains_an_engineering_gap_without_a_model_diagnosis(tmp_path):
    class NoModel:
        def complete_json(self, request):
            pytest.fail('No candidate should invoke a model')

    method = valid_method_input()
    method['initial_snapshot_id'] = compute_snapshot(tmp_path).snapshot_id
    method.pop('input_digest')
    method['input_digest'] = canonical_digest(method)
    trace = run_front_half(method, tmp_path, NoModel(),
        dict(model={'model': 'fixture'}, max_context_tokens=12000, max_output_tokens=1024),
        lambda text: len(text.split()))
    assert trace.status == 'EVIDENCE_GAP', trace.errors
    assert trace.diagnosis is None and trace.changeset is None
    assert not trace.model_calls
    assert trace.errors == ('no source candidates resolved',)
