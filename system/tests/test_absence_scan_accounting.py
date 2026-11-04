from contractedge.source import absence
from contractedge.source.snapshot import compute_snapshot


GAP = [{'id': 'G1', 'locator_hint': {'symbol': 'Target'}}]


def test_over_file_cap_records_no_matching_reads_but_keeps_incomplete_scope(tmp_path, monkeypatch):
    for name in ['a.go', 'b.go', 'c.md']:
        (tmp_path / name).write_text('Target\n')
    snapshot = compute_snapshot(tmp_path)
    def no_matching_read(*args):
        raise AssertionError('precheck must skip the content-matching loop')
    monkeypatch.setattr(absence, 'root_path', no_matching_read)
    records, _ = absence.collect_absence_search_evidence(snapshot, GAP, max_files=2)
    locator = records[0]['locator']
    assert locator['inspected_file_count'] == 0
    assert locator['inspected_byte_count'] == 0
    assert locator['complete'] is False and locator['truncated'] is True
    assert '3 files; limit is 2' in locator['incomplete_reason']


def test_complete_matching_count_and_bytes_remain_actual(tmp_path):
    (tmp_path / 'a.go').write_text('Target\n')
    (tmp_path / 'b.go').write_text('Other\n')
    records, _ = absence.collect_absence_search_evidence(compute_snapshot(tmp_path), GAP, max_files=2)
    locator = records[0]['locator']
    assert locator['inspected_file_count'] == 2
    assert locator['inspected_byte_count'] == 13
    assert locator['complete'] is True
    assert locator['matched_paths'] == ['a.go']


def test_byte_limit_counts_only_contents_that_were_matched(tmp_path):
    (tmp_path / 'a.go').write_text('Target\n')
    (tmp_path / 'b.go').write_text('Other\n' * 10)
    records, _ = absence.collect_absence_search_evidence(compute_snapshot(tmp_path), GAP,
                                                       max_files=2, max_bytes=8)
    locator = records[0]['locator']
    assert locator['inspected_file_count'] == 1
    assert locator['inspected_byte_count'] == 7
    assert locator['matched_paths'] == ['a.go']
    assert locator['complete'] is False and locator['truncated'] is True


def test_failed_read_counts_only_successful_prior_reads(tmp_path, monkeypatch):
    (tmp_path / 'a.go').write_text('Target\n')
    (tmp_path / 'b.go').write_text('Other\n')
    snapshot = compute_snapshot(tmp_path)
    original = absence.root_path
    def fail_second(snap, relative):
        if relative == 'b.go':
            raise OSError('fixture unreadable')
        return original(snap, relative)
    monkeypatch.setattr(absence, 'root_path', fail_second)
    records, _ = absence.collect_absence_search_evidence(snapshot, GAP, max_files=2, max_bytes=50)
    locator = records[0]['locator']
    assert locator['inspected_file_count'] == 1
    assert locator['inspected_byte_count'] == 7
    assert locator['max_bytes'] == 50
    assert locator['complete'] is False
    assert 'cannot read b.go' in locator['incomplete_reason']


def test_final_snapshot_change_preserves_scan_counts_bytes_and_limit(tmp_path, monkeypatch):
    (tmp_path / 'a.go').write_text('Target\n')
    snapshot = compute_snapshot(tmp_path)
    from dataclasses import replace
    monkeypatch.setattr(absence, 'compute_snapshot', lambda _: replace(snapshot, snapshot_id='changed'))
    records, _ = absence.collect_absence_search_evidence(snapshot, GAP, max_files=2, max_bytes=50)
    locator = records[0]['locator']
    assert locator['inspected_file_count'] == 1
    assert locator['inspected_byte_count'] == 7
    assert locator['max_bytes'] == 50
    assert locator['complete'] is False and locator['truncated'] is True
    assert locator['incomplete_reason'] == 'source snapshot changed during absence search'


def test_changed_content_counts_actual_read_length_and_cannot_prove_absence(tmp_path):
    (tmp_path / 'a.go').write_text('Old\n')
    snapshot = compute_snapshot(tmp_path)
    (tmp_path / 'a.go').write_text('Target\n')
    records, _ = absence.collect_absence_search_evidence(snapshot, GAP, max_files=2, max_bytes=50)
    locator = records[0]['locator']
    assert locator['inspected_file_count'] == 1
    assert locator['inspected_byte_count'] == 7
    assert locator['max_bytes'] == 50
    assert locator['complete'] is False and locator['truncated'] is True


def test_final_snapshot_change_preserves_full_match_count_beyond_path_preview(tmp_path, monkeypatch):
    from dataclasses import replace
    from contractedge.contracts.evidence import static_search_content_digest
    for index in range(60):
        (tmp_path / f'file_{index:02}.go').write_text('Target\n')
    snapshot = compute_snapshot(tmp_path)
    original, _ = absence.collect_absence_search_evidence(snapshot, GAP, max_files=60, max_bytes=500)
    monkeypatch.setattr(absence, 'compute_snapshot', lambda _: replace(snapshot, snapshot_id='changed'))
    records, audit = absence.collect_absence_search_evidence(snapshot, GAP, max_files=60, max_bytes=500)
    record, before = records[0], original[0]
    locator = record['locator']
    assert locator['match_count'] == 60
    assert locator['matched_paths'] == [f'file_{index:02}.go' for index in range(50)]
    assert locator['inspected_file_count'] == 60
    assert locator['inspected_byte_count'] == 420
    assert locator['max_bytes'] == 500
    assert locator['complete'] is False and locator['truncated'] is True
    assert locator['incomplete_reason'] == 'source snapshot changed during absence search'
    changed = {'complete', 'truncated', 'incomplete_reason'}
    assert {k: v for k, v in locator.items() if k not in changed} == {
        k: v for k, v in before['locator'].items() if k not in changed}
    assert audit['records'] == [locator]
    digest = static_search_content_digest(snapshot.snapshot_id, locator, record['excerpt'])
    assert record['content_digest'] == digest
    assert record['id'] == 'SEARCH-' + digest[:16]
    assert record['id'] != before['id'] and record['excerpt'] != before['excerpt']
