"""Direct helpers need their local argument types, without broader authority."""
import hashlib
from pathlib import Path
import pytest
from contractedge.execution.helper_context import local_go_helper_support
from contractedge.source.snapshot import compute_snapshot


def fixture(root, alias='policy', name='Rule', same_package=False):
    (root/'go.mod').write_text('module example.org/typed\n')
    (root/'engine').mkdir(); (root/'api').mkdir()
    target='package main\nimport e "example.org/typed/engine"\nfunc run(){ e.Apply(items, rules) }\n'
    (root/'main.go').write_text(target)
    prefix='' if same_package else alias+'.'
    imports='' if same_package else f'import {alias} "example.org/typed/api"\n'
    helper=f'''package engine
{imports}func Apply(items []string, rules []{prefix}{name}) []string {{
 for _, rule := range rules {{ if !rule.IsZero() {{ return keep(items, rule) }} }}
 return items
}}
func keep(items []string, rule {prefix}{name}) []string {{ return items }}
func unrelated() {{}}
'''
    (root/'engine/apply.go').write_text(helper)
    package='engine' if same_package else 'api'
    (root/package/'types.go').write_text(f'''package {package}
// {name} names the exact item to select; an empty rule is ignored.
type {name} struct {{ Name string }}
func (r {name}) IsZero() bool {{ return r.Name == "" }}
type Other struct {{}}
func (r Other) IsZero() bool {{ return false }}
''')
    return [dict(path='main.go',content=target)], [dict(target=dict(path='main.go',locator=dict(start_line=3,end_line=3)))]


@pytest.mark.parametrize('alias,name,same_package', [('policy','Rule',False),('spec','Choice',False),('','Filter',True)])
def test_helper_input_type_and_mentioned_method_reach_bounded_records(tmp_path,alias,name,same_package):
    targets,ops=fixture(tmp_path,alias,name,same_package)
    snapshot=compute_snapshot(tmp_path);records,audit=local_go_helper_support(snapshot,targets,ops)
    text='\n'.join(r['excerpt'] for r in records)
    assert f'type {name} struct' in text
    assert f'func (r {name}) IsZero()' in text
    assert 'func keep(' in text
    assert 'func unrelated' not in text and 'func (r Other)' not in text
    assert any(r.get('basis')=='LOCAL_HELPER_INPUT_TYPE' for r in audit)
    assert any(r.get('basis')=='LOCAL_HELPER_INPUT_METHOD' for r in audit)
    assert len(records)<=8
    for record in records:
        assert record['snapshot_id']==snapshot.snapshot_id
        assert hashlib.sha256(record['excerpt'].encode()).hexdigest()==record['content_digest']
        lines=(tmp_path/record['locator']['path']).read_text().splitlines(keepends=True)
        assert ''.join(lines[record['locator']['start_line']-1:record['locator']['end_line']])==record['excerpt']


@pytest.mark.parametrize('limit',[dict(max_scan_files=1),dict(max_scan_bytes=200),dict(max_bytes=30)])
def test_input_contracts_share_existing_scan_and_delivery_caps(tmp_path,limit):
    targets,ops=fixture(tmp_path);records,audit=local_go_helper_support(compute_snapshot(tmp_path),targets,ops,**limit)
    assert not any('type Rule struct' in r['excerpt'] for r in records)
    assert sum(len(r['excerpt'].encode()) for r in records)<=limit.get('max_bytes',24000)
    total=next(r for r in audit if r['result']=='HELPER_SCAN_TOTALS')
    assert total['indexed_files']<=limit.get('max_scan_files',128)
    assert total['indexed_bytes']<=limit.get('max_scan_bytes',4*1024*1024)


@pytest.mark.parametrize('mutation',['stale','duplicate_type','external','comment_only','symlink'])
def test_unavailable_or_ambiguous_types_do_not_become_contracts(tmp_path,mutation):
    targets,ops=fixture(tmp_path)
    p=tmp_path/'engine/apply.go'
    if mutation=='external':p.write_text(p.read_text().replace('example.org/typed/api','external.org/api'))
    if mutation=='comment_only':p.write_text('package engine\n// func Apply(rules []policy.Rule) { rules.IsZero() }\nfunc Apply() {}\n')
    if mutation=='duplicate_type':(tmp_path/'api/duplicate.go').write_text('package api\ntype Rule struct {}\n')
    if mutation=='symlink':
        original=tmp_path/'api/types.go';outside=tmp_path/'not-api.go';outside.write_text(original.read_text());original.unlink();original.symlink_to(outside)
    snapshot=compute_snapshot(tmp_path)
    if mutation=='stale':
        p=tmp_path/'api/types.go';p.write_text(p.read_text().replace('Name string','OtherName string'))
    records,_=local_go_helper_support(snapshot,targets,ops)
    assert not any('type Rule struct' in r['excerpt'] for r in records)
    assert not any('func (r Rule) IsZero()' in r['excerpt'] for r in records)


