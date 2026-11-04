"""Task continuity must not compete blindly with the located implementation."""
from pathlib import PurePosixPath

from contractedge.retrieval.candidates import build_candidate_set, extend_once
from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def go(path, text, **metadata):
    unit = parse_source_units(path, text, 's')[0]
    return {**unit, **metadata}


def extend(rows, extra, *, top_n=4, query='OptionFlag', path='worker'):
    initial = build_candidate_set('Preserve `OptionFlag` and ordinary values while merging resources', None,
        rows, [], top_n=top_n, max_context_tokens=30000, token_counter=len)
    return extend_once(initial, [dict(id='G', locator_hint=dict(path=path, search_term=query))],
        extra, max_context_tokens=30000, token_counter=len)


def test_receiver_context_survives_document_keyword_overlap():
    root = go('worker/visitor.go', 'func (v *Visitor) VisitList() { return }\n')
    helper = go('worker/visitor.go', 'func (*Visitor) IsCleared() bool { return true }\n')
    method = go('worker/map.go', 'func (v *Visitor) VisitMap() { v.IsCleared() }\n')
    doc = dict(id='doc', path='guide.md', text='OptionFlag Preserve ordinary values merging resources',
               snapshot_id='s', locator=dict(start_line=1,end_line=1))
    result = extend([root, doc], [helper, method], top_n=3)
    assert {root['id'], method['id'], helper['id']} == set(result.selected_ids)
    assert helper['id'] not in result.required_endpoint_ids


def test_actual_constructor_bridge_is_retained_but_comment_and_external_type_are_not():
    root = go('worker/visitor.go', 'func (v *Visitor) VisitList() {}\n')
    bridge = go('worker/tuple.go', 'func Merge() { walk(Visitor{}) }\n')
    comment = go('worker/noise.go', 'func Log() { /* Visitor{} */ record() }\n')
    external = go('worker/external.go', 'func Remote() { walk(remote.Visitor{}) }\n')
    result = extend([root], [comment, external, bridge], top_n=2)
    assert bridge['id'] in result.selected_ids
    assert not {comment['id'], external['id']} & set(result.selected_ids)


def test_namespace_and_receiver_identity_prevent_same_name_context_promotion():
    root = go('worker/source.go', 'func (v *Visitor) VisitList() {}\n')
    wanted = go('worker/local.go', 'func (v *Visitor) VisitMap() {}\n')
    foreign = go('other/local.go', 'func (v *Visitor) VisitMap() { OptionFlag() }\n')
    wrong_owner = go('worker/options.go', 'func (v *Options) VisitMap() { OptionFlag() }\n')
    result = extend([root], [foreign, wrong_owner, wanted], top_n=2)
    assert wanted['id'] in result.selected_ids


def test_direct_callee_is_preferred_before_another_same_receiver_peer():
    root = go('worker/source.go', 'func (v *Visitor) VisitMap() { v.IsCleared() }\n')
    helper = go('worker/helper.go', 'func (*Visitor) IsCleared() bool { return true }\n')
    peer = go('worker/other.go', 'func (v *Visitor) OrdinaryOptionFlagValues() {}\n')
    result = extend([root], [peer, helper], top_n=2)
    assert helper['id'] in result.selected_ids


def test_plain_local_call_does_not_follow_same_named_external_method():
    root = go('worker/source.go', 'func Lookup() { lookupTarget() }\n')
    wanted = go('worker/helper.go', 'func lookupTarget() {}\n')
    other = go('worker/other.go', 'func (r *Remote) lookupTarget() { OptionFlag() }\n')
    result = extend([root], [other, wanted], top_n=2)
    assert wanted['id'] in result.selected_ids


def test_external_qualified_call_does_not_acquire_unrelated_local_declaration():
    root = go('worker/source.go', 'func Lookup() { remote.lookupTarget() }\n')
    unrelated = go('worker/helper.go', 'func lookupTarget() {}\n')
    task = go('config/config.go', 'type Config struct { OptionFlag bool }\n')
    result = extend([root, task], [unrelated], top_n=2)
    assert task['id'] in result.selected_ids
    assert not result.ranking_features[unrelated['id']].get('gap_declaration_context')


def test_directory_only_context_cannot_turn_unconnected_functions_into_roots():
    task = go('config/config.go', 'type Config struct { OptionFlag bool }\n')
    noise = go('worker/metrics.go', 'func Metrics() {}\n',
               scope_extension='EVIDENCE_GAP_DIRECTORY_PATH', evidence_gap_refs=['G'])
    result = extend([task], [noise], top_n=1)
    assert result.selected_ids == (task['id'],)


def test_generic_api_utility_is_not_a_named_public_endpoint(tmp_path):
    (tmp_path/'api').mkdir()
    (tmp_path/'api/format.go').write_text('package api\nfunc getValueNode() {}\nfunc ParseResourceList() {}\n')
    snapshot = compute_snapshot(tmp_path)
    seeds = parse_source_units('api/format.go', (tmp_path/'api/format.go').read_text(), snapshot.snapshot_id)
    roles, _ = locate_gap_roles(snapshot, [dict(id='G', summary='merge resource list values',
        locator_hint=dict(path='worker',search_term='OptionFlag'))], dict(seed_units=seeds,max_new_files=4))
    assert not any(any(k.startswith('NAMED_PUBLIC_ENDPOINT:') for k in u.get('gap_role_keys',())) for u in roles)


