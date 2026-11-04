"""A missing shared reference cannot witness an explicitly retained JSON null."""
import json

import pytest

from contractedge.model.admission import quote_request
from contractedge.model.provider import ModelRequest
from contractedge.orchestration.semantic_delivery import (
    SemanticDeliveryError, delivery_context, inspect_delivery, require_delivery,
)


HISTORY = {
    'model_response': '{"edits":[]}',
    'validation_error': None,
    'plan_digest': 'a' * 64,
    'source_snapshot': 's0',
}


def inspect_history(body, *, prior_message=None):
    messages = []
    if prior_message is not None:
        messages.append({'role': 'user', 'content': json.dumps(prior_message)})
    messages.append({'role': 'user', 'content': json.dumps(body)})
    request = ModelRequest(model='offline', prompt_version='reference-boundary',
        max_output_tokens=8, messages=tuple(messages))
    context = delivery_context({}, consumer='EXECUTION_CORRECTION', snapshot_id='s0',
        historical_contexts=[HISTORY])
    return inspect_delivery(request, quote_request(None, request, len), context)


def shared_history(reference):
    projected = {key: value for key, value in HISTORY.items() if key != 'validation_error'}
    projected['validation_error_ref'] = reference
    return {'previous_execution_attempt': projected}


@pytest.mark.parametrize('reference', [
    '/missing', '/values/0', '/values/01', '/values/2',
    'feedback.public_check_results', 'unsupported-reference',
])
def test_unresolved_reference_cannot_replace_historical_null(reference):
    body = shared_history(reference)
    # A malformed container and noncanonical/out-of-range indices must all
    # remain unresolved, rather than becoming a delivered null.
    body['values'] = [False]
    report = inspect_history(body)
    with pytest.raises(SemanticDeliveryError):
        require_delivery(report)


def test_null_reference_cannot_resolve_against_a_different_message():
    body = shared_history('/shared_validation_error')
    report = inspect_history(body, prior_message={'shared_validation_error': None})
    with pytest.raises(SemanticDeliveryError):
        require_delivery(report)


def test_missing_direct_null_field_is_not_delivery():
    body = {'previous_execution_attempt': {
        key: value for key, value in HISTORY.items() if key != 'validation_error'}}
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect_history(body))


@pytest.mark.parametrize('mode', ['direct', 'absolute_reference', 'relative_reference'])
def test_an_actual_null_value_remains_valid_complete_content(mode):
    if mode == 'direct':
        body = {'previous_execution_attempt': dict(HISTORY)}
    elif mode == 'absolute_reference':
        body = shared_history('/shared_validation_error')
        body['shared_validation_error'] = None
    else:
        body = shared_history('feedback.public_check_results')
        body['previous_execution_attempt']['feedback'] = {'public_check_results': None}
    require_delivery(inspect_history(body))
