import hashlib

from contractedge.retrieval.candidates import build_candidate_set,extend_once


def unit(name,text,*,required=False,role=None):
    return dict(id=name,path=name+'.go',text=text,content_digest=hashlib.sha256(text.encode()).hexdigest(),
        snapshot_id='S',locator={'start_line':1,'end_line':1},required_for_gap_delivery=required,
        gap_role_keys=[role] if role else [])


def test_new_gap_cannot_displace_existing_required_source_when_all_fit_top_n():
    old=[unit('old'+str(i),'lifecycle',required=True,role='LIFECYCLE:'+str(i)) for i in range(6)]
    initial=build_candidate_set('lifecycle',None,old,[],top_n=12,max_context_tokens=100000,token_counter=len)
    new=[unit('new'+str(i),'ownership',required=True,role='G'+str(i)+':anchor') for i in range(3)]
    decoys=[unit('decoy'+str(i),'ownership '*1000) for i in range(12)]
    gaps=[dict(id='G',locator_hint={'symbol':'ownership'})]
    result=extend_once(initial,gaps,new+decoys,max_context_tokens=100000,token_counter=len)
    assert len(result.selected_ids)<=12
    assert set(result.required_endpoint_ids)<=set(result.selected_ids)
    assert {u['id'] for u in old+new}<=set(result.selected_ids)


def test_unfit_required_source_stays_explicit_without_raising_limits():
    initial=build_candidate_set('ownership',None,[],[],top_n=2,max_context_tokens=1000,token_counter=len)
    units=[unit(str(i),'ownership',required=True,role='G'+str(i)+':anchor') for i in range(3)]
    result=extend_once(initial,[dict(id='G',locator_hint={'symbol':'ownership'})],units,max_context_tokens=1000,token_counter=len)
    assert len(result.selected_ids)==2 and len(result.required_endpoint_ids)==3


def test_deduplicated_location_requires_the_actual_catalog_identity():
    old=unit('old','ownership',required=True,role='LIFECYCLE:old')
    initial=build_candidate_set('ownership',None,[old],[],top_n=2,max_context_tokens=1000,token_counter=len)
    alias={**old,'id':'exact','gap_role_keys':['G:anchor']}
    result=extend_once(initial,[dict(id='G',locator_hint={'symbol':'ownership'})],[alias],max_context_tokens=1000,token_counter=len)
    assert result.required_endpoint_ids==result.selected_ids==('exact',)


def test_missing_required_delivery_is_not_misreported_as_token_or_money_exhaustion(tmp_path):
    from unittest.mock import patch
    from tests.test_planning_evidence_extension import front_fixture
    from contractedge.orchestration.runner import run_front_half
    value,_=front_fixture(tmp_path)
    class NoCalls:
        def complete_json(self,request):raise AssertionError('Missing required evidence must stop before HTTP')
    with patch('contractedge.retrieval.gap_delivery.audit_gap_delivery',return_value=dict(status='INCOMPLETE',missing_required_ids=['missing-source'],records=[])):
        trace=run_front_half(value,tmp_path,NoCalls(),dict(model={'model':'fixture'},max_context_tokens=24000,
            max_output_tokens=1024,max_total_tokens=100000,gap_role_delivery_enabled=True),lambda text:len(text.split()))
    assert trace.status=='EVIDENCE_GAP'
    assert 'missing-source' in trace.errors[0]
    assert not trace.model_calls
