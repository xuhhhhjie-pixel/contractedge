"""A completed observed recovery may stay active across schemas, never across runs."""
from pathlib import Path
import pytest
from contractedge.model.provider import ModelResponse
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_truncation_recovery import TruncatedPlanningProvider
from tests.test_run_budget import run_fixture


class TwoStageStarvation(TruncatedPlanningProvider):
    def complete_json(self, request):
        if request.prompt_version.startswith('changeset.') and request.thinking != 'disabled':
            self.requests.append(request)
            return ModelResponse('{', dict(finish_reason='length', usage=dict(completion_tokens=1024,
                completion_tokens_details=dict(reasoning_tokens=900))))
        return super().complete_json(request)


def run(root, provider, **kwargs):
    return run_fixture(root, provider, lambda text: len(text.split()),
        planning_truncation_retry='disable_thinking', planning_truncation_separate_allowance=True, **kwargs)


def test_same_front_plan_uses_completed_recovery_without_a_second_retry(tmp_path):
    provider = TwoStageStarvation()
    trace = run(tmp_path, provider, planning_generation_recovery_scope='front_half')
    assert trace.status == 'READY', trace.errors
    assert len(provider.requests) == 3
    assert provider.requests[-1].thinking == 'disabled'
    policy = provider.requests[-1].metadata['planning_generation_policy']
    assert policy['scope'] == 'CURRENT_FRONT_HALF'
    assert policy['origin_stage'] == 'DIAGNOSIS'
    assert policy['origin_prompt_version'].startswith('diagnosis.')
    assert sum(call['stage'] == 'PLANNING_TRUNCATION_FORMAT_RETRY' for call in trace.model_calls) == 1


@pytest.mark.parametrize('scope', [None, 'prompt_version'])
def test_default_keeps_legacy_schema_boundary(tmp_path, scope):
    provider = TwoStageStarvation()
    config = {} if scope is None else dict(planning_generation_recovery_scope=scope)
    trace = run(tmp_path, provider, **config)
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) == 3
    assert provider.requests[-1].thinking is None


@pytest.mark.parametrize('value', ['all_cases', '', None, [], True])
def test_invalid_scope_rejected_before_model_dispatch(tmp_path, value):
    provider = TwoStageStarvation()
    with pytest.raises(ValueError, match='planning_generation_recovery_scope'):
        run(tmp_path, provider, planning_generation_recovery_scope=value)
    assert provider.requests == []


def test_no_completed_recovery_means_no_mode_change(tmp_path):
    provider = TruncatedPlanningProvider(repeats=0)
    trace = run(tmp_path, provider, planning_generation_recovery_scope='front_half')
    assert trace.status == 'READY'
    assert all(request.thinking is None for request in provider.requests)


@pytest.mark.parametrize('provider', [TruncatedPlanningProvider(repeats=2), TruncatedPlanningProvider(reasoning=False)])
def test_failed_or_non_reasoning_recovery_cannot_continue_or_add_calls(tmp_path, provider):
    trace = run(tmp_path, provider, planning_generation_recovery_scope='front_half')
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) <= 2


def test_global_call_limit_still_stops_later_plan(tmp_path):
    provider = TwoStageStarvation()
    trace = run(tmp_path, provider, planning_generation_recovery_scope='front_half', max_model_calls=2)
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) == 2


def test_new_invocation_resets_recovery_even_with_same_provider(tmp_path):
    provider = TwoStageStarvation()
    first = tmp_path / 'first'; first.mkdir()
    assert run(first, provider, planning_generation_recovery_scope='front_half').status == 'READY'
    second = tmp_path / 'second'; second.mkdir()
    before = len(provider.requests)
    assert run(second, provider, planning_generation_recovery_scope='front_half').status == 'READY'
    assert provider.requests[before].thinking is None
    # No new diagnosis starvation occurred on this invocation; its changeset
    # starts normally and consumes only its own existing recovery when truncated.
    assert provider.requests[before + 1].thinking is None
    assert provider.requests[before + 2].thinking == 'disabled'


def test_missing_usage_does_not_infer_reasoning_starvation(tmp_path):
    class Provider(TruncatedPlanningProvider):
        def complete_json(self, request):
            self.requests.append(request)
            return ModelResponse('{', {'finish_reason': 'length', 'usage': {}})
    provider = Provider()
    trace = run(tmp_path, provider, planning_generation_recovery_scope='front_half')
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) == 1
    assert provider.requests[0].thinking is None


def test_evidence_rediagnosis_and_plan_revision_keep_same_front_mode(tmp_path):
    from tests.test_planning_evidence_extension import PlanningGapProvider
    class Provider(PlanningGapProvider):
        requests = []
        def complete_json(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ModelResponse('{', dict(finish_reason='length', usage=dict(completion_tokens=1024,
                    completion_tokens_details=dict(reasoning_tokens=900))))
            return super().complete_json(request)
    provider = Provider()
    trace = run(tmp_path, provider, planning_generation_recovery_scope='front_half', max_model_calls=5)
    assert trace.status == 'READY', trace.errors
    assert [call['stage'] for call in trace.model_calls] == [
        'DIAGNOSIS', 'PLANNING_TRUNCATION_FORMAT_RETRY', 'SEMANTIC_CHANGESET',
        'DIAGNOSIS_EXTENSION', 'CHANGESET_REVISION']
    assert len(provider.requests) == 5
    assert all(request.thinking == 'disabled' for request in provider.requests[1:])
