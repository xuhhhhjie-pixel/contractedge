"""Required source coverage is witnessed in final messages, not paths or metadata."""
from dataclasses import replace
import hashlib
import json

import pytest

from contractedge.execution.topomen import build_execution_request, _execution_source_window, _execution_window_segments
from contractedge.model.admission import quote_request
from contractedge.model.provider import OpenAICompatibleProvider
from contractedge.orchestration.semantic_delivery import delivery_context, inspect_delivery, require_delivery, SemanticDeliveryError
from tests.test_execution_support_windows import fixture


REQUIREMENT = 'Preserve input identity'


class FinalMessageProvider(OpenAICompatibleProvider):
    """Exercise pure final serialization; no dispatch method is invoked."""

    def __init__(self, transform=None):
        super().__init__('https://fixture.invalid', 'UNUSED_FIXTURE_CREDENTIAL')
        self.transform = transform

    def prepare_request(self, request):
        prepared = super().prepare_request(request)
        if self.transform is None:
            return prepared
        wire = json.loads(prepared.body)
        body = json.loads(wire['messages'][1]['content'])
        self.transform(body)
        wire['messages'][1]['content'] = json.dumps(body)
        return replace(prepared, body=json.dumps(wire).encode())


def _request(root, plan, config):
    return build_execution_request(plan, root, {'allowed_paths': ['worker.*']}, len, config,
                                   requirement=REQUIREMENT)


def _context(root, plan, record, *, explicit_precondition=False):
    prerequisites = ([{'source_evidence_ref': record['id'], 'disposition': 'REQUIRED'}]
                     if explicit_precondition else plan.get('precondition_review', []))
    return delivery_context({'input_digest': plan['input_digest'], 'requirement_text': REQUIREMENT},
        consumer='EXECUTION', snapshot_id=plan['snapshot_id'], plan=plan,
        necessary_support=[record], prerequisites=prerequisites, source_root=root)


def _report(request, frame, transform=None):
    provider = FinalMessageProvider(transform)
    quote = quote_request(provider, request, len)
    report = inspect_delivery(request, quote, frame)
    assert report['request_projection']['authority'] == 'PREPARED_MESSAGES_SCHEMA_TOOLS'
    return report, quote


def _drop_support(body):
    body['read_only_source_support'] = []


def test_current_required_same_path_excerpt_cannot_disappear_after_builder(tmp_path):
    plan, config, record = fixture(tmp_path)
    request = _request(tmp_path, plan, config)
    body = json.loads(request.messages[1]['content'])
    assert record in body['read_only_source_support']
    assert record['excerpt'] not in body['current_target_files'][0]['content']
    frame = _context(tmp_path, plan, record)
    require_delivery(_report(request, frame)[0])
    with pytest.raises(SemanticDeliveryError):
        require_delivery(_report(request, frame, _drop_support)[0])


@pytest.mark.parametrize('explicit_precondition', [False, True])
def test_repeated_bytes_at_another_target_locator_cannot_replace_required_span(tmp_path, explicit_precondition):
    plan, config, record = fixture(tmp_path)
    record.update(excerpt='# context\n', content_digest=hashlib.sha256(b'# context\n').hexdigest(),
                  locator={'path': 'worker.py', 'start_line': 10, 'end_line': 10})
    if explicit_precondition:
        plan['precondition_review'] = [{'source_evidence_ref': record['id'], 'disposition': 'REQUIRED'}]
    request = _request(tmp_path, plan, config)
    body = json.loads(request.messages[1]['content'])
    assert record in body['read_only_source_support']
    assert record['excerpt'] in body['current_target_files'][0]['content']
    assert body['current_target_files'][0]['window_segments'] == [[185, 227]]
    frame = _context(tmp_path, plan, record)
    require_delivery(_report(request, frame)[0])
    with pytest.raises(SemanticDeliveryError):
        require_delivery(_report(request, frame, _drop_support)[0])


