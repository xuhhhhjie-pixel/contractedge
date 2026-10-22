"""Mock transport coverage; no real model endpoints or credentials."""
import io
import json
import urllib.error
from unittest.mock import patch

import pytest
from jsonschema import Draft202012Validator

from contractedge.cli import _build_provider
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.money import MoneyBudget
from contractedge.model.provider import ModelRequest, OpenAICompatibleProvider, OllamaNativeProvider

SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}, 'note': {'type': 'string'}},
          'required': ['answer'], 'additionalProperties': False}


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return json.dumps(self.body).encode()


def raw_response(content='{"answer":"ok"}', model='fixture-model'):
    result = {'id': 'fixture-response', 'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
              'usage': {'prompt_tokens': 3, 'completion_tokens': 2}}
    if model is not None: result['model'] = model
    return result


def request(schema=SCHEMA):
    return ModelRequest('fixture-model', 'fixture.policy',
        ({'role': 'user', 'content': 'Return JSON matching ' + json.dumps(schema)},), 64, output_schema=schema)


@pytest.fixture(autouse=True)
def dummy_key(monkeypatch):
    monkeypatch.setenv('CE_POLICY_FIXTURE_KEY', 'not-a-real-key')


def provider(**kwargs):
    return OpenAICompatibleProvider('https://fixture.invalid/v1', 'CE_POLICY_FIXTURE_KEY', **kwargs)


def test_default_metadata_identifies_json_mode_and_model():
    with patch('urllib.request.urlopen', return_value=Response(raw_response())) as transport:
        response = provider().complete_json(request())
    assert json.loads(transport.call_args.args[0].data)['response_format'] == {'type': 'json_object'}
    assert response.metadata['output_mode_effective'] == 'json_object'
    assert response.metadata['requested_model'] == 'fixture-model'
    assert response.metadata['reported_model'] == 'fixture-model'
    assert response.metadata['model_identity_status'] == 'MATCH'


def test_missing_model_is_unreported_not_an_invented_echo():
    with patch('urllib.request.urlopen', return_value=Response(raw_response(model=None))):
        response = provider().complete_json(request())
    assert response.metadata['reported_model'] is None
    assert response.metadata['model'] is None
    assert response.metadata['model_identity_status'] == 'UNREPORTED'


def test_strict_wire_schema_restores_optional_null_without_changing_original():
    original = json.loads(json.dumps(SCHEMA))
    with patch('urllib.request.urlopen', return_value=Response(raw_response('{"answer":"ok","note":null}'))) as transport:
        response = provider(output_mode='json_schema', supports_json_schema=True).complete_json(request())
    sent = json.loads(transport.call_args.args[0].data)['response_format']
    assert sent['type'] == 'json_schema' and sent['json_schema']['strict'] is True
    wire = sent['json_schema']['schema']
    assert set(wire['required']) == {'answer', 'note'}
    assert Draft202012Validator(wire).is_valid({'answer': 'ok', 'note': None})
    assert json.loads(response.content) == {'answer': 'ok'}
    assert response.metadata['raw_response_content'] == '{"answer":"ok","note":null}'
    assert response.metadata['output_schema_restoration'] == 'RESTORED'
    assert SCHEMA == original


def test_strict_projection_preserves_nested_arrays_and_original_nullable_values():
    from contractedge.model.output_contract import strict_transport_schema, restore_strict_content
    schema = {'type': 'object', 'properties': {'items': {'type': 'array', 'items': {'type': 'object',
        'properties': {'name': {'type': 'string'}, 'extra': {'type': 'string'},
                       'nullable': {'type': ['string', 'null']}},
        'required': ['name'], 'additionalProperties': False}}}, 'required': ['items'], 'additionalProperties': False}
    content = '{"items":[{"name":"one","extra":null,"nullable":null}]}'
    restored, status = restore_strict_content(content, schema, strict_transport_schema(schema))
    assert status == 'RESTORED'
    assert json.loads(restored) == {'items': [{'name': 'one', 'nullable': None}]}


@pytest.mark.parametrize('content,status', [('{"wrong":true}', 'WIRE_SCHEMA_MISMATCH'), ('{"answer":', 'INVALID_JSON')])
def test_invalid_strict_content_is_preserved_for_application_validation(content, status):
    with patch('urllib.request.urlopen', return_value=Response(raw_response(content))):
        response = provider(output_mode='json_schema', supports_json_schema=True).complete_json(request())
    assert response.content == content
    assert response.metadata['output_schema_restoration'] == status
    if status == 'WIRE_SCHEMA_MISMATCH':
        assert not Draft202012Validator(SCHEMA).is_valid(json.loads(response.content))


