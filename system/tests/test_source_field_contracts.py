import hashlib
from types import SimpleNamespace

from contractedge.diagnosis.evidence import field_contract_contexts
from contractedge.orchestration.runner import required_source_prerequisites
from contractedge.source.parsers import parse_source_units


SOURCE='''package api

type Request struct {
    // A request token is used during creation.
    // During refresh this field is ignored; use the refresh operation.
    Token *string `json:"token,omitempty"`
    // Optional display name.
    Name *string `json:"name,omitempty"`
}
'''


def units(text=SOURCE):
    return parse_source_units('api/request.go',text,'snapshot',max_lines=80)


def test_field_docs_are_exact_local_source_with_position_and_digest():
    records=field_contract_contexts(units(),'refresh the token')
    assert len(records)==1
    r=records[0];assert r['field_name']=='Token' and r['json_key']=='token'
    assert 'ignored' in r['excerpt'] and 'Optional display' not in r['excerpt']
    lines=SOURCE.splitlines(keepends=True)
    assert ''.join(lines[r['locator']['start_line']-1:r['locator']['end_line']])==r['excerpt']
    assert r['excerpt_digest']==hashlib.sha256(r['excerpt'].encode()).hexdigest()
    assert r['source_evidence_ref']=='SRC-'+units()[0]['id']
    assert 'Not runtime evidence' in r['interpretation']


def test_no_query_match_no_docs_or_non_go_does_not_invent_contract():
    assert field_contract_contexts(units(),'refresh password')==[]
    no_docs=SOURCE.replace('    // A request token is used during creation.\n','').replace('    // During refresh this field is ignored; use the refresh operation.\n','')
    assert field_contract_contexts(units(no_docs),'refresh token')==[]
    assert field_contract_contexts([{**u,'path':'example.py'} for u in units()],'refresh token')==[]


def test_prerequisite_uses_current_quote_and_does_not_trust_saved_excerpt():
    current=[{**u,'source_contract_fields':['Token']} for u in units()]
    rows=required_source_prerequisites(SimpleNamespace(candidates=current))
    assert len(rows)==1 and 'ignored' in rows[0]['reason']
    assert 'Refresh always applies token' not in rows[0]['reason']
    changed=[{**u,'source_contract_fields':['Token']} for u in units(SOURCE.replace('is ignored','is unchanged'))]
    assert required_source_prerequisites(SimpleNamespace(candidates=changed))!=rows
    assert required_source_prerequisites(SimpleNamespace(candidates=units()))==[]


def test_windows_are_bounded_deduplicated_without_partial_json():
    assert len(field_contract_contexts(units()+units(),'token name',max_records=1))==1
    assert field_contract_contexts(units(),'token',max_bytes=5)==[]
    assert len(field_contract_contexts(units()+units(),'token'))==1


def test_real_front_ready_and_execution_builder_keep_uncited_contract_source(tmp_path):
    import json
    from dataclasses import replace
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.runner import run_front_half
    from contractedge.orchestration.repair import validate_ready_trace
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.contracts.validation import canonical_digest, parse_method_input
    from contractedge.changeset.topomen import translate_for_topomen
    from contractedge.execution.topomen import build_execution_request
    from tests.test_orchestration import PipelineFixtureProvider
    from tests.test_planning_evidence_extension import front_fixture
    value,template=front_fixture(tmp_path)
    (tmp_path/'api').mkdir();(tmp_path/'api/request.go').write_text(SOURCE.replace('Token','Selected').replace('token','selected'))
    value['scope_hints'].append(dict(kind='path',value='api/request.go',raw_text='api/request.go',origin_ref='public.scope'))
    value['initial_snapshot_id']=compute_snapshot(tmp_path).snapshot_id;value['public_evidence'][0]['snapshot_id']=value['initial_snapshot_id']
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    class Provider(PipelineFixtureProvider):
        def __init__(self):super().__init__();self.requests=[]
        def complete_json(self,request):
            self.requests.append(request);body=json.loads(request.messages[1]['content'])
            if request.prompt_version.startswith('diagnosis.'):
                assert body['source_field_contract_contexts']
                # Fixture model chooses the YAML repair target from this same
                # unchanged evidence pool; the Go comment remains uncited.
                entries=body['evidence_catalog'];body['evidence_catalog']=sorted(entries,key=lambda e:e.get('locator',{}).get('path')!='templates/serviceaccount.yaml')
                fixture_request=replace(request,messages=(request.messages[0],{**request.messages[1],'content':json.dumps(body)}))
                return super().complete_json(fixture_request)
            assert len(body['source_prerequisites'])==1
            assert 'ignored' in body['source_prerequisites'][0]['reason']
            response=super().complete_json(request);plan=json.loads(response.content)
            plan['precondition_review']=[dict(source_evidence_ref=r['source_evidence_ref'],condition='This Go field is not consumed by the Helm template.',
                disposition='NOT_APPLICABLE',verification_refs=[],open_obligation_refs=[],rationale='The source-local comment describes a separate unused API; no propagation inference is made from it.') for r in body['source_prerequisites']]
            return ModelResponse(json.dumps(plan),{})
    p=Provider();p._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(template.read_bytes()).hexdigest()}
    config=dict(model={'model':'fixture'},max_context_tokens=24000,max_output_tokens=1024,max_total_tokens=100000,gap_role_delivery_enabled=True)
    trace=run_front_half(value,tmp_path,p,config,lambda s:len(s.split())).to_wire()
    assert trace['status']=='READY',trace['errors']
    packet,context=validate_ready_trace(trace,parse_method_input(value),tmp_path)
    support=context.retrieval_config['necessary_source_support']
    assert any(r['locator']['path']=='api/request.go' and r['delivery_required'] for r in support)
    # Only the model's YAML source is a violation reference; independent source
    # prerequisites still deliver the actual Go contract to the real consumer.
    plan=translate_for_topomen(trace['changeset'],'contractedge.topomen_plan.v1',diagnosis=trace['diagnosis'],source_support=support)
    request=build_execution_request(plan,tmp_path,dict(allowed_paths=['templates/**'],forbidden_paths=[]),lambda s:len(s.split()),
        dict(model='fixture',max_context_tokens=24000,execution_max_output_tokens=1024),requirement=value['requirement_text'])
    payload='\n'.join(m['content'] for m in request.messages)
    assert 'During refresh this field is ignored' in payload
    assert compute_snapshot(tmp_path).snapshot_id==value['initial_snapshot_id']


def test_multiple_source_roles_can_be_reviewed_without_an_impossible_schema():
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from contractedge import __file__ as package
    schema=json.loads((Path(package).parent/'contracts/schema/semantic_changeset.v1_1.json').read_text())
    review=schema['properties']['precondition_review']
    rows=[dict(source_evidence_ref=f'SRC-{i}',condition='Review actual source prerequisite',disposition='NOT_APPLICABLE',
        verification_refs=[],open_obligation_refs=[],rationale='Explicit independent source-role applicability review') for i in range(7)]
    Draft202012Validator(review).validate(rows)
