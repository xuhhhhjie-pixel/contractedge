"""Gap-specific interface locations must follow real fields, not name guesses."""
import pytest

from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.parsers import parse_source_units
from contractedge.retrieval.candidates import build_candidate_set


def fixture(root, call='w.queue.Publish(item)'):
    (root / 'go.mod').write_text('module example.org/app\n')
    (root / 'worker').mkdir()
    (root / 'bus').mkdir()
    (root / 'decoy').mkdir()
    source = '''package worker
import transport "example.org/app/bus"
type Worker struct {
 queue transport.Queue
}
func (w *Worker) Run() {
 CALL
}
'''.replace('CALL', call)
    (root / 'worker/work.go').write_text(source)
    contract = '''package bus
type Queue interface {
 // Publish requires a stable item identifier.
 Publish(item Item) error
 Pending() []Item
}
'''
    (root / 'bus/bus.go').write_text(contract)
    (root / 'decoy/bus.go').write_text(contract.replace('stable', 'wrong'))
    snap = compute_snapshot(root)
    index = parse_source_units('worker/work.go', source, snap.snapshot_id)
    gaps = [dict(id='G1', summary='Find queue publication contract',
                 locator_hint={'path': 'worker/work.go', 'symbol': 'Worker.Run',
                               'search_term': 'queue.Publish'})]
    return snap, gaps, dict(seed_declarations=index, max_new_files=4)


def contracts(units):
    return [u for u in units if u.get('scope_extension') == 'EXPLICIT_LOCAL_FIELD_CONTRACT']


def test_explicit_field_import_keeps_interface_preconditions_without_guessing_implementation(tmp_path):
    snap, gaps, limits = fixture(tmp_path)
    units, audit = locate_gap_roles(snap, gaps, limits)
    found = contracts(units)
    assert len(found) == 1
    assert found[0]['path'] == 'bus/bus.go'
    assert 'stable item identifier' in found[0]['text']
    assert 'Pending()' in found[0]['text']
    assert found[0]['evidence_gap_refs'] == ['G1']
    assert 'not implementation' in found[0]['association_status']
    assert found[0]['hop_distance'] == 2
    assert audit['inspected_files'] <= 4


@pytest.mark.parametrize('call', ['// w.queue.Publish(item)',
    's := "w.queue.Publish(item)"', 'other.queue.Publish(item)', 'w.queue.Other(item)'])
def test_comments_strings_other_receivers_and_unrequested_methods_do_not_trigger_lookup(tmp_path, call):
    snap, gaps, limits = fixture(tmp_path, call)
    units, _ = locate_gap_roles(snap, gaps, limits)
    assert not contracts(units)


@pytest.mark.parametrize('mutation', ['stale', 'missing', 'symlink', 'wrong_method', 'duplicate'])
def test_unavailable_stale_or_ambiguous_contract_is_not_delivered(tmp_path, mutation):
    snap, gaps, limits = fixture(tmp_path)
    p = tmp_path / 'bus/bus.go'
    if mutation == 'stale':
        p.write_text(p.read_text() + '// changed\n')
    elif mutation == 'missing':
        p.unlink()
    elif mutation == 'symlink':
        p.unlink(); p.symlink_to(tmp_path / 'decoy/bus.go')
    elif mutation == 'wrong_method':
        p.write_text(p.read_text().replace('Publish(', 'Other('))
        snap = compute_snapshot(tmp_path)
    else:
        (tmp_path / 'bus/duplicate.go').write_text(p.read_text())
        snap = compute_snapshot(tmp_path)
    units, audit = locate_gap_roles(snap, gaps, limits)
    assert not contracts(units)
    assert any(r.get('status', '').startswith('FIELD_CONTRACT_') for r in audit['records'])


@pytest.mark.parametrize('bound', ['file', 'bytes'])
def test_contract_lookup_respects_shared_file_and_byte_budget(tmp_path, bound):
    snap, gaps, limits = fixture(tmp_path)
    if bound == 'file':
        limits['max_new_files'] = 1
    else:
        limits['max_bytes'] = (tmp_path / 'worker/work.go').stat().st_size
    units, audit = locate_gap_roles(snap, gaps, limits)
    assert not contracts(units)
    assert audit['inspected_files'] <= limits['max_new_files']
    if bound == 'bytes':
        assert audit['inspected_bytes'] <= limits['max_bytes']


def test_full_file_budget_still_reuses_already_read_source_for_direct_callee(tmp_path):
    snap, gaps, limits = fixture(tmp_path, 'w.queue.Publish(item)\n w.consume()')
    path = tmp_path / 'worker/work.go'
    path.write_text(path.read_text() + 'func (w *Worker) consume() {\n retainIdentity()\n}\n')
    snap = compute_snapshot(tmp_path)
    text = path.read_text()
    limits['seed_declarations'] = parse_source_units('worker/work.go', text, snap.snapshot_id)
    limits['seed_units'] = parse_source_units('worker/work.go', text, snap.snapshot_id, max_lines=1)
    limits['max_new_files'] = 2
    units, audit = locate_gap_roles(snap, gaps, limits)
    assert contracts(units)
    assert any(u.get('symbol') == 'consume' and not u.get('truncated')
               and 'retainIdentity()' in u['text'] for u in units)
    assert audit['inspected_files'] == 2


