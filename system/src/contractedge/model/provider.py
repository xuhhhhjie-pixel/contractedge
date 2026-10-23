from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import urllib.request
from urllib.parse import urlsplit, unquote, parse_qsl
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from contractedge.model.output_contract import strict_transport_schema, restore_strict_content


@dataclass(frozen=True)
class ModelRequest:
    model: str
    prompt_version: str
    messages: tuple[Mapping[str, str], ...]
    max_output_tokens: int
    temperature: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] | None = None
    thinking: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    content: str
    metadata: Mapping[str, Any]


class ModelProvider(Protocol):
    def complete_json(self, request: ModelRequest) -> ModelResponse: ...


class FixtureProvider:
    def __init__(self, payload: Mapping[str, Any]):
        self.payload = dict(payload)

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(json.dumps(self.payload, ensure_ascii=False),
                             {'provider': 'fixture', 'model': request.model,
                              'prompt_version': request.prompt_version, 'finish_reason': 'fixture'})


class ProviderResponseError(RuntimeError):
    """A rejected received response remains available to the durable call ledger."""
    def __init__(self, message: str, response: ModelResponse):
        super().__init__(message)
        self.response = response
        self.provider_metadata = dict(response.metadata)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class _PreparedRequest:
    body: bytes
    metadata: Mapping[str, Any]
    overhead: str
    output_schema: Mapping[str, Any] | None = None
    wire_schema: Mapping[str, Any] | None = None

    def audit_record(self):
        # Only body and declared settings: never copy HTTP headers or endpoint URLs.
        settings = {key: self.metadata.get(key) for key in (
            'output_mode_requested', 'output_mode_effective', 'output_fallback_reason',
            'strict_schema', 'effective_thinking', 'effective_reasoning_effort',
            'requested_output_schema_sha256', 'wire_output_schema_sha256')}
        return {'version': 1, 'adapter': self.metadata['provider'],
                'adapter_class': self.metadata['adapter_class'], 'method': 'POST',
                'content_type': 'application/json', 'body_utf8': self.body.decode('utf-8'),
                'body_bytes': len(self.body), 'body_sha256': hashlib.sha256(self.body).hexdigest(),
                'effective_settings': settings}


_active_prepared_request = ContextVar('contractedge_prepared_request', default=None)


class _IdentityProvider:
    def complete_prepared_json(self, request, prepared):
        # Keep complete_json overrides active, without storing mutable per-call state
        # on a shared adapter or serializing its payload a second time.
        token = _active_prepared_request.set((self, request, prepared))
        try:
            return self.complete_json(request)
        finally:
            _active_prepared_request.reset(token)

    def _prepared_for(self, request):
        self._check_available()
        active = _active_prepared_request.get()
        if active is not None and active[0] is self:
            if active[1] is not request:
                raise ValueError('Provider override changed an already prepared request; transform it before preparation')
            return active[2]
        return self.prepare_request(request)

    def safe_error_message(self, exc):
        message = str(exc).replace(self.base_url, '[provider endpoint]')
        # Redirect/connection errors can mention a different credential-bearing URL.
        message = re.sub(r'https?://[^\s<>\"\']+', '[provider endpoint]', message)
        key_env = getattr(self, 'api_key_env', None)
        key = os.environ.get(key_env) if key_env else None
        sensitive = {key} if key else set()
        try:
            parsed = urlsplit(self.base_url)
            sensitive.update(v for v in (parsed.username, parsed.password) if v)
            sensitive.update(v for _, v in parse_qsl(parsed.query) if v)
        except ValueError:
            # A malformed endpoint should not surface its raw parser diagnostic.
            return type(exc).__name__ + ': provider request failed'
        sensitive.update(unquote(v) for v in tuple(sensitive))
        for value in sorted(sensitive, key=len, reverse=True):
            message = message.replace(value, '[credential]')
        return message

    def _configure_identity(self, policy, aliases):
        if policy not in {'record', 'require_match'}:
            raise ValueError('model_identity_policy must be record or require_match')
        if aliases is not None and not isinstance(aliases, Mapping):
            raise ValueError('model_aliases must map requested names to lists of reported names')
        self.model_aliases = {}
        for name, values in (aliases or {}).items():
            if (not isinstance(name, str) or not name.strip() or not isinstance(values, (list, tuple))
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(values) != len(set(values))):
                raise ValueError('model_aliases requires unique nonempty names in explicit lists')
            self.model_aliases[name] = tuple(values)
        self.model_identity_policy = policy
        self._identity_blocked = False

    def _check_available(self):
        if self._identity_blocked:
            raise RuntimeError('Provider stopped after model identity violation')

    def _identity(self, requested, reported):
        reported = reported if isinstance(reported, str) and reported.strip() else None
        status = ('UNREPORTED' if reported is None else 'MATCH' if reported == requested
                  else 'ALIAS' if reported in self.model_aliases.get(requested, ()) else 'MISMATCH')
        return {'requested_model': requested, 'reported_model': reported, 'model': reported,
                'model_identity_policy': self.model_identity_policy, 'model_identity_status': status,
                'model_identity_verified': status in {'MATCH', 'ALIAS'}}

    def _accept_response(self, content, metadata):
        response = ModelResponse(content, metadata)
        if self.model_identity_policy == 'require_match' and not metadata['model_identity_verified']:
            self._identity_blocked = True
            raise ProviderResponseError('Received model identity is not permitted by the configured policy', response)
        return response


