from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.execution import helper_context
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def fixture(tmp_path):
    (tmp_path/'go.mod').write_text('module example.org/repair\n')
    (tmp_path/'app').mkdir(); (tmp_path/'runtime').mkdir()
    caller='''package app
import rt "example.org/repair/runtime"
func Apply() {
 if len(filters)>0 { rt.Initialize(input) }
 selected := rt.SelectInput(input, filters)
 if len(filters)>0 { input=rt.Merge(output,selected,input); rt.Cleanup(input) }
}
'''
    (tmp_path/'app/app.go').write_text(caller)
    (tmp_path/'runtime/runtime.go').write_text('''package runtime
func Initialize(input []*Node) error { return SetAnnotation(ResourceID, "id") }
func SelectInput(input []*Node, filters []Filter) []*Node { return input }
func Merge(output, selected, input []*Node) []*Node {
 for _, n := range output { if n.GetAnnotations()[ResourceID] == "" { result = append(result,n) } }
 return result
}
func Cleanup(input []*Node) error { return ClearAnnotation(ResourceID) }
type Filter struct { Name string }
''')
    snap=compute_snapshot(tmp_path)
    seeds=[u for u in parse_source_units('app/app.go',caller,snap.snapshot_id) if u.get('symbol')=='Apply']
    assert len(seeds)==1
    return snap,seeds


def support(snapshot,seeds,**kwargs):
    api=getattr(helper_context,'planning_helper_source_support',None)
    assert callable(api),'Planning must reuse bounded helper contracts before a plan exists'
    return api(snapshot,seeds,**kwargs)


def test_direct_merge_and_identity_contracts_are_available_before_a_plan(tmp_path):
    snap,seeds=fixture(tmp_path)
    records,audit=support(snap,seeds)
    text='\n'.join(r['excerpt'] for r in records)
    assert 'func Merge(' in text and 'func Initialize(' in text and 'func Cleanup(' in text
    assert len(records)<=8 and sum(len(r['excerpt'].encode()) for r in records)<=24000
    for r in records:
        assert r['snapshot_id']==snap.snapshot_id and r['kind']=='SOURCE_SPAN'
        assert hashlib.sha256(r['excerpt'].encode()).hexdigest()==r['content_digest']
    assert any(r.get('result')=='HELPER_SCAN_TOTALS' and r['indexed_files']<=128 for r in audit)


@pytest.mark.parametrize('change',['old_snapshot','modified_file','wrong_excerpt','symlink'])
def test_invalid_seed_cannot_authorize_planning_helper_reads(tmp_path,change):
    snap,seeds=fixture(tmp_path)
    if change=='old_snapshot': seeds=[{**seeds[0],'snapshot_id':'old'}]
    elif change=='modified_file': (tmp_path/'app/app.go').write_text('// changed\n'+(tmp_path/'app/app.go').read_text())
    elif change=='wrong_excerpt': seeds=[{**seeds[0],'text':'func Apply() { rt.Merge(a,b,c) }'}]
    else:
        original=tmp_path/'app/app.go'; original.rename(tmp_path/'app/real.go'); original.symlink_to('real.go')
    records,audit=support(snap,seeds)
    assert records==[] and audit


def test_small_support_budget_retains_audit_instead_of_partial_helper(tmp_path):
    snap,seeds=fixture(tmp_path)
    records,audit=support(snap,seeds,max_bytes=1)
    assert records==[] and any(r.get('result')=='READ_ONLY_SUPPORT_BUDGET' for r in audit)


def test_helper_directory_symlink_is_not_a_current_source_contract(tmp_path):
    snap,seeds=fixture(tmp_path)
    (tmp_path/'runtime').rename(tmp_path/'real-runtime')
    (tmp_path/'runtime').symlink_to('real-runtime',target_is_directory=True)
    records,audit=support(snap,seeds)
    assert records==[] and any(r.get('result')=='HELPER_SOURCE_UNAVAILABLE' for r in audit)


