"""One non-resetting model budget shared by planning, execution and feedback."""
from __future__ import annotations

from dataclasses import asdict, replace
import time
from typing import Callable

from contractedge.model.admission import RequestQuote, _dispatch_quoted, _validate_quote, quote_request
from contractedge.model.provider import ModelRequest, ModelProvider, ModelResponse


class BudgetExceeded(RuntimeError):
    pass


class BudgetedProvider:
    def __init__(self, provider: ModelProvider, token_counter: Callable[[str], int], *,
                 max_tokens: int, max_seconds: float, max_calls: int = 24, on_update=None):
        if min(max_tokens, max_seconds, max_calls) <= 0:
            raise ValueError('Run budgets must be positive')
        self.provider = provider
        self.token_counter = token_counter
        self.max_tokens, self.max_seconds, self.max_calls = max_tokens, max_seconds, max_calls
        self.started = time.monotonic()
        self.reserved_tokens = 0
        self.calls = []
        self.prior_calls = 0
        self.on_update = on_update

    @property
    def remaining_seconds(self):
        return max(0.0, self.max_seconds - (time.monotonic() - self.started))

    def request_overhead(self, request: ModelRequest):
        hook = getattr(self.provider, 'request_overhead', None)
        value = hook(request) if callable(hook) else ''
        if not isinstance(value, str):
            raise ValueError('Provider request overhead must be serialized text')
        return value

    def quote_request(self, request: ModelRequest) -> RequestQuote:
        """Use this budget's tokenizer without reserving tokens, calls or money."""
        inner = quote_request(self.provider, request, self.token_counter)
        return replace(inner, _provider=self, _inner_quote=inner)

    def complete_json(self, request: ModelRequest):
        return self.complete_quoted(request, self.quote_request(request))

    def complete_quoted(self, request: ModelRequest, quote: RequestQuote):
        _validate_quote(self, request, quote)
        inner = quote._inner_quote
        if inner is None:
            raise ValueError('Budgeted dispatch requires its own request quote')
        _validate_quote(self.provider, request, inner)
        prompt_tokens, reserve = quote.input_tokens, quote.total_tokens
        if (self.remaining_seconds <= 0 or len(self.calls) + self.prior_calls >= self.max_calls
                or self.reserved_tokens + reserve > self.max_tokens):
            raise BudgetExceeded('Global model token/time/call budget exhausted')
        record = {'prompt_version': request.prompt_version, 'prompt_tokens_reserved': prompt_tokens,
                  'output_tokens_reserved': quote.output_tokens,
                  'dispatch_state': 'not_sent',
                  'messages': [dict(m) for m in request.messages], 'model': request.model}
        # Preserve every final provider-boundary parameter for exact offline replay.
        # Older ledgers remain readable but cannot certify absent parameters.
        record['request'] = asdict(request)
        record['request_metadata'] = dict(request.metadata)
        record['output_schema'] = dict(request.output_schema) if request.output_schema is not None else None
        record['request_digest'] = quote.request_digest
        prepared = quote.prepared
        hook = getattr(self.provider, 'request_metadata', None)
        if prepared is not None:
            record['provider_request_metadata'] = dict(prepared.metadata)
            record['transport_request'] = prepared.audit_record()
        elif callable(hook):
            record['provider_request_metadata'] = dict(hook(request))
        record['transport_schema_tokens_reserved'] = quote.overhead_tokens
        self.reserved_tokens += reserve
        self.calls.append(record)
        if self.on_update:
            self.on_update(self.calls)
        previous_timeout = getattr(self.provider, 'timeout_seconds', None)
        try:
            remaining = self.remaining_seconds
            if remaining <= 0:
                raise BudgetExceeded('Global deadline expired during provider request preparation')
            if previous_timeout is not None:
                self.provider.timeout_seconds = min(previous_timeout, remaining)
            # The reservation is durable before dispatch.  Once transport is
            # entered, a missing response is ``response_unknown`` and must not
            # be automatically replayed by an upper stage.
            record['dispatch_state'] = 'response_unknown'
            if self.on_update:
                self.on_update(self.calls)
            response = _dispatch_quoted(self.provider, request, inner)
            record.update(response_content=response.content, response_metadata=dict(response.metadata))
            record['dispatch_state'] = 'response_known'
            record['elapsed_seconds'] = time.monotonic() - self.started
            if self.remaining_seconds <= 0:
                raise BudgetExceeded('Model response exceeded the global time budget')
            return response
        except Exception as exc:
            received = getattr(exc, 'response', None)
            if isinstance(received, ModelResponse):
                record.update(response_content=received.content, response_metadata=dict(received.metadata))
                record['dispatch_state'] = 'response_known'
            if getattr(exc, 'provider_metadata', None) is not None:
                record['provider_error_metadata'] = dict(exc.provider_metadata)
            record['elapsed_seconds'] = time.monotonic() - self.started
            safe_error = getattr(self.provider, 'safe_error_message', None)
            message = safe_error(exc) if callable(safe_error) else str(exc)
            record['error'] = type(exc).__name__ + ': ' + message
            raise
        finally:
            if previous_timeout is not None:
                self.provider.timeout_seconds = previous_timeout
            if self.on_update:
                self.on_update(self.calls)

    def summary(self):
        return {'max_tokens': self.max_tokens, 'reserved_tokens': self.reserved_tokens,
                'max_seconds': self.max_seconds, 'elapsed_seconds': time.monotonic() - self.started,
                'max_calls': self.max_calls, 'model_calls': len(self.calls) + self.prior_calls,
                'resumed_model_calls': self.prior_calls}
