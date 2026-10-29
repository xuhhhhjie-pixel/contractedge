"""One non-resetting model budget shared by planning, execution and feedback."""
from __future__ import annotations

import time
from typing import Callable

from contractedge.model.provider import ModelRequest, ModelProvider


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

    def complete_json(self, request: ModelRequest):
        prompt_tokens = sum(int(self.token_counter(m['content'])) for m in request.messages)
        reserve = prompt_tokens + request.max_output_tokens
        if prompt_tokens < 0 or (self.remaining_seconds <= 0 or len(self.calls) + self.prior_calls >= self.max_calls
                                or self.reserved_tokens + reserve > self.max_tokens):
            raise BudgetExceeded('Global model token/time/call budget exhausted')
        self.reserved_tokens += reserve
        record = {'prompt_version': request.prompt_version, 'prompt_tokens_reserved': prompt_tokens,
                  'output_tokens_reserved': request.max_output_tokens,
                  'messages': [dict(m) for m in request.messages], 'model': request.model}
        record['request_metadata'] = dict(request.metadata)
        record['output_schema'] = dict(request.output_schema) if request.output_schema is not None else None
        self.calls.append(record)
        if self.on_update:
            self.on_update(self.calls)
        previous_timeout = getattr(self.provider, 'timeout_seconds', None)
        try:
            if previous_timeout is not None:
                self.provider.timeout_seconds = min(previous_timeout, self.remaining_seconds)
            response = self.provider.complete_json(request)
            record.update(response_content=response.content, response_metadata=dict(response.metadata))
            record['elapsed_seconds'] = time.monotonic() - self.started
            if self.remaining_seconds <= 0:
                raise BudgetExceeded('Model response exceeded the global time budget')
            return response
        except Exception as exc:
            record['error'] = type(exc).__name__ + ': ' + str(exc)
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
