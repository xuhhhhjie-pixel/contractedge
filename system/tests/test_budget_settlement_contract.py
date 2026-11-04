"""Token settlement must survive stage changes without releasing unknown calls."""
import copy
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelRequest, ModelResponse, ProviderResponseError
from contractedge.orchestration import runner
from contractedge.orchestration.repair import repair_case
from tests.test_run_budget import run_fixture
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_repair_execution import (
    make_public_fixture, PublicLoopRunner, TwoStepExecutor, AcceptPublicReview,
)
from contractedge.dataset.benchmark import method_input_from_task, file_sha


def metadata(identity=True, prompt=3, completion=2):
    return {'model_identity_verified': identity,
            'usage': {'prompt_tokens': prompt, 'completion_tokens': completion}}


class UsageProvider(PipelineFixtureProvider):
    def __init__(self):
        super().__init__()
        self.requests = []

    def complete_json(self, request):
        self.requests.append(asdict(request))
        response = super().complete_json(request)
        return ModelResponse(response.content, {**response.metadata, **metadata()})


def request():
    return ModelRequest('fixture-model', 'fixture.accounting',
                        ({'role': 'user', 'content': 'x' * 100},), 64)


def test_standalone_front_records_settlement_for_future_continuation(tmp_path):
    trace = run_fixture(tmp_path, UsageProvider(), lambda text: len(text.split()),
                        reconcile_observed_input=True, reconcile_observed_output=True)
    assert trace.status == 'READY', trace.errors
    assert len(trace.model_calls) == 2
    assert sum(call.get('prompt_tokens_accounted', call['prompt_tokens_reserved'])
               + call.get('output_tokens_accounted', call['output_tokens_reserved'])
               for call in trace.model_calls) == 10


def test_settlement_flags_change_only_accounting_not_actual_model_requests(tmp_path):
    control, treatment = UsageProvider(), UsageProvider()
    a, b = tmp_path / 'control', tmp_path / 'settled'
    a.mkdir(); b.mkdir()
    control_trace = run_fixture(a, control, lambda text: len(text.split()), run_id='fixed-budget-replay',
        reconcile_observed_input=False, reconcile_observed_output=False)
    settled_trace = run_fixture(b, treatment, lambda text: len(text.split()), run_id='fixed-budget-replay',
        reconcile_observed_input=True, reconcile_observed_output=True)
    assert control_trace.status == settled_trace.status == 'READY'
    assert control.requests == treatment.requests
    for request_value in treatment.requests:
        messages = json.dumps(request_value['messages'])
        assert '_tokens_accounted' not in messages
        assert '_tokens_released' not in messages
    assert all('prompt_tokens_accounted' not in c for c in control_trace.model_calls)


def test_front_and_shared_budget_reject_mismatched_identity_equally(tmp_path):
    class MismatchedUsage(UsageProvider):
        def complete_json(self, request):
            response = super().complete_json(request)
            return ModelResponse(response.content, {**response.metadata, **metadata(False)})
    counter = lambda text: len(text.split())
    trace = run_fixture(tmp_path, MismatchedUsage(), counter,
        reconcile_observed_input=True, reconcile_observed_output=True)
    assert trace.status == 'READY', trace.errors
    responses = iter(trace.model_calls)
    class Replay:
        def complete_json(self, request):
            call = next(responses)
            assert asdict(request) == call['request']
            return ModelResponse(call['response_content'], call['response_metadata'])
    shared = BudgetedProvider(Replay(), counter, max_tokens=80000, max_seconds=30,
        reconcile_observed_input=True, reconcile_observed_output=True)
    for call in trace.model_calls:
        shared.complete_json(ModelRequest(**call['request']))
        assert 'prompt_tokens_accounted' not in call and 'output_tokens_accounted' not in call
    assert all('prompt_tokens_accounted' not in call and 'output_tokens_accounted' not in call
               for call in shared.calls)
    assert shared.reserved_tokens == sum(call['prompt_tokens_reserved'] + call['output_tokens_reserved']
                                         for call in trace.model_calls)


def test_known_received_error_settles_usage_but_unknown_error_keeps_reservation():
    class Port:
        def complete_json(self, request):
            raise ProviderResponseError('Missing content', ModelResponse('', metadata()))
    budget = BudgetedProvider(Port(), len, max_tokens=1000, max_seconds=10,
                              reconcile_observed_input=True, reconcile_observed_output=True)
    with pytest.raises(ProviderResponseError):
        budget.complete_json(request())
    assert budget.calls[0]['dispatch_state'] == 'response_known'
    assert budget.reserved_tokens == 5
    class Unknown:
        def complete_json(self, request):
            raise TimeoutError('No response')
    budget = BudgetedProvider(Unknown(), len, max_tokens=1000, max_seconds=10,
                              reconcile_observed_input=True, reconcile_observed_output=True)
    with pytest.raises(TimeoutError):
        budget.complete_json(request())
    assert budget.calls[0]['dispatch_state'] == 'response_unknown'
    assert budget.reserved_tokens == 164