def test_return_only_type_does_not_expand_input_contracts(tmp_path):
    targets,ops=fixture(tmp_path);p=tmp_path/'engine/apply.go'
    p.write_text('package engine\nimport policy "example.org/typed/api"\nfunc Apply(items []string) policy.Rule { return policy.Rule{} }\n')
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert not any('type Rule struct' in r['excerpt'] for r in records)


def test_pointer_receiver_is_resolved_to_exact_input_type(tmp_path):
    targets,ops=fixture(tmp_path);p=tmp_path/'api/types.go';p.write_text(p.read_text().replace('(r Rule)', '(r *Rule)'))
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert any('func (r *Rule) IsZero()' in r['excerpt'] for r in records)


def test_duplicate_method_is_not_chosen_arbitrarily(tmp_path):
    targets,ops=fixture(tmp_path)
    (tmp_path/'api/duplicate.go').write_text('package api\nfunc (r Rule) IsZero() bool { return false }\n')
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert any('type Rule struct' in r['excerpt'] for r in records)
    assert not any('func (r Rule) IsZero()' in r['excerpt'] for r in records)


def test_record_cap_does_not_grow_for_helper_type_dependencies(tmp_path):
    targets,ops=fixture(tmp_path)
    for index in range(12):
        with (tmp_path/'engine/apply.go').open('a') as f:f.write(f'func More{index}(rule policy.Rule) {{}}\n')
    targets[0]['content']=targets[0]['content'].replace('e.Apply(items, rules)',
        '; '.join(['e.Apply(items, rules)',*[f'e.More{i}(rule)' for i in range(12)]]))
    (tmp_path/'main.go').write_text(targets[0]['content'])
    records,audit=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert len(records)==8
    assert any(row['result']=='READ_ONLY_SUPPORT_BUDGET' for row in audit)


def test_final_execution_request_retains_input_contracts_without_new_edit_targets(tmp_path):
    import json
    from contractedge.execution.topomen import build_execution_request
    from tests.test_repair_execution import bound_plan
    targets,ops=fixture(tmp_path);(tmp_path/'templates').mkdir();(tmp_path/'templates/serviceaccount.yaml').write_text('name: default\n')
    plan=bound_plan(tmp_path);snapshot=compute_snapshot(tmp_path)
    plan['operations'][0]['target'].update(path='main.go',content_digest=snapshot.file_digests['main.go'],locator=ops[0]['target']['locator'])
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['main.go']},len,
        dict(model='fixture',provider='openai_compatible',max_context_tokens=50000,execution_max_output_tokens=1024,
             execution_helper_support_enabled=True,max_execution_support_bytes=24000),requirement='Select the explicitly named items')
    payload=json.loads(request.messages[1]['content']);text='\n'.join(r['excerpt'] for r in payload['read_only_source_support'])
    assert 'type Rule struct' in text and 'func (r Rule) IsZero()' in text
    assert {s['path'] for s in payload['current_target_files']}=={'main.go'}
    assert len(payload['read_only_source_support'])<=8


@pytest.mark.parametrize('kind',['type','method','callee'])
def test_truncated_duplicate_still_prevents_arbitrary_contract_choice(tmp_path,kind):
    targets,ops=fixture(tmp_path)
    if kind=='type':
        (tmp_path/'api/platform.go').write_text('package api\ntype Rule struct {\n'+''.join(f' F{i} string\n' for i in range(100))+'}\n')
    elif kind=='method':
        (tmp_path/'api/platform.go').write_text('package api\nfunc (r Rule) IsZero() bool {\n'+' _ = r\n'*100+' return false\n}\n')
    else:
        (tmp_path/'engine/platform.go').write_text('package engine\nfunc keep(items []string) []string {\n'+' _ = items\n'*100+' return items\n}\n')
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    text='\n'.join(r['excerpt'] for r in records)
    if kind=='type':assert 'type Rule struct' not in text
    elif kind=='method':assert 'func (r Rule) IsZero()' not in text
    else:assert 'func keep(' not in text


@pytest.mark.parametrize('binding',['parameter','local','range','var','var_block'])
def test_shadowed_callback_is_not_mistaken_for_package_implementation(tmp_path,binding):
    targets,ops=fixture(tmp_path)
    signature='items []string, rules []policy.Rule'
    if binding=='parameter':
        signature+=', keep func([]string, policy.Rule) []string';body='return keep(items, rules[0])'
    elif binding=='local':
        body='keep := otherCallback; return keep(items, rules[0])'
    elif binding=='var':
        body='var other, keep func([]string, policy.Rule) []string; _ = other; return keep(items, rules[0])'
    elif binding=='var_block':
        body='var (\n other, keep func([]string, policy.Rule) []string\n); _ = other; return keep(items, rules[0])'
    else:
        signature+=', callbacks []func([]string, policy.Rule) []string';body='for _, keep := range callbacks { return keep(items, rules[0]) }; return items'
    (tmp_path/'engine/apply.go').write_text('package engine\nimport policy "example.org/typed/api"\n'+
        f'func Apply({signature}) []string {{ {body} }}\n'+
        'func keep(items []string, rule policy.Rule) []string { return items }\n')
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert not any(r['excerpt'].lstrip().startswith('func keep(') for r in records)
