"""Public fixture regressions for source-consumer operation capabilities."""
import hashlib
import json
from pathlib import Path

import pytest

from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.execution.topomen import PlanRejected, accept_plan
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import front_fixture
from tests.test_repair_execution import bound_plan


class OperationProvider(PipelineFixtureProvider):
    def __init__(self, remove_count=0):
        super().__init__()
        self.remove_count = remove_count
        self.requests = []

    def complete_json(self, request):
        response = super().complete_json(request)
        if request.prompt_version.startswith('diagnosis.'):
            return response
        self.requests.append(request)
        if len(self.requests) <= self.remove_count:
            body = json.loads(response.content)
            body['changes'][0]['operation'] = 'REMOVE'
            return ModelResponse(json.dumps(body), response.metadata)
        return response


def run_fixture(root, *, remove_count=0, **config):
    value, path = front_fixture(root)
    original = path.read_bytes()
    provider = OperationProvider(remove_count)
    provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(original).hexdigest()}
    settings = {'model': {'model': 'fixture'}, 'max_context_tokens': 24000,
                'max_output_tokens': 1024, 'max_total_tokens': 100000,
                'max_format_retries': 1, **config}
    provider.method_input = value
    trace = run_front_half(value, root, provider, settings, lambda text: len(text.split()))
    assert path.read_bytes() == original
    return trace, provider


@pytest.mark.parametrize('schema_in_prompt', [True, False])
def test_request_advertises_actual_source_operations(tmp_path, schema_in_prompt):
    trace, provider = run_fixture(tmp_path, model={'model': 'fixture', 'schema_in_prompt': schema_in_prompt})
    assert trace.status == 'READY'
    request = provider.requests[0]
    assert request.output_schema['properties']['changes']['items']['properties']['operation']['enum'] == ['MODIFY', 'CREATE']
    wire = json.loads(json.dumps(list(request.messages)))
    payload = json.loads(wire[1]['content'])
    assert payload['public_execution_capabilities']['supported_operations'] == ['MODIFY', 'CREATE']
    assert 'MODIFY, CREATE' in wire[0]['content']
    if schema_in_prompt:
        assert payload['output_schema'] == request.output_schema
    assert request.metadata['execution_capabilities_digest']


def test_remove_rejected_before_ready_with_original_retry_limit(tmp_path):
    trace, provider = run_fixture(tmp_path, remove_count=10)
    assert trace.status == 'INVALID_PLAN'
    assert len(provider.requests) == 2
    feedback = json.loads(provider.requests[1].messages[1]['content'])['validation_feedback']
    assert any(row['code'] == 'UNSUPPORTED_EXECUTION_OPERATION' and row['path'] == 'changes[0].operation' for row in feedback)
    assert len(trace.model_calls) == 3


def test_correction_uses_existing_allowance_and_preserves_legal_modify(tmp_path):
    trace, provider = run_fixture(tmp_path, remove_count=1)
    assert trace.status == 'READY'
    assert len(provider.requests) == 2
    assert trace.changeset['changes'][0]['operation'] == 'MODIFY'
    assert len(trace.model_calls) == 3
    feedback = json.loads(provider.requests[1].messages[1]['content'])['validation_feedback']
    assert any(row['code'] == 'UNSUPPORTED_EXECUTION_OPERATION' for row in feedback)


def test_unsupported_operation_does_not_replenish_call_budget(tmp_path):
    trace, provider = run_fixture(tmp_path, remove_count=10, max_model_calls=2)
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) == 1
    assert len(trace.model_calls) == 2


def test_configured_narrowing_applies_to_request_and_validation(tmp_path):
    trace, provider = run_fixture(tmp_path, public_execution_capabilities={'supported_operations': ['CREATE']})
    assert trace.status == 'INVALID_PLAN'
    assert provider.requests[0].output_schema['properties']['changes']['items']['properties']['operation']['enum'] == ['CREATE']


@pytest.mark.parametrize('capabilities', [
    {'supported_operations': ['REMOVE']}, {'supported_operations': []},
    {'supported_operations': 'MODIFY'}, {'supported_operations': ['MODIFY', 'MODIFY']},
    {'consumer_id': 'unknown-consumer'}, {'execution_backend': 'unknown-backend'},
    {'execution_backend': ['direct']},
])
def test_invalid_or_expanded_capabilities_fail_explicitly(capabilities):
    from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
    with pytest.raises(ValueError):
        resolve_execution_capabilities(capabilities)


def test_unknown_backend_fails_before_any_model_request(tmp_path):
    trace, provider = run_fixture(tmp_path, execution_backend='unknown-backend')
    assert trace.status == 'INFRASTRUCTURE_ERROR'
    assert not trace.model_calls
    assert not provider.requests
    assert any('Unknown execution backend' in error for error in trace.errors)


def test_consumer_keeps_final_rejection_and_honors_narrowing(tmp_path):
    (tmp_path/'templates').mkdir()
    path = tmp_path/'templates/serviceaccount.yaml'
    path.write_text('name: default\n')
    plan = bound_plan(tmp_path)
    plan['operations'][0]['operation'] = 'REMOVE'
    with pytest.raises(PlanRejected, match='whole-file deletion'):
        accept_plan(plan, tmp_path, {'allowed_paths': ['**']})
    plan['operations'][0]['operation'] = 'MODIFY'
    plan['public_execution_capabilities'] = {'supported_operations': ['CREATE']}
    with pytest.raises(PlanRejected, match='unsupported operation'):
        accept_plan(plan, tmp_path, {'allowed_paths': ['**']})
    assert path.read_text() == 'name: default\n'


def test_general_protocol_still_expresses_remove():
    import contractedge
    schema = json.loads((Path(contractedge.__file__).parent/'contracts/schema/semantic_changeset.v1_1.json').read_text())
    assert 'REMOVE' in schema['$defs']['change']['properties']['operation']['enum']


def test_ready_trace_cannot_resume_under_different_operation_limits(tmp_path):
    from contractedge.contracts.validation import parse_method_input
    from contractedge.orchestration.repair import validate_ready_trace
    trace, provider = run_fixture(tmp_path)
    with pytest.raises(ValueError, match='execution capabilities differ'):
        validate_ready_trace(trace.to_wire(), parse_method_input(provider.method_input), tmp_path,
                             capabilities={'supported_operations': ['CREATE']})


def test_repair_case_retains_configured_narrowing_after_public_projection(tmp_path):
    from contractedge.orchestration.repair import repair_case
    from tests.test_benchmark_current import make_public_fixture
    from tests.test_repair_execution import PublicLoopRunner, TwoStepExecutor
    public, _ = make_public_fixture(tmp_path/'benchmark')
    active = tmp_path/'run/workspace/source/repo/templates/serviceaccount.yaml'

    class CurrentProvider(OperationProvider):
        def complete_json(self, request):
            self._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(active.read_bytes()).hexdigest()}
            return super().complete_json(request)

    provider, executor = CurrentProvider(), TwoStepExecutor()
    repair_case(public, tmp_path, 'CASE-1', tmp_path/'run', provider,
                lambda text: len(text.split()),
                {'model': 'fixture', 'max_context_tokens': 24000, 'max_output_tokens': 1024,
                 'max_total_tokens': 100000, 'max_rounds': 1,
                 'public_execution_capabilities': {'supported_operations': ['CREATE']}},
                public_runner=PublicLoopRunner(public), executor=executor)
    assert provider.requests
    assert all(request.output_schema['properties']['changes']['items']['properties']['operation']['enum'] == ['CREATE']
               for request in provider.requests)
    assert executor.calls == 0
