"""Actual execution target sharing replaces only an excerpt, not its metadata."""
from dataclasses import replace
import json

import pytest

from contractedge.model.admission import quote_request
from contractedge.model.provider import OpenAICompatibleProvider
from contractedge.orchestration.semantic_delivery import (
    SemanticDeliveryError, delivery_context, inspect_delivery, require_delivery,
)
from tests.test_execution_support_windows import fixture, request


@pytest.mark.parametrize('mutation', [
    'unchanged', 'omit_origin', 'change_origin', 'omit_delivery_required', 'false_delivery_required',
])
def test_exact_target_content_preserves_complete_original_support_metadata(tmp_path, mutation):
    plan, config, record = fixture(tmp_path, support_start=195, support_end=197)
    config['max_execution_support_bytes'] = 0
    built, body = request(tmp_path, plan, config)
    identity = body['accepted_plan']['coordination_context']['source_support'][0]
    assert 'excerpt' not in identity
    assert identity['origin'] == record['origin']
    assert body['read_only_source_support'] == []
    assert record['excerpt'] in body['current_target_files'][0]['content']
    if mutation == 'omit_origin':
        identity.pop('origin')
    elif mutation == 'change_origin':
        identity['origin'] = 'An unrelated producer'
    elif mutation == 'omit_delivery_required':
        identity.pop('delivery_required')
    elif mutation == 'false_delivery_required':
        identity['delivery_required'] = False
    altered = replace(built, messages=(built.messages[0], {
        **built.messages[1], 'content': json.dumps(body)}))
    provider = OpenAICompatibleProvider('https://fixture.invalid', 'UNUSED_FIXTURE_CREDENTIAL')
    context = delivery_context({}, consumer='EXECUTION', snapshot_id=plan['snapshot_id'],
        plan=plan, necessary_support=[record], source_root=tmp_path)
    report = inspect_delivery(altered, quote_request(provider, altered, len), context)
    if mutation in {'omit_origin', 'change_origin'}:
        with pytest.raises(SemanticDeliveryError):
            require_delivery(report)
    else:
        require_delivery(report)
