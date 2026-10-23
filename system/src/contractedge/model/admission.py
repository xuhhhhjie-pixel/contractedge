"""Pure context quotes for one prepared provider request; quoting never dispatches.

input_tokens includes serialized provider overhead, matching the durable
prompt_tokens_reserved ledger. A quote is bound to its provider, request fields
and prepared bytes; it is not a reservation or an automatic retry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Callable

from contractedge.model.provider import ModelRequest


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _request_digest(request: ModelRequest) -> str:
    return _digest(asdict(request))


def _prepared_digest(prepared: Any) -> str | None:
    if prepared is None:
        return None
    body = prepared.body
    if not isinstance(body, bytes):
        raise ValueError('Prepared provider body must be immutable bytes')
    return _digest({'body_sha256': hashlib.sha256(body).hexdigest(),
        'metadata': dict(prepared.metadata), 'overhead': prepared.overhead,
        'output_schema': getattr(prepared, 'output_schema', None),
        'wire_schema': getattr(prepared, 'wire_schema', None)})


@dataclass(frozen=True)
class RequestQuote:
    input_tokens: int
    output_tokens: int
    overhead_tokens: int
    total_tokens: int
    request_digest: str
    prepared: Any = field(default=None, repr=False, compare=False)
    _provider: Any = field(default=None, repr=False, compare=False)
    _prepared_digest: str | None = field(default=None, repr=False, compare=False)
    _inner_quote: RequestQuote | None = field(default=None, repr=False, compare=False)


def _validate_quote(provider: Any, request: ModelRequest, quote: RequestQuote) -> None:
    if not isinstance(quote, RequestQuote) or quote._provider is not provider:
        raise ValueError('Request quote belongs to another provider')
    if quote.request_digest != _request_digest(request):
        raise ValueError('Request fields changed after context quoting')
    if quote._prepared_digest != _prepared_digest(quote.prepared):
        raise ValueError('Prepared provider request changed after context quoting')


def _count(counter: Callable[[str], int], text: str) -> int:
    value = int(counter(text))
    if value < 0:
        raise ValueError('Request token counter cannot return a negative count')
    return value


def quote_request(provider: Any, request: ModelRequest,
                  counter: Callable[[str], int]) -> RequestQuote:
    """Prepare at most once and count context, without consuming any budget."""
    hook = getattr(provider, 'quote_request', None)
    if callable(hook):
        quote = hook(request)
        _validate_quote(provider, request, quote)
        return quote
    if type(request.max_output_tokens) is not int or request.max_output_tokens < 0:
        raise ValueError('Request output reservation must be a nonnegative integer')
    prepare = getattr(provider, 'prepare_request', None)
    dispatch = getattr(provider, 'complete_prepared_json', None)
    prepared = prepare(request) if callable(prepare) and callable(dispatch) else None
    if prepared is not None:
        overhead = prepared.overhead
    else:
        overhead_hook = getattr(provider, 'request_overhead', None)
        overhead = overhead_hook(request) if callable(overhead_hook) else ''
    if not isinstance(overhead, str):
        raise ValueError('Provider request overhead must be serialized text')
    overhead_tokens = _count(counter, overhead) if overhead else 0
    input_tokens = sum(_count(counter, message['content']) for message in request.messages) + overhead_tokens
    return RequestQuote(input_tokens, request.max_output_tokens, overhead_tokens,
        input_tokens + request.max_output_tokens, _request_digest(request), prepared,
        provider, _prepared_digest(prepared))


def context_fits(quote: RequestQuote, max_context_tokens: int,
                 model_context_tokens: int | None = None) -> bool:
    """Apply the input cap and, when declared, the complete model context cap."""
    if type(max_context_tokens) is not int or max_context_tokens < 0:
        raise ValueError('Input context limit must be a nonnegative integer')
    if model_context_tokens is not None and (type(model_context_tokens) is not int or model_context_tokens < 0):
        raise ValueError('Model context limit must be a nonnegative integer')
    return (quote.input_tokens <= max_context_tokens
        and (model_context_tokens is None or quote.total_tokens <= model_context_tokens))


def _dispatch_quoted(provider: Any, request: ModelRequest, quote: RequestQuote):
    """Internal dispatch after admission/commit; prepared bytes remain authoritative.

    Durable on_update callbacks can mutate the caller's request after reservation.
    Never rebuild those committed bytes from that mutable object.
    """
    if quote._provider is not provider or quote._prepared_digest != _prepared_digest(quote.prepared):
        raise ValueError('Prepared provider request changed after admission')
    hook = getattr(provider, 'complete_quoted', None)
    if callable(hook):
        return hook(request, quote)
    if quote.prepared is not None:
        return provider.complete_prepared_json(request, quote.prepared)
    # Legacy providers have no frozen bytes to protect against a persistence
    # callback mutating the request after reservation. Never send unquoted data.
    _validate_quote(provider, request, quote)
    return provider.complete_json(request)


def complete_quoted(provider: Any, request: ModelRequest, quote: RequestQuote):
    """Validate the quote before reservation, then dispatch the quoted bytes."""
    _validate_quote(provider, request, quote)
    return _dispatch_quoted(provider, request, quote)