def test_unknown_endpoint_fails_before_http_in_strict_mode():
    with patch('urllib.request.urlopen') as transport, pytest.raises(ValueError, match='not declared'):
        provider(output_mode='json_schema').complete_json(request())
    transport.assert_not_called()


def test_explicit_preflight_fallback_is_logged_and_uses_one_call():
    port = provider(output_mode='json_schema', schema_fallback='json_object')
    budget = BudgetedProvider(port, len, max_tokens=100000, max_seconds=10, max_calls=1)
    with patch('urllib.request.urlopen', return_value=Response(raw_response())) as transport:
        response = budget.complete_json(request())
    assert transport.call_count == 1 and budget.summary()['model_calls'] == 1
    assert response.metadata['output_mode_requested'] == 'json_schema'
    assert response.metadata['output_mode_effective'] == 'json_object'
    assert response.metadata['output_fallback_reason']
    with pytest.raises(BudgetExceeded): budget.complete_json(request())


@pytest.mark.parametrize('schema', [
    {'type': 'object', 'properties': {}, 'additionalProperties': True},
    {'$ref': '#/$defs/thing', '$defs': {'thing': {'type': 'object'}}},
])
def test_unhandled_schema_is_explicit_not_silently_rewritten(schema):
    with patch('urllib.request.urlopen') as transport, pytest.raises(ValueError):
        provider(output_mode='json_schema', supports_json_schema=True).complete_json(request(schema))
    transport.assert_not_called()


def test_http_rejection_does_not_hide_an_extra_fallback_attempt():
    port = provider(output_mode='json_schema', supports_json_schema=True, schema_fallback='json_object')
    budget = BudgetedProvider(port, len, max_tokens=100000, max_seconds=10)
    error = urllib.error.HTTPError('https://fixture.invalid', 400, 'Bad Request', {}, io.BytesIO(b'unsupported'))
    with patch('urllib.request.urlopen', side_effect=error) as transport, pytest.raises(urllib.error.HTTPError):
        budget.complete_json(request())
    assert transport.call_count == 1 and budget.summary()['model_calls'] == 1
    assert budget.calls[0]['provider_request_metadata']['output_mode_effective'] == 'json_schema'
    assert budget.calls[0]['error']


@pytest.mark.parametrize('reported,status,verified', [
    ('fixture-model', 'MATCH', True), ('fixture-version', 'ALIAS', True),
    ('unlisted-model', 'MISMATCH', False), (None, 'UNREPORTED', False),
])
def test_identity_policy_distinguishes_exact_alias_unknown_and_missing(reported, status, verified):
    with patch('urllib.request.urlopen', return_value=Response(raw_response(model=reported))):
        response = provider(model_aliases={'fixture-model': ['fixture-version']}).complete_json(request())
    assert response.metadata['model_identity_status'] == status
    assert response.metadata['model_identity_verified'] is verified
    assert response.metadata['reported_model'] == reported


@pytest.mark.parametrize('reported', ['unlisted-model', None])
def test_strict_identity_preserves_response_usage_reservation_and_stops_http(tmp_path, reported):
    money = MoneyBudget(tmp_path/'money.json', limit_cny='10', model='fixture-model',
                        input_cny_per_million='2', output_cny_per_million='8')
    port = provider(model_identity_policy='require_match', money_budget=money)
    updates = []
    budget = BudgetedProvider(port, len, max_tokens=100000, max_seconds=10,
                              on_update=lambda calls: updates.append(json.loads(json.dumps(calls))))
    with patch('urllib.request.urlopen', return_value=Response(raw_response(model=reported))) as transport:
        with pytest.raises(RuntimeError, match='model identity'): budget.complete_json(request())
        with pytest.raises(RuntimeError, match='model identity'): budget.complete_json(request())
    assert transport.call_count == 1
    assert budget.calls[0]['response_content'] == '{"answer":"ok"}'
    assert budget.calls[0]['response_metadata']['usage'] == {'prompt_tokens': 3, 'completion_tokens': 2}
    assert updates[-1][0]['response_metadata']['reported_model'] == reported
    ledger = json.loads(money.path.read_text())
    assert len(ledger['calls']) == 1
    assert ledger['calls'][0]['charged_micro_cny'] == ledger['calls'][0]['reserved_micro_cny']
    assert ledger['calls'][0]['status'] == 'USAGE_UNKNOWN'
    assert budget.reserved_tokens > 0


