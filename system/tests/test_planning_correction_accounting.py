"""Evidence acquisition must not spend the one contract-correction allowance."""
import hashlib
import json
from unittest.mock import patch

import pytest

from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import front_fixture


def needs_evidence(body):
    body.update(status='NEEDS_EVIDENCE',planned_violation_ids=[],changes=[],postconditions=[],
        open_obligations=[dict(id='G1',outcome_id='O1',obligation_refs=['A1'],
            statement='Render the selected ServiceAccount',reason='Check optional implementation')],
        outcome_coverage=[dict(outcome_id='O1',repair_supported=[],preserve_required=[],unresolved=['O1'])],
        evidence_gaps=[dict(id='G1',summary='Check optional template inventory',
            locator_hint={'path':'templates/optional-serviceaccount.yaml'})])


class SequenceProvider(PipelineFixtureProvider):
    def __init__(self,steps):
        super().__init__();self.steps=steps;self.plan_calls=0;self.diagnosis_calls=0;self.requests=[]
    def complete_json(self,request):
        response=super().complete_json(request)
        if request.prompt_version.startswith('diagnosis.'):
            self.diagnosis_calls+=1;return response
        self.plan_calls+=1;self.requests.append(json.loads(request.messages[1]['content']))
        assert self.plan_calls<=len(self.steps),'Unexpected extra correction request'
        mode=self.steps[self.plan_calls-1];body=json.loads(response.content)
        if mode=='parse':return ModelResponse('{',{})
        if mode in {'gap','invalid_gap'}:needs_evidence(body)
        if mode in {'invalid','invalid_gap'}:body['outcome_coverage'][0]['preserve_required']=['O1']
        return ModelResponse(json.dumps(body),{})


def run(root,steps,*,allowance=1,calls=12):
    value,source=front_fixture(root);provider=SequenceProvider(steps)
    provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(source.read_bytes()).hexdigest()}
    cfg=dict(model={'model':'fixture'},max_context_tokens=24000,max_output_tokens=1024,
        max_total_tokens=100000,max_format_retries=allowance)
    shared=BudgetedProvider(provider,lambda s:len(s.split()),max_tokens=100000,max_seconds=60,max_calls=calls)
    trace=run_front_half(value,root,shared,cfg,lambda s:len(s.split()))
    assert source.read_text()=='apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n'
    return trace,provider,shared


@pytest.mark.parametrize('invalid',['invalid','parse'])
def test_invalid_first_plan_after_extension_receives_one_existing_correction(tmp_path,invalid):
    trace,provider,_=run(tmp_path,['gap',invalid,'valid'])
    assert trace.status=='READY',trace.errors
    assert provider.diagnosis_calls==2 and provider.plan_calls==3
    assert provider.requests[2]['validation_feedback']
    if invalid=='invalid':assert any(f['code']=='PRESERVE_CONSTRAINT_MISSING' for f in provider.requests[2]['validation_feedback'])
    assert trace.candidate_set.extension_used


@pytest.mark.parametrize('allowance,expected',[(0,2),(1,3)])
@pytest.mark.parametrize('invalid',['invalid','parse'])
def test_repeated_invalid_plans_stop_at_configured_correction_count(tmp_path,allowance,expected,invalid):
    trace,provider,_=run(tmp_path,['gap',invalid,invalid],allowance=allowance)
    assert trace.status==('INVALID_OUTPUT' if invalid=='parse' else 'INVALID_PLAN'),trace.errors
    assert provider.plan_calls==expected


@pytest.mark.parametrize('invalid',['invalid','parse'])
def test_correction_before_extension_is_not_reset_after_rediagnosis(tmp_path,invalid):
    trace,provider,_=run(tmp_path,[invalid,'gap','invalid','valid'])
    assert trace.status=='INVALID_PLAN'
    assert provider.plan_calls==3 and provider.diagnosis_calls==2
    assert provider.requests[1]['validation_feedback']
    assert not provider.requests[2]['validation_feedback']


def test_second_evidence_request_does_not_grant_a_second_expansion(tmp_path):
    trace,provider,_=run(tmp_path,['gap','gap','valid'])
    assert trace.status=='NEEDS_EVIDENCE'
    assert provider.plan_calls==2 and provider.diagnosis_calls==2
    assert sum(r.get('trigger')=='CHANGESET_NEEDS_EVIDENCE' for r in trace.retrieval_rounds)==1


@pytest.mark.parametrize('failure',['no_evidence','selection_error'])
def test_failed_expansion_cannot_loop_without_spending_a_correction(tmp_path,failure):
    if failure=='no_evidence':
        with patch('contractedge.orchestration.runner.extract_gap_extension_units',return_value=([],{})) as search, \
                patch('contractedge.orchestration.runner.collect_absence_search_evidence',return_value=([],{})):
            trace,provider,_=run(tmp_path,['invalid_gap','invalid_gap','valid'])
        assert search.call_count==1
    else:
        with patch('contractedge.orchestration.runner.extend_once',side_effect=ValueError('capacity')) as extend:
            trace,provider,_=run(tmp_path,['invalid_gap','invalid_gap','valid'])
        assert extend.call_count==1
    assert trace.status=='INVALID_PLAN'
    assert provider.plan_calls==2


def test_extra_planning_correction_still_cannot_bypass_shared_call_cap(tmp_path):
    trace,provider,budget=run(tmp_path,['gap','invalid','valid'],calls=4)
    assert trace.status=='BUDGET_EXHAUSTED'
    assert len(budget.calls)==4
    assert provider.plan_calls==2