def test_literal_named_utility_remains_locatable(tmp_path):
    (tmp_path/'api').mkdir()
    (tmp_path/'api/format.go').write_text('package api\nfunc getValueNode() {}\n')
    snapshot = compute_snapshot(tmp_path)
    seeds = parse_source_units('api/format.go', (tmp_path/'api/format.go').read_text(), snapshot.snapshot_id)
    roles, _ = locate_gap_roles(snapshot, [dict(id='G', summary='locate getValueNode',
        locator_hint=dict(path='worker',search_term='getValueNode'))], dict(seed_units=seeds,max_new_files=4))
    assert any(u.get('symbol')=='getValueNode' for u in roles)


def test_existing_connector_recovers_bounded_same_file_free_callees(tmp_path):
    (tmp_path/'worker').mkdir()
    (tmp_path/'helpers').mkdir()
    (tmp_path/'worker/root.go').write_text('package worker\nfunc Run() { helpers.Lookup() }\n')
    text = ('package helpers\nfunc Lookup() { first(); second(); third() }\n'
            'func first() {}\nfunc second() {}\nfunc third() {}\n')
    (tmp_path/'helpers/helpers.go').write_text(text)
    snapshot = compute_snapshot(tmp_path)
    connector = next(u for u in parse_source_units('helpers/helpers.go', text, snapshot.snapshot_id)
                     if u.get('symbol') == 'Lookup')
    roles, audit = locate_gap_roles(snapshot, [dict(id='G', locator_hint=dict(
        path='worker/root.go', symbol='Run'))], dict(seed_units=[connector],max_new_files=2))
    optional = [u for u in roles if u.get('direct_local_callee_support')]
    assert {u['symbol'] for u in optional} == {'first', 'second'}
    assert all(u.get('optional_gap_context') and not u.get('required_for_gap_delivery') for u in optional)
    assert audit['inspected_files'] == 2


def test_connector_callee_read_keeps_snapshot_and_file_budget(tmp_path):
    (tmp_path/'worker').mkdir()
    (tmp_path/'helpers').mkdir()
    (tmp_path/'worker/root.go').write_text('package worker\nfunc Run() { helpers.Lookup() }\n')
    text = 'package helpers\nfunc Lookup() { first() }\nfunc first() {}\n'
    (tmp_path/'helpers/helpers.go').write_text(text)
    snapshot = compute_snapshot(tmp_path)
    connector = next(u for u in parse_source_units('helpers/helpers.go', text, snapshot.snapshot_id)
                     if u.get('symbol') == 'Lookup')
    roles, audit = locate_gap_roles(snapshot, [dict(id='G', locator_hint=dict(
        path='worker/root.go', symbol='Run'))], dict(seed_units=[connector],max_new_files=1))
    assert not any(u.get('direct_local_callee_support') for u in roles)
    assert audit['inspected_files'] == 1
    (tmp_path/'helpers/helpers.go').write_text(text + '// changed\n')
    roles, audit = locate_gap_roles(snapshot, [dict(id='G', locator_hint=dict(
        path='worker/root.go', symbol='Run'))], dict(seed_units=[connector],max_new_files=2))
    assert not any(u.get('direct_local_callee_support') for u in roles)
    assert any(row['status']=='LOCAL_CALLEE_SNAPSHOT_MISMATCH' for row in audit['records'])


def test_optional_callees_follow_requested_declaration_before_incidental_connector_order(tmp_path):
    (tmp_path/'worker').mkdir()
    (tmp_path/'helpers').mkdir()
    (tmp_path/'worker/root.go').write_text('package worker\nfunc Execute() { helpers.InstallSettings(); helpers.LookupTarget() }\n')
    settings = ('package helpers\nfunc InstallSettings() { /* lookup target lookup target */ a(); b() }\n'
                'func a() {}\nfunc b() {}\n')
    lookup = ('package helpers\nfunc LookupTarget() { resolveTarget(); validateTarget() }\n'
              'func resolveTarget() {}\nfunc validateTarget() {}\n')
    (tmp_path/'helpers/settings.go').write_text(settings)
    (tmp_path/'helpers/lookup.go').write_text(lookup)
    snapshot = compute_snapshot(tmp_path)
    seeds = [next(u for u in parse_source_units(path, text, snapshot.snapshot_id) if u.get('symbol') == name)
             for path, text, name in [('helpers/settings.go', settings, 'InstallSettings'),
                                      ('helpers/lookup.go', lookup, 'LookupTarget')]]
    roles, audit = locate_gap_roles(snapshot, [dict(id='G', summary='lookup target implementation',
        locator_hint=dict(path='worker/root.go', symbol='Execute'))], dict(seed_units=seeds,max_new_files=3))
    optional = [u for u in roles if u.get('direct_local_callee_support')]
    assert {u['symbol'] for u in optional} == {'resolveTarget', 'validateTarget'}
    assert audit['inspected_files'] <= 3
