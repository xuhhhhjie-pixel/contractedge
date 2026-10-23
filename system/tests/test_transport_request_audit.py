"""Final HTTP bytes are durable before dispatch; transports are deterministic fakes."""
import copy
import hashlib
import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.money import MoneyBudget
from contractedge.model.provider import ModelRequest, OpenAICompatibleProvider, OllamaNativeProvider


SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}, 'note': {'type': 'string'}},
          'required': ['answer'], 'additionalProperties': False}


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return json.dumps(self.body).encode('utf-8')


def request(schema=SCHEMA, thinking=None):
    return ModelRequest('fixture-model', 'fixture.transport',
        ({'role': 'user', 'content': 'Return JSON. 保留当前来源。'},), 64,
        temperature=0.25, output_schema=copy.deepcopy(schema), thinking=thinking)


def response(kind, model='fixture-model'):
    if kind == 'openai':
        return {'id': 'fixture-id', 'model': model, 'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
                'choices': [{'message': {'content': '{"answer":"ok"}'}, 'finish_reason': 'stop'}]}
    return {'model': model, 'message': {'content': '{"answer":"ok"}'}, 'done_reason': 'stop',
            'prompt_eval_count': 3, 'eval_count': 2}


def provider(kind, **options):
    if kind == 'openai':
        return OpenAICompatibleProvider('https://fixture.invalid/v1', 'CE_TRANSPORT_FIXTURE_KEY', **options)
    return OllamaNativeProvider('http://fixture.invalid', **options)


@pytest.fixture(autouse=True)
def dummy_key(monkeypatch):
    monkeypatch.setenv('CE_TRANSPORT_FIXTURE_KEY', 'transport-fixture-token')


def budget_with_disk_ledger(port, tmp_path, **options):
    path = tmp_path / 'calls.json'
    def persist(calls):
        path.write_text(json.dumps(calls, ensure_ascii=False), encoding='utf-8')
    return BudgetedProvider(port, len, max_tokens=100000, max_seconds=10,
                            on_update=persist, **options), path


def assert_pre_dispatch_audit(path, http_request, adapter):
    record = json.loads(path.read_text(encoding='utf-8'))[0]
    audit = record['transport_request']
    assert audit['adapter'] == adapter
    assert audit['adapter_class'].startswith('contractedge.model.provider.')
    assert audit['method'] == 'POST' and audit['content_type'] == 'application/json'
    assert audit['body_utf8'].encode('utf-8') == http_request.data
    assert audit['body_sha256'] == hashlib.sha256(http_request.data).hexdigest()
    assert audit['body_bytes'] == len(http_request.data)
    assert 'response_content' not in record and 'error' not in record
    serialized = json.dumps(record)
    assert 'transport-fixture-token' not in serialized and 'Authorization' not in serialized
    return record, json.loads(http_request.data)


@pytest.mark.parametrize('mode,support,fallback,override,effective,thinking,effort', [
    ('json_object', False, 'error', None, 'json_object', 'enabled', 'high'),
    ('json_schema', True, 'error', 'disabled', 'json_schema', 'disabled', None),
    ('json_schema', False, 'json_object', None, 'json_object', 'enabled', 'high'),
])
def test_openai_final_payload_is_persisted_before_dispatch(tmp_path, mode, support, fallback,
                                                          override, effective, thinking, effort):
    port = provider('openai', output_mode=mode, supports_json_schema=support, schema_fallback=fallback,
                    thinking='enabled', reasoning_effort='high')
    req = request(thinking=override)
    budget, path = budget_with_disk_ledger(port, tmp_path)
    seen = []
    def transport(http_request, timeout):
        record, payload = assert_pre_dispatch_audit(path, http_request, 'openai-compatible')
        seen.append(payload)
        assert record['request']['thinking'] == override
        assert payload['thinking'] == {'type': thinking}
        assert payload.get('reasoning_effort') == effort
        assert payload['response_format']['type'] == effective
        settings = record['transport_request']['effective_settings']
        assert settings['output_mode_requested'] == mode and settings['output_mode_effective'] == effective
        assert settings['effective_thinking'] == thinking and settings['effective_reasoning_effort'] == effort
        assert settings['strict_schema'] is (effective == 'json_schema')
        if effective == 'json_schema':
            assert payload['response_format']['json_schema']['strict'] is True
            assert payload['response_format']['json_schema']['schema']['required'] == ['answer', 'note']
        return Response(response('openai'))
    with patch('urllib.request.urlopen', side_effect=transport):
        budget.complete_json(req)
    assert len(seen) == 1 and budget.calls[0]['response_metadata']['usage']['completion_tokens'] == 2


