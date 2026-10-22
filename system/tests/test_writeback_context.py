import json

from contractedge.execution.topomen import build_execution_request
from contractedge.source.snapshot import compute_snapshot
from tests.test_repair_execution import bound_plan


def test_filtered_collection_writeback_is_source_grounded_and_not_an_edit_grant(tmp_path):
    from contractedge.execution.writeback_context import writeback_context
    text='''package pipeline
func transform(all []Node, narrow bool) []Node {
    selected := Select(all)
    output := Run(selected)
    if narrow {
        all = Merge(output.Nodes, selected, all)
    } else {
        all = output.Nodes
    }
    return all
}
'''
    op={'target':{'path':'pipeline.go','locator':{'start_line':2,'end_line':11}}}
    result=writeback_context([{'path':'pipeline.go','content':text}],[op],'snapshot')
    assert len(result['sites'])==1
    row=result['sites'][0]
    assert row['assignment']=='all = output.Nodes'
    assert 'all = Merge' in row['excerpt'] and 'else' in row['excerpt']
    assert row['snapshot_id']=='snapshot' and row['locator']['path']=='pipeline.go'
    assert row['locator']['start_line']<=8<=row['locator']['end_line']
    assert 'not a dataflow proof' in result['boundary']
    assert 'retention' in result['review_question']
    assert writeback_context([{'path':'pipeline.go','content':text}],[op],'snapshot',max_bytes=10)['sites']==[]


def test_comments_unselected_functions_and_python_are_not_go_writeback_facts():
    from contractedge.execution.writeback_context import writeback_context
    text='''package p
func active() {
    // all = output.Nodes
    message := "all = output.Nodes"
    _ = message
}
func unrelated() { all = output.Nodes }
'''
    op={'target':{'path':'p.go','locator':{'start_line':2,'end_line':6}}}
    assert not writeback_context([{'path':'p.go','content':text}],[op],'S')['sites']
    assert not writeback_context([{'path':'p.py','content':'all = output.nodes'}],[op],'S')['sites']


def test_writeback_hint_reaches_actual_execution_request_without_expanding_targets(tmp_path):
    (tmp_path/'templates').mkdir();(tmp_path/'templates/serviceaccount.yaml').write_text('name: default\n')
    (tmp_path/'p.go').write_text('package p\nfunc update() {\n    all = output.Nodes\n}\n')
    plan=bound_plan(tmp_path);snap=compute_snapshot(tmp_path)
    plan['operations'][0]['target'].update(path='p.go',content_digest=snap.file_digests['p.go'],locator={'start_line':2,'end_line':4})
    config=dict(model='test',provider='openai_compatible',max_context_tokens=50000,
                execution_max_output_tokens=1000,writeback_context_enabled=True)
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['p.go']},len,config,requirement='Retain unrelated inputs')
    body=json.loads(request.messages[1]['content'])
    assert body['collection_writeback_context']['sites'][0]['snapshot_id']==snap.snapshot_id
    assert [x['path'] for x in body['current_target_files']]==['p.go']
