import copy
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contractedge.diagnosis.evidence import (source_context_new, source_context_offer,
    source_context_commit, source_context_records, source_context_restart,
    source_context_for_candidates, source_context_projection)
from contractedge.model.budget import BudgetExceeded
from contractedge.retrieval.candidates import build_candidate_set, extend_once, trim_context_bundle


def empty(front='front'):
    return source_context_new(dict(run_id='run',case_id='case',input_digest='input',front_id=front))


def record(name='one', snapshot='S', path='one.go'):
    text=f'func {name}() {{}}\n'
    return dict(id='SRC-'+name,kind='SOURCE_SPAN',snapshot_id=snapshot,
        locator=dict(path=path,start_line=1,end_line=1),excerpt=text,
        content_digest=hashlib.sha256(text.encode()).hexdigest(),origin='test source')


def test_unsent_draft_is_not_adopted_but_unknown_dispatch_keeps_body():
    active=empty(); draft=source_context_offer(active,[record()],producer='initial')
    assert source_context_commit(active,draft,'not_sent') == active
    unknown=source_context_commit(active,draft,'response_unknown')
    assert source_context_records(unknown)==[record()]
    draft['records'][0]['excerpt']='mutated'
    assert source_context_records(unknown)==[record()]
    with pytest.raises(ValueError):source_context_commit(active,unknown,'sent_maybe')


def test_current_and_historical_identity_are_separate_not_latest_wins():
    active=source_context_offer(empty(),[record()],producer='initial')
    current=record(snapshot='S1')
    current['origin']='Fresh exact re-read'
    updated=source_context_offer(active,[current],producer='reread')
    assert [r['snapshot_id'] for r in updated['records']]==['S','S1']
    assert not source_context_records(updated,producer='execution_helper')
    with pytest.raises(ValueError):
        source_context_offer(active,[record('invented',snapshot='S1')],producer='reread')
    with pytest.raises(ValueError):
        source_context_offer(active,[{**record(),'locator':{'path':'elsewhere.go'}}],producer='extension')


def test_source_extension_count_and_bytes_cannot_reset_on_retry_or_duplicate_read():
    active=empty()
    for index in range(12):
        active=source_context_offer(active,[record(str(index))],producer='extension')
    again=source_context_offer(active,[record('0')],producer='extension')
    assert len(again['records'])==12
    with pytest.raises(BudgetExceeded):source_context_offer(again,[record('extra')],producer='extension')
    bad=record();bad['excerpt']='x'*24001;bad['content_digest']=hashlib.sha256(bad['excerpt'].encode()).hexdigest()
    with pytest.raises(BudgetExceeded):source_context_offer(empty(),[bad],producer='extension')


def test_root_replacement_retires_only_uninherited_sources_and_never_relabels():
    active=source_context_offer(empty(),[record(),record('unused')],producer='initial')
    active=source_context_offer(active,[record(snapshot='S1')],producer='reread')
    binding=empty('rediagnose')['binding']
    explicit=source_context_restart(active,binding,{'snapshot_id':'S','evidence_refs':['SRC-one']})
    assert [(r['id'],r['snapshot_id']) for r in explicit['records']]==[('SRC-one','S')]
    unqualified=source_context_restart(active,binding,{'evidence_refs':['SRC-one']})
    assert {r['snapshot_id'] for r in unqualified['records']}=={'S','S1'}
    with pytest.raises(ValueError):source_context_commit(active,explicit,'response_known')


def test_committed_source_cannot_be_evicted_or_changed_by_a_later_builder():
    active=source_context_offer(empty(),[record()],producer='initial',candidate_ids=['one'])
    for change in ('records','candidate_ids','producer_refs'):
        draft=copy.deepcopy(active);draft[change]=[] if change!='producer_refs' else {}
        with pytest.raises(ValueError):source_context_commit(active,draft,'response_known')


def test_twelve_sent_uncited_candidates_and_new_endpoint_share_one_context():
    from tests.test_required_extension_selection import unit
    units=[unit(str(i),f'func F{i}() {{}}\n') for i in range(12)]
    base=build_candidate_set('F0',None,units,[],top_n=12,max_context_tokens=100000,token_counter=len)
    method=SimpleNamespace(declared_artifacts=(),public_evidence=())
    active=source_context_for_candidates(empty(),method,base,'S')
    extra=unit('extra','func Extra() {}\n',required=True,role='G:anchor')
    grown=extend_once(base,[{'id':'G','locator_hint':{'symbol':'Extra'}}],[extra],
        max_context_tokens=100000,token_counter=len,source_context=active)
    assert list(grown.candidates[:12])==list(base.candidates)
    assert len(grown.candidates)==13
    trimmed=trim_context_bundle(grown,request_tokens=1000,limit=1,adopted_ids=active['candidate_ids'])
    assert trimmed.candidates==base.candidates
    assert trim_context_bundle(trimmed,request_tokens=1000,limit=1,
        adopted_ids=active['candidate_ids']).candidates==base.candidates