class OpenAICompatibleProvider(_IdentityProvider):
    """Explicit chat-completions modes; unknown endpoints retain JSON compatibility."""

    def __init__(self, base_url: str, api_key_env: str, timeout_seconds: int = 120, *,
                 money_budget=None, thinking=None, reasoning_effort=None,
                 output_mode='json_object', supports_json_schema=False, schema_fallback='error',
                 model_identity_policy='record', model_aliases=None,
                 context_tokens=None, token_counter=None):
        self.base_url = base_url.rstrip('/')
        self.api_key_env, self.timeout_seconds, self.money_budget = api_key_env, timeout_seconds, money_budget
        if thinking not in {None, 'enabled', 'disabled'}:
            raise ValueError('thinking must be enabled or disabled')
        if reasoning_effort not in {None, 'low', 'high', 'max'}:
            raise ValueError('reasoning_effort must be low, high or max')
        if reasoning_effort is not None and thinking != 'enabled':
            raise ValueError('An explicit reasoning effort requires thinking=enabled')
        self.thinking, self.reasoning_effort = thinking, reasoning_effort
        if output_mode not in {'json_object', 'json_schema'}:
            raise ValueError('output_mode must be json_object or json_schema')
        if type(supports_json_schema) is not bool:
            raise ValueError('supports_json_schema must be an explicit boolean')
        if schema_fallback not in {'error', 'json_object'}:
            raise ValueError('schema_fallback must be error or json_object')
        if context_tokens is not None and (type(context_tokens) is not int or context_tokens <= 0 or not callable(token_counter)):
            raise ValueError('A positive context_tokens limit requires a token counter')
        self.output_mode, self.supports_json_schema, self.schema_fallback = output_mode, supports_json_schema, schema_fallback
        self.context_tokens, self.token_counter = context_tokens, token_counter
        self._configure_identity(model_identity_policy, model_aliases)

    def _format(self, request):
        self._check_available()
        metadata = {'output_mode_requested': self.output_mode, 'output_mode_effective': self.output_mode,
                    'output_fallback_reason': None, 'application_validation_required': True,
                    'endpoint_schema_support': 'DECLARED' if self.supports_json_schema else 'UNKNOWN',
                    'model_identity_policy': self.model_identity_policy}
        wire = None
        if self.output_mode == 'json_schema':
            if request.output_schema is None:
                raise ValueError('json_schema mode requires request.output_schema')
            reason = None
            if not self.supports_json_schema:
                reason = 'JSON Schema endpoint support is not declared'
            else:
                try:
                    wire = strict_transport_schema(request.output_schema)
                except ValueError as exc:
                    reason = str(exc)
            if reason:
                if self.schema_fallback == 'error':
                    raise ValueError(reason)
                metadata.update(output_mode_effective='json_object', output_fallback_reason=reason)
        if wire is None:
            format_value = {'type': 'json_object'}
        else:
            name = re.sub('[^a-zA-Z0-9_-]', '_', request.prompt_version)[:64] or 'contractedge_output'
            format_value = {'type': 'json_schema', 'json_schema': {'name': name, 'strict': True, 'schema': wire}}
        metadata['requested_output_schema_sha256'] = _digest(request.output_schema) if request.output_schema is not None else None
        metadata['wire_output_schema_sha256'] = _digest(wire) if wire is not None else None
        return format_value, metadata, wire

    def request_metadata(self, request):
        return self._format(request)[1]

    def request_overhead(self, request):
        format_value, _, wire = self._format(request)
        return json.dumps(format_value, ensure_ascii=False) if wire is not None else ''

    def prepare_request(self, request: ModelRequest):
        format_value, metadata, wire = self._format(request)
        payload = {'model': request.model, 'messages': list(request.messages),
                   'max_tokens': request.max_output_tokens, 'temperature': request.temperature,
                   'response_format': format_value}
        if request.thinking not in {None, 'enabled', 'disabled'}:
            raise ValueError('Request thinking override must be enabled or disabled')
        thinking = self.thinking if request.thinking is None else request.thinking
        if thinking is not None:
            payload['thinking'] = {'type': thinking}
        if thinking == 'enabled' and self.reasoning_effort is not None:
            payload['reasoning_effort'] = self.reasoning_effort
        metadata.update(provider='openai-compatible', prompt_version=request.prompt_version,
                        adapter_class=type(self).__module__ + '.' + type(self).__qualname__,
                        strict_schema=wire is not None, effective_thinking=thinking,
                        effective_reasoning_effort=self.reasoning_effort if thinking == 'enabled' else None)
        return _PreparedRequest(json.dumps(payload, ensure_ascii=False).encode('utf-8'), metadata,
            json.dumps(format_value, ensure_ascii=False) if wire is not None else '',
            copy.deepcopy(request.output_schema), wire)

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        prepared = self._prepared_for(request)
        metadata, wire = dict(prepared.metadata), prepared.wire_schema
        if self.context_tokens is not None:
            payload = json.loads(prepared.body)
            count = sum(int(self.token_counter(message['content'])) for message in payload['messages'])
            count += int(self.token_counter(prepared.overhead)) if prepared.overhead else 0
            if count + request.max_output_tokens > self.context_tokens:
                raise ValueError('Serialized output schema and messages exceed the configured model context')
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f'model API key env var {self.api_key_env!r} is not configured')
        body = prepared.body
        http_request = urllib.request.Request(self.base_url + '/chat/completions', data=body,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
        reservation = None
        if self.money_budget is not None:
            reservation = self.money_budget.reserve(request.model, len(body) + 1024,
                request.max_output_tokens, prompt_version=request.prompt_version)
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode('utf-8'))
        except Exception as exc:
            exc.provider_metadata = metadata
            raise
        if not isinstance(raw, Mapping):
            raise ProviderResponseError('Chat response is not an object', ModelResponse('', metadata))
        metadata.update(self._identity(request.model, raw.get('model')))
        metadata.update(usage=raw.get('usage'), request_id=raw.get('id'))
        choices = raw.get('choices')
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
        message = choice.get('message') or {}
        content = message.get('content') if isinstance(message, Mapping) else None
        metadata['finish_reason'] = choice.get('finish_reason')
        if reservation is not None:
            usage = raw.get('usage') if metadata['model_identity_verified'] else None
            metadata['money_usage_identity_verified'] = metadata['model_identity_verified']
            try:
                self.money_budget.settle(reservation, usage)
            except Exception as exc:
                exc.response = ModelResponse(content if isinstance(content, str) else '', metadata)
                exc.provider_metadata = metadata
                raise
        if not isinstance(content, str):
            metadata['raw_response_content'] = content
            raise ProviderResponseError('Chat response is missing message.content', ModelResponse('', metadata))
        if wire is not None:
            metadata['raw_response_content'] = content
            content, status = restore_strict_content(content, prepared.output_schema, wire)
            metadata['output_schema_restoration'] = status
        return self._accept_response(content, metadata)


