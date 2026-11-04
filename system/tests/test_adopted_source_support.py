"""The builder's adopted helper/API records remain independent of wire output."""
from copy import deepcopy
import hashlib
import json

import pytest

from contractedge.model.admission import quote_request
from contractedge.model.provider import ModelRequest
from contractedge.orchestration import semantic_delivery as delivery
from tests.test_adopted_stage_history import WireCapture
from tests.test_compiler_dependency_context import fixture, collect


SOURCE = dict(id='HELPER-1', kind='SOURCE_SPAN', snapshot_id='current',
    locator=dict(path='helper.go', start_line=1, end_line=1),
    excerpt='func helper() {}\n', content_digest=hashlib.sha256(b'func helper() {}\n').hexdigest(),
    origin='Current exact helper declaration')


def registered_request(body, records):
    request = ModelRequest(model='offline', prompt_version='helper-registration', max_output_tokens=8,
        messages=({'role': 'user', 'content': json.dumps(body)},))
    # Before implementation the actual behavior is no builder registration.
    # This fallback permits a behavioral RED, not an import/API-error RED.
    register = getattr(delivery, 'with_adopted_source_support', lambda request, records: request)
    return register(request, records)


@pytest.mark.parametrize('kind', ['local_helper', 'public_dependency'])
@pytest.mark.parametrize('fault', ['none', 'body', 'metadata'])
def test_adopted_source_and_public_api_require_body_and_original_identity(tmp_path, kind, fault):
    if kind == 'local_helper':
        record = deepcopy(SOURCE)
    else:
        snapshot, feedback, _, _ = fixture(tmp_path)
        records, _ = collect(snapshot, feedback)
        assert len(records) == 1
        record = records[0]
    body = {'selected_material': [deepcopy(record)]}
    request = registered_request(body, [record])
    def mutate(messages):
        parsed = json.loads(messages[0]['content'])
        if fault == 'body':
            parsed['selected_material'][0].pop('excerpt')
        elif fault == 'metadata':
            parsed['selected_material'][0].pop('origin' if kind == 'local_helper' else 'archive_sha256')
        messages[0]['content'] = json.dumps(parsed)
        return messages
    port = WireCapture(mutate)
    context = delivery.delivery_context({}, consumer='EXECUTION', snapshot_id=record.get('snapshot_id', record.get('current_source_snapshot')))
    report = delivery.inspect_delivery(request, quote_request(port, request, len), context)
    if fault != 'none':
        with pytest.raises(delivery.SemanticDeliveryError):
            delivery.require_delivery(report)
    else:
        delivery.require_delivery(report)
        assert report['required_content_count'] == 1
    assert port.sent == []


def test_covered_source_shares_exact_target_bytes_without_duplicate_body(tmp_path):
    (tmp_path / 'helper.go').write_text(SOURCE['excerpt'])
    identity = {key: value for key, value in SOURCE.items() if key != 'excerpt'}
    body = {'source_support': [identity],
        'current_target_files': [{'path': 'helper.go', 'content': SOURCE['excerpt']}],
        'acceptance': {'snapshot_id': 'current'}}
    request = registered_request(body, [SOURCE])
    context = delivery.delivery_context({}, consumer='EXECUTION', snapshot_id='current',
        plan={'operations': [{'target': {'path': 'helper.go'}}]},
        necessary_support=[SOURCE], source_root=tmp_path)
    report = delivery.inspect_delivery(request, quote_request(None, request, len), context)
    delivery.require_delivery(report)
    assert sum(row['kind'] == 'EVIDENCE' for row in report['subjects']) == 1
    assert next(row for row in report['subjects'] if row['kind'] == 'EVIDENCE')['required_content_gate']


def test_registration_freezes_the_chosen_record_without_changing_the_wire():
    records = [deepcopy(SOURCE)]
    original = ModelRequest(model='offline', prompt_version='immutable-helper', max_output_tokens=8,
        messages=({'role': 'user', 'content': json.dumps({'selected_material': records})},))
    register = getattr(delivery, 'with_adopted_source_support', lambda request, records: request)
    request = register(original, records)
    assert request.messages == original.messages and original.metadata == {}
    records[0]['excerpt'] = 'modified after selection'
    report = delivery.inspect_delivery(request, quote_request(None, request, len),
        delivery.delivery_context({}, consumer='EXECUTION', snapshot_id='current'))
    delivery.require_delivery(report)
    assert report['required_content_count'] == 1
