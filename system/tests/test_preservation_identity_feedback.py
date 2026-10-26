"""Wrong preservation IDs must be rejected with an actionable correction."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from contractedge.changeset.prompt import build_changeset_request
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.validation import parse_method_input, validate_diagnosis
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan


def diagnosis():
    return validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)


def impact(ref):
    return dict(preserve_id=ref, scope='CHANGES', change_refs=['C1'], rationale='Shared production path.')


@pytest.mark.parametrize('expected,supplied,missing,unexpected,duplicate', [
    ([], ['O1'], [], ['O1'], []),
    (['KEEP-OUTPUT'], ['O1'], ['KEEP-OUTPUT'], ['O1'], []),
    (['KEEP-OUTPUT'], ['KEEP-OUTPUT','KEEP-OUTPUT'], [], [], ['KEEP-OUTPUT']),
    (['KEEP-OUTPUT'], [], ['KEEP-OUTPUT'], [], []),
])
def test_identity_failure_feedback_names_the_actual_set_difference(expected,supplied,missing,unexpected,duplicate):
    # A generic error leaves the executor unable to distinguish an extra row
    # from an omitted constraint. Do not repair the model's plan in the engine.
    plan=valid_plan()
    plan['preserve_constraints']=[dict(id=ref,outcome_id='O1',obligation_refs=['A1'],
        statement='Keep the selected object.',current_satisfaction='UNKNOWN',evidence_refs=[]) for ref in expected]
    plan['preservation_impact']=[impact(ref) for ref in supplied]
    before=deepcopy(plan)
    result=validate_semantic_changeset(plan,diagnosis(),None)
    messages=[r.message for r in result.errors if r.code=='PRESERVATION_IMPACT_INCOMPLETE']
    assert len(messages)==1
    message=messages[0]
    for label,values in [('expected',expected),('missing',missing),('unexpected',unexpected),('duplicate',duplicate)]:
        assert label+'='+json.dumps(values,ensure_ascii=False) in message
    assert 'preserve_constraints[].id' in message and 'outcome_id' in message
    assert plan==before


def test_no_allowed_preservations_schema_rejects_outcome_as_an_impact_id():
    packet=diagnosis();packet.raw['requirement_spec']['traceability_required']=True
    context=SimpleNamespace(run_id='run-1',case_id='demo',snapshot_id='snap-0',
        input_digest=packet.input_digest,model_config={'model':'fixture'},budget={'max_output_tokens':1000})
    request=build_changeset_request(packet,context)
    schema=request.output_schema['properties']['preservation_impact']
    assert list(Draft202012Validator(schema).iter_errors([impact('O1')])), 'Empty preservation set must forbid an invented impact'
    assert not list(Draft202012Validator(schema).iter_errors([]))
    assert request.output_schema['properties']['preserve_constraints']['maxItems']==0


def test_real_preservation_remains_available_for_mixed_repaired_and_untouched_atoms():
    packet=diagnosis();packet.raw['requirement_spec']['traceability_required']=True
    packet.raw['obligation_atoms'].append(dict(id='A2',outcome_id='O1',statement='Keep the disabled branch.',source_refs=['requirement_text:0:49'],support='EXPLICIT'))
    context=SimpleNamespace(run_id='run-1',case_id='demo',snapshot_id='snap-0',
        input_digest=packet.input_digest,model_config={'model':'fixture'},budget={'max_output_tokens':1000})
    request=build_changeset_request(packet,context)
    schema=request.output_schema['properties']['preservation_impact']
    assert not list(Draft202012Validator(schema).iter_errors([impact('KEEP-DISABLED')]))
    assert request.output_schema['properties']['preserve_constraints'].get('maxItems',32)>0


def test_current_valid_impact_reaches_validation_unchanged():
    plan=valid_plan();plan['preserve_constraints']=[dict(id='KEEP',outcome_id='O1',obligation_refs=['A1'],
        statement='Keep existing object.',current_satisfaction='UNKNOWN',evidence_refs=[])]
    plan['preservation_impact']=[impact('KEEP')]
    result=validate_semantic_changeset(plan,diagnosis(),None)
    assert not any(r.code.startswith('PRESERVATION_IMPACT') for r in result.errors)


def test_single_existing_correction_receives_id_differences_and_exact_prior_draft(tmp_path):
    from contractedge.model.provider import ModelResponse
    from dataclasses import replace
    from tests.test_orchestration import PipelineFixtureProvider
    from tests.test_run_budget import run_fixture

    class Provider(PipelineFixtureProvider):
        rejected=None
        corrections=0
        def complete_json(self,request):
            body=json.loads(request.messages[1]['content'])
            response=super().complete_json(replace(request,prompt_version='diagnosis.v1.1.24')
                if request.prompt_version.startswith('diagnosis.') else request)
            if request.prompt_version.startswith('diagnosis.'):
                payload=json.loads(response.content)
                payload['obligation_atoms'][0]['source_quote']=body['requirement_spec']['raw_text']
                return ModelResponse(json.dumps(payload),{})
            if body['validation_feedback']:
                self.corrections+=1
                assert request.messages[2]['content']==self.rejected
                issue=next(e for e in body['validation_feedback'] if e['code']=='PRESERVATION_IMPACT_INCOMPLETE')
                assert 'expected=[]' in issue['message'] and 'unexpected=["O1"]' in issue['message']
                fixed=json.loads(response.content);fixed['preservation_impact']=[]
                return ModelResponse(json.dumps(fixed),{})
            draft=json.loads(response.content)
            assert draft['preserve_constraints']==[]
            draft['preservation_impact']=[impact('O1')]
            self.rejected=json.dumps(draft)
            return ModelResponse(self.rejected,{})
    provider=Provider()
    trace=run_fixture(tmp_path,provider,lambda text:len(text.split()))
    assert trace.status=='READY',trace.errors
    assert provider.corrections==1
    assert len(trace.model_calls)==3
