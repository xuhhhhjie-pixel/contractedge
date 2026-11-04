"""Fresh gap evidence can recover source indexed before Top-N selection."""
from copy import deepcopy

import pytest

from contractedge.retrieval.candidates import build_candidate_set, extend_once
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_gap_extension_units, extract_units


def indexed_source(root):
    (root / 'guide.md').write_text('Overview of the public lifecycle.\n')
    (root / 'pkg').mkdir()
    (root / 'pkg/worker.go').write_text(
        'package worker\nfunc Process() { ReadState() }\n')
    snapshot = compute_snapshot(root)
    paths = ['guide.md', 'pkg/worker.go']
    units = extract_units(snapshot, {'seed_paths': paths}, max_extension_files=0)
    return snapshot, paths, units


def gap(path='pkg', query='ReadState', identifier='G1'):
    return {'id': identifier, 'locator_hint': {'path': path, 'search_term': query}}


def test_indexed_production_dropped_from_top_n_is_recovered_as_gap_endpoint(tmp_path):
    indexed_source(tmp_path)
    (tmp_path / 'pkg/worker_test.go').write_text(
        'package worker\n' + '\n' * 10 + 'func TestProcess() { ReadState() }\n')
    snapshot = compute_snapshot(tmp_path)
    paths = ['guide.md', 'pkg/worker.go']
    units = extract_units(snapshot, {'seed_paths': paths}, max_extension_files=0)
    owner = next(u for u in units if u.get('symbol') == 'Process')
    selected = build_candidate_set('Overview public lifecycle', None, units, [],
                                  top_n=1, max_context_tokens=10000, token_counter=len)
    assert owner['id'] in selected.dropped_ids
    assert selected.candidates[0]['path'] == 'guide.md'

    extension, _ = extract_gap_extension_units(snapshot, paths, units, [gap()])
    recovered = next(u for u in extension if u.get('symbol') == 'Process')
    assert recovered['id'] == owner['id']
    assert recovered['text'] == owner['text']
    assert recovered['evidence_gap_refs'] == ['G1']
    result = extend_once(selected, [gap()], extension,
                         max_context_tokens=10000, token_counter=len)
    assert result.selected_ids == (owner['id'],)
    assert result.required_endpoint_ids == (owner['id'],)


def test_same_gap_directory_query_retains_verified_identity_and_context(tmp_path):
    snapshot, paths, units = indexed_source(tmp_path)
    owner = next(u for u in units if u.get('symbol') == 'Process')
    owner.update(source_focus_basis='PUBLIC_ENTRY_OR_EXPLICIT_RESOURCE_FILE',
                 boundary_scope_anchor=True, precondition_review_required=True)
    before = deepcopy(units)
    extension, _ = extract_gap_extension_units(snapshot, paths, units, [gap()])
    assert len(extension) == 1
    recovered = extension[0]
    for field in ('id', 'snapshot_id', 'content_digest', 'locator', 'text',
                  'scope_path_rank', 'source_focus_basis', 'boundary_scope_anchor',
                  'precondition_review_required'):
        assert recovered[field] == owner[field]
    assert recovered['evidence_gap_refs'] == ['G1']
    assert 'not a verified' in recovered['association_status']
    assert 'required_for_gap_delivery' not in recovered
    assert units == before


def test_changed_file_cannot_requalify_its_indexed_snapshot(tmp_path):
    snapshot, paths, units = indexed_source(tmp_path)
    source = tmp_path / 'pkg/worker.go'
    source.write_text('package worker\nfunc Process() { ReadState(); OtherState() }\n')
    extension, audit = extract_gap_extension_units(snapshot, paths, units, [gap()])
    assert extension == []
    assert audit['inspected_bytes'] == source.stat().st_size
    assert audit['matched_files'] == []


@pytest.mark.parametrize('changed_field', ['snapshot_id', 'text', 'content_digest'])
def test_stale_index_metadata_is_not_inherited_by_a_fresh_read(tmp_path, changed_field):
    snapshot, paths, units = indexed_source(tmp_path)
    owner = next(u for u in units if u.get('symbol') == 'Process')
    expected_id = owner['id']
    owner.update(id='stale-alias', source_focus_basis='STALE_CONTEXT')
    owner[changed_field] = 'stale-index-value'
    extension, _ = extract_gap_extension_units(snapshot, paths, units, [gap()])
    assert len(extension) == 1
    assert extension[0]['id'] == expected_id
    assert extension[0]['snapshot_id'] == snapshot.snapshot_id
    assert extension[0]['text'] == 'func Process() { ReadState() }\n'
    assert 'source_focus_basis' not in extension[0]


