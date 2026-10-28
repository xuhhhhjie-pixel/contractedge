"""Exact local callback/read/write windows survive the existing source flow."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.source.parsers import parse_source_units
from contractedge.orchestration.runner import required_source_prerequisites


SOURCE='''package p
func serve() {
    var active *Engine
    view := func() int { return active.Count() }
    active = makeEngine()
    _ = view
}
'''


def units(text=SOURCE,path='reader.go'):
    return parse_source_units(path,text,'S',max_lines=80)


def contexts(records,**limits):
    from contractedge.diagnosis.evidence import reader_reference_contexts
    return reader_reference_contexts(records,**limits)


@pytest.mark.parametrize('slot,callback,method',[('active','view','Count'),('selected','readState','Snapshot')])
def test_exact_reader_and_later_write_are_highlighted_without_runtime_claim(slot,callback,method):
    text=SOURCE.replace('active',slot).replace('view',callback).replace('Count',method)
    rows=contexts(units(text));assert len(rows)==1
    r=rows[0];assert (r['variable'],r['callback'],r['receiver_method'])==(slot,callback,method)
    assert r['source_evidence_ref'].startswith('SRC-') and r['snapshot_id']=='S'
    lines=text.splitlines(keepends=True)
    for window in [r['reader'],*r['writes']]:
        loc=window['locator'];expected=''.join(lines[loc['start_line']-1:loc['end_line']])
        assert window['excerpt']==expected
        assert window['excerpt_digest']==hashlib.sha256(expected.encode()).hexdigest()
    assert 'not' in r['interpretation'].lower() and 'permission' in r['interpretation'].lower()


@pytest.mark.parametrize('mutation',['comment','string','shadow','constant','non_go','stale_digest','truncated','parameter_shadow'])
def test_unsupported_or_unbound_relationship_is_not_a_reference_fact(mutation):
    text=SOURCE;path='reader.go'
    if mutation=='comment':text=text.replace('    view :=','    // view :=')
    elif mutation=='string':text=text.replace('    view := func() int { return active.Count() }','    s := `view := func() int { return active.Count() }`')
    elif mutation=='shadow':text=text.replace('    active =','    active :=')
    elif mutation=='constant':text=text.replace('    active = makeEngine()\n','')
    elif mutation=='parameter_shadow':text=text.replace('func() int { return active.Count() }','func(active *Engine) int { return active.Count() }')
    elif mutation=='non_go':path='reader.py'
    data=units(text,path)
    if mutation=='stale_digest':data=[{**u,'content_digest':'0'*64} for u in data]
    if mutation=='truncated':data=[{**u,'truncated':True} for u in data]
    assert contexts(data)==[]


def test_windows_obey_limit_and_deduplicate_exact_source_identity():
    data=units()
    assert len(contexts(data+data,max_records=1))==1
    assert contexts(data,max_bytes=5)==[]
    assert contexts(data,max_records=0)==[]


def test_reference_role_becomes_a_source_prerequisite_even_when_not_a_json_field():
    declared=[{**u,'gap_role_keys':['SOURCE_READER:'+u['id']]} for u in units()]
    rows=required_source_prerequisites(SimpleNamespace(candidates=declared))
    assert len(rows)==1
    assert 'active' in rows[0]['reason'] and 'view' in rows[0]['reason']
    assert 'publication' in rows[0]['reason'].lower()


def test_current_reader_write_changes_recompute_the_highlight():
    first=contexts(units());second=contexts(units(SOURCE.replace('makeEngine()','buildReplacement()')))
    assert first[0]['source_content_digest']!=second[0]['source_content_digest']
    assert 'buildReplacement' in second[0]['writes'][0]['excerpt']


@pytest.mark.parametrize('shadow',[
    '    writer := func(active *Engine) {\n        active = makeEngine()\n    }\n',
    '    {\n        var active *Engine\n        active = makeEngine()\n    }\n',
])
def test_nested_parameter_or_var_shadow_cannot_be_a_publication_write(shadow):
    assert contexts(units(SOURCE.replace('    active = makeEngine()\n',shadow)))==[]


@pytest.mark.parametrize('extend',[False,True])
def test_reader_reference_survives_diagnosis_plan_ready_and_execution_without_edit_grant(tmp_path,extend):
    from dataclasses import replace
    from contractedge.changeset.topomen import translate_for_topomen
    from contractedge.contracts.validation import canonical_digest,parse_method_input
    from contractedge.execution.topomen import build_execution_request,PlanRejected
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.runner import run_front_half
    from contractedge.orchestration.repair import validate_ready_trace
    from contractedge.source.snapshot import compute_snapshot
    from tests.test_planning_evidence_extension import front_fixture,PlanningGapProvider
    from tests.test_orchestration import PipelineFixtureProvider
    value,template=front_fixture(tmp_path);(tmp_path/'reader.go').write_text(SOURCE)
    value['scope_hints'].append(dict(kind='path',value='reader.go',origin_ref='public.scope',raw_text='reader.go'))
    value['initial_snapshot_id']=compute_snapshot(tmp_path).snapshot_id
    value['public_evidence'][0]['snapshot_id']=value['initial_snapshot_id']
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    class Provider(PlanningGapProvider if extend else PipelineFixtureProvider):
        def complete_json(self,request):
            body=json.loads(request.messages[1]['content'])
            if request.prompt_version.startswith('diagnosis.'):
                rows=body['source_reader_reference_contexts']
                assert rows[0]['variable']=='active' and rows[0]['callback']=='view'
                body['evidence_catalog'].sort(key=lambda e:e.get('locator',{}).get('path')!='templates/serviceaccount.yaml')
                request=replace(request,messages=(request.messages[0],{**request.messages[1],'content':json.dumps(body)}))
            response=super().complete_json(request)
            if request.prompt_version.startswith('changeset.'):
                source=next(r for r in body['source_prerequisites'] if 'publication' in r['reason'])
                assert 'active' in source['reason'] and 'makeEngine' in source['reason']
                plan=json.loads(response.content)
                plan['precondition_review']=[dict(source_evidence_ref=r['source_evidence_ref'],
                    condition='This unrelated Go reader is a read-only control, not consumed by the Helm render.',
                    disposition='NOT_APPLICABLE',verification_refs=[],open_obligation_refs=[],
                    establishment=dict(mode='NOT_APPLICABLE',source_quote='',change_refs=[],mechanism='No state handoff is changed by the Helm edit.'),
                    rationale='No state handoff is changed by this Helm-only fixture.') for r in body['source_prerequisites']]
                return ModelResponse(json.dumps(plan),{})
            return response
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(template.read_bytes()).hexdigest()}
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},gap_role_delivery_enabled=True,
        max_context_tokens=50000,max_output_tokens=1024,max_total_tokens=200000),lambda s:len(s.split())).to_wire()
    assert trace['status']=='READY',trace['errors']
    assert trace['candidate_set']['extension_used']==extend
    _,context=validate_ready_trace(trace,parse_method_input(value),tmp_path)
    plan=translate_for_topomen(trace['changeset'],'contractedge.topomen_plan.v1',diagnosis=trace['diagnosis'],
        source_support=context.retrieval_config['necessary_source_support'])
    config=dict(model='fixture',helper_preconditions_enabled=True,max_context_tokens=50000,execution_max_output_tokens=1024)
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda s:len(s.split()),config,requirement=value['requirement_text'])
    body=json.loads(request.messages[1]['content'])
    assert body['source_reader_reference_contexts'], [(r.get('id'),r.get('kind'),r.get('locator'),r.get('excerpt','')[:80]) for r in body['read_only_source_support']]
    assert body['source_reader_reference_contexts'][0]['variable']=='active'
    assert {r['path'] for r in body['current_target_files']}=={'templates/serviceaccount.yaml'}
    (tmp_path/'reader.go').write_text(SOURCE.replace('makeEngine()','replacement()'))
    with pytest.raises(PlanRejected):
        build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda s:len(s.split()),config,requirement=value['requirement_text'])


def test_editable_target_is_highlighted_from_current_file_without_inventing_evidence_id(tmp_path):
    from contractedge.execution.topomen import build_execution_request
    from contractedge.source.snapshot import compute_snapshot
    from tests.test_repair_execution import bound_plan
    (tmp_path/'templates').mkdir();(tmp_path/'templates/serviceaccount.yaml').write_text('name: default\n')
    (tmp_path/'reader.go').write_text(SOURCE)
    snapshot=compute_snapshot(tmp_path);plan=bound_plan(tmp_path)
    plan['operations'][0]['target'].update(path='reader.go',content_digest=snapshot.file_digests['reader.go'],locator=dict(start_line=2,end_line=7))
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['reader.go']},lambda s:len(s.split()),
        dict(model='fixture',helper_preconditions_enabled=True,max_context_tokens=50000,execution_max_output_tokens=1024),requirement='Keep readers ready')
    body=json.loads(request.messages[1]['content']);row=body['source_reader_reference_contexts'][0]
    assert row['variable']=='active'
    assert row['source_target']==dict(path='reader.go',snapshot_id=snapshot.snapshot_id,content_digest=snapshot.file_digests['reader.go'])
    assert 'source_evidence_ref' not in row and 'candidate_id' not in row
    assert not body['read_only_source_support']
    assert len(json.dumps(body['source_reader_reference_contexts'],ensure_ascii=False).encode())<=8000


def test_unmarked_historical_source_does_not_retroactively_create_ready_requirements():
    assert required_source_prerequisites(SimpleNamespace(candidates=units()))==[]
