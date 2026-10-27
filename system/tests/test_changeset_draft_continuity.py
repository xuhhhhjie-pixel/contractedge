"""Plan corrections must receive the actual draft, never an invented valid plan."""
import json
import copy
import pytest
from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.validation import canonical_digest
from contractedge.orchestration.repair import validate_ready_trace,revise_plan
from contractedge.orchestration.runner import run_front_half
from contractedge.model.provider import ModelResponse
from tests import test_feedback_context as fixtures
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_run_budget import run_fixture


@pytest.fixture
def context():
    value=fixtures.FeedbackContextTests();value.setUp()
    try:yield value
    finally:value.doCleanups()


def attempt(context):
    return dict(run_id=context.trace['run_id'],snapshot_id=context.trace['snapshot_id'],
        input_digest=context.trace['input_digest'],diagnosis_digest=canonical_digest(context.trace['diagnosis']),
        response_content=json.dumps(context.trace['changeset']),state='REJECTED_PLAN_DRAFT')


def packet_context(context):
    from contractedge.contracts.validation import parse_method_input
    return validate_ready_trace(context.trace,parse_method_input(context.method),context.source)


def test_runner_correction_preserves_exact_rejected_draft_before_model_regeneration(tmp_path):
    class Provider(PipelineFixtureProvider):
        draft=None
        def complete_json(self,request):
            body=json.loads(request.messages[1]['content'])
            if request.prompt_version.startswith('changeset.') and body['validation_feedback']:
                assert request.messages[2]['role']=='assistant'
                assert request.messages[2]['content']==self.draft
                instructions=json.loads(request.messages[3]['content'])
                assert instructions['previous_attempt_state']=='REJECTED_PLAN_DRAFT'
                assert instructions['binding']['snapshot_id']==body['run_context']['snapshot_id']
                assert instructions['validation_feedback']==body['validation_feedback']
            response=super().complete_json(request)
            if request.prompt_version.startswith('changeset.') and self.draft is None:
                value=json.loads(response.content);value.pop('status');self.draft=json.dumps(value)
                return ModelResponse(self.draft,{})
            return response
    trace=run_fixture(tmp_path,Provider(),lambda s:len(s.split()))
    assert trace.status=='READY',trace.errors
    assert [c['stage'] for c in trace.model_calls]==['DIAGNOSIS','SEMANTIC_CHANGESET','CHANGESET_REVISION']


class Captured(BaseException):pass


def test_public_feedback_plan_revision_contains_current_plan_without_mutating_trace(context):
    from contractedge.contracts.validation import parse_method_input
    before=copy.deepcopy(context.trace)
    class Provider:
        def complete_json(self,request):
            view=json.loads(request.messages[2]['content'])
            assert view['postconditions']==before['changeset']['postconditions']
            assert view['changes'][0]['target']=={'target_kind':'EXISTING','source_path':before['changeset']['changes'][0]['target']['path'],'unit_refs':before['changeset']['changes'][0]['target']['unit_refs']}
            assert json.loads(request.messages[3]['content'])['previous_attempt_state']=='CURRENT_PLAN_FOR_REVISION'
            raise Captured()
    with pytest.raises(Captured):
        revise_plan(context.trace,parse_method_input(context.method),context.source,context.feedback,Provider(),len,context.config)
    assert context.trace==before


@pytest.mark.parametrize('field',['run_id','snapshot_id','input_digest','diagnosis_digest'])
def test_foreign_plan_draft_cannot_enter_correction(context,field):
    packet,run=packet_context(context);prior=attempt(context);prior[field]='other'
    with pytest.raises(ValueError,match='Previous ChangeSet'):
        build_changeset_request(packet,run,[{'code':'INVALID'}],previous_attempt=prior)


def test_draft_without_correction_reason_is_rejected(context):
    packet,run=packet_context(context)
    with pytest.raises(ValueError,match='Previous ChangeSet'):
        build_changeset_request(packet,run,previous_attempt=attempt(context))


def test_initial_planning_request_has_no_previous_draft(context):
    packet,run=packet_context(context);request=build_changeset_request(packet,run)
    assert len(request.messages)==2


@pytest.mark.parametrize('state',[[],{},None])
def test_malformed_prior_state_is_rejected_without_untyped_failure(context,state):
    packet,run=packet_context(context);prior=attempt(context);prior['state']=state
    with pytest.raises(ValueError,match='Previous ChangeSet'):
        build_changeset_request(packet,run,[{'code':'INVALID'}],previous_attempt=prior)


def test_new_evidence_context_does_not_reuse_old_rejected_plan(tmp_path):
    from tests.test_planning_evidence_extension import PlanningGapProvider
    class Provider(PlanningGapProvider):
        def complete_json(self,request):
            if request.prompt_version.startswith('changeset.') and self.diagnosis_calls==2:
                assert len(request.messages)==2
                assert 'previous_changeset_sha256' not in request.metadata
            return super().complete_json(request)
    trace=run_fixture(tmp_path,Provider(),lambda s:len(s.split()))
    assert trace.status=='READY',trace.errors


def test_current_plan_revision_uses_model_target_dialect_and_preserves_obligations(context):
    from jsonschema import Draft202012Validator
    packet, run = packet_context(context)
    prior = attempt(context); prior['state'] = 'CURRENT_PLAN_FOR_REVISION'
    original = json.loads(prior['response_content'])
    request = build_changeset_request(packet, run, [{'code':'PUBLIC_FAILURE'}], previous_attempt=prior)
    projected = json.loads(request.messages[2]['content'])
    target_schema = request.output_schema['properties']['changes']['items']['properties']['target']
    for change in projected['changes']:
        assert not list(Draft202012Validator(target_schema).iter_errors(change['target']))
    assert projected['postconditions'] == original['postconditions']
    assert projected['preserve_constraints'] == original['preserve_constraints']
    assert projected['open_obligations'] == original['open_obligations']
    assert [{k:v for k,v in c.items() if k!='target'} for c in projected['changes']] == [
        {k:v for k,v in c.items() if k!='target'} for c in original['changes']]
    assert context.trace['changeset'] == original
