"""Actual shared-budget rejection must not create calls or spend corrections."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from contractedge.contracts.validation import canonical_digest
from contractedge.experiments.modes import generate_nonrelation
from contractedge.model.budget import BudgetExceeded, BudgetedProvider
from contractedge.model.provider import ModelRequest, ModelResponse, OllamaNativeProvider, OpenAICompatibleProvider
from contractedge.model.money import MoneyBudgetExceeded
from contractedge.orchestration import runner
from contractedge.orchestration.state import PolicyLedger
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_method_input


class InvalidProvider:
    def __init__(self): self.requests = []
    def complete_json(self, request):
        self.requests.append(request)
        return ModelResponse('{"steps":[]}', {})


def front_input(root):
    (root / 'main.go').write_text('package main\nfunc render() {}\n')
    method = valid_method_input()
    method['scope_hints'] = [dict(kind='path', value='main.go', raw_text='main.go', origin_ref='scope')]
    method['initial_snapshot_id'] = compute_snapshot(root).snapshot_id
    method.pop('input_digest', None)
    method['input_digest'] = canonical_digest(method)
    return method


def front_config(**extra):
    return dict(run_id='facts', model={'model': 'fixture'}, max_context_tokens=10000,
        max_total_tokens=100000, max_format_retries=1, max_model_calls=2, **extra)


def test_front_shared_reentry_projects_only_its_real_ledger_slice(tmp_path, monkeypatch):
    method = front_input(tmp_path)
    config = front_config()
    policy = PolicyLedger(config)
    monkeypatch.setattr(runner, 'PolicyLedger', lambda config: policy)
    provider = InvalidProvider()
    counter = lambda text: len(text.split())
    budget = BudgetedProvider(provider, counter, max_tokens=100000, max_seconds=30, max_calls=2)
    budget.complete_json(ModelRequest('fixture', 'earlier-stage', ({'role': 'user', 'content': 'Earlier'},), 10))
    trace = runner.run_front_half(method, tmp_path, budget, config, counter)
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) == len(budget.calls) == 2
    assert len(trace.model_calls) == 1
    assert all(trace.model_calls[0][key] == value for key, value in budget.calls[1].items())
    assert trace.model_calls[0]['stage'] == 'DIAGNOSIS'
    assert policy.used('format_correction', 'diagnosis:front') == 0
    assert 'calls' in ' '.join(trace.errors).lower()
    assert 'required=' in ' '.join(trace.errors) and 'allowed=' in ' '.join(trace.errors)


def test_standalone_front_uses_canonical_dispatch_records(tmp_path):
    method = front_input(tmp_path)
    provider = InvalidProvider()
    config = {**front_config(), 'max_format_retries': 0}
    trace = runner.run_front_half(method, tmp_path, provider, config, lambda text: len(text.split()))
    assert len(provider.requests) == len(trace.model_calls) == 1
    call = trace.model_calls[0]
    assert call['dispatch_state'] == 'response_known'
    assert call['request']['messages'] == list(provider.requests[0].messages) or call['request']['messages'] == provider.requests[0].messages
    assert call['stage'] == 'DIAGNOSIS'
    assert call['validation_error'] if 'validation_error' in call else trace.status == 'INVALID_OUTPUT'


def test_direct_plan_global_rejection_does_not_consume_unstarted_correction(tmp_path):
    (tmp_path / 'app.py').write_text('value = 0\n')
    method = SimpleNamespace(requirement_text='Set value to one.', input_digest='a' * 64,
        declared_artifacts=(), case_id='public')
    config = dict(model='fixture', max_context_tokens=10000, max_output_tokens=64, max_format_retries=1)
    policy = PolicyLedger(config)
    journal = SimpleNamespace(engine=SimpleNamespace(policy=policy), record_dispatch=lambda *a, **k: None)
    provider = InvalidProvider()
    budget = BudgetedProvider(provider, lambda text: 1, max_tokens=10000, max_seconds=30, max_calls=1)
    with pytest.raises(BudgetExceeded):
        generate_nonrelation('without_relation', method, tmp_path, {'allowed_paths': ['app.py']},
            budget, lambda text: 1, config, journal=journal)
    assert len(provider.requests) == len(budget.calls) == 1
    assert policy.used('format_correction', 'direct-plan:mode') == 0


@pytest.mark.parametrize('guard', ['tokens', 'calls', 'time'])
def test_global_rejection_describes_actual_guard_without_call(guard):
    provider = InvalidProvider()
    budget = BudgetedProvider(provider, len, max_tokens=100, max_seconds=30, max_calls=1)
    if guard == 'calls': budget.prior_calls = 1
    elif guard == 'time': budget.started -= 31
    else: budget.reserved_tokens = 100
    request = ModelRequest('fixture', 'facts', ({'role': 'user', 'content': 'hello'},), 10)
    with pytest.raises(BudgetExceeded) as caught:
        budget.complete_json(request)
    message = str(caught.value).lower()
    assert guard in message and 'required=' in message and 'allowed=' in message
    assert budget.calls == provider.requests == []
    assert budget.failure_call_index(caught.value) is None
    assert budget.failure_dispatch_state(caught.value) == 'not_sent'


def test_front_local_token_rejection_retains_required_and_allowed_size(tmp_path):
    provider = InvalidProvider()
    trace = runner.run_front_half(front_input(tmp_path), tmp_path, provider,
        {**front_config(), 'max_total_tokens': 1}, lambda text: len(text.split()))
    assert trace.status == 'BUDGET_EXHAUSTED' and not provider.requests and not trace.model_calls
    assert 'required=' in ' '.join(trace.errors) and 'allowed=' in ' '.join(trace.errors)


def test_ollama_request_constructor_failure_is_proven_unsent(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: pytest.fail('No HTTP may occur'))
    def reject(*args, **kwargs): raise ValueError('Local Request construction failed')
    monkeypatch.setattr('urllib.request.Request', reject)
    budget = BudgetedProvider(OllamaNativeProvider('http://unused.invalid'), len,
        max_tokens=10000, max_seconds=30)
    request = ModelRequest('fixture', 'facts', ({'role': 'user', 'content': 'hello'},), 10)
    with pytest.raises(ValueError) as caught: budget.complete_json(request)
    assert budget.failure_dispatch_state(caught.value) == 'not_sent'
    assert budget.calls[0]['dispatch_state'] == 'not_sent'
    assert budget.reserved_tokens == 15


def correction_provider(monkeypatch, *, boundary, truncated=False):
    """Actual pre-HTTP proof on the second adapter invocation; no network."""
    monkeypatch.setenv('CE_FACT_GAP_OFFLINE_KEY', 'public-test-fixture-only')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: pytest.fail('No real HTTP'))
    class RejectMoney:
        path = Path('/unused/offline-fixture-ledger')
        def reserve(self, *args, **kwargs): raise MoneyBudgetExceeded('Offline admission rejected')
    class Provider(OpenAICompatibleProvider):
        count = 0
        def complete_json(self, request):
            self.count += 1
            if self.count == 1:
                return ModelResponse('' if truncated else '{"steps":[]}',
                    {'finish_reason': 'length', 'usage': {'completion_tokens': 10,
                     'completion_tokens_details': {'reasoning_tokens': 10}}} if truncated else {})
            if boundary == 'unknown':
                raise TimeoutError('Fixture entered transport without a response')
            return super().complete_json(request)
    return Provider('https://unused.invalid', 'CE_FACT_GAP_OFFLINE_KEY', money_budget=RejectMoney())


@pytest.mark.parametrize('boundary', ['not_sent', 'unknown'])
@pytest.mark.parametrize('recovery', ['format', 'truncation', 'separate_truncation'])
def test_front_corrections_follow_actual_dispatch_including_known_pre_http_rejection(
        tmp_path, monkeypatch, boundary, recovery):
    method = front_input(tmp_path)
    config = {**front_config(), 'max_model_calls': 4,
        'planning_truncation_retry': 'disable_thinking',
        'planning_truncation_separate_allowance': recovery == 'separate_truncation'}
    policy = PolicyLedger(config)
    monkeypatch.setattr(runner, 'PolicyLedger', lambda config: policy)
    provider = correction_provider(monkeypatch, boundary=boundary, truncated=recovery != 'format')
    budget = BudgetedProvider(provider, lambda text: len(text.split()),
        max_tokens=100000, max_seconds=30, max_calls=4)
    trace = runner.run_front_half(method, tmp_path, budget, config, lambda text: len(text.split()))
    assert provider.count == len(trace.model_calls) == len(budget.calls) == 2
    assert trace.model_calls[-1]['dispatch_state'] == boundary.replace('unknown', 'response_unknown')
    assert policy.used('format_correction', 'diagnosis:front') == int(boundary == 'unknown' and recovery != 'separate_truncation')
    assert policy.used('truncation_recovery', 'front') == int(boundary == 'unknown' and recovery != 'format')
    assert budget.reserved_tokens == sum(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] for c in budget.calls)
    assert all('response_content' not in call for call in budget.calls[1:])


def test_direct_plan_money_rejection_keeps_reservation_without_correction_charge(tmp_path, monkeypatch):
    (tmp_path / 'app.py').write_text('value = 0\n')
    method = SimpleNamespace(requirement_text='Set value to one.', input_digest='a' * 64,
        declared_artifacts=(), case_id='public')
    config = dict(model='fixture', max_context_tokens=10000, max_output_tokens=64, max_format_retries=1)
    policy = PolicyLedger(config)
    journal = SimpleNamespace(engine=SimpleNamespace(policy=policy), record_dispatch=lambda *a, **k: None)
    provider = correction_provider(monkeypatch, boundary='not_sent')
    budget = BudgetedProvider(provider, lambda text: 1, max_tokens=10000, max_seconds=30, max_calls=4)
    with pytest.raises(MoneyBudgetExceeded):
        generate_nonrelation('without_relation', method, tmp_path, {'allowed_paths': ['app.py']},
            budget, lambda text: 1, config, journal=journal)
    assert len(budget.calls) == provider.count == 2
    assert budget.calls[-1]['dispatch_state'] == 'not_sent'
    assert policy.used('format_correction', 'direct-plan:mode') == 0
    assert budget.reserved_tokens == sum(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] for c in budget.calls)


def test_ollama_transport_cannot_reuse_an_earlier_proof_for_identical_prepared_bytes(monkeypatch):
    adapter = OllamaNativeProvider('http://unused.invalid')
    budget = BudgetedProvider(adapter, len, max_tokens=10000, max_seconds=30)
    request = ModelRequest('fixture', 'facts', ({'role': 'user', 'content': 'hello'},), 10)
    quote = budget.quote_request(request)
    failure = TimeoutError('Reused exception across different transport boundaries')
    original_request = __import__('urllib.request', fromlist=['Request']).Request
    def reject(*args, **kwargs): raise failure
    monkeypatch.setattr('urllib.request.Request', reject)
    with pytest.raises(TimeoutError): adapter.complete_prepared_json(request, quote.prepared)
    assert hasattr(failure, '_provider_pre_http_failure')
    monkeypatch.setattr('urllib.request.Request', original_request)
    monkeypatch.setattr('urllib.request.urlopen', reject)
    with pytest.raises(TimeoutError): budget.complete_quoted(request, quote)
    assert budget.calls[-1]['dispatch_state'] == 'response_unknown'
    assert not hasattr(failure, '_provider_pre_http_failure')
    assert budget.reserved_tokens == 15
