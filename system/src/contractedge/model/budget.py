"""One non-resetting model budget shared by planning, execution and feedback."""
from __future__ import annotations

from dataclasses import asdict, replace
from collections.abc import Mapping
import hashlib
import time
from typing import Callable

from contractedge.model.admission import RequestQuote, _dispatch_quoted, _validate_quote, quote_request
from contractedge.model.provider import (ModelRequest, ModelProvider, ModelResponse,
                                         consume_pre_http_failure)


class BudgetExceeded(RuntimeError):
    pass


def recorded_dispatch_state(record: Mapping) -> str:
    """Project existing dispatch facts; only old records may lack a state.

    A confirmed local rejection legitimately has no response. Unknown calls
    stay unknown even if partial content was recorded. Contradictory explicit
    facts cannot authorize response consumption or candidate selection.
    """
    state = record.get('dispatch_state')
    has_response = isinstance(record.get('response_content'), str)
    if state is None:
        return 'response_known' if has_response else 'response_unknown'
    if state not in {'not_sent', 'response_unknown', 'response_known'}:
        raise ValueError('Unsupported recorded dispatch state')
    if state == 'response_known' and not has_response:
        raise ValueError('Known dispatch response is missing its durable content')
    if state == 'not_sent' and record.get('response_content') is not None:
        raise ValueError('Unsent dispatch cannot contain a response')
    return state


def token_settlement_fields(prompt_tokens: int, output_tokens: int, metadata, *,
                            reconcile_input: bool = False, reconcile_output: bool = False) -> dict:
    """One trusted-usage policy for dispatch, front accounting and saved traces.

    Missing/invalid usage and a rejected model identity retain the original
    reservation. Input settlement additionally requires positive verification.
    Output-only callers keep the established legacy identity-unknown policy.
    This function never changes a monetary ledger or infers an absent response.
    """
    if any(type(value) is not int or value < 0 for value in (prompt_tokens, output_tokens)):
        raise ValueError('Token reservations must be explicit nonnegative integers')
    if not isinstance(metadata, Mapping) or metadata.get('model_identity_verified') is False:
        return {}
    usage = metadata.get('usage')
    if not isinstance(usage, Mapping):
        return {}
    result = {}
    for label, reserved, field, enabled in (
            ('prompt', prompt_tokens, 'prompt_tokens',
             reconcile_input and metadata.get('model_identity_verified') is True),
            ('output', output_tokens, 'completion_tokens', reconcile_output)):
        observed = usage.get(field)
        if enabled and type(observed) is int and 0 <= observed <= reserved:
            result[label + '_tokens_accounted'] = observed
            if observed < reserved:
                result[label + '_tokens_released'] = reserved - observed
    return result


def recorded_token_charge(record: Mapping) -> int:
    """Restore only an explicit, validated settlement, never refund old calls.

    Unknown dispatches retain their complete reservation even if a malformed
    record contains purported accounting fields. Missing accounting fields in
    historical traces also retain their original upper bound.
    """
    prompt = record.get('prompt_tokens_reserved')
    output = record.get('output_tokens_reserved')
    if any(type(value) is not int or value < 0 for value in (prompt, output)):
        raise ValueError('Resuming requires explicit nonnegative prior token reservations')
    if recorded_dispatch_state(record) != 'response_known':
        return prompt + output
    expected = token_settlement_fields(prompt, output, record.get('response_metadata'),
        reconcile_input='prompt_tokens_accounted' in record,
        reconcile_output='output_tokens_accounted' in record)
    total = 0
    for label, reserved in (('prompt', prompt), ('output', output)):
        field = label + '_tokens_accounted'
        value = record.get(field, reserved)
        if type(value) is not int or not 0 <= value <= reserved:
            raise ValueError('Invalid prior token settlement: ' + field)
        released = record.get(label + '_tokens_released', reserved - value)
        if type(released) is not int or released != reserved - value:
            raise ValueError('Prior token release differs from settlement: ' + field)
        total += value if expected.get(field) == value else reserved
    return total