def test_initial_front_continuation_keeps_previously_settled_input(tmp_path):
    public, _ = make_public_fixture(tmp_path / 'benchmark')
    public_runner = PublicLoopRunner(public)
    workspace = tmp_path / 'front-workspace'
    prepared = public_runner.prepare('CASE-1', workspace, tmp_path / 'prepare')
    source = Path(prepared['source_root'])
    observation = public_runner.observe(workspace, tmp_path / 'initial-observation')
    provider = UsageProvider()
    provider._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(source / 'templates/serviceaccount.yaml')}
    method, _ = method_input_from_task(public, 'CASE-1')
    config = {'model': 'fixture', 'max_context_tokens': 12000, 'max_output_tokens': 2048,
              'max_total_tokens': 80000, 'max_rounds': 2,
              'reconcile_observed_input': True, 'reconcile_observed_output': True}
    trace = runner.run_front_half(method, source.parent, provider,
        {**config, 'model': {'model': 'fixture'}}, lambda text: len(text.split()),
        public_observations=observation['observations']).to_wire()
    assert trace['status'] == 'READY', trace['errors']
    # Model the already supported BudgetedProvider durable ledger, isolating
    # resume from the separate missing-fields bug in standalone front traces.
    for call in trace['model_calls']:
        call.update(dispatch_state='response_known', prompt_tokens_accounted=3,
                    output_tokens_accounted=2,
                    prompt_tokens_released=call['prompt_tokens_reserved']-3,
                    output_tokens_released=call['output_tokens_reserved']-2)
    preserved = copy.deepcopy(trace['model_calls'])
    report = repair_case(public, tmp_path, 'CASE-1', tmp_path / 'run', provider,
        lambda text: len(text.split()), config, initial_trace=trace,
        public_runner=public_runner, executor=TwoStepExecutor(), reviewer=AcceptPublicReview())
    assert report['status'] == 'PUBLIC_REVIEW_COMPLETE', report['errors']
    assert report['budget']['resumed_model_calls'] == len(preserved)
    assert report['budget']['reserved_tokens'] == len(preserved) * 5
    assert trace['model_calls'] == preserved


@pytest.mark.parametrize('identity', [False, None])
def test_input_usage_without_verified_identity_stays_reserved(identity):
    class Port:
        def complete_json(self, request): return ModelResponse('{}', metadata(identity))
    budget = BudgetedProvider(Port(), len, max_tokens=1000, max_seconds=10,
                              reconcile_observed_input=True)
    budget.complete_json(request())
    assert budget.reserved_tokens == 164


@pytest.mark.parametrize('value', [-1, True, 1000, '3', None])
def test_invalid_usage_never_releases_input_or_output(value):
    class Port:
        def complete_json(self, request): return ModelResponse('{}', metadata(True, value, value))
    budget = BudgetedProvider(Port(), len, max_tokens=1000, max_seconds=10,
                              reconcile_observed_input=True, reconcile_observed_output=True)
    budget.complete_json(request())
    assert budget.reserved_tokens == 164


@pytest.mark.parametrize('point', ['APPLIED', 'UNKNOWN'])
def test_terminal_recovery_preserves_settlement_and_unknown_call_after_process_exit(tmp_path, point, monkeypatch):
    from tests.test_consumer_trial_recovery import CHILD, recover
    from tests import test_consumer_trial_recovery as fixtures
    from contractedge.development import public_demo
    from contractedge.development.public_demo import freeze_example, default_config
    # Frozen methods intentionally contain only src; use the unchanged public
    # synthetic examples shipped with the existing test suite, never a grader.
    monkeypatch.setattr(public_demo, 'SYSTEM_ROOT', Path(fixtures.__file__).resolve().parents[1])
    public = freeze_example('config-binding', tmp_path)
    config = {**default_config('config-binding'), 'execution_backend': 'direct', 'max_rounds': 1,
              'run_id': 'settlement-recovery', 'reconcile_observed_input': True,
              'reconcile_observed_output': True}
    config.pop('topomen_source_root', None)
    config.pop('topomen_source_sha256', None)
    child = CHILD.replace('  return super().complete_json(request)', '''  response=super().complete_json(request)
  from contractedge.model.provider import ModelResponse
  return ModelResponse(response.content,{**response.metadata,'model_identity_verified':True,
   'usage':{'prompt_tokens':3,'completion_tokens':2}})''')
    script = tmp_path / 'child.py'
    script.write_text(child)
    completed = subprocess.run([sys.executable, '-B', str(script), str(tmp_path), 'topomen',
                                point, json.dumps(config)], capture_output=True, text=True)
    assert completed.returncode == 73, completed.stdout + completed.stderr
    ledger = tmp_path / 'run/model_calls.json'
    before = ledger.read_bytes()
    calls = json.loads(before)
    expected = 15 if point == 'APPLIED' else 10 + calls[-1]['prompt_tokens_reserved'] + calls[-1]['output_tokens_reserved']
    report = recover(tmp_path, public, config)
    assert report['budget']['reserved_tokens'] == expected
    assert report['budget']['model_calls'] == 3
    assert report['recovery']['dispatches'] == 0
    assert report['recovery']['unknown_reservations_retained'] is (point == 'UNKNOWN')
    assert ledger.read_bytes() == before