def test_fresh_source_and_declared_artifact_keep_distinct_provenance():
    from tests.test_required_extension_selection import unit
    original=unit('base','func Base() {}\n')
    base=build_candidate_set('Added',None,[original],[],top_n=12,max_context_tokens=100000,token_counter=len)
    method=SimpleNamespace(case_id='case',declared_artifacts=(),public_evidence=())
    active=source_context_for_candidates(empty(),method,base,'S')
    source=unit('source','func Added() {}\n',required=True,role='G:anchor')
    artifact={**source,'id':'artifact','origin_kind':'DECLARED_ARTIFACT','artifact_id':'PUBLIC'}
    grown=extend_once(base,[{'id':'G','locator_hint':{'symbol':'Added'}}],[source,artifact],
        max_context_tokens=100000,token_counter=len,source_context=active)
    assert set(grown.selected_ids)=={'base','source','artifact'}
    assert set(grown.required_endpoint_ids)=={'source','artifact'}
    same_source={**source,'id':'source-alias','origin_kind':'SOURCE_SPAN'}
    canonical=extend_once(base,[{'id':'G','locator_hint':{'symbol':'Added'}}],[source,same_source],
        max_context_tokens=100000,token_counter=len,source_context=active)
    assert len(canonical.selected_ids)==2
    assert set(canonical.required_endpoint_ids)==set(canonical.selected_ids)-{'base'}


def test_reused_adopted_id_cannot_change_origin_and_trim_recounts_actual_members():
    from tests.test_required_extension_selection import unit
    original=unit('base','func Base() {}\n')
    base=build_candidate_set('Added',None,[original],[],top_n=12,max_context_tokens=100000,token_counter=len)
    method=SimpleNamespace(case_id='case',declared_artifacts=(),public_evidence=())
    active=source_context_for_candidates(empty(),method,base,'S')
    other={**original,'origin_kind':'DECLARED_ARTIFACT','artifact_id':'PUBLIC'}
    with pytest.raises(ValueError,match='adopted candidate identity'):
        extend_once(base,[{'id':'G','locator_hint':{'symbol':'Base'}}],[other],
            max_context_tokens=100000,token_counter=len,source_context=active)
    extra=unit('extra','func Added() {}\n')
    grown=extend_once(base,[{'id':'G','locator_hint':{'symbol':'Added'}}],[extra],
        max_context_tokens=100000,token_counter=len,source_context=active)
    trimmed=trim_context_bundle(grown,request_tokens=1000,limit=1,
        adopted_ids=active['candidate_ids'],token_counter=len)
    assert trimmed.context_tokens==base.context_tokens
    assert {row['id'] for row in trimmed.delivery_audit if row['delivered']}=={'base'}


def test_later_extension_admission_clears_old_unselected_drop_reason():
    from tests.test_required_extension_selection import unit
    base=build_candidate_set('Base',None,[unit('base','func Base() {}\n'),
        unit('later','func Later() {}\n')],[],top_n=1,max_context_tokens=100000,token_counter=len)
    method=SimpleNamespace(case_id='case',declared_artifacts=(),public_evidence=())
    active=source_context_for_candidates(empty(),method,base,'S')
    assert base.dropped_ids==('later',)
    grown=extend_once(base,[{'id':'G','locator_hint':{'symbol':'Later'}}],
        [unit('later','func Later() {}\n',required=True,role='G:anchor')],
        max_context_tokens=100000,token_counter=len,source_context=active)
    assert grown.selected_ids==('base','later')
    assert 'later' not in grown.dropped_ids
    assert 'later' not in grown.drop_reasons
    assert next(row for row in grown.delivery_audit if row['id']=='later')['drop_reason'] is None


def test_target_projection_keeps_two_windows_without_sending_hidden_read_buffer():
    target=dict(path='one.go',snapshot_id='S',source_file_sha256='f'*64,
        content='read buffer including omitted source',selected_target={
            'path':'one.go','content':'first\n...\nlast\n','windowed_from_current_target':True,
            'window_segments':[[1,1],[99,99]],'source_file_sha256':'f'*64})
    active=source_context_offer(empty(),[],producer='execution_helper',targets=[target])
    view=source_context_projection(active)['targets'][0]
    assert view['content']=='first\n...\nlast\n'
    assert view['window_segments']==[[1,1],[99,99]]
    assert not source_context_projection(active,supplied_targets=[target])['targets']


@pytest.mark.parametrize('sent',[False,True])
def test_actual_front_dispatch_controls_adoption(tmp_path,sent):
    from contractedge.orchestration.runner import run_front_half
    from tests.test_planning_evidence_extension import front_fixture
    value,_=front_fixture(tmp_path)
    class Provider:
        calls=0
        def complete_json(self,request):
            self.calls+=1
            raise TimeoutError('transport response unknown')
    provider=Provider()
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},
        max_context_tokens=24000,max_output_tokens=1024,max_total_tokens=100000 if sent else 1),
        lambda s:len(s.split()))
    context=(trace.run_context or {}).get('retrieval_config',{}).get('source_context')
    assert bool(context and context['records']) is sent
    assert provider.calls==int(sent)
    if sent:assert trace.model_calls[-1]['dispatch_state']=='response_unknown'


