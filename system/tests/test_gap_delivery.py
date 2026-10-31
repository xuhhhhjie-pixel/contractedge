import hashlib
import importlib.util
import json
from contractedge.source.snapshot import compute_snapshot
from contractedge.retrieval.candidates import build_candidate_set,extend_once


def api():
    assert importlib.util.find_spec('contractedge.retrieval.gap_delivery'), 'Gap role delivery implementation missing'
    from contractedge.retrieval import gap_delivery
    return gap_delivery


def test_exact_missing_handler_reaches_context_among_many_matches(tmp_path):
    (tmp_path/'read.go').write_text('package api\nfunc getGroupsHandler() { reader.Groups() }\nfunc status() { other() }\n')
    (tmp_path/'write.go').write_text('package state\nfunc Reload() { state.Reset() }\n')
    snapshot=compute_snapshot(tmp_path)
    gaps=[dict(id='G1',summary='Read groups at API handler',locator_hint={'path':'read.go','search_term':'groups'}),
          dict(id='G2',summary='State replacement during reload',locator_hint={'path':'write.go','symbol':'Reload'})]
    units,audit=api().locate_gap_roles(snapshot,gaps,{'max_new_files':4,'seed_units':[]})
    docs=[dict(id='D'+str(i),path=f'doc{i}.md',text='groups reload '*30,locator={'start_line':1,'end_line':1},snapshot_id=snapshot.snapshot_id,anchor_priority=5) for i in range(12)]
    candidates=build_candidate_set('groups reload',None,docs,[],top_n=4,max_context_tokens=12000,token_counter=len)
    extended=extend_once(candidates,gaps,units,max_context_tokens=12000,token_counter=len)
    assert {'read.go','write.go'} <= {u['path'] for u in extended.candidates}
    assert audit['inspected_files']<=4


def test_delivery_checks_decoded_catalog_and_detects_dropped_body():
    unit=dict(id='C1',text='quote = "汉字"\n',content_digest=hashlib.sha256('quote = "汉字"\n'.encode()).hexdigest(),gap_role_keys=['G1:anchor'])
    request=json.dumps({'evidence_catalog':[dict(id='SRC-C1',excerpt=unit['text'],content_digest=unit['content_digest'])]})
    assert api().audit_gap_delivery([], [unit], request)['status']=='DELIVERED'
    messages=json.dumps([{'role':'user','content':request}])
    assert api().audit_gap_delivery([], [unit], messages)['status']=='DELIVERED'
    schema_request=json.dumps({'output_schema':{'properties':{'role':{'type':'string'}}},
                               'evidence_catalog':json.loads(request)['evidence_catalog']})
    assert api().audit_gap_delivery([], [unit], schema_request)['status']=='DELIVERED'
    clipped=json.dumps({'evidence_catalog':[dict(id='SRC-C1',excerpt='prefix',content_digest=unit['content_digest'])]})
    assert api().audit_gap_delivery([], [unit], clipped)['status']=='INCOMPLETE'


def test_gap_receipt_distinguishes_found_but_not_selected_from_not_found():
    required = 'C-required'
    found = api().audit_gap_delivery(
        [{'required_unit_ids': [required], 'available_unit_ids': [required]}],
        [], json.dumps({'evidence_catalog': []}))
    assert found['status'] == 'INCOMPLETE'
    assert found['records'] == [{
        'unit_id': required, 'roles': [], 'found': True, 'selected': False,
        'delivered': False, 'cited': None, 'drop_reason': 'FOUND_NOT_SELECTED'}]

    missing = api().audit_gap_delivery(
        [{'required_unit_ids': [required], 'available_unit_ids': []}],
        [], json.dumps({'evidence_catalog': []}))
    assert missing['records'][0]['drop_reason'] == 'NOT_FOUND'
    assert missing['failure_codes'] == ['NOT_FOUND']


def test_gap_receipt_marks_selected_clipped_and_model_citation():
    unit = dict(id='C2', text='exact body',
                content_digest=hashlib.sha256(b'exact body').hexdigest(),
                gap_role_keys=['G1:anchor'])
    clipped = api().audit_gap_delivery(
        [{'required_unit_ids': ['C2'], 'available_unit_ids': ['C2']}], [unit],
        json.dumps({'evidence_catalog': [{'id': 'SRC-C2', 'excerpt': 'prefix',
                                          'content_digest': unit['content_digest']}]}))
    assert clipped['records'][0]['drop_reason'] == 'NOT_DELIVERED'

    delivered = api().audit_gap_delivery(
        [{'required_unit_ids': ['C2'], 'available_unit_ids': ['C2']}], [unit],
        json.dumps({'evidence_catalog': [{'id': 'SRC-C2', 'excerpt': unit['text'],
                                          'content_digest': unit['content_digest']}]}))
    cited = api().annotate_gap_delivery_citations(delivered, {'evidence_refs': ['SRC-C2']})
    assert cited['model_citation_status'] == 'CITED'
    assert cited['records'][0]['cited'] is True
    not_cited = api().annotate_gap_delivery_citations(delivered, {'evidence_refs': []})
    assert not_cited['model_citation_status'] == 'NOT_CITED'
    assert not_cited['records'][0]['cited'] is False


