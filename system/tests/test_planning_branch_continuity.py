"""Same-snapshot planning concerns stay reviewable, never become source facts."""
from copy import deepcopy
import hashlib,json
import pytest
from contractedge.orchestration.runner import diagnosis_continuity


def fixture():
    text='func checkRules() { selectInputs() }\n'
    source=dict(id='SRC-rule',kind='SOURCE_SPAN',snapshot_id='S',locator=dict(path='flow.go',start_line=9,end_line=9),excerpt=text,content_digest=hashlib.sha256(text.encode()).hexdigest())
    prior=dict(snapshot_id='S',input_digest='I',run_id='R',evidence_refs=[source],expected_relations=[],violations=[],obligation_atoms=[dict(id='A1',outcome_id='O1')],requirement_spec=dict(outcomes=[dict(id='O1')]))
    plan=dict(snapshot_id='S',input_digest='I',run_id='R',status='NEEDS_EVIDENCE',production_path_review=[dict(source_evidence_id='SRC-rule',disposition='UNRESOLVED',rationale='The second owning path also includes its configuration as ordinary input.',change_refs=[],open_obligation_refs=['OO1'])],open_obligations=[dict(id='OO1',outcome_id='O1',obligation_refs=['A1'],statement='The configuration stays out of ordinary input in both owning paths.',reason='Shared selection helper needs evidence',behavior_check_refs=['generic:rules.excludes_configuration'])])
    return prior,plan,[source]


def call(prior,plan,evidence):
    return diagnosis_continuity(prior,'S',evidence,[],input_digest='I',planning_context=plan)


def test_unresolved_branch_and_open_predicate_survive_even_without_supported_claims():
    prior,plan,evidence=fixture();result=call(prior,plan,evidence)
    row=result['planning_branch_assessments'][0]
    assert row['source_evidence_ref']=='SRC-rule'
    assert row['source_content_digest']==evidence[0]['content_digest']
    assert row['rationale']==plan['production_path_review'][0]['rationale']
    assert row['open_obligations']==plan['open_obligations']
    assert not result['claims_for_reassessment']
    assert 'not' in result['planning_branch_interpretation'].lower()
    assert prior['violations']==[] and len(prior.get('units',[]))==0


@pytest.mark.parametrize('mutation',['snapshot','input','run','digest','excerpt','missing_source','unknown_open','wrong_atom','out_of_scope','preserve','claimed_edit'])
def test_unbound_or_unresolved_inapplicable_context_cannot_be_promoted(mutation):
    prior,plan,evidence=fixture()
    if mutation=='snapshot':plan['snapshot_id']='OLD'
    elif mutation=='input':plan['input_digest']='OTHER'
    elif mutation=='run':plan['run_id']='OTHER'
    elif mutation=='digest':evidence[0]['content_digest']='0'*64
    elif mutation=='excerpt':evidence[0]['excerpt']='other source'
    elif mutation=='missing_source':evidence=[]
    elif mutation=='unknown_open':plan['production_path_review'][0]['open_obligation_refs']=['absent']
    elif mutation=='wrong_atom':plan['open_obligations'][0]['obligation_refs']=['absent']
    elif mutation=='out_of_scope':plan['production_path_review'][0]['disposition']='OUT_OF_SCOPE'
    elif mutation=='preserve':plan['production_path_review'][0]['disposition']='PRESERVE'
    elif mutation=='claimed_edit':plan['production_path_review'][0]['change_refs']=['CH1']
    result=call(prior,plan,evidence)
    assert not (result or {}).get('planning_branch_assessments')


def test_context_free_legacy_continuity_is_unchanged():
    prior,plan,evidence=fixture()
    assert diagnosis_continuity(prior,'S',evidence,[],input_digest='I') is None
    prior['violations']=[dict(id='V1',relation_id='R1',causal_status='SUPPORTED',evidence_refs=['SRC-rule'])]
    before=diagnosis_continuity(prior,'S',evidence,[],input_digest='I')
    wrong={**plan,'snapshot_id':'OLD'}
    assert call(prior,wrong,evidence)==before
    assert 'planning_branch_assessments' not in before


def test_complete_records_fit_four_record_eight_kib_bound_with_omissions():
    prior,plan,evidence=fixture();rows=[]
    for i in range(8):
        source={**evidence[0],'id':f'SRC-{i}'};evidence.append(source);prior['evidence_refs'].append(source)
        rows.append({**plan['production_path_review'][0],'source_evidence_id':source['id'],'rationale':'why '*500})
    plan['production_path_review']=rows
    result=call(prior,plan,evidence)
    assert 0<len(result['planning_branch_assessments'])<=4
    assert len(json.dumps(result['planning_branch_assessments'],ensure_ascii=False).encode())<=8000
    assert result['omitted_planning_branch_count']>0

