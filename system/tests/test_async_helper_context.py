from contractedge.execution.helper_context import local_go_helper_support
from contractedge.source.snapshot import compute_snapshot


def fixture(root):
    (root/'go.mod').write_text('module example.org/demo\n')
    (root/'aaa').mkdir(); (root/'worker').mkdir()
    (root/'aaa/helpers.go').write_text('package aaa\n'+'\n'.join(f'func H{i}(){{}}' for i in range(10)))
    (root/'worker/lifecycle.go').write_text('package worker\n'
        'func NewWorker() *Worker { return &Worker{} }\n'
        'func (w *Other) Run() { wrongReceiver() }\n'
        'func (w *Worker) Run() {\n'+''.join('// lifecycle\n' for _ in range(85))+
        ' initializeState()\n}\n'
        'func (w *Worker) Groups() { readState() }\n')
    text='package main\nimport a "example.org/demo/aaa"\nimport w "example.org/demo/worker"\nfunc main(){\n'
    text+='\n'.join(f'a.H{i}()' for i in range(10))+'\nservice := w.NewWorker()\ngo service.Run()\nservice.Groups()\n}\n'
    (root/'main.go').write_text(text)
    return [dict(path='main.go',content=text)],[dict(target=dict(path='main.go',locator=dict(start_line=4,end_line=20)))]


def test_async_constructor_body_and_reader_precede_unrelated_alphabetic_helpers(tmp_path):
    targets,ops=fixture(tmp_path)
    records,audit=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    joined='\n'.join(r['excerpt'] for r in records)
    assert len(records)<=8 and sum(len(r['excerpt'].encode()) for r in records)<=24000
    assert 'initializeState()' in joined and 'func NewWorker()' in joined and 'readState()' in joined
    assert 'wrongReceiver()' not in joined
    assert records[0]['locator']['path']=='worker/lifecycle.go'
    assert any(r.get('basis')=='LOCAL_CONSTRUCTOR_ASYNC_RECEIVER' for r in audit)


def test_ambiguous_constructor_receiver_is_not_resolved_as_a_verified_target(tmp_path):
    targets,ops=fixture(tmp_path)
    targets[0]['content']=targets[0]['content'].replace('go service.Run()',
        'service = w.NewOther()\ngo service.Run()')
    (tmp_path/'main.go').write_text(targets[0]['content'])
    records,audit=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert not any('initializeState()' in r['excerpt'] for r in records)
    assert any(r['result']=='AMBIGUOUS_LOCAL_CONSTRUCTION' for r in audit)


def test_async_bodies_do_not_exceed_original_support_budget(tmp_path):
    targets,ops=fixture(tmp_path)
    records,audit=local_go_helper_support(compute_snapshot(tmp_path),targets,ops,max_bytes=80)
    assert sum(len(r['excerpt'].encode()) for r in records)<=80
    assert not any('initializeState()' in r['excerpt'] for r in records)


def test_commented_or_quoted_async_example_does_not_reprioritize_helpers(tmp_path):
    targets,ops=fixture(tmp_path)
    targets[0]['content']=targets[0]['content'].replace('go service.Run()', '// go service.Run()')
    (tmp_path/'main.go').write_text(targets[0]['content'])
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert records[0]['locator']['path']=='aaa/helpers.go'


def test_unresolved_receiver_reassignment_never_chooses_async_body(tmp_path):
    targets,ops=fixture(tmp_path)
    targets[0]['content']=targets[0]['content'].replace('go service.Run()',
        'service = otherService\ngo service.Run()')
    (tmp_path/'main.go').write_text(targets[0]['content'])
    records,audit=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert not any('initializeState()' in r['excerpt'] for r in records)
    assert any(r['result']=='ASYNC_RECEIVER_REASSIGNMENT' for r in audit)


def test_constructor_return_type_and_receiver_can_be_declared_in_separate_files(tmp_path):
    targets,ops=fixture(tmp_path)
    p=tmp_path/'worker/lifecycle.go';p.write_text(p.read_text().replace('func NewWorker() *Worker { return &Worker{} }\n',''))
    (tmp_path/'worker/z_constructor.go').write_text('package worker\nfunc NewWorker() *Worker { return &Worker{} }\n')
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert any('initializeState()' in r['excerpt'] for r in records)


def test_one_same_receiver_hop_supplies_initialization_implementation(tmp_path):
    targets,ops=fixture(tmp_path)
    p=tmp_path/'worker/lifecycle.go';text=p.read_text().replace(' initializeState()', ' w.process()')
    text+='func (w *Worker) process() { initializeState(); w.deep() }\n'
    text+='func (w *Other) process() { wrongProcess() }\n'
    text+='func (w *Worker) deep() { outOfScopeTransitiveCall() }\n'
    p.write_text(text)
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    joined='\n'.join(r['excerpt'] for r in records)
    assert 'initializeState()' in joined and 'wrongProcess()' not in joined
    assert 'outOfScopeTransitiveCall()' not in joined
