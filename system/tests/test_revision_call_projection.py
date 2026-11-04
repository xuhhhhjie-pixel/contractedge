"""A public-feedback revision keeps the existing budget's settled call facts."""
import copy

import pytest

from contractedge.contracts.validation import parse_method_input
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.model.budget import BudgetedProvider, BudgetExceeded, recorded_token_charge
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case, revise_plan
from contractedge.orchestration.runner import run_front_half
from tests.test_budget_settlement_contract import UsageProvider
from tests.test_repair_execution import PublicLoopRunner, TwoStepExecutor, AcceptPublicReview, make_public_fixture


def revision_fixture(tmp_path):
    from pathlib import Path
    from contractedge.source.snapshot import compute_snapshot
    public, _ = make_public_fixture(tmp_path / 'benchmark')
    runner = PublicLoopRunner(public)
    workspace = tmp_path / 'front-workspace'
    prepared = runner.prepare('CASE-1', workspace, tmp_path / 'prepare')
    source = Path(prepared['source_root'])
    observations = runner.observe(workspace, tmp_path / 'initial-observation')['observations']
    method, _ = method_input_from_task(public, 'CASE-1')
    config = {'model': 'fixture', 'max_context_tokens': 24000, 'max_output_tokens': 2048,
        'max_total_tokens': 80000, 'max_rounds': 2, 'max_elapsed_seconds': 30, 'max_model_calls': 8,
        'reconcile_observed_input': True, 'reconcile_observed_output': True}
    provider = UsageProvider()
    provider._snapshot_digests = compute_snapshot(source).file_digests
    budget = BudgetedProvider(provider, lambda text: len(text.split()), max_tokens=80000,
        max_seconds=30, max_calls=8, reconcile_observed_input=True, reconcile_observed_output=True)
    trace = run_front_half(method, source.parent, budget, {**config, 'model': {'model': 'fixture'}},
        budget.token_counter, public_observations=observations).to_wire()
    assert trace['status'] == 'READY', trace['errors']
    return public, runner, parse_method_input(method), source, config, budget, trace


def test_settled_revision_is_canonical_and_initial_trace_import_keeps_its_actual_charge(tmp_path):
    public, runner, method, source, config, budget, trace = revision_fixture(tmp_path)
    before = copy.deepcopy(trace)
    revised = revise_plan(trace, method, source, {'rationale': 'Recheck current requirement'},
        budget, budget.token_counter, config)
    assert trace == before
    assert len(revised['model_calls']) == len(budget.calls) == 3
    call = revised['model_calls'][-1]
    assert {key: call[key] for key in budget.calls[-1]} == budget.calls[-1]
    assert call['stage'] == 'EXECUTION_PLAN_REVISION' and isinstance(call['runner_binding'], dict)
    assert recorded_token_charge(call) == 5
    assert sum(recorded_token_charge(row) for row in revised['model_calls']) == budget.reserved_tokens == 15
    report = repair_case(public, tmp_path, 'CASE-1', tmp_path / 'continued', budget.provider,
        budget.token_counter, config, initial_trace=revised, public_runner=runner,
        executor=TwoStepExecutor(), reviewer=AcceptPublicReview())
    assert report['status'] == 'PUBLIC_REVIEW_COMPLETE', report['errors']
    assert report['budget']['resumed_model_calls'] == 3
    assert report['budget']['reserved_tokens'] == 15


def test_standalone_revision_uses_the_same_explicit_budget_and_settlement_policy(tmp_path):
    _, _, method, source, config, budget, trace = revision_fixture(tmp_path)
    revised = revise_plan(trace, method, source, {'rationale': 'Recheck current requirement'},
        budget.provider, budget.token_counter, config)
    call = revised['model_calls'][-1]
    assert call['dispatch_state'] == 'response_known' and isinstance(call['request'], dict)
    assert recorded_token_charge(call) == 5


@pytest.mark.parametrize('limit', ['calls', 'tokens', 'elapsed'])
def test_standalone_revision_does_not_reset_the_prior_trace_budget(tmp_path, limit):
    _, _, method, source, config, budget, trace = revision_fixture(tmp_path)
    revised = revise_plan(trace, method, source, {'rationale': 'Recheck current requirement'},
        budget.provider, budget.token_counter, config)
    call = revised['model_calls'][-1]
    if limit == 'calls':
        config['max_model_calls'] = len(trace['model_calls'])
    elif limit == 'tokens':
        # A single revision fits alone, but not after the two settled front calls.
        config['max_total_tokens'] = call['prompt_tokens_reserved'] + call['output_tokens_reserved'] + 9
    else:
        trace['model_calls'][-1]['elapsed_seconds'] = config['max_elapsed_seconds'] + 1
    before = copy.deepcopy(trace)
    def forbidden_dispatch(request):
        raise AssertionError('The prior trace has already consumed the remaining allowance')
    budget.provider.complete_json = forbidden_dispatch
    with pytest.raises(BudgetExceeded):
        revise_plan(trace, method, source, {'rationale': 'Recheck current requirement'},
            budget.provider, budget.token_counter, config)
    assert trace == before


@pytest.mark.parametrize('failure', ['admission', 'unknown', 'invalid_plan'])
def test_failed_revision_does_not_fabricate_or_append_a_call_to_the_previous_trace(tmp_path, failure):
    _, _, method, source, config, budget, trace = revision_fixture(tmp_path)
    before = copy.deepcopy(trace)
    if failure == 'admission':
        budget.max_calls = len(budget.calls)
        error = BudgetExceeded
    else:
        def failed(request):
            if failure == 'unknown':
                raise TimeoutError('Controlled unknown revision')
            return ModelResponse('{}', {})
        budget.provider.complete_json = failed
        error = TimeoutError if failure == 'unknown' else (ValueError, RuntimeError)
    with pytest.raises(error):
        revise_plan(trace, method, source, {'rationale': 'Recheck current requirement'},
                    budget, budget.token_counter, config)
    assert trace == before
    assert len(budget.calls) == (2 if failure == 'admission' else 3)
    if failure == 'unknown':
        assert budget.calls[-1]['dispatch_state'] == 'response_unknown'
    elif failure == 'invalid_plan':
        assert budget.calls[-1]['dispatch_state'] == 'response_known'
