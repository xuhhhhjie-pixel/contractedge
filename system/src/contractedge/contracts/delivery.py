"""Wire-neutral evidence projections and exact source-delivery witnesses."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping


def reread_source_locator(record: Mapping, current_text: str) -> dict | None:
    """Locate one exact unchanged excerpt now, preserving known incompleteness.

    Original declaration bounds stay on the historical record. A reread of a
    prefix cannot establish the enclosing declaration's current end.
    """
    excerpt, original = record.get('excerpt'), record.get('locator')
    if (not isinstance(excerpt, str) or not excerpt or not isinstance(original, Mapping)
            or record.get('content_digest') != hashlib.sha256(excerpt.encode()).hexdigest()
            or current_text.count(excerpt) != 1):
        return None
    first = current_text.index(excerpt)
    last = first + len(excerpt)
    start_line = current_text.count('\n', 0, first) + 1
    end_line = current_text.count('\n', 0, last) + (0 if current_text[last - 1:last] == '\n' else 1)
    if not any(key in original for key in ('start_char', 'end_char', 'truncated')):
        # Existing line-only records retain their established reread dialect;
        # this does not assert completeness absent from their original record.
        return {'path': original.get('path'), 'start_line': start_line, 'end_line': max(start_line, end_line)}
    return {'path': original.get('path'), 'start_line': start_line, 'end_line': max(start_line, end_line),
            'column_start': first - (current_text.rfind('\n', 0, first) + 1) + 1,
            'start_char': first, 'end_char': last,
            **({'truncated': original['truncated']} if 'truncated' in original else {})}


def source_span_matches(record: Mapping, current_text: str) -> bool:
    """Validate exact excerpt bytes at their declared current source lines."""
    if not isinstance(record, Mapping) or not isinstance(current_text, str):
        return False
    excerpt, locator = record.get('excerpt'), record.get('locator')
    if (not isinstance(excerpt, str) or not excerpt or not isinstance(locator, Mapping)
            or hashlib.sha256(excerpt.encode()).hexdigest() != record.get('content_digest')):
        return False
    if 'truncated' in locator and type(locator['truncated']) is not bool:
        return False
    start, end = locator.get('start_line'), locator.get('end_line')
    if 'start_char' in locator or 'end_char' in locator:
        first, last = locator.get('start_char'), locator.get('end_char')
        if (type(first) is not int or type(last) is not int
                or not 0 <= first < last <= len(current_text)
                or current_text[first:last] != excerpt):
            return False
        actual_start = current_text.count('\n', 0, first) + 1
        actual_end = current_text.count('\n', 0, last) + (0 if current_text[last - 1:last] == '\n' else 1)
        column = first - (current_text.rfind('\n', 0, first) + 1) + 1
        return (type(start) is int and type(end) is int
                and start == actual_start and end == max(actual_start, actual_end)
                and type(locator.get('column_start', column)) is int
                and locator.get('column_start', column) == column)
    if start is None and end is None:
        return excerpt in current_text
    lines = current_text.splitlines(keepends=True)
    if (type(start) is not int or type(end) is not int
            or not 1 <= start <= end <= len(lines)):
        return False
    return excerpt in ''.join(lines[start - 1:end])


def _delivered_window_segments(target: Mapping, current_text: str):
    if not isinstance(target, Mapping) or not isinstance(current_text, str):
        return None
    content, segments = target.get('content'), target.get('window_segments')
    if (target.get('windowed_from_current_target') is not True or not isinstance(content, str)
            or not isinstance(segments, (list, tuple)) or not segments):
        return None
    lines = current_text.splitlines(keepends=True)
    previous_end, content_offset = 0, 0
    for segment in segments:
        if (not isinstance(segment, (list, tuple)) or len(segment) != 2
                or type(segment[0]) is not int or type(segment[1]) is not int
                or not previous_end < segment[0] <= segment[1] <= len(lines)):
            return None
        begin, end = segment
        part = ''.join(lines[begin - 1:end])
        position = content.find(part, content_offset)
        if position < 0:
            return None
        content_offset = position + len(part)
        previous_end = end
    return segments


def target_supplies_source(target: Mapping, current_text: str, *, allow_window=False) -> bool:
    """Witness the complete current file, or explicitly declared exact windows."""
    if not isinstance(target, Mapping) or not isinstance(current_text, str):
        return False
    windowed = target.get('windowed_from_current_target')
    if windowed is None or windowed is False:
        return target.get('content') == current_text
    return bool(allow_window and _delivered_window_segments(target, current_text) is not None)


def target_supplies_support(target: Mapping, record: Mapping, current_text: str) -> bool:
    """One coverage rule for execution construction and final wire admission.

    File identity alone never witnesses an excerpt in a bounded target. Repeated
    bytes elsewhere and an envelope spanning omitted lines do not cover its locator.
    Malformed final projections return False rather than raising shape errors.
    """
    if not isinstance(target, Mapping) or not source_span_matches(record, current_text):
        return False
    locator = record['locator']
    if (not isinstance(locator.get('path'), str) or target.get('path') != locator['path']
            or not isinstance(target.get('content'), str) or record['excerpt'] not in target['content']):
        return False
    if target_supplies_source(target, current_text):
        return True
    segments = _delivered_window_segments(target, current_text)
    start, end = locator.get('start_line'), locator.get('end_line')
    return (segments is not None and type(start) is int and type(end) is int
            and any(begin <= start <= end <= finish for begin, finish in segments))


@dataclass(frozen=True)
class Delivery:
    """Selected complete records and an auditable, content-bound receipt.

    COMPLETE describes delivery of required evidence, not semantic correctness.
    INCOMPLETE and REJECTED must not authorize a downstream model request.
    """

    records: tuple[dict[str, Any], ...]
    receipt: dict[str, Any]
