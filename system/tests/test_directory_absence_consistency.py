"""Known directory queries are not negative exact-file evidence."""
from contractedge.contracts.evidence import supports_expected_missing_evidence
from contractedge.source.absence import collect_absence_search_evidence
from contractedge.source.snapshot import compute_snapshot


def fixture(root):
    (root/'worker').mkdir();(root/'worker/controller.go').write_text('package worker\nfunc Present() {}\n')
    return compute_snapshot(root)


def test_known_directory_is_not_emitted_as_complete_zero_hit_exact_file_search(tmp_path):
    snapshot=fixture(tmp_path)
    records,audit=collect_absence_search_evidence(snapshot,[dict(id='G',locator_hint=dict(path='worker',search_term='Present controller'))],max_files=0,max_bytes=0)
    assert not records
    assert audit['skipped_directory_hints'][0]['query']=='worker'
    assert audit['skipped_directory_hints'][0]['matched_file_count']==1


def test_directory_hint_does_not_disable_valid_missing_file_or_symbol_checks(tmp_path):
    snapshot=fixture(tmp_path)
    records,audit=collect_absence_search_evidence(snapshot,[
        dict(id='G',locator_hint=dict(path='worker',symbol='NeverDeclared')),
        dict(id='F',locator_hint=dict(path='worker/missing.go'))],max_files=20)
    assert len(records)==2
    assert all(supports_expected_missing_evidence(r) for r in records)
    assert {r['locator']['query'] for r in records}=={'NeverDeclared','worker/missing.go'}


def test_prefix_lookalikes_and_existing_extensionless_file_are_distinguished(tmp_path):
    (tmp_path/'worker2').mkdir();(tmp_path/'worker2/controller.go').write_text('package worker\n')
    (tmp_path/'LICENSE').write_text('Existing extensionless public file.\n')
    snapshot=compute_snapshot(tmp_path)
    records,audit=collect_absence_search_evidence(snapshot,[dict(id='A',locator_hint=dict(path='worker')),
        dict(id='B',locator_hint=dict(path='LICENSE'))],max_files=0)
    assert len(records)==2
    assert supports_expected_missing_evidence(records[0])
    assert not supports_expected_missing_evidence(records[1])
    assert not audit.get('skipped_directory_hints',[])