def test_six_reserved_roles_do_not_hide_direct_callee_in_already_read_file(tmp_path):
    snap, gaps, limits = fixture(tmp_path, 'w.queue.Publish(item)\n w.consume()')
    path = tmp_path / 'worker/work.go'
    path.write_text(path.read_text() + '''func (w *Worker) consume() { retainIdentity() }
func (w *Worker) Sync() { synchronize() }
func (w *Worker) Stop() { cancel() }
''')
    (tmp_path / 'api').mkdir()
    (tmp_path / 'api/groups.go').write_text('package api\nfunc getQueueHandler() { expose() }\n')
    snap = compute_snapshot(tmp_path)
    limits['seed_declarations'] = parse_source_units('worker/work.go', path.read_text(), snap.snapshot_id)
    limits['seed_declarations'] += parse_source_units('api/groups.go', (tmp_path / 'api/groups.go').read_text(), snap.snapshot_id)
    for symbol in ['Sync', 'Stop']:
        gaps.append(dict(id=symbol, summary='queue API', locator_hint={'path': 'worker/work.go', 'symbol': symbol}))
    units, audit = locate_gap_roles(snap, gaps, limits)
    assert len([u for u in units if u.get('gap_role_keys')]) == 6
    support = [u for u in units if u.get('symbol') == 'consume']
    assert len(support) == 1
    assert not support[0].get('gap_role_keys')
    assert not support[0].get('required_for_gap_delivery')
    assert support[0]['evidence_gap_refs'] == ['G1']
    assert audit['inspected_files'] <= 4


def test_explicit_distinct_callee_is_not_penalized_for_sharing_its_callers_file(tmp_path):
    snap, gaps, limits = fixture(tmp_path, 'w.queue.Publish(item)\n w.consume()')
    path = tmp_path / 'worker/work.go'
    path.write_text(path.read_text() + 'func (w *Worker) consume() { retainIdentity() }\n')
    snap = compute_snapshot(tmp_path)
    limits['seed_declarations'] = parse_source_units('worker/work.go', path.read_text(), snap.snapshot_id)
    units, _ = locate_gap_roles(snap, gaps, limits)
    support = next(u for u in units if u.get('symbol') == 'consume')
    decoy = {**support, 'id': 'test-decoy', 'path': 'tests/queue_test.go',
             'scope_extension': 'UNCONNECTED_LEXICAL_MATCH', 'evidence_gap_refs': [],
             'same_receiver_callee_support': False}
    result = build_candidate_set('queue publication contract', None, [*units, decoy], [],
                                 top_n=4, max_context_tokens=10000, token_counter=len)
    assert support['id'] in result.selected_ids
    assert decoy['id'] not in result.selected_ids


def test_callee_binding_survives_coalescing_with_existing_generic_declaration(tmp_path):
    snap, gaps, limits = fixture(tmp_path, 'w.queue.Publish(item)\n w.consume()')
    path = tmp_path / 'worker/work.go'
    path.write_text(path.read_text() + 'func (w *Worker) consume() { retainIdentity() }\n')
    snap = compute_snapshot(tmp_path)
    limits['seed_declarations'] = parse_source_units('worker/work.go', path.read_text(), snap.snapshot_id)
    units, _ = locate_gap_roles(snap, gaps, limits)
    support = next(u for u in units if u.get('symbol') == 'consume')
    generic = {**support, 'scope_extension': 'EVIDENCE_GAP_DIRECT_NEIGHBOR'}
    generic.pop('same_receiver_callee_support', None)
    result = build_candidate_set('queue', None, [generic, *units], [],
                                 top_n=12, max_context_tokens=10000, token_counter=len)
    assert next(u for u in result.candidates if u['id'] == support['id']).get('same_receiver_callee_support') is True


def test_import_text_inside_raw_string_cannot_replace_actual_external_import(tmp_path):
    snap, gaps, limits = fixture(tmp_path)
    path = tmp_path / 'worker/work.go'
    path.write_text(path.read_text().replace('transport "example.org/app/bus"',
        'transport "outside.org/bus"') + 'const documentation = `\ntransport "example.org/app/bus"\n`\n')
    snap = compute_snapshot(tmp_path)
    limits['seed_declarations'] = parse_source_units('worker/work.go', path.read_text(), snap.snapshot_id)
    units, _ = locate_gap_roles(snap, gaps, limits)
    assert not contracts(units)