def test_settlement_is_idempotent_and_persisted_before_and_after_dispatch():
    observed = []
    class Port:
        def complete_json(self, request):
            assert observed[-1]['dispatch_state'] == 'response_unknown'
            assert observed[-1]['prompt_tokens_reserved'] == 100
            return ModelResponse('{}', metadata())
    budget = BudgetedProvider(Port(), len, max_tokens=1000, max_seconds=10,
        on_update=lambda calls: observed.append(copy.deepcopy(calls[-1])),
        reconcile_observed_input=True, reconcile_observed_output=True)
    req = request()
    quote = budget.quote_request(req)
    result = budget.complete_quoted(req, quote)
    budget._reconcile_output_reservation(budget.calls[0], quote, result)
    assert budget.reserved_tokens == 5
    assert observed[0]['dispatch_state'] == 'not_sent'
    assert observed[-1]['prompt_tokens_accounted'] == 3


def test_legacy_calls_are_not_retroactively_refunded_and_unknown_calls_ignore_claimed_settlement():
    from contractedge.model.budget import recorded_token_charge
    legacy = {'prompt_tokens_reserved': 100, 'output_tokens_reserved': 64,
              'response_content': '{}', 'response_metadata': metadata()}
    assert recorded_token_charge(legacy) == 164
    unknown = {**legacy, 'dispatch_state': 'response_unknown', 'response_content': None,
               'prompt_tokens_accounted': 0, 'output_tokens_accounted': 0}
    assert recorded_token_charge(unknown) == 164


@pytest.mark.parametrize('value', [-1, True, 1000, '3'])
def test_invalid_saved_settlement_cannot_increase_future_allowance(value):
    from contractedge.model.budget import recorded_token_charge
    call = {'prompt_tokens_reserved': 100, 'output_tokens_reserved': 64,
            'response_content': '{}', 'response_metadata': metadata(),
            'prompt_tokens_accounted': value}
    with pytest.raises(ValueError): recorded_token_charge(call)


def test_saved_settlement_must_match_original_verified_usage():
    from contractedge.model.budget import recorded_token_charge
    call = {'prompt_tokens_reserved': 100, 'output_tokens_reserved': 64,
            'response_content': '{}', 'response_metadata': metadata(),
            'prompt_tokens_accounted': 0, 'output_tokens_accounted': 2}
    assert recorded_token_charge(call) == 102


def test_token_rejection_does_not_dispatch_or_create_financial_reservation(tmp_path, monkeypatch):
    from contractedge.model.money import MoneyBudget
    from contractedge.model.provider import OpenAICompatibleProvider
    from contractedge.model.budget import BudgetExceeded
    money = MoneyBudget(tmp_path / 'money.json', limit_cny='1', model='fixture-model',
                        input_cny_per_million='2', output_cny_per_million='8')
    monkeypatch.setenv('CE_BUDGET_FAKE_KEY', 'not-a-real-key')
    port = OpenAICompatibleProvider('https://example.invalid', 'CE_BUDGET_FAKE_KEY', money_budget=money)
    def forbidden(*args, **kwargs): raise AssertionError('Rejected request reached HTTP')
    monkeypatch.setattr('urllib.request.urlopen', forbidden)
    budget = BudgetedProvider(port, len, max_tokens=1, max_seconds=10,
        reconcile_observed_input=True, reconcile_observed_output=True)
    with pytest.raises(BudgetExceeded): budget.complete_json(request())
    assert budget.calls == []
    assert money.summary()['calls'] == 0
