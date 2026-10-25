from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


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
                             {"provider": "fixture", "model": request.model,
                              "prompt_version": request.prompt_version, "finish_reason": "fixture"})


class OpenAICompatibleProvider:
    """Optional OpenAI-compatible chat-completions port; never used without explicit setup."""

    def __init__(self, base_url: str, api_key_env: str, timeout_seconds: int = 120, *, money_budget=None, thinking=None, reasoning_effort=None):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.money_budget = money_budget
        if thinking not in {None, 'enabled', 'disabled'}:
            raise ValueError('thinking must be enabled or disabled')
        self.thinking = thinking
        if reasoning_effort not in {None, 'low', 'high', 'max'}:
            raise ValueError('reasoning_effort must be low, high or max')
        if reasoning_effort is not None and thinking != 'enabled':
            raise ValueError('An explicit reasoning effort requires thinking=enabled')
        self.reasoning_effort = reasoning_effort

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"model API key env var {self.api_key_env!r} is not configured")
        payload = {"model": request.model, "messages": list(request.messages),
                           "max_tokens": request.max_output_tokens, "temperature": request.temperature,
                           "response_format": {"type": "json_object"}}
        if request.thinking not in {None, 'enabled', 'disabled'}:
            raise ValueError('Request thinking override must be enabled or disabled')
        thinking = self.thinking if request.thinking is None else request.thinking
        if thinking is not None:
            payload['thinking'] = {'type': thinking}
        if thinking == 'enabled' and self.reasoning_effort is not None:
            payload['reasoning_effort'] = self.reasoning_effort
        body = json.dumps(payload, ensure_ascii=False).encode()
        http_request = urllib.request.Request(self.base_url + "/chat/completions", data=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
        reservation = None
        if self.money_budget is not None:
            # UTF-8 byte bound includes serialized protocol overhead; not a model tokenizer.
            reservation = self.money_budget.reserve(request.model, len(body) + 1024,
                request.max_output_tokens, prompt_version=request.prompt_version)
        with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
        if reservation is not None:
            self.money_budget.settle(reservation, raw.get('usage'))
        choice = raw["choices"][0]
        return ModelResponse(choice["message"]["content"], {
            "provider": "openai-compatible", "model": raw.get("model", request.model),
            "prompt_version": request.prompt_version, "finish_reason": choice.get("finish_reason"),
            "usage": raw.get("usage"), "request_id": raw.get("id"),
            "effective_thinking": thinking,
            "effective_reasoning_effort": self.reasoning_effort if thinking == 'enabled' else None,
        })


class OllamaNativeProvider:
    """Ollama native chat API with JSON-Schema-constrained output."""

    def __init__(self, base_url: str, timeout_seconds: int = 600, context_tokens: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        if context_tokens is not None and context_tokens <= 0:
            raise ValueError('Native model context must be positive')
        self.context_tokens = context_tokens

    def complete_json(self, request: ModelRequest) -> ModelResponse:
        body = {"model": request.model, "messages": list(request.messages), "stream": False,
                "format": dict(request.output_schema) if request.output_schema is not None else "json",
                "options": {"temperature": request.temperature, "num_predict": request.max_output_tokens}}
        if self.context_tokens is not None:
            body['options']['num_ctx'] = self.context_tokens
        http_request = urllib.request.Request(self.base_url + "/api/chat",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
        message = raw.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise ValueError("Ollama chat response is missing message.content")
        return ModelResponse(message["content"], {
            "provider": "ollama-native", "model": raw.get("model", request.model),
            "prompt_version": request.prompt_version, "finish_reason": raw.get("done_reason"),
            "context_tokens": self.context_tokens,
            "usage": {"prompt_tokens": raw.get("prompt_eval_count"),
                      "completion_tokens": raw.get("eval_count")},
        })
