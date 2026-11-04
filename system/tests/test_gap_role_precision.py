"""Source spans preserve alias semantics while lexical hints stay optional."""
import hashlib
import json
import pytest

from contractedge.retrieval.candidates import build_candidate_set, extend_once, requires_gap_delivery
from contractedge.retrieval.gap_delivery import locate_gap_roles, audit_gap_delivery
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def locate(root, files, gaps, **limits):
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    snap = compute_snapshot(root)
    seeds = [u for path, text in files.items()
             for u in parse_source_units(path, text, snap.snapshot_id)]
    found, audit = locate_gap_roles(snap, gaps,
        dict(seed_units=seeds, seed_declarations=seeds, max_new_files=8, **limits))
    return snap, seeds, found, audit


def test_receiver_exact_struct_and_separate_real_import_are_both_delivered(tmp_path):
    source = ('package service\nimport (\n sdk "example.org/locked/client"\n)\n'
              + '// unrelated prefix\n' * 80
              + 'type Manager struct { client *sdk.Client }\n'
              + 'func (m *Manager) Send() { m.client.Send() }\n')
    snap, seeds, units, _ = locate(tmp_path, {'service/owner.go': source},
        [dict(id='G1', summary='Locate Manager.Send',
              locator_hint={'path':'service/owner.go','symbol':'Send'})])
    owner = next(u for u in units if u.get('symbol') == 'Manager')
    assert owner['text'] == 'type Manager struct { client *sdk.Client }\n'
    assert owner['locator']['start_line'] == 85
    imports = [u for u in units if u.get('kind') == 'SOURCE_TEXT' and 'import (' in u['text']]
    assert len(imports) == 1
    binding = imports[0]
    assert binding['text'] == 'import (\n sdk "example.org/locked/client"\n)'
    assert not binding.get('symbol') and not binding['declared_symbols']
    for u in (owner, binding):
        assert requires_gap_delivery(u)
        assert source[u['locator']['start_char']:u['locator']['end_char']] == u['text']
        assert u['content_digest'] == hashlib.sha256(u['text'].encode()).hexdigest()
        assert u['snapshot_id'] == snap.snapshot_id
    initial = build_candidate_set('Send', None, [], [], top_n=12,
        max_context_tokens=12000, token_counter=len)
    result = extend_once(initial, [dict(id='G1', summary='Send', locator_hint={'path':'service/owner.go','symbol':'Send'})], units,
        max_context_tokens=12000, token_counter=len)
    assert {owner['id'], binding['id']} <= set(result.selected_ids)
    assert {owner['id'], binding['id']} <= set(result.required_endpoint_ids)
    # A selected owner alone cannot prove that the required import was delivered.
    receipt = audit_gap_delivery([{'required_unit_ids': result.required_endpoint_ids}],
        [u for u in result.candidates if u['id'] != binding['id']], json.dumps({'evidence_catalog': []}))
    assert receipt['status'] == 'INCOMPLETE' and binding['id'] in receipt['missing_required_ids']


def test_receiver_without_qualified_type_does_not_spend_import_slot(tmp_path):
    source = 'package p\nimport "fmt"\ntype Worker struct { count int }\nfunc (w *Worker) Run() {}\n'
    _, _, units, _ = locate(tmp_path, {'owner.go': source},
        [dict(id='G1',summary='Run',locator_hint={'path':'owner.go','symbol':'Run'})])
    owner = next(u for u in units if u.get('symbol') == 'Worker')
    assert owner['text'] == 'type Worker struct { count int }\n'
    assert not any(u.get('kind') == 'SOURCE_TEXT' and 'import ' in u['text'] for u in units)