class OllamaNativeProvider(_IdentityProvider):
    """Ollama native chat API; its original schema format remains unchanged."""

    def __init__(self, base_url: str, timeout_seconds: int = 600, context_tokens: int | None = None, *,
                 model_identity_policy='record', model_aliases=None):
        self.base_url, self.timeout_seconds = base_url.rstrip('/'), timeout_seconds
        if context_tokens is not None and context_tokens <= 0:
            raise ValueError('Native model context must be positive')
        self.context_tokens = context_tokens
        self._configure_identity(model_identity_policy, model_aliases)

    def request_metadata(self, request):
        mode = 'native_schema' if request.output_schema is not None else 'json_object'
        return {'output_mode_requested': mode, 'output_mode_effective': mode,
                'model_identity_policy': self.model_identity_policy, 'application_validation_required': True}

    def request_overhead(self, request):
        self._check_available()
        return json.dumps({'format': request.output_schema}, ensure_ascii=False) if request.output_schema is not None else ''

    def prepare_request(self, request: ModelRequest):
        self._check_available()
        body = {'model': request.model, 'messages': list(request.messages), 'stream': False,
                'format': dict(request.output_schema) if request.output_schema is not None else 'json',
                'options': {'temperature': request.temperature, 'num_predict': request.max_output_tokens}}
        if self.context_tokens is not None:
            body['options']['num_ctx'] = self.context_tokens
        metadata = self.request_metadata(request)
        metadata.update(provider='ollama-native', prompt_version=request.prompt_version,
                        adapter_class=type(self).__module__ + '.' + type(self).__qualname__,
                        context_tokens=self.context_tokens, strict_schema=False,
                        effective_thinking=None, effective_reasoning_effort=None)
        return _PreparedRequest(json.dumps(body, ensure_ascii=False).encode('utf-8'), metadata,
            self.request_overhead(request), copy.deepcopy(request.output_schema))

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        prepared = self._prepared_for(request)
        http_request = urllib.request.Request(self.base_url + '/api/chat',
            data=prepared.body, headers={'Content-Type': 'application/json'}, method='POST')
        metadata = dict(prepared.metadata)
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode('utf-8'))
        except Exception as exc:
            exc.provider_metadata = metadata
            raise
        if not isinstance(raw, Mapping):
            raise ProviderResponseError('Ollama response is not an object', ModelResponse('', metadata))
        metadata.update(self._identity(request.model, raw.get('model')))
        metadata.update(finish_reason=raw.get('done_reason'),
                        usage={'prompt_tokens': raw.get('prompt_eval_count'), 'completion_tokens': raw.get('eval_count')})
        message = raw.get('message')
        if not isinstance(message, Mapping) or not isinstance(message.get('content'), str):
            raise ProviderResponseError('Ollama chat response is missing message.content', ModelResponse('', metadata))
        return self._accept_response(message['content'], metadata)