def test_allowed_alias_can_settle_usage(tmp_path):
    money = MoneyBudget(tmp_path/'money.json', limit_cny='10', model='fixture-model',
                        input_cny_per_million='2', output_cny_per_million='8')
    with patch('urllib.request.urlopen', return_value=Response(raw_response(model='fixture-version'))):
        provider(money_budget=money, model_identity_policy='require_match',
                 model_aliases={'fixture-model': ['fixture-version']}).complete_json(request())
    assert json.loads(money.path.read_text())['calls'][0]['status'] == 'SETTLED'


def test_strict_schema_transport_is_included_in_shared_token_budget():
    port = provider(output_mode='json_schema', supports_json_schema=True)
    req = request()
    messages = sum(len(message['content']) for message in req.messages)
    budget = BudgetedProvider(port, len, max_tokens=messages + req.max_output_tokens, max_seconds=10)
    with patch('urllib.request.urlopen') as transport, pytest.raises(BudgetExceeded):
        budget.complete_json(req)
    transport.assert_not_called()
    assert not budget.calls


def test_native_ollama_keeps_original_format_and_adds_identity_metadata():
    raw = {'model': 'fixture-version', 'message': {'content': '{"answer":"ok"}'},
           'done_reason': 'stop', 'prompt_eval_count': 3, 'eval_count': 2}
    port = OllamaNativeProvider('http://fixture.invalid', model_identity_policy='require_match',
        model_aliases={'fixture-model': ['fixture-version']})
    with patch('urllib.request.urlopen', return_value=Response(raw)) as transport:
        response = port.complete_json(request())
    assert json.loads(transport.call_args.args[0].data)['format'] == SCHEMA
    assert response.metadata['model_identity_status'] == 'ALIAS'
    assert response.metadata['output_mode_effective'] == 'native_schema'


def test_cli_config_wires_output_and_identity_without_reading_credentials():
    port, _ = _build_provider({'provider': 'openai_compatible', 'base_url': 'https://fixture.invalid',
        'api_key_env': 'UNSET_FIXTURE_KEY', 'model': 'fixture-model', 'tokenizer': 'builtins:len',
        'output_mode': 'json_schema', 'supports_json_schema': True, 'schema_in_prompt': False,
        'model_identity_policy': 'require_match', 'model_aliases': {'fixture-model': ['fixture-version']}})
    assert port.output_mode == 'json_schema'
    assert port.model_identity_policy == 'require_match'


@pytest.mark.parametrize('options', [
    {'output_mode': 'text'}, {'supports_json_schema': 'yes'}, {'schema_fallback': 'silent'},
    {'model_identity_policy': 'ignore'}, {'model_aliases': {'fixture-model': 'version'}},
])
def test_invalid_configuration_fails_explicitly(options):
    with pytest.raises(ValueError): provider(**options)


def test_strict_transport_runs_the_actual_diagnosis_and_planning_pipeline(tmp_path):
    import hashlib
    from contractedge.orchestration.runner import run_front_half
    from tests.test_orchestration import PipelineFixtureProvider
    from tests.test_planning_evidence_extension import front_fixture
    value, path = front_fixture(tmp_path)
    fixture = PipelineFixtureProvider()
    fixture._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(path.read_bytes()).hexdigest()}
    sent = []

    def fixture_wire(value, schema):
        if isinstance(value, dict) and schema.get('type') == 'string' and 'id' in value:
            return value['id']
        if isinstance(value, dict) and 'properties' in schema:
            result = {}
            aliases = {'target_kind': 'kind', 'source_path': 'path'}
            for key, prop in schema['properties'].items():
                source_key = key if key in value else aliases.get(key, key)
                if source_key in value:
                    result[key] = fixture_wire(value[source_key], prop)
                elif key not in schema.get('required', []):
                    result[key] = None
                else:
                    raise AssertionError('Public fixture lacks required model field: ' + key)
            return result
        if isinstance(value, list):
            return [fixture_wire(item, schema['items']) for item in value]
        return value

    class RecordingPort(OpenAICompatibleProvider):
        def complete_json(self, model_request):
            self.active_request = model_request
            return super().complete_json(model_request)

    port = RecordingPort('https://fixture.invalid/v1', 'CE_POLICY_FIXTURE_KEY',
                         output_mode='json_schema', supports_json_schema=True, model_identity_policy='require_match')

    def transport(http_request, timeout):
        wire_request = json.loads(http_request.data)
        sent.append(wire_request)
        generated = json.loads(fixture.complete_json(port.active_request).content)
        if port.active_request.prompt_version.startswith('diagnosis.'):
            public = json.loads(port.active_request.messages[1]['content'])
            generated['requirement_horizon'] = [row['statement'] for row in public['requirement_spec']['outcomes']]
            generated['outcome_coverage'] = {row['outcome_id']: {
                'disposition': 'REPAIR_SUPPORTED', 'preservation_status': 'NOT_APPLICABLE',
                'preservation_evidence_refs': []} for row in generated['outcome_coverage']}
        wire_value = fixture_wire(generated, port.active_request.output_schema)
        errors = list(Draft202012Validator(wire_request['response_format']['json_schema']['schema']).iter_errors(wire_value))
        assert not errors, [(list(error.absolute_path), error.message) for error in errors[:3]]
        return Response(raw_response(json.dumps(wire_value)))

    with patch('urllib.request.urlopen', side_effect=transport):
        trace = run_front_half(value, tmp_path, port,
            {'model': {'model': 'fixture-model', 'schema_in_prompt': False}, 'max_context_tokens': 24000,
             'max_output_tokens': 1024, 'max_total_tokens': 100000}, lambda text: len(text.split()))
    assert trace.status == 'READY', trace.errors
    assert len(sent) == 2
    assert all(call['response_metadata']['output_mode_effective'] == 'json_schema' for call in trace.model_calls)
    assert all('output_schema' not in json.loads(item['messages'][1]['content']) for item in sent)
    assert all(call['response_metadata']['model_identity_status'] == 'MATCH' for call in trace.model_calls)