def test_config_template_gap_does_not_require_an_api_or_writer(tmp_path):
    (tmp_path/'template.yaml').write_text('kind: Job\nmetadata:\n  name: selected\n')
    units,audit=api().locate_gap_roles(compute_snapshot(tmp_path),[dict(id='G1',summary='Find Job template',locator_hint={'path':'template.yaml','search_term':'Job'})],{})
    assert units and all(not u.get('verified_causal_edge') for u in units)
    assert not any('API' in role for u in units for role in u['gap_role_keys'])


def test_found_but_budget_dropped_endpoint_remains_required():
    a=dict(id='A',path='a.go',text='read()',content_digest=hashlib.sha256(b'read()').hexdigest(),snapshot_id='S0',locator={'start_line':1,'end_line':1},gap_role_keys=['G1:anchor'],evidence_gap_refs=['G1'])
    b=dict(a,id='B',path='b.go',text='write()'*100,gap_role_keys=['G2:anchor'],evidence_gap_refs=['G2'])
    b['content_digest']=hashlib.sha256(b['text'].encode()).hexdigest()
    initial=build_candidate_set('read',None,[],[],top_n=12,max_context_tokens=100,token_counter=len)
    gaps=[dict(id='G1',locator_hint={'path':'a.go','symbol':'read'}),dict(id='G2',locator_hint={'path':'b.go','symbol':'write'})]
    selected=extend_once(initial,gaps,[a,b],max_context_tokens=100,token_counter=len)
    assert 'B' in selected.required_endpoint_ids
    request=json.dumps({'evidence_catalog':[dict(id='SRC-A',excerpt=a['text'],content_digest=a['content_digest'])]})
    receipt=api().audit_gap_delivery([{'required_unit_ids':selected.required_endpoint_ids}],list(selected.candidates),request)
    assert receipt['status']=='INCOMPLETE' and receipt['missing_required_ids']==['B']


def test_exact_gap_in_long_declaration_preserves_late_state_replacement(tmp_path):
    text=('package p\nfunc run() {\n'+' println("setup")\n'*190+
          ' coordinator.Subscribe(func() { state.Stop(); state = NewState() })\n}\n')
    (tmp_path/'main.go').write_text(text)
    gap=dict(id='G1',summary='reload subscriber state replacement',
             locator_hint={'path':'main.go','search_term':'Subscribe'})
    units,audit=api().locate_gap_roles(compute_snapshot(tmp_path),[gap],{})
    declarations=[u for u in units if u.get('symbol')=='run']
    assert declarations and 'state = NewState()' in declarations[0]['text']
    assert not declarations[0]['truncated']
    assert audit['inspected_files']==1
    from contractedge.source.parsers import parse_source_units
    prefix=parse_source_units('main.go',text,compute_snapshot(tmp_path).snapshot_id,max_lines=80)[0]
    assert prefix['truncated']
    assert prefix['id']!=declarations[0]['id'], 'Different delivered excerpts need different evidence identities'


def test_gap_reader_receiver_reaches_local_package_without_generic_constructor_decoys(tmp_path):
    (tmp_path/'go.mod').write_text('module example.org/app\n')
    (tmp_path/'main.go').write_text('''package main
import "example.org/app/state"
func run() {
 reader := state.NewReader()
 serve(reader.Groups())
}
''')
    (tmp_path/'state').mkdir()
    (tmp_path/'state/reader.go').write_text('''package state
func NewReader() *Reader { return &Reader{} }
func (r *Reader) Groups() []Group { return r.groups }
''')
    gap=dict(id='G1',summary='production groups API reader',locator_hint={'path':'main.go','search_term':'groups'})
    units,audit=api().locate_gap_roles(compute_snapshot(tmp_path),[gap],dict(max_new_files=4))
    assert any(u['path']=='state/reader.go' and u.get('symbol')=='Groups' for u in units)
    assert audit['inspected_files']<=4
    assert all(not u.get('verified_causal_edge') for u in units)


