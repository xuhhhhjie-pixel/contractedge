"""Structured source hits must compete before ungrounded lexical anchor bonuses."""
import hashlib
import pytest
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_gap_extension_units
from contractedge.retrieval.candidates import _gap_local_context, build_candidate_set, extend_once, requires_gap_delivery
from contractedge.retrieval.gap_delivery import locate_gap_roles


def unit(text='func Bind(config *Args, spec *Spec) { config.PlainHTTP = spec.PlainHTTP }',path='pkg/controller/release.go'):
    result=parse_source_units(path,text+'\n','snapshot-current')[0]
    return {**result,'evidence_gap_refs':['G1']}


def gap(**hint):
    return {'id':'G1','summary':'Find the missing configuration handoff',
            'locator_hint':{'path':'pkg/controller/release.go','symbol':'Endpoint','search_term':'PlainHTTP',**hint}}


def is_context(item,gaps=None):
    context,_=_gap_local_context([item],set(),gaps or [gap()])
    return item['id'] in context


def test_exact_same_gap_field_handoff_becomes_soft_context_root():
    bridge=unit()
    assert is_context(bridge)
    assert not requires_gap_delivery(bridge)
    assert not bridge.get('required_for_gap_delivery')


@pytest.mark.parametrize('hint',[
    {'path':'pkg/controller/release.go','symbol':'Bind'},
    {'path':'pkg/controller','search_term':'PlainHTTP'},
    {'path':'pkg/controller/','search_term':'PlainHTTP'},
])
def test_precise_file_or_directory_query_can_locate_complete_source(hint):
    item=unit()
    gaps=[{'id':'G1','summary':'unrelated prose','locator_hint':hint}]
    assert is_context(item,gaps)


@pytest.mark.parametrize('hint',[
    {'path':'pkg/controller'},
    {'path':'pkg/controller','symbol':'Other'},
    {'path':'pkg/controller-other','search_term':'PlainHTTP'},
    {'path':'pkg/controller/other.go','search_term':'PlainHTTP'},
    {'path':'../pkg/controller','search_term':'PlainHTTP'},
    {'path':'/pkg/controller','search_term':'PlainHTTP'},
    {'symbol':'Bind'},
    {'search_term':'PlainHTTP'},
])
def test_path_only_prose_or_wrong_scope_cannot_create_query_root(hint):
    assert not is_context(unit(),[{'id':'G1','summary':'Bind PlainHTTP configuration','locator_hint':hint}])


def test_gap_refs_and_path_query_cannot_be_borrowed_between_gaps():
    item=unit()
    assert not is_context(item,[gap(path='pkg/other/release.go'),
        {'id':'G2','summary':'Find field','locator_hint':{'path':item['path'],'search_term':'PlainHTTP'}}])
    assert not is_context({**item,'evidence_gap_refs':['G2']})


@pytest.mark.parametrize('text',[
    'func Bind() { /* PlainHTTP */ consume() }',
    'func Bind() { // PlainHTTP\n consume() }',
    'func Bind() { value := "PlainHTTP"; consume(value) }',
    'func Bind() { value := `PlainHTTP`; consume(value) }',
])
def test_comment_or_string_only_match_is_not_source_code_query_root(text):
    assert not is_context(unit(text))


@pytest.mark.parametrize('text',[
    'type Client interface { Endpoint() }',
    'func Caller() { Endpoint() }',
    'func OtherEndpoint() {}',
])
def test_symbol_request_matches_declaration_name_not_method_signature_or_call(text):
    item=unit(text)
    assert not is_context(item,[{'id':'G1','summary':'Endpoint','locator_hint':{'path':item['path'],'symbol':'Endpoint'}}])


@pytest.mark.parametrize('mutation',[
    {'truncated':True}, {'content_digest':'wrong'}, {'origin_kind':'DECLARED_ARTIFACT'},
    {'path':'tests/controller_test.go'}, {'evidence_gap_refs':[]},
])
def test_incomplete_or_wrong_provenance_does_not_become_query_root(mutation):
    item={**unit(),**mutation}
    assert not is_context(item)


def test_locator_truncation_flag_also_prevents_promotion():
    item=unit();item['locator']={**item['locator'],'truncated':True}
    assert not is_context(item)