def _historical_selection(root):
    plan, config, record = fixture(root, language='go', support_start=150, support_end=153,
                                   target_start=260, target_end=262, historical=True)
    plan['operations'][0]['target'].pop('locator')
    record['excerpt'] += '// This old contract changed during the prior accepted edit.\n'
    record['content_digest'] = hashlib.sha256(record['excerpt'].encode()).hexdigest()
    request = _request(root, plan, config)
    body = json.loads(request.messages[1]['content'])
    assert body['read_only_source_support'] == []
    assert body['current_target_files'][0]['content'] == (root / 'worker.go').read_text()
    assert record['excerpt'] not in body['current_target_files'][0]['content']
    return plan, record, request


@pytest.mark.parametrize('final_target', ['omitted', 'gapped-window'])
def test_historical_selection_continuity_requires_complete_current_target(tmp_path, final_target):
    plan, record, request = _historical_selection(tmp_path)
    frame = _context(tmp_path, plan, record)
    require_delivery(_report(request, frame)[0])

    def narrow_target(body):
        if final_target == 'omitted':
            body['current_target_files'] = []
            return
        full = (tmp_path / 'worker.go').read_text()
        content, start, end, windowed = _execution_source_window(full, [(260, 262)], path='worker.go')
        assert windowed
        segments = [list(span) for span in _execution_window_segments(full, [(260, 262)], path='worker.go')]
        assert segments == [[1, 120], [240, 282]]
        body['current_target_files'][0].update(content=content, line_start=start, line_end=end,
            windowed_from_current_target=True, window_segments=segments,
            source_file_sha256=hashlib.sha256((tmp_path / 'worker.go').read_bytes()).hexdigest())

    with pytest.raises(SemanticDeliveryError):
        require_delivery(_report(request, frame, narrow_target)[0])


def test_complete_current_target_cannot_discharge_explicit_historical_prerequisite(tmp_path):
    plan, record, request = _historical_selection(tmp_path)
    frame = _context(tmp_path, plan, record, explicit_precondition=True)
    with pytest.raises(SemanticDeliveryError):
        require_delivery(_report(request, frame)[0])


def test_internal_covered_helper_catalog_is_neither_wire_content_nor_delivery_proof(tmp_path):
    plan, config, record = fixture(tmp_path, support_start=195, support_end=197)
    request = _request(tmp_path, plan, config)
    body = json.loads(request.messages[1]['content'])
    assert body['read_only_source_support'] == []
    assert record['excerpt'] in body['current_target_files'][0]['content']
    marker = 'INTERNAL_COVERED_HELPER_CATALOG_NOT_FOR_TRANSPORT'
    request = replace(request, metadata={**request.metadata,
        'covered_source_support': [{**record, 'internal_analysis_marker': marker}]})
    frame = _context(tmp_path, plan, record)
    report, quote = _report(request, frame)
    assert b'covered_source_support' not in quote.prepared.body
    assert marker.encode() not in quote.prepared.body
    require_delivery(report)

    def remove_actual_source(body):
        body['current_target_files'] = []
        body['read_only_source_support'] = []

    stripped, quote = _report(request, frame, remove_actual_source)
    assert b'covered_source_support' not in quote.prepared.body
    assert marker.encode() not in quote.prepared.body
    with pytest.raises(SemanticDeliveryError):
        require_delivery(stripped)


def test_historical_prerequisite_needs_fresh_source_even_when_old_quote_is_fully_delivered(tmp_path):
    plan, config, record = fixture(tmp_path, historical=True)
    plan['precondition_review'] = [{'source_evidence_ref': record['id'], 'disposition': 'REQUIRED'}]
    request = _request(tmp_path, plan, config)
    body = json.loads(request.messages[1]['content'])
    assert body['accepted_plan']['coordination_context']['source_support'][0]['excerpt'] == record['excerpt']
    assert body['accepted_plan']['coordination_context']['source_support'][0]['snapshot_id'] != plan['snapshot_id']
    assert body['read_only_source_support'][0]['snapshot_id'] == plan['snapshot_id']
    frame = _context(tmp_path, plan, record, explicit_precondition=True)
    require_delivery(_report(request, frame)[0])
    with pytest.raises(SemanticDeliveryError, match='REQUIRED_SOURCE_UNVERIFIABLE'):
        require_delivery(_report(request, frame, _drop_support)[0])