def test_seed_growth_between_stat_and_read_is_bounded_and_rejected(tmp_path,monkeypatch):
    from pathlib import Path
    _,_=fixture(tmp_path);path=tmp_path/'app/app.go';small=path.read_bytes()
    large=b'//'+b'x'*(600*1024)+b'\n'+small;path.write_bytes(large)
    snap=compute_snapshot(tmp_path)
    seeds=[u for u in parse_source_units('app/app.go',large.decode(),snap.snapshot_id) if u.get('symbol')=='Apply']
    path.write_bytes(small);real_open=Path.open;restored=False;read_sizes=[]
    class Bounded:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def read(self,size=-1):read_sizes.append(size);return self.stream.read(size)
    def changed_open(p,mode='r',*args,**kwargs):
        nonlocal restored
        if p==path and mode=='rb':
            if not restored:restored=True;path.write_bytes(large)
            return Bounded(real_open(p,mode,*args,**kwargs))
        return real_open(p,mode,*args,**kwargs)
    monkeypatch.setattr(Path,'open',changed_open)
    records,audit=support(snap,seeds)
    assert records==[], 'A raced seed larger than512KiB must not be accepted'
    assert read_sizes and all(0<=n<=512*1024+1 for n in read_sizes)
    assert audit


def test_seed_file_and_size_budgets_are_finite(tmp_path):
    snap,seeds=fixture(tmp_path)
    for i in range(5):
        (tmp_path/f'app/extra{i}.go').write_text((tmp_path/'app/app.go').read_text())
    snap=compute_snapshot(tmp_path)
    more=[u for i in range(5) for u in parse_source_units(f'app/extra{i}.go',
        (tmp_path/f'app/extra{i}.go').read_text(),snap.snapshot_id)]
    _,audit=support(snap,more)
    assert any(r.get('result')=='SOURCE_SEED_BUDGET' for r in audit)
    (tmp_path/'app/app.go').write_text('//'+('x'*(512*1024))+'\n'+(tmp_path/'app/app.go').read_text())
    snap=compute_snapshot(tmp_path)
    seed=parse_source_units('app/app.go',(tmp_path/'app/app.go').read_text(),snap.snapshot_id)
    records,audit=support(snap,seed)
    assert not records and any(r.get('result')=='SOURCE_SEED_BUDGET' for r in audit)


def test_prerequisite_contract_limit_stops_instead_of_silently_omitting_records():
    from contractedge.orchestration.runner import required_source_prerequisites
    from contractedge.model.budget import BudgetExceeded
    candidates=SimpleNamespace(candidates=())
    with pytest.raises(BudgetExceeded,match='12-record'):
        required_source_prerequisites(candidates,helper_support=[{'id':f'HELPER-{i}'} for i in range(13)])


def test_planning_request_reviews_readonly_helper_without_granting_edit_permission(tmp_path):
    from contractedge.orchestration.runner import required_planning_source_support,required_source_prerequisites
    from contractedge.changeset.prompt import build_changeset_request
    from contractedge.contracts.validation import parse_method_input,validate_diagnosis
    from tests.test_contracts import valid_diagnosis,valid_method_input
    snap,seeds=fixture(tmp_path);helpers,_=support(snap,seeds)
    candidates=SimpleNamespace(candidates=tuple(seeds),extension_used=False,required_endpoint_ids=())
    method=SimpleNamespace(declared_artifacts=(),public_evidence=())
    records=required_planning_source_support(method,candidates,snap.snapshot_id,helper_support=helpers)
    prerequisites=required_source_prerequisites(candidates,helper_support=helpers)
    assert {r['source_evidence_ref'] for r in prerequisites}=={r['id'] for r in helpers}
    assert all(r['delivery_required'] for r in records)
    packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
    context=SimpleNamespace(run_id='run-1',case_id='RW-DEMO',snapshot_id=snap.snapshot_id,
        input_digest=packet.input_digest,model_config={'model':'fixture'},budget={'max_output_tokens':1024},
        retrieval_config={'necessary_source_support':records,'source_prerequisites':prerequisites})
    request=build_changeset_request(packet,context);body=json.loads(request.messages[1]['content'])
    assert body['read_only_source_support']==records and body['source_prerequisites']==prerequisites
    assert 'precondition_review' in request.output_schema['required']
    assert 'runtime/runtime.go' not in {p['path'] for p in body['existing_target_paths']}
    assert 'readiness' in request.messages[0]['content']