def test_front_half_retains_received_identity_error_without_recounting_blocked_port(tmp_path):
    from contractedge.orchestration.runner import run_front_half
    from tests.test_planning_evidence_extension import front_fixture
    value, path = front_fixture(tmp_path)
    before = path.read_bytes()
    port = provider(model_identity_policy='require_match')
    with patch('urllib.request.urlopen', return_value=Response(raw_response(model='different-model'))) as transport:
        trace = run_front_half(value, tmp_path, port,
            {'model': {'model': 'fixture-model'}, 'max_context_tokens': 24000,
             'max_output_tokens': 1024, 'max_total_tokens': 100000}, len)
    assert trace.status == 'INFRASTRUCTURE_ERROR'
    assert len(trace.model_calls) == 1 and transport.call_count == 1
    call = trace.model_calls[0]
    assert call['response_content'] == '{"answer":"ok"}'
    assert call['response_metadata']['reported_model'] == 'different-model'
    assert call['response_metadata']['usage']['completion_tokens'] == 2
    assert call['error'].startswith('ProviderResponseError:')
    assert path.read_bytes() == before


def test_declared_model_context_accounts_for_strict_schema_before_http():
    req = request()
    messages = sum(len(message['content']) for message in req.messages)
    port = provider(output_mode='json_schema', supports_json_schema=True,
                    context_tokens=messages + req.max_output_tokens, token_counter=len)
    with patch('urllib.request.urlopen') as transport, pytest.raises(ValueError, match='model context'):
        port.complete_json(req)
    transport.assert_not_called()


def test_record_policy_retains_conservative_money_charge_for_unknown_model(tmp_path):
    money = MoneyBudget(tmp_path/'money.json', limit_cny='10', model='fixture-model',
                        input_cny_per_million='2', output_cny_per_million='8')
    with patch('urllib.request.urlopen', return_value=Response(raw_response(model='unknown'))):
        response = provider(money_budget=money).complete_json(request())
    assert response.metadata['model_identity_status'] == 'MISMATCH'
    ledger = json.loads(money.path.read_text())['calls'][0]
    assert ledger['status'] == 'USAGE_UNKNOWN'
    assert ledger['charged_micro_cny'] == ledger['reserved_micro_cny']



def test_request_metadata_failure_does_not_leave_an_unrecorded_reservation():
    class InvalidMetadata:
        def request_metadata(self, request): raise ValueError('invalid prepared metadata')
        def complete_json(self, request): raise AssertionError('transport must not run')
    budget = BudgetedProvider(InvalidMetadata(), len, max_tokens=100000, max_seconds=10)
    with pytest.raises(ValueError, match='invalid prepared metadata'): budget.complete_json(request())
    assert not budget.calls and budget.reserved_tokens == 0


def test_deadline_expiring_during_metadata_preparation_stops_before_transport():
    clock = [0.0]
    class SlowPreparation:
        def request_metadata(self, request): clock[0] = 2.0; return {}
        def complete_json(self, request): raise AssertionError('transport must not run')
    with patch('contractedge.model.budget.time.monotonic', side_effect=lambda: clock[0]):
        budget = BudgetedProvider(SlowPreparation(), len, max_tokens=100000, max_seconds=1)
        with pytest.raises(BudgetExceeded, match='request preparation'): budget.complete_json(request())
    assert len(budget.calls) == 1 and 'error' in budget.calls[0]