def test_import_block_is_shared_between_two_required_receivers(tmp_path):
    source = ('package p\nimport sdk "example.org/sdk"\n'
              'type A struct { client *sdk.Client }\n'
              'type B struct { client *sdk.Client }\n'
              'func (a *A) First() {}\nfunc (b *B) Second() {}\n')
    _, _, units, _ = locate(tmp_path, {'owner.go': source}, [
        dict(id='G1',summary='First',locator_hint={'path':'owner.go','symbol':'First'}),
        dict(id='G2',summary='Second',locator_hint={'path':'owner.go','symbol':'Second'})])
    bindings = [u for u in units if 'import sdk' in u['text']]
    assert len(bindings) == 1
    assert requires_gap_delivery(bindings[0])
    assert {'A','B'} <= {u.get('symbol') for u in units}
    assert len(units) <= 8


@pytest.mark.parametrize('summary,symbol', [
    ('The handoff is not established', 'Not'),
    ('Find the groups API handler', 'getGroupsHandler'),
])
def test_prose_named_function_remains_available_but_optional(tmp_path, summary, symbol):
    files = {'main.go':'package main\nfunc reloadConfig() {}\n',
             'api/helpers.go':f'package api\nfunc {symbol}() {{}}\n'}
    _, _, units, _ = locate(tmp_path, files, [dict(id='G1',summary=summary,
        locator_hint={'path':'main.go','symbol':'reloadConfig'})])
    named = next(u for u in units if u.get('symbol') == symbol)
    assert named['optional_gap_context'] is True
    assert not requires_gap_delivery(named)


def test_exact_not_locator_stays_required_even_with_prose_match(tmp_path):
    files = {'main.go':'package main\nfunc reloadConfig() {}\n',
             'api/helpers.go':'package api\nfunc Not() {}\n'}
    _, _, units, _ = locate(tmp_path, files, [dict(id='G1',summary='Handoff not established',
        locator_hint={'path':'api/helpers.go','symbol':'Not'})])
    named = next(u for u in units if u.get('symbol') == 'Not')
    assert 'G1:anchor' in named['gap_role_keys'] and requires_gap_delivery(named)
    result = extend_once(build_candidate_set('Not',None,[],[],top_n=12,
        max_context_tokens=1,token_counter=len), [dict(id='G1',summary='Not',locator_hint={'path':'api/helpers.go','symbol':'Not'})], units,
        max_context_tokens=1,token_counter=len)
    assert named['id'] in result.required_endpoint_ids and named['id'] not in result.selected_ids


def test_lexical_reverse_caller_is_optional_and_exact_anchor_wins(tmp_path):
    files={'p.go':'package p\nfunc Target() {}\nfunc Caller() { Target() }\n'}
    gaps=[dict(id='G1',summary='Target path',locator_hint={'path':'p.go','symbol':'Target'})]
    _, _, units, _ = locate(tmp_path, files, gaps)
    caller=next(u for u in units if u.get('symbol')=='Caller')
    target=next(u for u in units if u.get('symbol')=='Target')
    assert caller['optional_gap_context'] is True and not requires_gap_delivery(caller)
    assert requires_gap_delivery(target)
    explicit={**caller,'gap_role_keys':['G2:anchor'],'optional_gap_context':False}
    selected=extend_once(build_candidate_set('Target',None,[],[],top_n=12,
        max_context_tokens=12000,token_counter=len), gaps, [*units,explicit],
        max_context_tokens=12000,token_counter=len)
    assert caller['id'] in selected.required_endpoint_ids


@pytest.mark.parametrize('declaration', [
    'import sdk "example.org/pkg/client"',
    'import "example.org/pkg/v2"',
    'import . "example.org/pkg/client"',
])
def test_alias_source_is_preserved_without_guessing_implicit_package_name(tmp_path, declaration):
    field = 'client Client' if 'import .' in declaration else 'client *sdk.Client'
    source = f'package p\n{declaration}\n// separate declaration\ntype Owner struct {{ {field} }}\nfunc (o *Owner) Work() {{}}\n'
    _, _, units, _ = locate(tmp_path, {'owner.go':source}, [dict(id='G1',summary='Work',
        locator_hint={'path':'owner.go','symbol':'Work'})])
    imports=[u for u in units if u.get('kind')=='SOURCE_TEXT' and 'import ' in u['text']]
    assert len(imports)==1 and imports[0]['text']==declaration
    assert requires_gap_delivery(imports[0])