@pytest.mark.parametrize('downgrade',[False,'disabled','zero_budget'])
def test_initial_replanned_ready_and_execution_keep_declared_helper_contracts(tmp_path,downgrade):
    from contractedge.contracts.validation import canonical_digest,parse_method_input
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.runner import run_front_half
    from contractedge.orchestration.repair import validate_ready_trace
    from contractedge.changeset.topomen import translate_for_topomen
    from contractedge.execution.topomen import build_execution_request
    from tests.test_planning_evidence_extension import PlanningGapProvider,front_fixture
    value,template=front_fixture(tmp_path);snap,seeds=fixture(tmp_path)
    value['scope_hints'].append(dict(kind='path',value='app/app.go',raw_text='app/app.go',origin_ref='public.scope'))
    value['initial_snapshot_id']=snap.snapshot_id;value['public_evidence'][0]['snapshot_id']=snap.snapshot_id
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    seen=[]
    class Provider(PlanningGapProvider):
        def complete_json(self,request):
            body=json.loads(request.messages[1]['content'])
            if request.prompt_version.startswith('diagnosis.'):
                body['evidence_catalog'].sort(key=lambda e:e.get('locator',{}).get('path')!='templates/serviceaccount.yaml')
                request=replace(request,messages=(request.messages[0],{**request.messages[1],'content':json.dumps(body)}))
            if request.prompt_version.startswith('changeset.'):
                helpers=[r for r in body['read_only_source_support'] if r['id'].startswith('HELPER-')]
                assert any('func Merge(' in r['excerpt'] for r in helpers),'Initial and replanned requests must receive actual merge contract'
                seen.append({r['id'] for r in helpers})
            response=super().complete_json(request)
            if request.prompt_version.startswith('changeset.'):
                answer=json.loads(response.content)
                answer['precondition_review']=[dict(source_evidence_ref=r['source_evidence_ref'],condition='Local Go helper contract',
                    disposition='NOT_APPLICABLE',verification_refs=[],open_obligation_refs=[],
                    establishment=dict(mode='NOT_APPLICABLE',source_quote='',change_refs=[],mechanism='Unchanged Go helpers are not used by the YAML edit.'),
                    rationale='The accepted edit is only the YAML template; the supplied Go caller and helpers remain unchanged.')
                    for r in body['source_prerequisites']]
                return ModelResponse(json.dumps(answer),{})
            return response
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(template.read_bytes()).hexdigest()}
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},helper_preconditions_enabled=True,
        max_context_tokens=32000,max_output_tokens=1024,max_total_tokens=160000),lambda s:len(s.split())).to_wire()
    assert trace['status']=='READY',(trace['errors'],trace.get('run_context'),
        [(u.get('path'),u.get('symbol'),u.get('kind')) for u in trace.get('candidate_set',{}).get('candidates',[])])
    assert len(seen)==2 and seen[0]==seen[1]
    packet,context=validate_ready_trace(trace,parse_method_input(value),tmp_path)
    assert context.retrieval_config['planning_helper_contracts'] is True
    records=context.retrieval_config['necessary_source_support']
    plan=translate_for_topomen(trace['changeset'],'contractedge.topomen_plan.v1',diagnosis=trace['diagnosis'],source_support=records)
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda s:len(s.split()),
        dict(model='fixture',max_context_tokens=32000,execution_max_output_tokens=1024),requirement=value['requirement_text'])
    body=json.loads(request.messages[1]['content'])
    assert any('func Merge(' in r['excerpt'] for r in body['read_only_source_support'])
    assert {r['path'] for r in body['current_target_files']}=={'templates/serviceaccount.yaml'}
    import copy
    assert context.retrieval_config['precondition_establishment_required'] is True
    assert all('establishment' in r for r in plan['precondition_review'])
    assert body['accepted_plan']['precondition_review'] == plan['precondition_review']
    downgraded = copy.deepcopy(trace)
    downgraded['run_context']['retrieval_config']['precondition_establishment_required'] = False
    for row in downgraded['run_context']['retrieval_config']['source_prerequisites']:
        row.pop('establishment_required', None)
    with pytest.raises(ValueError, match='establishment requirements'):
        validate_ready_trace(downgraded,parse_method_input(value),tmp_path)
    altered=copy.deepcopy(trace)
    altered['run_context']['retrieval_config']['necessary_source_support']=[r for r in records if not r['id'].startswith('HELPER-')]
    with pytest.raises(ValueError,match='prerequisite context'):
        validate_ready_trace(altered,parse_method_input(value),tmp_path)
    if downgrade:
        altered=copy.deepcopy(trace);retrieval=altered['run_context']['retrieval_config']
        retrieval['necessary_source_support']=[r for r in records if not r['id'].startswith('HELPER-')]
        retrieval['source_prerequisites']=[r for r in retrieval['source_prerequisites'] if not r['source_evidence_ref'].startswith('HELPER-')]
        altered['changeset']['precondition_review']=[r for r in altered['changeset']['precondition_review'] if not r['source_evidence_ref'].startswith('HELPER-')]
        if downgrade=='disabled':retrieval['planning_helper_contracts']=False
        else:retrieval['planning_helper_max_bytes']=0
        with pytest.raises(ValueError,match='planning declaration'):
            validate_ready_trace(altered,parse_method_input(value),tmp_path)
