"""Selected context must not disappear merely because diagnosis did not cite it."""
import json
from dataclasses import replace
from types import SimpleNamespace

from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.validation import parse_method_input, validate_diagnosis
from contractedge.orchestration.runner import required_planning_source_support
from contractedge.retrieval.candidates import build_candidate_set
from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_diagnosis, valid_method_input


def test_extension_preserves_uncited_selected_type_without_new_edit_permission(tmp_path):
    (tmp_path/'owner.go').write_text('package p\ntype Owner struct { client *ExternalClient }\n')
    snapshot=compute_snapshot(tmp_path)
    units=parse_source_units('owner.go',(tmp_path/'owner.go').read_text(),snapshot.snapshot_id)
    selected=build_candidate_set('Owner',None,units,[],top_n=12,max_context_tokens=12000,token_counter=len)
    method=SimpleNamespace(declared_artifacts=(),public_evidence=())
    # Initial context retains the existing opt-in support policy. Once evidence
    # acquisition has been spent, all finally selected source stays available.
    assert required_planning_source_support(method,selected,snapshot.snapshot_id)==[]
    selected=replace(selected,extension_used=True)
    support=required_planning_source_support(method,selected,snapshot.snapshot_id)
    assert len(support)==1 and support[0]['excerpt']==units[0]['text']
    assert support[0]['delivery_required'] and support[0]['snapshot_id']==snapshot.snapshot_id
    diagnosis=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
    context=SimpleNamespace(run_id='run-1',case_id='RW-DEMO',snapshot_id=snapshot.snapshot_id,
        input_digest=diagnosis.input_digest,model_config={'model':'fixture'},budget={'max_output_tokens':1024},
        retrieval_config={'necessary_source_support':support})
    request=build_changeset_request(diagnosis,context)
    body=json.loads(request.messages[1]['content'])
    assert body['read_only_source_support']==support
    assert 'owner.go' not in {p['path'] for p in body['existing_target_paths']}
    assert 'not required to pre-exist' in request.messages[0]['content']
    assert 'Missing implementation evidence requires NEEDS_EVIDENCE' not in request.messages[0]['content']


def test_literal_gap_miss_is_not_reported_as_a_found_endpoint(tmp_path):
    (tmp_path/'update.go').write_text('package p\nfunc Update() { buildPayload() }\n')
    units,audit=locate_gap_roles(compute_snapshot(tmp_path),[
        dict(id='G',summary='Update request handling',locator_hint=dict(path='update.go',search_term='ReplaceLabels'))],{})
    rows=[r for r in audit['records'] if r.get('gap_id')=='G']
    assert not any(r['status']=='FOUND' for r in rows)
    assert any(r['status']=='EXACT_QUERY_NOT_FOUND_IN_FILE' for r in rows)
    # Lexical context may still be useful, but it is never a matching endpoint
    # or a whole-repository assertion that the external operation is absent.
    assert all(r['status']=='LEXICAL_CONTEXT' for r in rows if r.get('unit_id'))
    assert all(not u.get('verified_causal_edge') for u in units)


def test_uncited_extension_context_survives_ready_and_execution_with_budget_guard(tmp_path):
    import hashlib
    import pytest
    from contractedge.changeset.topomen import translate_for_topomen
    from contractedge.contracts.validation import canonical_digest
    from contractedge.execution.topomen import build_execution_request, PlanRejected
    from contractedge.orchestration.repair import validate_ready_trace
    from contractedge.orchestration.runner import run_front_half
    from tests.test_planning_evidence_extension import PlanningGapProvider, front_fixture
    value,template=front_fixture(tmp_path)
    (tmp_path/'owner.go').write_text('package p\ntype Owner struct { client *ExternalClient }\n')
    value['scope_hints'].append(dict(kind='path',value='owner.go',raw_text='owner.go',origin_ref='public.scope'))
    value['initial_snapshot_id']=compute_snapshot(tmp_path).snapshot_id
    value['public_evidence'][0]['snapshot_id']=value['initial_snapshot_id']
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    class Provider(PlanningGapProvider):
        def complete_json(self,request):
            body=json.loads(request.messages[1]['content'])
            # The fixture chooses the real YAML repair from the unchanged pool;
            # it deliberately never cites the additional Go declaration.
            if request.prompt_version.startswith('diagnosis.'):
                body['evidence_catalog'].sort(key=lambda e:e.get('locator',{}).get('path')!='templates/serviceaccount.yaml')
                request=replace(request,messages=(request.messages[0],{**request.messages[1],'content':json.dumps(body)}))
            if request.prompt_version.startswith('changeset.') and self.plan_calls==1:
                assert any(r['locator']['path']=='owner.go' for r in body['read_only_source_support'])
            return super().complete_json(request)
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(template.read_bytes()).hexdigest()}
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},max_context_tokens=24000,
        max_output_tokens=1024,max_total_tokens=100000),lambda s:len(s.split())).to_wire()
    assert trace['status']=='READY',trace['errors']
    packet,context=validate_ready_trace(trace,parse_method_input(value),tmp_path)
    support=context.retrieval_config['necessary_source_support']
    assert any(r['locator']['path']=='owner.go' for r in support)
    assert all(r['locator'].get('path')!='owner.go' for r in trace['diagnosis']['evidence_refs'])
    plan=translate_for_topomen(trace['changeset'],'contractedge.topomen_plan.v1',diagnosis=trace['diagnosis'],source_support=support)
    config=dict(model='fixture',max_context_tokens=24000,execution_max_output_tokens=1024)
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda s:len(s.split()),config,requirement=value['requirement_text'])
    body=json.loads(request.messages[1]['content'])
    assert any('client *ExternalClient' in r['excerpt'] for r in body['read_only_source_support'])
    assert {r['path'] for r in body['current_target_files']}=={'templates/serviceaccount.yaml'}
    with pytest.raises(PlanRejected,match='source was not delivered'):
        build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda s:len(s.split()),
            {**config,'max_execution_support_bytes':1},requirement=value['requirement_text'])
    assert compute_snapshot(tmp_path).snapshot_id==value['initial_snapshot_id']