def test_archived_import_is_never_import_evidence(tmp_path):
    source=('package p\nimport sdk "example.org/current"\n'
            'var archived = `\nimport sdk "example.org/archive"\n`\n'
            '/*\nimport sdk "example.org/comment"\n*/\n'
            'type Owner struct { client *sdk.Client }\nfunc (o *Owner) Work() {}\n')
    _,_,units,_=locate(tmp_path, {'owner.go':source}, [dict(id='G1',summary='Work',
        locator_hint={'path':'owner.go','symbol':'Work'})])
    imports=[u['text'] for u in units if u.get('kind')=='SOURCE_TEXT' and 'import ' in u['text']]
    assert imports==['import sdk "example.org/current"']


def test_import_support_does_not_consume_semantic_role_but_stays_mandatory(tmp_path):
    source=('package p\nimport sdk "example.org/sdk"\n// separate declaration\ntype Owner struct { client *sdk.Client }\n'
            'func (o *Owner) Work() {}\n'+''.join(f'func Other{i}() {{}}\n' for i in range(4)))
    gaps=[dict(id='G1',summary='Work',locator_hint={'path':'owner.go','symbol':'Work'})]
    gaps += [dict(id=f'G{i+2}',summary=f'Other{i}',locator_hint={'path':'owner.go','symbol':f'Other{i}'}) for i in range(4)]
    _,_,units,audit=locate(tmp_path,{'owner.go':source},gaps)
    owners=[u for u in units if u.get('association_status','').startswith('SAME_PACKAGE_RECEIVER')]
    assert len(owners)==2  # Complete owner plus separately located import.
    assert not any(r['status']=='RECEIVER_DECLARATION_ROLE_BUDGET' for r in audit['records'])
    assert sum(requires_gap_delivery(u) for u in units)==7
    assert units[-1]['kind']=='SOURCE_TEXT' and 'import sdk' in units[-1]['text']
    # The final physical-record cap still applies; discovery did not make
    # seven mandatory records fit in six. The missing record remains required.
    result=build_candidate_set('Work',None,units,[],top_n=6,max_context_tokens=12000,token_counter=len)
    missing={u['id'] for u in units if requires_gap_delivery(u)}-set(result.selected_ids)
    assert len(result.selected_ids)==6 and missing
    receipt=audit_gap_delivery([{'required_unit_ids':[u['id'] for u in units if requires_gap_delivery(u)]}],
        list(result.candidates), json.dumps({'evidence_catalog':[]}))
    assert receipt['status']=='INCOMPLETE' and missing<=set(receipt['missing_required_ids'])


def test_oversize_import_is_not_clipped_and_claimed_delivered(tmp_path):
    source=('package p\nimport (\n sdk "example.org/sdk"\n'+' // comment\n'*165+')\n'
            'type Owner struct { client *sdk.Client }\nfunc (o *Owner) Work() {}\n')
    _,_,units,audit=locate(tmp_path,{'owner.go':source},[dict(id='G1',summary='Work',
        locator_hint={'path':'owner.go','symbol':'Work'})])
    assert not any(u.get('association_status','').startswith('SAME_PACKAGE_RECEIVER') for u in units)
    assert any(r['status']=='RECEIVER_IMPORT_CONTEXT_NOT_INCLUDED' for r in audit['records'])