def test_real_pinned_api_is_delivered_but_never_offered_as_diagnosis_evidence(tmp_path):
    import json
    from contractedge.diagnosis.prompt import build_diagnosis_request
    from contractedge.changeset.prompt import build_changeset_request
    from contractedge.execution.topomen import build_execution_request
    from contractedge.contracts.validation import parse_method_input,validate_diagnosis
    from tests.test_contracts import valid_method_input,valid_diagnosis
    from tests.test_compiler_dependency_context import request_fixture
    snap,feedback,root,plan,config=request_fixture(tmp_path)
    base=source_context_new({**empty()['binding'],'input_digest':plan['input_digest']})
    execution=build_execution_request(plan,root,{'allowed_paths':['main.go']},len,config,
        requirement='source fixture',feedback=feedback,source_context=base)
    adopted=source_context_commit(base,execution.metadata['source_context'],'response_known')
    api=source_context_records(adopted,kind='PUBLIC_DEPENDENCY_DECLARATION')[0]
    assert 'locator' not in api
    method=parse_method_input(valid_method_input())
    context=SimpleNamespace(run_id='run',case_id=method.case_id,snapshot_id=snap.snapshot_id,
        input_digest=method.input_digest,model_config={'model':'fixture'},budget={'max_output_tokens':1024},
        retrieval_config={'source_context':adopted,'necessary_source_support':[]})
    candidates=build_candidate_set('',None,[],[],top_n=12,max_context_tokens=10000,token_counter=len)
    request=build_diagnosis_request(method,valid_diagnosis()['requirement_spec'],candidates,context)
    body=json.loads(request.messages[1]['content'])
    assert api['id'] not in body['allowed_evidence_ids']
    assert api in body['adopted_source_context']['records']
    packet=validate_diagnosis(valid_diagnosis(),method,None)
    planning=build_changeset_request(packet,context)
    assert api in json.loads(planning.messages[1]['content'])['adopted_source_context']['records']


def test_ready_restores_dispatched_helpers_without_running_selection_again(tmp_path,monkeypatch):
    import json
    from contractedge.contracts.validation import canonical_digest,parse_method_input
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.runner import run_front_half
    from contractedge.orchestration.repair import validate_ready_trace
    from tests.test_planning_evidence_extension import front_fixture,PlanningGapProvider
    from tests.test_planning_helper_contracts import fixture
    value,template=front_fixture(tmp_path);snap,_=fixture(tmp_path)
    value['scope_hints'].append(dict(kind='path',value='app/app.go',raw_text='app/app.go',origin_ref='public.scope'))
    value['initial_snapshot_id']=snap.snapshot_id;value['public_evidence'][0]['snapshot_id']=snap.snapshot_id
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    class Provider(PlanningGapProvider):
        def complete_json(self,request):
            body=json.loads(request.messages[1]['content'])
            if request.prompt_version.startswith('diagnosis.'):
                body['evidence_catalog'].sort(key=lambda r:r.get('locator',{}).get('path')!='templates/serviceaccount.yaml')
                request=replace(request,messages=(request.messages[0],{**request.messages[1],'content':json.dumps(body)}))
            response=super().complete_json(request)
            if request.prompt_version.startswith('changeset.'):
                answer=json.loads(response.content)
                answer['precondition_review']=[dict(source_evidence_ref=r['source_evidence_ref'],
                    condition='Local Go helper contract',disposition='NOT_APPLICABLE',verification_refs=[],open_obligation_refs=[],
                    establishment=dict(mode='NOT_APPLICABLE',source_quote='',change_refs=[],
                        mechanism='The YAML edit does not use the Go helper.'),rationale='Go source is unchanged.')
                    for r in body['source_prerequisites']]
                return ModelResponse(json.dumps(answer),{})
            return response
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(template.read_bytes()).hexdigest()}
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},helper_preconditions_enabled=True,
        max_context_tokens=32000,max_output_tokens=1024,max_total_tokens=160000),lambda s:len(s.split())).to_wire()
    assert trace['status']=='READY',trace['errors']
    expected=source_context_records(trace['run_context']['retrieval_config']['source_context'],producer='planning_helper')
    assert expected
    def cannot_reselect(*args,**kwargs):raise AssertionError('READY must not rerun helper selection')
    monkeypatch.setattr('contractedge.execution.helper_context.planning_helper_source_support',cannot_reselect)
    _,context=validate_ready_trace(trace,parse_method_input(value),tmp_path)
    assert source_context_records(context.retrieval_config['source_context'],producer='planning_helper')==expected
    changed=copy.deepcopy(trace)
    changed['run_context']['retrieval_config']['source_context']['records'].pop()
    with pytest.raises(ValueError):validate_ready_trace(changed,parse_method_input(value),tmp_path)
