"""Review regressions for complete source spans and conservative lexical binding."""
import json
import pytest
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot
from contractedge.diagnosis.evidence import reader_reference_contexts,build_evidence_catalog
from contractedge.contracts.validation import parse_method_input
from contractedge.retrieval.candidates import CandidateSet
from contractedge.execution.topomen import build_execution_request
from tests.test_contracts import valid_method_input
from tests.test_repair_execution import bound_plan

TEXT='''package p
func serve() {
    var active *Engine
    view := func() int { return active.Count() }
    active = makeEngine()
    consume()
    _ = view
}
'''

@pytest.mark.parametrize('clipped',[True,False])
def test_read_only_executor_requires_complete_declaration_after_catalog_projection(tmp_path,clipped):
    (tmp_path/'templates').mkdir();(tmp_path/'templates/serviceaccount.yaml').write_text('name: default\n')
    (tmp_path/'reader.go').write_text(TEXT)
    snapshot=compute_snapshot(tmp_path)
    unit=next(u for u in parse_source_units('reader.go',TEXT,snapshot.snapshot_id,max_lines=4 if clipped else 80) if u.get('symbol')=='serve')
    assert unit['truncated'] is clipped
    candidates=CandidateSet((unit,),(unit['id'],),(),{},(),0,50000,12)
    record=build_evidence_catalog(parse_method_input(valid_method_input()),candidates,snapshot.snapshot_id)[0]
    plan=bound_plan(tmp_path);plan['coordination_context']={'source_support':[{**record,'delivery_required':True}]}
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda s:len(s.split()),
        dict(model='fixture',helper_preconditions_enabled=True,max_context_tokens=50000,execution_max_output_tokens=1024),requirement='Keep readers ready')
    body=json.loads(request.messages[1]['content'])
    assert any(r['id']==record['id'] for r in body['read_only_source_support'])
    assert bool(body['source_reader_reference_contexts']) is (not clipped)

@pytest.mark.parametrize('shadow',[
    '    {\n        active, err := makeEngine(), 0\n        active = otherEngine()\n        _, _ = active, err\n    }\n',
    '    {\n        var (\n            active *Engine\n        )\n        active = otherEngine()\n    }\n',
])
def test_tuple_or_grouped_var_shadow_is_not_the_reader_reference_write(shadow):
    text=TEXT.replace('    active = makeEngine()\n',shadow)
    units=parse_source_units('reader.go',text,'S',max_lines=80)
    assert reader_reference_contexts(units)==[]