@pytest.mark.parametrize('imports,field', [
    ('import . `example.org/remote`','client Client'),
    ('import (. "example.org/remote"; sdk "example.org/sdk")','client Client'),
    ('import (sdk "example.org/sdk"; other "example.org/other")','client *sdk.Client'),
    ('import sdk `example.org/sdk`','client *sdk.Client'),
])
def test_real_go_import_syntax_survives_without_spec_parser_binding(tmp_path, imports, field):
    source=f'package p\n{imports}\n// separate source spans\ntype Owner struct {{ {field} }}\nfunc (o *Owner) Work() {{}}\n'
    snap,_,units,audit=locate(tmp_path,{'owner.go':source},[dict(id='G1',summary='Work',
        locator_hint={'path':'owner.go','symbol':'Work'})])
    support=[u for u in units if u.get('kind')=='SOURCE_TEXT' and 'import ' in u['text']]
    assert len(support)==1 and support[0]['text']==imports
    assert requires_gap_delivery(support[0])
    assert source[support[0]['locator']['start_char']:support[0]['locator']['end_char']]==imports
    assert not any(r['status']=='RECEIVER_IMPORT_CONTEXT_NOT_INCLUDED' for r in audit['records'])


def test_symbol_only_exact_request_remains_required(tmp_path):
    _,_,units,_=locate(tmp_path,{'api/not.go':'package api\nfunc Not() {}\n'},[
        dict(id='G1',summary='Find the named function',locator_hint={'symbol':'Not'})])
    found=next(u for u in units if u.get('symbol')=='Not')
    assert requires_gap_delivery(found) and not found['optional_gap_context']


def test_adjacent_import_and_owner_share_exact_contiguous_source_without_prefix(tmp_path):
    source='// license\npackage p\nimport sdk "example.org/sdk"\n\ntype Owner struct { client *sdk.Client }\nfunc (o *Owner) Work() {}\n'
    snap,_,units,_=locate(tmp_path,{'owner.go':source},[dict(id='G1',summary='Work',
        locator_hint={'path':'owner.go','symbol':'Work'})])
    owner=next(u for u in units if u.get('symbol')=='Owner')
    assert owner['text']=='import sdk "example.org/sdk"\n\ntype Owner struct { client *sdk.Client }\n'
    assert owner['locator']['start_line']==3 and owner['locator']['end_line']==5
    assert source[owner['locator']['start_char']:owner['locator']['end_char']]==owner['text']
    assert sum('import sdk' in u['text'] for u in units)==1


@pytest.mark.parametrize('path,required', [('api',True),('api/',True),('api/.',True),
    ('api-other',False),('../api',False),('/api',False)])
def test_symbol_locator_directory_uses_path_segments(tmp_path,path,required):
    _,_,units,_=locate(tmp_path,{'api/helpers.go':'package api\nfunc Not() {}\n'},[
        dict(id='G1',summary='Find the named function',locator_hint={'path':path,'symbol':'Not'})])
    found=next(u for u in units if u.get('symbol')=='Not')
    assert requires_gap_delivery(found) is required


@pytest.mark.parametrize('hint,required', [
    ({'path':'api/helpers.go','search_term':'Not'},True),
    ({'search_term':'Not'},False),
    ({'path':'api/helpers.go','search_term':'not'},False),
])
def test_file_exact_search_symbol_is_preserved_without_promoting_prose(tmp_path,hint,required):
    _,_,units,_=locate(tmp_path,{'api/helpers.go':'package api\nfunc Not() {}\n'},[
        dict(id='G1',summary='The handoff is not established',locator_hint=hint)])
    found=next(u for u in units if u.get('symbol')=='Not')
    assert requires_gap_delivery(found) is required
    if required:
        assert 'G1:anchor' in found['gap_role_keys']


def test_many_small_import_declarations_share_existing_aggregate_source_bound(tmp_path):
    source=('package p\n'+''.join(f'import p{i} "example.org/{i}"\n' for i in range(161))
            +'// separate declaration\ntype Owner struct { client *p0.Client }\nfunc (o *Owner) Work() {}\n')
    _,_,units,audit=locate(tmp_path,{'owner.go':source},[dict(id='G1',summary='Work',
        locator_hint={'path':'owner.go','symbol':'Work'})])
    assert not any(u.get('association_status','').startswith('SAME_PACKAGE_RECEIVER') for u in units)
    assert any(r['status']=='RECEIVER_IMPORT_CONTEXT_NOT_INCLUDED' for r in audit['records'])
