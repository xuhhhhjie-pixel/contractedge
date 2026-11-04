"""Exact public identifiers may locate disconnected read-only production code."""
from pathlib import Path
import pytest

from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units, extract_gap_extension_units


def setup(root, query='NextQueueTokenName'):
    (root / 'docs.md').write_text('Queue lifecycle documentation.\n')
    (root / 'controller.go').write_text('package controller\nfunc ProcessItem() {\n'
        + ' prepare()\n' * 90 + ' compare(obj.' + query + ')\n terminalState()\n}\n')
    snap = compute_snapshot(root)
    seeds = extract_units(snap, {'seed_paths': ['docs.md']})
    gap = dict(id='G1', locator_hint={'search_term': query})
    return snap, seeds, gap


def test_precise_identifier_locates_current_complete_body_without_requiring_model_path(tmp_path):
    snap, seeds, gap = setup(tmp_path)
    units, audit = extract_gap_extension_units(snap, ['docs.md'], seeds, [gap])
    found = [u for u in units if u['path'] == 'controller.go' and u.get('symbol') == 'ProcessItem']
    assert len(found) == 1 and not found[0]['truncated']
    assert 'terminalState()' in found[0]['text']
    assert found[0]['scope_extension'] == 'EVIDENCE_GAP_PRECISE_IDENTIFIER'
    assert found[0]['evidence_gap_refs'] == ['G1']
    assert 'not' in found[0]['association_status']
    assert audit['inspected_files'] <= 2000


@pytest.mark.parametrize('query', ['state', 'controller', 'error', 'queue token', 'key', 'newValue'])
def test_generic_words_or_prose_do_not_open_disconnected_source(tmp_path, query):
    snap, seeds, gap = setup(tmp_path, query)
    units, _ = extract_gap_extension_units(snap, ['docs.md'], seeds, [gap])
    assert not any(u['path'] == 'controller.go' for u in units)


@pytest.mark.parametrize('budget', ['files', 'bytes', 'units'])
def test_precise_identifier_does_not_bypass_scan_or_candidate_budget(tmp_path, budget):
    snap, seeds, gap = setup(tmp_path)
    seed_paths = ['docs.md']
    if budget == 'files':
        (tmp_path / 'seed.go').write_text('package seed\nfunc Anchor() {}\n')
        snap = compute_snapshot(tmp_path)
        seed_paths = ['seed.go']
        seeds = extract_units(snap, {'seed_paths': seed_paths})
    kwargs = {'max_files': 1} if budget == 'files' else {'max_search_bytes': 1} if budget == 'bytes' else {'max_units': 0}
    units, _ = extract_gap_extension_units(snap, seed_paths, seeds, [gap], **kwargs)
    assert not any(u['path'] == 'controller.go' for u in units)


def test_substring_and_wrong_case_are_not_precise_identifier_evidence(tmp_path):
    snap, seeds, gap = setup(tmp_path)
    for query in ['QueueTokenName', 'nextQueueTokenName']:
        gap['locator_hint']['search_term'] = query
        units, _ = extract_gap_extension_units(snap, ['docs.md'], seeds, [gap])
        assert not any(u['path'] == 'controller.go' for u in units)


def test_same_name_in_two_files_remains_two_locations_not_unique_causal_resolution(tmp_path):
    snap, seeds, gap = setup(tmp_path)
    (tmp_path / 'other.go').write_text((tmp_path / 'controller.go').read_text())
    snap = compute_snapshot(tmp_path)
    units, _ = extract_gap_extension_units(snap, ['docs.md'], seeds, [gap])
    assert {u['path'] for u in units} == {'controller.go', 'other.go'}
    assert all('not' in u['association_status'] for u in units)


def test_stale_source_is_rejected_but_still_consumes_inspected_byte_budget(tmp_path):
    snap, seeds, gap = setup(tmp_path)
    path = tmp_path / 'controller.go'
    path.write_text(path.read_text() + '// changed after snapshot\n')
    units, audit = extract_gap_extension_units(snap, ['docs.md'], seeds, [gap])
    assert not units
    assert audit['inspected_bytes'] == path.stat().st_size
