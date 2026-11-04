"""Malformed final projections never become source-delivery witnesses."""
import hashlib

import pytest

from contractedge.contracts.delivery import source_span_matches, target_supplies_source, target_supplies_support


TEXT = 'one\nshared\nthree\nshared\nfive\n'
RECORD = {'id': 'S1', 'excerpt': 'shared\n',
          'content_digest': hashlib.sha256(b'shared\n').hexdigest(),
          'locator': {'path': 'a.go', 'start_line': 2, 'end_line': 2}}


@pytest.mark.parametrize('segments', [None, [], [[1]], [[1, 2, 3]], ['bad'],
    [[True, 2]], [[1, '2']], [[0, 2]], [[3, 2]], [[1, 99]], [[4, 4], [1, 2]], [[1, 3], [2, 4]]])
def test_invalid_window_segments_return_false(segments):
    target = {'path': 'a.go', 'content': TEXT, 'windowed_from_current_target': True,
              'window_segments': segments}
    assert not target_supplies_source(target, TEXT, allow_window=True)
    assert not target_supplies_support(target, RECORD, TEXT)


@pytest.mark.parametrize('record', [None, [], {}, {'excerpt': 3},
    {**RECORD, 'locator': []}, {**RECORD, 'content_digest': None},
    {**RECORD, 'locator': {'path': 'a.go', 'start_line': True, 'end_line': 2}}])
def test_invalid_source_record_returns_false(record):
    assert not source_span_matches(record, TEXT)
    assert not target_supplies_support({'path': 'a.go', 'content': TEXT}, record, TEXT)


def test_valid_window_is_not_a_complete_source_or_a_different_original_span():
    target = {'path': 'a.go', 'content': 'shared\nfive\n',
              'windowed_from_current_target': True, 'window_segments': [[4, 5]]}
    assert target_supplies_source(target, TEXT, allow_window=True)
    assert not target_supplies_source(target, TEXT)
    assert not target_supplies_support(target, RECORD, TEXT)
    assert target_supplies_support(target, {**RECORD,
        'locator': {'path': 'a.go', 'start_line': 4, 'end_line': 4}}, TEXT)


def test_declared_segment_bytes_must_exist_in_final_target_content():
    target = {'path': 'a.go', 'content': 'unrelated\n',
              'windowed_from_current_target': True, 'window_segments': [[1, 2]]}
    assert not target_supplies_source(target, TEXT, allow_window=True)
    assert not target_supplies_support(target, RECORD, TEXT)


def test_one_delivered_byte_span_cannot_witness_two_original_segments():
    target = {'content': 'same\n', 'windowed_from_current_target': True,
              'window_segments': [[1, 1], [3, 3]]}
    assert not target_supplies_source(target, 'same\ngap\nsame\n', allow_window=True)


def test_delivered_window_segments_preserve_original_order():
    target = {'content': 'last\nfirst\n', 'windowed_from_current_target': True,
              'window_segments': [[1, 1], [3, 3]]}
    assert not target_supplies_source(target, 'first\ngap\nlast\n', allow_window=True)