def test_comment_mask_keeps_actual_code_match_on_same_line():
    item=unit('func Bind(c *Args) { /* PlainHTTP explanation */ c.PlainHTTP = true }')
    assert is_context(item)


def test_same_capacity_selects_queried_config_body_without_new_required_endpoint(tmp_path):
    source=('package control\n'
            'type Client interface { Endpoint() }\n'
            'func Endpoint() {}\n'
            'func Bind(config *Args, spec *Spec) { config.PlainHTTP = spec.PlainHTTP }\n')
    (tmp_path/'control.go').write_text(source)
    snapshot=compute_snapshot(tmp_path)
    seeds=parse_source_units('control.go',source,snapshot.snapshot_id)
    gaps=[{'id':'G1','summary':'Find configuration handoff',
           'locator_hint':{'path':'control.go','symbol':'Endpoint','search_term':'PlainHTTP'}}]
    found,_=extract_gap_extension_units(snapshot,['control.go'],seeds,gaps)
    roles,_=locate_gap_roles(snapshot,gaps,dict(seed_units=seeds,seed_declarations=seeds,max_new_files=4))
    initial=build_candidate_set('PlainHTTP configuration handoff',None,[],[],top_n=2,max_context_tokens=10000,token_counter=len)
    result=extend_once(initial,gaps,[*found,*roles],max_context_tokens=10000,token_counter=len)
    bridge=next(u for u in result.candidates if u.get('symbol')=='Bind')
    assert len(result.selected_ids)==2
    assert {'Endpoint','Bind'}=={u.get('symbol') for u in result.candidates}
    assert bridge['id'] not in result.required_endpoint_ids
    assert not requires_gap_delivery(bridge)
    assert 'config.PlainHTTP = spec.PlainHTTP' in bridge['text']
    interface=next(u for u in roles if u.get('symbol')=='Client')
    assert interface['anchor_priority']==0 and interface['optional_gap_context'] is True


def test_summary_named_function_loses_anchor_boost_but_explicit_symbol_keeps_it(tmp_path):
    (tmp_path/'api').mkdir();(tmp_path/'api/helpers.go').write_text('package api\nfunc Not() {}\n')
    (tmp_path/'main.go').write_text('package main\nfunc Endpoint() {}\n')
    snapshot=compute_snapshot(tmp_path)
    seeds=parse_source_units('api/helpers.go',(tmp_path/'api/helpers.go').read_text(),snapshot.snapshot_id)
    prose=[{'id':'G1','summary':'Handoff is not established','locator_hint':{'path':'main.go','symbol':'Endpoint'}}]
    units,_=locate_gap_roles(snapshot,prose,dict(seed_declarations=seeds,max_new_files=4))
    named=next(u for u in units if u.get('symbol')=='Not')
    assert not requires_gap_delivery(named) and named['anchor_priority']==0
    explicit=[{'id':'G1','summary':'Locate explicit function','locator_hint':{'symbol':'Not'}}]
    units,_=locate_gap_roles(snapshot,explicit,dict(seed_declarations=seeds,max_new_files=4))
    named=next(u for u in units if u.get('symbol')=='Not')
    assert requires_gap_delivery(named) and named['anchor_priority']==4


@pytest.mark.parametrize('query,text',[
    ('Not','func Bind() { notify() }'),
    ('PlainHTTP','func Bind(c *Args) { c.PlainHTTPEnabled = true }'),
    ('config.PlainHTTP','func Bind(config *Args) { config.PlainHTTPEnabled = true }'),
])
def test_scan_substring_is_not_a_precise_identifier_context_root(query,text):
    assert not is_context(unit(text),[gap(search_term=query)])


def test_case_insensitive_exact_identifier_search_still_matches():
    assert is_context(unit(),[gap(search_term='plainhttp')])


def test_new_query_root_does_not_expand_to_languages_without_go_binding_parser():
    item=unit('function Bind() { config.PlainHTTP = true; }',path='pkg/controller/release.ts')
    assert not is_context(item,[gap(path=item['path'])])


def test_symbol_metadata_cannot_replace_actual_declaration_name():
    item=unit('func Other() {}');item['symbol']='Bind'
    assert not is_context(item,[{'id':'G1','summary':'explicit symbol','locator_hint':{'path':item['path'],'symbol':'Bind'}}])
