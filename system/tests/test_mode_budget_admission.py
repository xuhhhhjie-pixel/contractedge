"""Mode input capacity failures stop before dispatch without format blame."""
import json
from types import SimpleNamespace

import pytest

from contractedge.experiments.modes import generate_nonrelation
from contractedge.model.budget import BudgetExceeded, BudgetedProvider


@pytest.mark.parametrize('mode', ['without_relation', 'direct'])
def test_presemantic_inventory_limit_is_budget_failure_before_any_call(tmp_path, mode):
    (tmp_path / 'app.py').write_text('#' + 'x' * (4 * 1024 * 1024))
    method = SimpleNamespace(requirement_text='Preserve the public value.',
        input_digest='a' * 64, declared_artifacts=(), case_id='public')
    def forbidden(*args, **kwargs):
        pytest.fail('Admission rejection must not dispatch or consume a format correction')
    budget = BudgetedProvider(SimpleNamespace(complete_json=forbidden), len,
        max_tokens=10000000, max_seconds=30)
    journal = SimpleNamespace(engine=SimpleNamespace(policy=SimpleNamespace(consume=forbidden)))
    with pytest.raises(BudgetExceeded, match='inventory exceeds byte limit'):
        generate_nonrelation(mode, method, tmp_path, {'allowed_paths': ['app.py']},
            budget, len, {'model': 'fixture', 'max_context_tokens': 1000}, journal=journal)
    assert budget.calls == [] and budget.reserved_tokens == 0


@pytest.mark.parametrize('limit', ['input', 'model_window'])
def test_direct_plan_context_rejection_reaches_budget_terminal_without_call_or_format_usage(tmp_path, limit):
    from contractedge.development import public_demo
    from contractedge.development.method_panel import PanelProvider, panel_config
    from contractedge.development.method_panel_fixtures import PanelRunner, freeze_panel_case
    from contractedge.orchestration.repair import repair_case
    public = freeze_panel_case('config-binding/base', tmp_path)
    runner = PanelRunner(public, 'config-binding/base')
    provider = PanelProvider('config-binding/base', 'without_relation', runner)
    config = {**panel_config('without_relation'), 'max_format_retries': 1}
    config['max_context_tokens' if limit == 'input' else 'model_context_tokens'] = 1
    output = tmp_path / 'repair'
    report = repair_case(public, tmp_path / 'unused-tools', runner.case, output, provider,
        public_demo.count_tokens, config, public_runner=runner)
    assert report['status'] == 'BUDGET_EXHAUSTED'
    assert report['primary_failure']['failure_class'] == 'infrastructure'
    assert report['primary_failure']['dispatch_state'] == 'not_sent'
    assert report['execution_format_corrections_used'] == 0
    assert provider.requests == []
    assert json.loads((output / 'model_calls.json').read_text()) == []
    state = json.loads((output / 'state.json').read_text())
    assert all(row['used'] == 0 for row in state.get('policy_usage', [])
               if row['policy'] == 'format_correction')


@pytest.mark.parametrize('failure', ['context', 'unknown'])
def test_direct_plan_correction_admission_does_not_spend_unstarted_format_attempt(tmp_path, failure):
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.state import PolicyLedger
    (tmp_path / 'app.py').write_text('value = 0\n')
    method = SimpleNamespace(requirement_text='Set the public value to one.',
        input_digest='a' * 64, declared_artifacts=(), case_id='public')
    requests = []
    class Provider:
        def complete_json(self, request):
            requests.append(request)
            if len(requests) == 1:
                return ModelResponse('{"steps":[]}', {})
            raise TimeoutError('Correction transport returned no response')
    def counter(text):
        return 1000 if failure == 'context' and 'Correct this same independent plan contract:' in text else 1
    config = {'model': 'fixture', 'max_context_tokens': 10,
              'max_output_tokens': 64, 'max_format_retries': 1}
    policies = PolicyLedger(config)
    receipts = []
    journal = SimpleNamespace(engine=SimpleNamespace(policy=policies),
        record_dispatch=lambda stage, **kwargs: receipts.append((stage, kwargs)))
    budget = BudgetedProvider(Provider(), counter, max_tokens=10000, max_seconds=30, max_calls=3)
    with pytest.raises(BudgetExceeded if failure == 'context' else TimeoutError):
        generate_nonrelation('without_relation', method, tmp_path, {'allowed_paths': ['app.py']},
            budget, counter, config, journal=journal)
    expected = 1 if failure == 'context' else 2
    assert len(requests) == len(budget.calls) == len(receipts) == expected
    assert policies.used('format_correction', 'direct-plan:mode') == expected - 1
    assert budget.calls[-1]['dispatch_state'] == ('response_known' if failure == 'context' else 'response_unknown')
