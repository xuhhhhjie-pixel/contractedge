import copy
import json
import tempfile
from pathlib import Path

from contractedge.orchestration.runner import diagnosis_continuity
from contractedge.model.provider import ModelResponse
from tests.test_planning_evidence_extension import PlanningGapProvider
from tests.test_run_budget import run_fixture


def context():
    record=dict(id='SRC-X',snapshot_id='current',kind='SOURCE_SPAN',content_digest='digest',
                locator=dict(path='main.go',start_line=1,end_line=2),excerpt='documented provider contract')
    prior=dict(snapshot_id='current',input_digest='requirement',evidence_refs=[record],
        expected_relations=[dict(id='R1',condition='on update',expected_contract='external state matches')],
        violations=[dict(id='V1',relation_id='R1',causal_status='SUPPORTED',support_basis='STATIC_INFERENCE',
            evidence_refs=['SRC-X'],broken_behavior='existing path ignores this input')])
    return prior,record


def test_foreign_snapshot_and_requirement_do_not_transfer_prior_claims():
    prior,record=context()
    assert diagnosis_continuity(prior,'other',[record],[],input_digest='requirement') is None
    assert diagnosis_continuity(prior,'current',[record],[],input_digest='other') is None


def test_current_evidence_identity_and_body_must_match_and_missing_is_explicit():
    prior,record=context()
    original=copy.deepcopy(prior)
    for field,bad in [('snapshot_id','other'),('content_digest','forged'),('excerpt','different contract'),('locator',{'path':'other.go'})]:
        changed={**record,field:bad}
        view=diagnosis_continuity(prior,'current',[changed],[],input_digest='requirement')
        claim=view['claims_for_reassessment'][0]
        assert claim['current_evidence_refs']==[]
        assert claim['unavailable_prior_evidence_refs']==['SRC-X']
    assert prior==original
    view=diagnosis_continuity(prior,'current',[record],[],input_digest='requirement')
    assert view['claims_for_reassessment'][0]['current_evidence_refs']==['SRC-X']


def test_whole_claim_omission_is_bounded_and_counted_without_cutting_json():
    prior,record=context()
    prior['violations']=[{**prior['violations'][0],'id':f'V{i}','broken_behavior':'x'*13000} for i in range(3)]
    view=diagnosis_continuity(prior,'current',[record],[],input_digest='requirement')
    assert view['claims_for_reassessment']==[] and view['omitted_claim_count']==3
    assert json.loads(json.dumps(view))==view


def test_actual_planning_extension_receives_previous_claim_and_exact_current_source():
    class Provider(PlanningGapProvider):
        def __init__(self):super().__init__();self.seen=[]
        def complete_json(self,request):
            body=json.loads(request.messages[1]['content']);self.seen.append(body)
            if request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls:
                view=body['diagnosis_continuity'];assert view['snapshot_id']==body['run_context']['snapshot_id']
                assert view['extension_gaps'][0]['id']=='G1'
                claim=view['claims_for_reassessment'][0];assert claim['violation']['id']=='V1'
                catalog={e['id']:e for e in body['evidence_catalog']}
                assert claim['current_evidence_refs']
                assert all(ref in catalog and catalog[ref]['excerpt'] for ref in claim['current_evidence_refs'])
                assert 'not trusted facts' in view['instruction']
            return super().complete_json(request)
    p=Provider()
    with tempfile.TemporaryDirectory() as td:
        trace=run_fixture(Path(td),p,lambda s:len(s.split()))
    assert trace.status=='READY',trace.errors
    assert 'diagnosis_continuity' not in p.seen[0]
    assert [c['stage'] for c in trace.model_calls]==['DIAGNOSIS','SEMANTIC_CHANGESET','DIAGNOSIS_EXTENSION','CHANGESET_REVISION']
    assert trace.model_calls[2]['request_metadata']['diagnosis_continuity']['claim_ids']==['V1']


def test_current_reassessment_may_withdraw_old_claim_without_forcing_a_repair():
    class Provider(PlanningGapProvider):
        def complete_json(self,request):
            response=super().complete_json(request)
            if request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls==2:
                body=json.loads(response.content);body.update(status='EVIDENCE_GAP',violations=[],unverified_outcomes=['O1'],
                    evidence_gaps=[dict(id='G2',summary='Current evidence is insufficient to support the previous inference; leave verification open.',locator_hint={'path':'templates/serviceaccount.yaml'})],
                    outcome_coverage=[dict(outcome_id='O1',repair_supported=[],preserve_required=[],preserve_current_satisfaction=[],unresolved=['O1'])])
                return ModelResponse(json.dumps(body),{})
            return response
    p=Provider()
    with tempfile.TemporaryDirectory() as td:
        trace=run_fixture(Path(td),p,lambda s:len(s.split()))
    assert trace.status=='EVIDENCE_GAP',trace.errors
    assert len(trace.model_calls)==3 and trace.changeset is None