@pytest.mark.parametrize('schema', [None, SCHEMA])
def test_ollama_final_native_format_and_options_are_persisted(tmp_path, schema):
    port = provider('ollama', context_tokens=8192)
    budget, path = budget_with_disk_ledger(port, tmp_path)
    def transport(http_request, timeout):
        record, payload = assert_pre_dispatch_audit(path, http_request, 'ollama-native')
        assert payload['format'] == (schema if schema is not None else 'json')
        assert payload['options'] == {'temperature': 0.25, 'num_predict': 64, 'num_ctx': 8192}
        assert payload['stream'] is False and 'think' not in payload
        settings = record['transport_request']['effective_settings']
        assert settings['output_mode_effective'] == ('native_schema' if schema is not None else 'json_object')
        assert settings['effective_thinking'] is None and settings['effective_reasoning_effort'] is None
        return Response(response('ollama'))
    with patch('urllib.request.urlopen', side_effect=transport):
        budget.complete_json(request(schema, thinking='enabled'))


@pytest.mark.parametrize('kind', ['openai', 'ollama'])
@pytest.mark.parametrize('failure', ['http400', 'timeout', 'identity'])
def test_failed_dispatch_retains_payload_usage_and_conservative_reservations(tmp_path, kind, failure):
    money = MoneyBudget(tmp_path/'money.json', limit_cny='10', model='fixture-model',
                        input_cny_per_million='2', output_cny_per_million='8')
    options = {'model_identity_policy': 'require_match'}
    if kind == 'openai': options['money_budget'] = money
    port = provider(kind, **options)
    budget, path = budget_with_disk_ledger(port, tmp_path)
    def transport(http_request, timeout):
        assert_pre_dispatch_audit(path, http_request, 'openai-compatible' if kind == 'openai' else 'ollama-native')
        if failure == 'http400':
            raise urllib.error.HTTPError(http_request.full_url, 400, 'Bad Request', {}, io.BytesIO(b'unsupported'))
        if failure == 'timeout': raise TimeoutError('fixture timeout')
        return Response(response(kind, model='unlisted-model'))
    with patch('urllib.request.urlopen', side_effect=transport) as send, pytest.raises(Exception):
        budget.complete_json(request())
    assert send.call_count == 1 and len(budget.calls) == 1 and budget.reserved_tokens > 64
    record = json.loads(path.read_text(encoding='utf-8'))[0]
    assert record['transport_request']['body_sha256'] and record['error']
    if failure == 'identity':
        assert record['response_content'] == '{"answer":"ok"}'
        assert record['response_metadata']['usage'] == {'prompt_tokens': 3, 'completion_tokens': 2}
        assert record['response_metadata']['model_identity_status'] == 'MISMATCH'
    if kind == 'openai':
        charge = json.loads(money.path.read_text())['calls'][0]
        if failure == 'identity':
            assert charge['status'] == 'USAGE_UNKNOWN'
            assert charge['charged_micro_cny'] == charge['reserved_micro_cny']
        else:
            assert charge['status'] == 'RESERVED'


def test_prepared_body_is_serialized_once_and_complete_json_override_remains_active(tmp_path):
    class RecordingPort(OpenAICompatibleProvider):
        def complete_json(self, req):
            self.active_request = req
            return super().complete_json(req)
    port = RecordingPort('https://fixture.invalid/v1', 'CE_TRANSPORT_FIXTURE_KEY',
                         thinking='enabled', reasoning_effort='high')
    req = request()
    writes, body_serializations = [], []
    dumps = json.dumps
    def serialize(value, *args, **kwargs):
        if isinstance(value, dict) and {'model', 'messages', 'max_tokens'} <= value.keys():
            body_serializations.append(value)
        return dumps(value, *args, **kwargs)
    def persist(calls):
        writes.append(copy.deepcopy(calls))
        if len(writes) == 1:
            req.messages[0]['content'] = 'mutated after preparation'
            port.thinking = 'disabled'
    budget = BudgetedProvider(port, len, max_tokens=100000, max_seconds=10, on_update=persist)
    def transport(http_request, timeout):
        assert port.active_request is req
        audit = writes[0][0]['transport_request']
        assert audit['body_utf8'].encode() == http_request.data
        payload = json.loads(http_request.data)
        assert payload['messages'][0]['content'] == 'Return JSON. 保留当前来源。'
        assert payload['thinking'] == {'type': 'enabled'}
        return Response(response('openai'))
    with patch('contractedge.model.provider.json.dumps', side_effect=serialize), \
         patch('urllib.request.urlopen', side_effect=transport):
        budget.complete_json(req)
    assert len(body_serializations) == 1


def test_preparation_failure_never_reserves_or_dispatches(tmp_path):
    budget, _ = budget_with_disk_ledger(provider('openai', output_mode='json_schema'), tmp_path)
    with patch('urllib.request.urlopen') as transport, pytest.raises(ValueError, match='not declared'):
        budget.complete_json(request())
    assert not budget.calls and budget.reserved_tokens == 0
    transport.assert_not_called()


