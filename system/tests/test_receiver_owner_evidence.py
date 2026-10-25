"""Receiver declarations must not lose to same-name methods in other Go packages."""
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot
from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.retrieval.candidates import build_candidate_set, extend_once


def fixture(root):
    files = {
        'service/update.go': 'package service\nfunc (m *Manager) Update() { m.client.Send() }\n',
        'service/owner.go': 'package service\nimport sdk "example.org/locked/client"\ntype Manager struct { client *sdk.Client }\n',
        'unrelated/owner.go': 'package unrelated\ntype Manager struct { client *WrongClient }\n',
        'unrelated/update.go': 'package unrelated\nfunc (m *Manager) Update() { m.client.Send(); removal(); tags() }\n',
    }
    for name, text in files.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    snap=compute_snapshot(root)
    units=[u for name,text in files.items() for u in parse_source_units(name,text,snap.snapshot_id)]
    index=[{k:u[k] for k in ('id','path','symbol','locator','kind','truncated') if k in u} for u in units]
    seeds=[u for u in units if u['path']!='service/owner.go']
    gaps=[dict(id='G1',summary='Find client binding used by update and removal of tags',
               locator_hint={'path':'service/update.go','symbol':'Update'})]
    return snap,seeds,index,gaps


def test_same_package_receiver_type_and_import_reach_final_selected_context(tmp_path):
    snap,seeds,index,gaps=fixture(tmp_path)
    units,audit=locate_gap_roles(snap,gaps,dict(seed_units=seeds,seed_declarations=index,max_new_files=4))
    owner=next((u for u in units if u['path']=='service/owner.go'),None)
    assert owner is not None, 'Receiver field declaration was lost before final selection'
    assert 'client *sdk.Client' in owner['text'] and 'example.org/locked/client' in owner['text']
    assert owner['required_for_gap_delivery'] and not owner.get('verified_causal_edge')
    assert audit['inspected_files']<=4
    initial=build_candidate_set('update tags',None,seeds,[],top_n=12,max_context_tokens=10000,token_counter=len)
    selected=extend_once(initial,gaps,units,max_context_tokens=10000,token_counter=len)
    assert owner['id'] in selected.selected_ids and owner['id'] in selected.required_endpoint_ids
    assert len(selected.candidates)<=12
    from contractedge.orchestration.runner import required_planning_source_support
    from tests.test_contracts import valid_method_input
    from contractedge.contracts.validation import parse_method_input
    support=required_planning_source_support(parse_method_input(valid_method_input()),selected,snap.snapshot_id)
    assert any(r['locator']['path']=='service/owner.go' and 'client *sdk.Client' in r['excerpt'] for r in support)


def test_same_named_foreign_receiver_is_not_owner_binding(tmp_path):
    snap,seeds,index,gaps=fixture(tmp_path)
    units,_=locate_gap_roles(snap,gaps,dict(seed_units=seeds,seed_declarations=index,max_new_files=4))
    owners=[u for u in units if u.get('association_status','').startswith('SAME_PACKAGE_RECEIVER')]
    assert owners and {u['path'] for u in owners}=={'service/owner.go'}


def test_receiver_file_budget_shortage_is_explicit(tmp_path):
    snap,seeds,index,gaps=fixture(tmp_path)
    units,audit=locate_gap_roles(snap,gaps,dict(seed_units=seeds,seed_declarations=index,max_new_files=1))
    assert not any(u['path']=='service/owner.go' for u in units)
    assert any(r.get('status')=='RECEIVER_DECLARATION_FILE_BUDGET' for r in audit['records'])


def test_changed_or_renamed_receiver_cannot_reuse_index_body(tmp_path):
    snap,seeds,index,gaps=fixture(tmp_path)
    (tmp_path/'service/owner.go').write_text('package service\ntype Renamed struct { client *Different }\n')
    units,audit=locate_gap_roles(snap,gaps,dict(seed_units=seeds,seed_declarations=index,max_new_files=4))
    assert not any(u['path']=='service/owner.go' for u in units)
    assert any(r.get('status')=='RECEIVER_DECLARATION_SNAPSHOT_MISMATCH' for r in audit['records'])


