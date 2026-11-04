"""Observation-limit prose cannot acquire source-selection or edit authority."""
import json

import pytest

from contractedge.contracts.validation import canonical_digest
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import front_fixture


def source_fixture(root, *, explicit=True):
    value, _ = front_fixture(root)
    (root / 'pkg/runtime').mkdir(parents=True)
    (root / 'pkg/runtime/config.go').write_text(
        'package runtime\nfunc FromConfig() { ignoreUnrelatedState() }\n')
    (root / 'worker.go').write_text('package worker\nfunc RenderAccount() { ServiceAccount() }\n')
    if not explicit:
        value['scope_hints'] = [dict(kind='symbol', value='RenderAccount',
            raw_text='RenderAccount', origin_ref='public.requirement')]
    value['initial_snapshot_id'] = compute_snapshot(root).snapshot_id
    value['public_evidence'][0]['snapshot_id'] = value['initial_snapshot_id']
    value.pop('input_digest')
    value['input_digest'] = canonical_digest(value)
    return value


def config(description, **extra):
    return dict(model={'model': 'fixture'}, max_context_tokens=24000,
        model_context_tokens=24000, max_output_tokens=1024, max_total_tokens=100000,
        max_format_retries=0, top_n=2,
        public_execution_capabilities={
            'task_observation_limit': description,
            'environment_observation_limit': 'Only a public build is available.',
            'editable_policy': {'allowed_paths': ['templates/*'], 'forbidden_paths': []},
        }, **extra)


class CaptureInvalid:
    def __init__(self):
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        return ModelResponse('not-json', {})


def capture(root, value, description, **extra):
    provider = CaptureInvalid()
    trace = run_front_half(value, root, provider, config(description, **extra),
                           lambda text: len(text.split()))
    return trace, provider.requests


@pytest.mark.parametrize('description', [
    'runtime.FromConfig is not executed or claimed.',
    'Build runtime.FromConfig; this is not a behavior observation.',
    'The unrelated runtime.FromConfig stage is observed separately.',
])
def test_observation_prose_cannot_filter_requirement_seeds_or_reserve_source_slots(tmp_path, description):
    value = source_fixture(tmp_path, explicit=False)
    baseline, _ = capture(tmp_path, value, 'Build only.')
    trace, requests = capture(tmp_path, value, description)
    assert baseline.scope_resolution.seed_paths == ('worker.go',)
    assert trace.scope_resolution.seed_paths == baseline.scope_resolution.seed_paths
    assert trace.candidate_set.selected_ids == baseline.candidate_set.selected_ids
    assert trace.candidate_set.required_endpoint_ids == baseline.candidate_set.required_endpoint_ids == ()
    assert not any(u.get('boundary_scope_anchor') or u.get('boundary_scope_critical')
                   for u in trace.candidate_set.candidates)
    payload = json.loads(requests[0].messages[1]['content'])
    assert payload['public_execution_capabilities']['task_observation_limit'] == description
    assert payload['public_execution_capabilities']['environment_observation_limit'] == 'Only a public build is available.'


def test_observation_prose_cannot_widen_the_initial_request_allowance(tmp_path):
    value = source_fixture(tmp_path)
    trace, requests = capture(tmp_path, value, 'runtime.FromConfig is not executed or claimed.',
                              initial_diagnosis_context_tokens=1)
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert requests == []


def test_exact_task_path_remains_available_despite_observation_exclusion(tmp_path):
    value = source_fixture(tmp_path)
    value['scope_hints'] = [dict(kind='path', value='pkg/runtime/config.go',
        raw_text='pkg/runtime/config.go', origin_ref='public.scope')]
    value.pop('input_digest')
    value['input_digest'] = canonical_digest(value)
    trace, requests = capture(tmp_path, value, 'runtime.FromConfig is not executed or claimed.')
    assert trace.scope_resolution.seed_paths == ('pkg/runtime/config.go',)
    assert [u['path'] for u in trace.candidate_set.candidates] == ['pkg/runtime/config.go']
    assert requests


def test_registered_api_entrypoint_still_supplies_bounded_source_focus(tmp_path):
    value = source_fixture(tmp_path, explicit=False)
    settings = config('runtime.FromConfig is not executed or claimed.')
    settings['public_execution_capabilities']['registered_public_observation_adapters'] = [{
        'producer_manifest_sha256': 'a' * 64,
        'api_entrypoints': [{'package': 'pkg/runtime', 'symbol': 'FromConfig'}],
    }]
    provider = CaptureInvalid()
    trace = run_front_half(value, tmp_path, provider, settings, lambda text: len(text.split()))
    assert set(trace.scope_resolution.seed_paths) == {'worker.go', 'pkg/runtime/config.go'}
    assert any(u['path'] == 'pkg/runtime/config.go' for u in trace.candidate_set.candidates)
    assert any(a['origin_ref'].startswith('METHOD_PUBLIC_PRODUCER_API:')
               for a in trace.scope_resolution.search_audit)


def test_observation_prose_does_not_create_planning_scope_units_or_edit_targets(tmp_path):
    value = source_fixture(tmp_path)

    class CapturedPlan(BaseException):
        pass

    class Provider(PipelineFixtureProvider):
        planning = None

        def complete_json(self, request):
            if request.prompt_version.startswith('changeset.'):
                self.planning = json.loads(request.messages[1]['content'])
                raise CapturedPlan()
            return super().complete_json(request)

    provider = Provider()
    with pytest.raises(CapturedPlan):
        run_front_half(value, tmp_path, provider,
            config('runtime.FromConfig is not executed or claimed.'), lambda text: len(text.split()))
    payload = provider.planning
    assert {u['id'] for u in payload['diagnosis_packet']['units']} == {'U1', 'U2'}
    assert [target['path'] for target in payload['existing_target_paths']] == ['templates/serviceaccount.yaml']
    assert not any(e['id'].startswith('SRC-SCOPE-') for e in payload['diagnosis_packet']['evidence_refs'])
    assert payload['public_execution_capabilities']['editable_policy'] == {
        'allowed_paths': ['templates/*'], 'forbidden_paths': []}
