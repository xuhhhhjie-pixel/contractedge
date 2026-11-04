import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from contractedge.contracts.validation import _load_schema
from contractedge.orchestration.transitions import behavior_plan_reassessment_required, resolve_repair_route
from tests.test_behavior_plan_reassessment import values


@pytest.mark.parametrize('name', ['ce.diagnosis_packet.v1_1', 'ce.semantic_changeset.v1_1'])
@pytest.mark.parametrize('locator', [{}, {'search_term': ''}, {'path': '  ', 'symbol': '\t'}])
def test_gap_requires_an_actionable_locator(name, locator):
    schema = _load_schema(name)
    item = (schema['$defs']['evidenceGap'] if 'diagnosis' in name
            else schema['properties']['evidence_gaps']['items'])
    assert not Draft202012Validator(item).is_valid(dict(id='G1', summary='Find source', locator_hint=locator))
    assert Draft202012Validator(item).is_valid(dict(id='G1', summary='Find source',
                                                  locator_hint={'path': 'a.go', 'symbol': ''}))


def test_unrelated_prerequisite_does_not_reset_partial_progress():
    plan, feedback = values()
    passed = deepcopy(plan['postconditions'][0])
    passed.update(id='P_OK', behavior_check_refs=['generic:b'])
    plan['postconditions'].append(passed)
    plan['precondition_review'][0]['verification_refs'] = ['P_OK']
    feedback['route'] = 'EXECUTE'
    assert not behavior_plan_reassessment_required(plan, 'S1', feedback)
    resolved = resolve_repair_route({'changeset': plan}, feedback, current_snapshot='S1')
    assert resolved['route'] == 'EXECUTE'
    assert resolved['feedback'] == feedback
    plan['precondition_review'][0]['verification_refs'] = [plan['postconditions'][0]['id']]
    assert resolve_repair_route({'changeset': plan}, feedback, current_snapshot='S1')['route'] == 'DIAGNOSIS'


@pytest.mark.parametrize('hint', ['', ' \t '])
def test_empty_gap_corrects_without_spending_acquisition(tmp_path, hint):
    from contractedge.orchestration.runner import run_front_half
    from contractedge.model.provider import ModelResponse
    from tests.test_orchestration import PipelineFixtureProvider
    from tests.test_planning_evidence_extension import front_fixture
    method, _ = front_fixture(tmp_path)
    class Empty(PipelineFixtureProvider):
        def complete_json(self, request):
            reply = json.loads(super().complete_json(request).content)
            reply.update(status='EVIDENCE_GAP', evidence_gaps=[dict(id='G1', summary='Find source',
                locator_hint={'search_term': hint})])
            return ModelResponse(json.dumps(reply), {})
    trace = run_front_half(method, tmp_path, Empty(), dict(model='fixture', max_context_tokens=24000,
        max_total_tokens=100000, max_output_tokens=1024, max_format_retries=1,
        max_evidence_extensions=2, gap_role_delivery_enabled=True), lambda t: len(t.split()))
    assert trace.status == 'INVALID_OUTPUT'
    assert len(trace.model_calls) == 2
    assert len(trace.retrieval_rounds) == 1
    assert all(not row['extension_used'] for row in trace.retrieval_rounds)