class BudgetedProvider:
    def __init__(self, provider: ModelProvider, token_counter: Callable[[str], int], *,
                 max_tokens: int, max_seconds: float, max_calls: int = 24, on_update=None,
                 reconcile_observed_output: bool = False,
                 reconcile_observed_input: bool = False):
        if min(max_tokens, max_seconds, max_calls) <= 0:
            raise ValueError('Run budgets must be positive')
        self.provider = provider
        self.token_counter = token_counter
        self.max_tokens, self.max_seconds, self.max_calls = max_tokens, max_seconds, max_calls
        # Reservations remain conservative because the runner may use an
        # upper-bound tokenizer. Trusted provider usage can settle output and
        # (when explicitly enabled) input reservations. Keep both opt-ins so
        # legacy ledgers and providers without trustworthy usage stay safe.
        if type(reconcile_observed_output) is not bool:
            raise ValueError('reconcile_observed_output must be boolean')
        if type(reconcile_observed_input) is not bool:
            raise ValueError('reconcile_observed_input must be boolean')
        self.reconcile_observed_output = reconcile_observed_output
        self.reconcile_observed_input = reconcile_observed_input
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

    def require_admission(self, quote: RequestQuote, *, token_limit=None, call_limit=None,
                          deadline=None, reserve_after: int = 0):
        """Check the same ledger, optionally preserving an existing stage floor.

        Quoting and checking do not create a reservation. Stage callers may
        tighten existing limits; they never maintain a second usage counter.
        """
        def reject(message):
            error = BudgetExceeded(message)
            error.dispatch_state = 'not_sent'
            error._budget_failure_owner = self
            error._budget_call_record = None
            raise error
        now = time.monotonic()
        end = min(self.started + self.max_seconds, deadline) if deadline is not None else self.started + self.max_seconds
        if now >= end:
            reject(f'Global model time budget exhausted: required=>0 seconds, allowed=0 seconds, exceeded_by={now - end}')
        calls = len(self.calls) + self.prior_calls + 1
        allowed_calls = min(self.max_calls, call_limit) if call_limit is not None else self.max_calls
        if calls > allowed_calls:
            reject(f'Global model calls budget exhausted: required={calls}, allowed={allowed_calls}, gap={calls - allowed_calls}')
        allowed_tokens = min(self.max_tokens, token_limit) if token_limit is not None else self.max_tokens
        required_tokens = self.reserved_tokens + quote.total_tokens + reserve_after
        if required_tokens > allowed_tokens:
            reject(f'Global model tokens budget exhausted: required={required_tokens}, allowed={allowed_tokens}, '
                   f'gap={required_tokens - allowed_tokens}, used={self.reserved_tokens}, '
                   f'input={quote.input_tokens}, output={quote.output_tokens}, reserved_after={reserve_after}')

    def complete_quoted(self, request: ModelRequest, quote: RequestQuote):
        _validate_quote(self, request, quote)
        inner = quote._inner_quote
        if inner is None:
            raise ValueError('Budgeted dispatch requires its own request quote')
        _validate_quote(self.provider, request, inner)
        prompt_tokens, reserve = quote.input_tokens, quote.total_tokens
        self.require_admission(quote)
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
            # Persist the same validated bytes that will be dispatched. The
            # optional adapter audit may contain settings or only a digest.
            body = prepared.body
            record['transport_request'] = {**dict(prepared.audit_record()),
                'body_utf8': body.decode('utf-8'), 'body_bytes': len(body),
                'body_sha256': hashlib.sha256(body).hexdigest()}
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
            self._reconcile_output_reservation(record, quote, response)
            record['elapsed_seconds'] = time.monotonic() - self.started
            if self.remaining_seconds <= 0:
                raise BudgetExceeded('Model response exceeded the global time budget')
            return response
        except Exception as exc:
            received = getattr(exc, 'response', None)
            proven_not_sent = consume_pre_http_failure(exc, self.provider, request, prepared)
            metadata = getattr(exc, 'provider_metadata', None)
            if (proven_not_sent and isinstance(metadata, Mapping)
                    and isinstance(metadata.get('admission_rejection'), Mapping)):
                # on_update may mutate the caller's message mappings. The
                # committed quote and transport bytes remain authoritative.
                exc.provider_metadata = {**metadata, 'admission_rejection': {
                    **metadata['admission_rejection'], 'request_digest': quote.request_digest,
                    'body_sha256': record['transport_request']['body_sha256']}}
            if isinstance(received, ModelResponse):
                record.update(response_content=received.content, response_metadata=dict(received.metadata))
                record['dispatch_state'] = 'response_known'
                self._reconcile_output_reservation(record, quote, received)
            elif proven_not_sent and record['dispatch_state'] != 'response_known':
                record['dispatch_state'] = 'not_sent'
            # The same exception carries its own record to later stages. A new
            # pre-admission failure must never borrow a previous call's state.
            exc.dispatch_state = record['dispatch_state']
            exc._budget_failure_owner = self
            exc._budget_call_record = record
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

    def _reconcile_output_reservation(self, record, quote: RequestQuote, response: ModelResponse) -> None:
        """Apply the shared settlement once, including rejected known responses."""
        before = recorded_token_charge(record)
        record.update(token_settlement_fields(quote.input_tokens, quote.output_tokens,
            response.metadata, reconcile_input=self.reconcile_observed_input,
            reconcile_output=self.reconcile_observed_output))
        self.reserved_tokens -= before - recorded_token_charge(record)

    def summary(self):
        return {'max_tokens': self.max_tokens, 'reserved_tokens': self.reserved_tokens,
                'max_seconds': self.max_seconds, 'elapsed_seconds': time.monotonic() - self.started,
                'max_calls': self.max_calls, 'model_calls': len(self.calls) + self.prior_calls,
                'resumed_model_calls': self.prior_calls}

    def failure_call_index(self, exc):
        if getattr(exc, '_budget_failure_owner', None) is not self:
            return None
        record = getattr(exc, '_budget_call_record', None)
        return next((i for i, value in enumerate(self.calls) if value is record), None)

    def failure_dispatch_state(self, exc):
        if isinstance(getattr(exc, 'response', None), ModelResponse):
            return 'response_known'
        if getattr(exc, '_budget_failure_owner', None) is self:
            index = self.failure_call_index(exc)
            return self.calls[index]['dispatch_state'] if index is not None else 'not_sent'
        return None