def test_reload_gap_carries_named_read_endpoint_from_summary_and_existing_scope(tmp_path):
    from contractedge.source.parsers import parse_source_units
    (tmp_path/'go.mod').write_text('module example.org/app\n')
    (tmp_path/'main.go').write_text('''package main
import "example.org/app/state"
func run() {
 reader := state.NewReader()
 reloadConfig(func() { reader.Reload() })
 observe(reader.Groups())
}
''')
    (tmp_path/'state').mkdir();(tmp_path/'api').mkdir();(tmp_path/'test').mkdir()
    (tmp_path/'state/reader.go').write_text('package state\nfunc (r *Reader) Groups() []Group { return r.groups }\nfunc (r *Reader) Reload() {}\n')
    observer='package api\nfunc getGroupsHandler() {\n'+' prepare()\n'*95+' respond(reader.Groups())\n}\n'
    (tmp_path/'api/groups.go').write_text(observer)
    (tmp_path/'test/groups_test.go').write_text('package test\nfunc getGroupsHandler() { fake() }\n')
    snap=compute_snapshot(tmp_path);seeds=[]
    for path in ['api/groups.go','test/groups_test.go']:
        seeds+=parse_source_units(path,(tmp_path/path).read_text(),snap.snapshot_id)
    gap=dict(id='G1',summary='Find reload path affecting the groups API provider',locator_hint={'path':'main.go','search_term':'reloadConfig'})
    units,audit=api().locate_gap_roles(snap,[gap],dict(max_new_files=4,seed_units=seeds))
    assert any(u['path']=='state/reader.go' and u.get('symbol')=='Groups' for u in units)
    assert any(u['path']=='api/groups.go' and not u['truncated'] and 'respond(reader.Groups())' in u['text'] for u in units)
    assert not any(u['path'].startswith('test/') and u.get('gap_role_keys') for u in units)
    assert audit['inspected_files']<=4


def test_declaration_index_preserves_scoped_handler_omitted_from_intermediate_pool(tmp_path):
    from contractedge.source.units import extract_units
    (tmp_path/'main.go').write_text('package main\nfunc reloadConfig() {}\n')
    (tmp_path/'api').mkdir();(tmp_path/'api/groups.go').write_text('package api\nfunc getGroupsHandler() { reader.Groups() }\n')
    snap=compute_snapshot(tmp_path);index=[]
    seeds=extract_units(snap,{'seed_paths':['main.go','api/groups.go']},max_units=1,
                        max_extension_files=0,declaration_index=index)
    assert len(seeds)==1 and seeds[0]['path']=='main.go'
    assert any(u.get('symbol')=='getGroupsHandler' for u in index)
    assert all('text' not in u for u in index)
    gap=dict(id='G1',summary='reload affecting groups API',locator_hint={'path':'main.go','symbol':'reloadConfig'})
    units,audit=api().locate_gap_roles(snap,[gap],dict(seed_units=seeds,seed_declarations=index,max_new_files=4))
    assert any(u.get('symbol')=='getGroupsHandler' and 'reader.Groups' in u['text'] for u in units)
    assert audit['inspected_files']<=4
    initial=build_candidate_set('reload',None,seeds,[],top_n=2,max_context_tokens=10000,token_counter=len)
    extended=extend_once(initial,[gap],units,max_context_tokens=10000,token_counter=len)
    handler=next(u for u in units if u.get('symbol')=='getGroupsHandler')
    assert handler['id'] in extended.required_endpoint_ids and handler['id'] in extended.selected_ids


def test_character_clipped_text_windows_have_distinct_evidence_identity():
    from contractedge.source.parsers import _unit
    text='state = value; '*100
    short=_unit('S','file.txt',text,0,len(text),'BOUNDED_TEXT_FALLBACK',unit_kind='SOURCE_TEXT',max_chars=40)
    full=_unit('S','file.txt',text,0,len(text),'BOUNDED_TEXT_FALLBACK',unit_kind='SOURCE_TEXT',max_chars=4000)
    assert short['id']!=full['id']


def test_request_trimming_does_not_drop_both_production_branches_with_their_wrapper():
    from contractedge.retrieval.candidates import CandidateSet,trim_context_bundle
    units=[dict(id='wrapper',path='p.go',text='setup '*300,truncated=True),
           dict(id='mutator',path='p.go',text='mutate'),dict(id='validator',path='p.go',text='validate')]
    features={u['id']:{'lexical_sibling_candidate':int(u['id']!='wrapper')} for u in units}
    cs=CandidateSet(tuple(units),tuple(u['id'] for u in units),(),features,
        ({'source':'wrapper','target':'mutator'},{'source':'wrapper','target':'validator'}),2000,4000,12)
    fitted=trim_context_bundle(cs,request_tokens=5000,limit=4000)
    assert {'mutator','validator'}<=set(fitted.selected_ids)
    assert 'wrapper' not in fitted.selected_ids
