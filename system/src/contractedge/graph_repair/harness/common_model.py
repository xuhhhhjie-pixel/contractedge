"""Formal local Live model adapter using the common request and settlement path."""
from contextlib import contextmanager, suppress
from http.client import HTTPConnection, HTTPException
import socket
import threading
import time
from urllib.parse import urlsplit

from minisweagent.agents.default import TerminatingException

from contractedge.graph_repair.state import append_jsonl, write_json
from contractedge.model.budget import BudgetExceeded, BudgetedProvider
from contractedge.model.money import MoneyBudget
from contractedge.model.provider import ModelRequest, OpenAICompatibleProvider
from contractedge.tokenizers import utf8_byte_token_bound


class BudgetStop(TerminatingException):
    """No further agent actions fit the common run budget."""


class ProviderStop(TerminatingException):
    """The current response could not authorize another action."""


class LocalChatProvider(OpenAICompatibleProvider):
    """One direct loopback HTTP exchange with a single absolute deadline."""

    def __init__(self, endpoint, trace, action_deadline, **options):
        url = urlsplit(endpoint)
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
                or url.netloc != f'127.0.0.1:{url.port}' or url.path != '/v1' or url.query or url.fragment):
            raise ValueError('The formal local transport requires http://127.0.0.1:PORT/v1')
        self.port = url.port
        self.trace = trace
        self.action_deadline = action_deadline
        super().__init__(endpoint, '', model_identity_policy='require_match', **options)

    def _format(self, request):
        if request.output_schema is not None:
            raise ValueError('The Live text protocol cannot enforce a JSON schema')
        _, metadata, _ = super()._format(request)
        metadata.update(output_mode_requested='text', output_mode_effective='text')
        return None, metadata, None

    def _api_key(self):
        return 'contractedge-local-only'

    @contextmanager
    def _open_request(self, request, timeout):
        start = time.monotonic()
        deadline = min(start + timeout, self.action_deadline)
        timeout = deadline - start
        record = {'start_monotonic': start, 'deadline_monotonic': deadline, 'timeout_seconds': timeout}
        if timeout <= 0:
            raise TimeoutError('Local model request deadline expired before connecting')
        append_jsonl(self.trace, {**record, 'event': 'transport_entered'})
        client = HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        response = timer = None
        expired = threading.Event()
        try:
            client.connect()
            sock = client.sock

            def abort():
                expired.set()
                with suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Local model request deadline expired')
            timer = threading.Timer(remaining, abort)
            timer.daemon = True
            timer.start()
            client.request(request.get_method(), '/v1/chat/completions', request.data,
                           dict(request.header_items()))
            response = client.getresponse()
            record['http_status'] = response.status
            if response.status != 200:
                raise HTTPException(f'Local model HTTP status {response.status}')
            yield response
            if expired.is_set() or time.monotonic() >= deadline:
                raise TimeoutError('Local model request deadline expired')
        except (OSError, HTTPException, ValueError) as exc:
            if expired.is_set() or isinstance(exc, socket.timeout):
                record['error'] = 'TimeoutError'
                raise TimeoutError('Local model request deadline expired') from exc
            record['error'] = type(exc).__name__
            raise
        finally:
            if timer is not None:
                timer.cancel()
            if response is not None:
                response.close()
            client.close()
            append_jsonl(self.trace, {**record, 'event': 'transport_ended', 'end_monotonic': time.monotonic()})


class LiveModel:
    """Map the existing Agent model protocol to the shared budgeted provider."""

    def __init__(self, profile, endpoint, out, action_deadline):
        self.profile = profile
        self.out = out
        self.deadline = action_deadline
        model = profile['model']
        pricing = profile['pricing']
        self.money = MoneyBudget(out / 'money.json', model=model['id'], limit_cny=pricing['limit_cny'],
                                 input_cny_per_million=pricing['input_cny_per_million'],
                                 output_cny_per_million=pricing['output_cny_per_million'])
        provider = LocalChatProvider(endpoint, out / 'transport.jsonl', action_deadline,
                                     timeout_seconds=model['request_seconds'], money_budget=self.money)
        limits = profile['budget']
        self.budget = BudgetedProvider(
            provider, utf8_byte_token_bound, max_tokens=limits['tokens'], max_calls=limits['calls'],
            max_seconds=max(0.001, action_deadline - time.monotonic()),
            reconcile_observed_input=True, reconcile_observed_output=True,
            on_update=lambda calls: write_json(out / 'requests.json', calls),
        )
        write_json(out / 'requests.json', [])

    @property
    def n_calls(self):
        return len(self.budget.calls)

    @property
    def cost(self):
        return self.money.summary()['committed_micro_cny'] / 1_000_000

    def get_template_vars(self):
        return {'n_model_calls': self.n_calls, 'model_cost': self.cost}

    def query(self, messages, **kwargs):
        # Agent messages carry local timestamps. Only the chat wire fields go out.
        wire_messages = tuple({key: message[key] for key in ('role', 'content')} for message in messages)
        model = self.profile['model']
        request = ModelRequest(model['id'], 'common-live-v1', wire_messages, model['max_output_tokens'],
                               model['temperature'], metadata={'action_deadline_monotonic': self.deadline})
        try:
            quote = self.budget.quote_request(request)
            try:
                self.budget.require_admission(quote, deadline=self.deadline)
            except BudgetExceeded as exc:
                write_json(self.out / 'admission_stop.json', {
                    'state': 'not_sent', 'reason': str(exc), 'input_tokens': quote.input_tokens,
                    'output_tokens': quote.output_tokens, 'deadline_monotonic': self.deadline,
                    'request_digest': quote.request_digest,
                })
                raise
            response = self.budget.complete_quoted(request, quote)
        except BudgetExceeded as exc:
            raise BudgetStop(str(exc)) from exc
        except Exception as exc:
            raise ProviderStop(f'{type(exc).__name__}: local model response unavailable') from exc
        if not response.content.strip():
            raise ProviderStop('Empty local model response')
        return {'content': response.content}