def test_requalification_deduplicates_and_keeps_the_existing_unit_budget(tmp_path):
    snapshot, paths, units = indexed_source(tmp_path)
    source = tmp_path / 'pkg/worker.go'
    source.write_text(source.read_text() + 'func Poll() { ReadState() }\n')
    snapshot = compute_snapshot(tmp_path)
    units = extract_units(snapshot, {'seed_paths': paths}, max_extension_files=0)
    extension, audit = extract_gap_extension_units(snapshot, paths, units + units,
        [gap(), gap(), gap(identifier='G2')], max_units=1)
    assert len(extension) == 1
    assert extension[0]['id'] in {u['id'] for u in units}
    assert extension[0]['evidence_gap_refs'] == ['G1', 'G2']
    assert audit['candidate_count'] == 2


def test_symbol_locator_requalifies_the_existing_declaration_without_its_caller(tmp_path):
    indexed_source(tmp_path)
    source = tmp_path / 'pkg/worker.go'
    source.write_text(source.read_text() + 'func ReadState() {}\n')
    snapshot = compute_snapshot(tmp_path)
    paths = ['pkg/worker.go']
    units = extract_units(snapshot, {'seed_paths': paths}, max_extension_files=0)
    owner = next(u for u in units if u.get('symbol') == 'ReadState')
    extension, _ = extract_gap_extension_units(snapshot, paths, units,
        [{'id': 'G1', 'locator_hint': {'path': 'pkg', 'symbol': 'ReadState'}}])
    assert [u['id'] for u in extension] == [owner['id']]
    assert extension[0]['evidence_gap_refs'] == ['G1']


def test_requalification_stops_at_the_existing_file_limit(tmp_path):
    indexed_source(tmp_path)
    (tmp_path / 'pkg/later.go').write_text('package worker\nfunc Poll() { ReadState() }\n')
    snapshot = compute_snapshot(tmp_path)
    paths = ['pkg/later.go', 'pkg/worker.go']
    units = extract_units(snapshot, {'seed_paths': paths}, max_extension_files=0)
    extension, audit = extract_gap_extension_units(snapshot, paths, units, [gap()], max_files=1)
    assert len(extension) == 1
    assert extension[0]['path'] == 'pkg/later.go'
    assert audit['inspected_files'] == 1
    assert audit['inspected_bytes'] == (tmp_path / 'pkg/later.go').stat().st_size
    assert audit['truncated_search'] is True


@pytest.mark.parametrize('limits', [
    {'max_files': 0}, {'max_units': 0}, {'max_file_bytes': 1}, {'max_search_bytes': 1},
])
def test_requalification_cannot_bypass_read_or_output_budgets(tmp_path, limits):
    snapshot, paths, units = indexed_source(tmp_path)
    extension, audit = extract_gap_extension_units(snapshot, paths, units, [gap()], **limits)
    assert extension == []
    assert audit['inspected_files'] <= limits.get('max_files', 2000)


def test_requalification_does_not_emit_nonmatching_or_outside_gap_indexed_units(tmp_path):
    indexed_source(tmp_path)
    (tmp_path / 'pkg/irrelevant.go').write_text('package worker\nfunc Ignore() {}\n')
    (tmp_path / 'pkg-other').mkdir()
    (tmp_path / 'pkg-other/worker.go').write_text(
        'package other\nfunc Outside() { ReadState() }\n')
    paths = ['pkg/worker.go', 'pkg/irrelevant.go', 'pkg-other/worker.go']
    snapshot = compute_snapshot(tmp_path)
    units = extract_units(snapshot, {'seed_paths': paths}, max_extension_files=0)
    extension, _ = extract_gap_extension_units(snapshot, paths, units, [gap()])
    assert [(u['path'], u.get('symbol')) for u in extension] == [('pkg/worker.go', 'Process')]


@pytest.mark.parametrize('gaps', [
    [{'id': 'G1', 'locator_hint': {'path': 'pkg'}}],
    [{'id': 'G1', 'locator_hint': {'path': 'pkg'}},
     {'id': 'G2', 'locator_hint': {'path': 'missing', 'search_term': 'ReadState'}}],
    [gap(query='worker.go')],
])
def test_path_only_or_cross_gap_hints_do_not_reemit_indexed_source(tmp_path, gaps):
    snapshot, paths, units = indexed_source(tmp_path)
    extension, _ = extract_gap_extension_units(snapshot, paths, units, gaps)
    assert extension == []