def test_invalid_thinking_override_never_creates_a_phantom_call(tmp_path):
    budget, _ = budget_with_disk_ledger(provider('openai'), tmp_path)
    with patch('urllib.request.urlopen') as transport, pytest.raises(ValueError, match='thinking override'):
        budget.complete_json(request(thinking='invalid'))
    assert not budget.calls and budget.reserved_tokens == 0
    transport.assert_not_called()


def test_prepared_request_deadline_is_checked_before_dispatch(tmp_path):
    clock = [0.0]
    port = provider('openai')
    prepare = port.prepare_request
    def slow_prepare(req):
        prepared = prepare(req)
        clock[0] = 2.0
        return prepared
    with patch('contractedge.model.budget.time.monotonic', side_effect=lambda: clock[0]):
        budget = BudgetedProvider(port, len, max_tokens=100000, max_seconds=1)
        with patch.object(port, 'prepare_request', side_effect=slow_prepare), \
             patch('urllib.request.urlopen') as transport, pytest.raises(BudgetExceeded):
            budget.complete_json(request())
    assert not budget.calls and budget.reserved_tokens == 0
    transport.assert_not_called()


def test_budget_limits_and_preparation_deadline_prevent_dispatch(tmp_path):
    port = provider('openai')
    budget, _ = budget_with_disk_ledger(port, tmp_path, max_calls=1)
    with patch('urllib.request.urlopen', return_value=Response(response('openai'))) as transport:
        budget.complete_json(request())
        with pytest.raises(BudgetExceeded): budget.complete_json(request())
    assert transport.call_count == 1 and len(budget.calls) == 1


def test_credential_bearing_endpoint_and_errors_do_not_enter_transport_ledger(tmp_path):
    endpoint = 'https://fixture-user:fixture-password@fixture.invalid/v1?api_key=fixture-query'
    port = OpenAICompatibleProvider(endpoint, 'CE_TRANSPORT_FIXTURE_KEY')
    budget, path = budget_with_disk_ledger(port, tmp_path)
    redirected = 'https://redirect-user:redirect-password@redirect.invalid/?key=redirect-query'
    error = urllib.error.URLError(endpoint + '/chat/completions transport-fixture-token ' + redirected)
    with patch('urllib.request.urlopen', side_effect=error), pytest.raises(urllib.error.URLError):
        budget.complete_json(request())
    serialized = path.read_text(encoding='utf-8')
    for sensitive in ('fixture-user', 'fixture-password', 'fixture-query', 'transport-fixture-token',
                      'redirect-user', 'redirect-password', 'redirect-query', 'Authorization'):
        assert sensitive not in serialized


def test_subclass_cannot_silently_replace_an_already_audited_request(tmp_path):
    from dataclasses import replace
    class TransformingPort(OpenAICompatibleProvider):
        def complete_json(self, req):
            return super().complete_json(replace(req, temperature=0.9))
    port = TransformingPort('https://fixture.invalid/v1', 'CE_TRANSPORT_FIXTURE_KEY')
    budget, path = budget_with_disk_ledger(port, tmp_path)
    with patch('urllib.request.urlopen') as send, pytest.raises(ValueError, match='already prepared'):
        budget.complete_json(request())
    send.assert_not_called()
    record = json.loads(path.read_text())[0]
    assert json.loads(record['transport_request']['body_utf8'])['temperature'] == 0.25
    assert record['error'] and budget.reserved_tokens > 0


@pytest.mark.parametrize('fragment', ['fixture-password@fixture.invalid', 'encoded@password', 'query-secret', 'fixture%2Dquery'])
def test_partial_url_credentials_are_removed_from_errors(tmp_path, fragment):
    import http.client
    port = OpenAICompatibleProvider(
        'https://fixture-user:encoded%40password@fixture.invalid/v1?api_key=query-secret',
        'CE_TRANSPORT_FIXTURE_KEY')
    # A second endpoint makes the ordinary stdlib nonnumeric-port diagnostic explicit.
    if fragment.startswith('fixture-password'):
        port.base_url = 'https://fixture-user:fixture-password@fixture.invalid/v1'
    if fragment == 'fixture%2Dquery':
        port.base_url = 'https://fixture.invalid/v1?api_key=fixture%2Dquery'
    budget, path = budget_with_disk_ledger(port, tmp_path)
    error = http.client.InvalidURL("nonnumeric port: '" + fragment + "'")
    with patch('urllib.request.urlopen', side_effect=error), pytest.raises(http.client.InvalidURL):
        budget.complete_json(request())
    saved = path.read_text()
    assert fragment not in saved
    assert all(secret not in saved for secret in ['fixture-password', 'encoded@password', 'query-secret'])