@pytest.mark.parametrize('mutation',['open_none','open_string','open_nonrecord','rows_string','row_none','refs_object','atoms_none','reason_object'])
def test_invalid_model_plan_fields_cannot_crash_reassessment(mutation):
    prior,plan,evidence=fixture()
    if mutation=='open_none':plan['open_obligations']=None
    elif mutation=='open_string':plan['open_obligations']='bad'
    elif mutation=='open_nonrecord':plan['open_obligations']=[None]
    elif mutation=='rows_string':plan['production_path_review']='bad'
    elif mutation=='row_none':plan['production_path_review']=[None]
    elif mutation=='refs_object':plan['production_path_review'][0]['open_obligation_refs']=[{}]
    elif mutation=='atoms_none':plan['open_obligations'][0]['obligation_refs']=None
    elif mutation=='reason_object':plan['production_path_review'][0]['rationale']={}
    result=call(prior,plan,evidence)
    assert not (result or {}).get('planning_branch_assessments')


@pytest.mark.parametrize('invalid_outcome',[False,True])
def test_actual_extension_and_format_correction_keep_planner_concern(tmp_path,invalid_outcome):
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.runner import run_front_half
    from tests.test_planning_evidence_extension import PlanningGapProvider,front_fixture
    value,source=front_fixture(tmp_path)
    class Provider(PlanningGapProvider):
        def __init__(self):super().__init__();self.concern_seen=[]
        def complete_json(self,request):
            request_body=json.loads(request.messages[1]['content'])
            if request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls:
                rows=request_body['diagnosis_continuity'].get('planning_branch_assessments',[])
                if invalid_outcome:
                    assert rows==[]
                    self.concern_seen.append(None)
                    return super().complete_json(request)
                assert len(rows)==1 and rows[0]['rationale']=='Another supplied production branch remains unresolved.'
                assert rows[0]['open_obligations'][0]['id']=='G1'
                catalog={e['id']:e for e in request_body['evidence_catalog']}
                assert rows[0]['source_evidence_ref'] in catalog
                assert rows[0]['source_content_digest']==catalog[rows[0]['source_evidence_ref']]['content_digest']
                self.concern_seen.append(rows[0])
            response=super().complete_json(request)
            if request.prompt_version.startswith('changeset.') and self.plan_calls==1:
                body=json.loads(response.content)
                record=next(e for e in request_body['diagnosis_packet']['evidence_refs'] if e['kind']=='SOURCE_SPAN')
                if invalid_outcome:body['open_obligations'][0]['outcome_id']='ABSENT'
                body['production_path_review']=[dict(source_evidence_id=record['id'],disposition='UNRESOLVED',rationale='Another supplied production branch remains unresolved.',change_refs=[],open_obligation_refs=['G1'])]
                return ModelResponse(json.dumps(body),{})
            if request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls==2:
                body=json.loads(response.content);body['violations'][0]['support_basis']='INVALID'
                return ModelResponse(json.dumps(body),{})
            return response
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(source.read_bytes()).hexdigest()}
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},max_context_tokens=24000,
        max_output_tokens=1024,max_total_tokens=100000,max_format_retries=1,
        diagnosis_format_retry_per_context=True),lambda text:len(text.split()))
    assert trace.status=='READY',trace.errors
    assert provider.diagnosis_calls==(2 if invalid_outcome else 3) and provider.plan_calls==2
    assert len(provider.concern_seen)==(1 if invalid_outcome else 2)
    if invalid_outcome:
        assert 'REFERENCE_NOT_FOUND' in {e['code'] for e in trace.model_calls[1]['validation_feedback']}
    assert trace.candidate_set.extension_used


@pytest.mark.parametrize('mutation',['unknown_outcome','wrong_parent','empty_atoms','empty_statement','null_statement','unknown_field','duplicate_open_id'])
def test_invalid_obligation_cannot_become_reassessment_context(mutation):
    prior,plan,evidence=fixture()
    if mutation=='unknown_outcome':plan['open_obligations'][0]['outcome_id']='ABSENT'
    elif mutation=='wrong_parent':prior['obligation_atoms'][0]['outcome_id']='O2'
    elif mutation=='empty_atoms':plan['open_obligations'][0]['obligation_refs']=[]
    elif mutation=='empty_statement':plan['open_obligations'][0]['statement']=''
    elif mutation=='null_statement':plan['open_obligations'][0]['statement']=None
    elif mutation=='unknown_field':plan['open_obligations'][0]['unregistered_claim']=True
    elif mutation=='duplicate_open_id':plan['open_obligations'].append({**plan['open_obligations'][0],'statement':'Contradictory duplicate'})
    result=call(prior,plan,evidence)
    assert not (result or {}).get('planning_branch_assessments')