def test_duplicate_receiver_declarations_are_not_guessed(tmp_path):
    snap,seeds,index,gaps=fixture(tmp_path)
    text='package service\ntype Manager struct { client *OtherClient }\n'
    (tmp_path/'service/alternate.go').write_text(text)
    snap=compute_snapshot(tmp_path)
    index+=parse_source_units('service/alternate.go',text,snap.snapshot_id)
    units,audit=locate_gap_roles(snap,gaps,dict(seed_units=seeds,seed_declarations=index,max_new_files=4))
    assert not any(u.get('association_status','').startswith('SAME_PACKAGE_RECEIVER') for u in units)
    assert any(r.get('status')=='RECEIVER_DECLARATION_AMBIGUOUS' for r in audit['records'])


def test_deleted_receiver_declaration_is_reported_without_old_evidence(tmp_path):
    snap,seeds,index,gaps=fixture(tmp_path)
    (tmp_path/'service/owner.go').unlink()
    units,audit=locate_gap_roles(snap,gaps,dict(seed_units=seeds,seed_declarations=index,max_new_files=4))
    assert not any(u['path']=='service/owner.go' for u in units)
    assert any(r.get('status')=='RECEIVER_DECLARATION_UNAVAILABLE' for r in audit['records'])


def test_current_snapshot_reads_new_type_body_instead_of_old_index_payload(tmp_path):
    snap,seeds,index,gaps=fixture(tmp_path)
    (tmp_path/'service/owner.go').write_text('package service\ntype Manager struct { client *NewClient }\n')
    current=compute_snapshot(tmp_path)
    units,_=locate_gap_roles(current,gaps,dict(seed_units=seeds,seed_declarations=index,max_new_files=4))
    owner=next(u for u in units if u['path']=='service/owner.go')
    assert 'NewClient' in owner['text'] and 'sdk.Client' not in owner['text']
    assert owner['snapshot_id']==current.snapshot_id


def test_cannot_claim_receiver_delivery_when_final_context_is_too_small(tmp_path):
    from contractedge.retrieval.gap_delivery import audit_gap_delivery
    import json
    snap,seeds,index,gaps=fixture(tmp_path)
    units,_=locate_gap_roles(snap,gaps,dict(seed_units=seeds,seed_declarations=index,max_new_files=4))
    owner=next(u for u in units if u['path']=='service/owner.go')
    initial=build_candidate_set('update',None,[],[],top_n=12,max_context_tokens=20,token_counter=len)
    selected=extend_once(initial,gaps,units,max_context_tokens=20,token_counter=len)
    receipt=audit_gap_delivery([{'required_unit_ids':selected.required_endpoint_ids}],list(selected.candidates),json.dumps({'evidence_catalog':[]}))
    assert receipt['status']=='INCOMPLETE' and owner['id'] in receipt['missing_required_ids']


def test_context_trimming_does_not_drop_required_group_with_optional_members_left():
    from contractedge.retrieval.candidates import CandidateSet,trim_context_bundle
    units=tuple(dict(id=n,path=n+'.go',text=n*100,locator={'start_line':1,'end_line':1},
                     gap_role_keys=['G:'+n] if n in {'A','C'} else []) for n in ['A','B','C','D'])
    cs=CandidateSet(units,tuple('ABCD'),(),{},({'source':'A','target':'B','edge_kind':'INFERRED'},
                     {'source':'C','target':'D','edge_kind':'INFERRED'}),400,500,12,False,{},required_endpoint_ids=('A','C'))
    fitted=trim_context_bundle(cs,request_tokens=600,limit=500)
    assert {'A','C'}<=set(fitted.selected_ids), 'Soft lexical groups must not evict required facts before optional source'
    assert len(fitted.selected_ids)==3
