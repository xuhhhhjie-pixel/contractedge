"""Production DirectPlan dispatch errors preserve only their current primary."""
from dataclasses import asdict
import json
import sys
import traceback

import pytest

from contractedge.development import public_demo
from contractedge.execution.consumers.direct import ModifyOnlyConsumer
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration.recorder import ProviderJournal, RunRecorder


@pytest.fixture
def direct_plan_case(tmp_path, monkeypatch):
    public = public_demo.freeze_example('config-binding', tmp_path)
    runner = public_demo.PublicExampleRunner(public, 'config-binding')
    config = {**public_demo.default_config('config-binding'), 'execution_backend': 'direct',
              'method_mode': 'without_relation', 'max_rounds': 1, 'max_format_retries': 0,
              'run_id': 'direct-plan-dispatch-primary'}
    config.pop('topomen_source_root', None)
    config.pop('topomen_source_sha256', None)
    journals = []
    original_attach = ProviderJournal.attach
    def attach(self, provider):
        journals.append(self)
        return original_attach(self, provider)
    monkeypatch.setattr(ProviderJournal, 'attach', attach)

    class Provider:
        def __init__(self):
            self.requests = []
            self.error = None
            self.before_response = None
        def complete_json(self, request):
            self.requests.append(asdict(request))
            assert request.prompt_version == 'direct-plan.v1'
            if self.error is not None:
                raise self.error
            body = json.loads(request.messages[1]['content'])
            response = ModelResponse(json.dumps({
                'schema_version': 'ce.direct_plan.v1', 'snapshot_id': body['snapshot_id'],
                'input_digest': body['input_digest'], 'goal': 'Use selected name and retain defaults.',
                'steps': [{'id': 'own', 'path': 'app.py', 'operation': 'MODIFY',
                           'intent': 'Bind the configured production name.', 'depends_on': []}],
            }), {'zero_cost': True, 'provider': 'direct-plan-offline-fixture'})
            if self.before_response is not None:
                self.before_response()
            return response

    provider = Provider()
    faults = []
    original_write = RunRecorder._write_once
    def write(self, name, value):
        if not faults and name.startswith('journal/stages/dispatch/') and '.direct-' in name:
            active = sys.exc_info()[1]
            error = OSError('fixture DirectPlan dispatch receipt I/O failure')
            faults.append({'exception': error, 'path': name,
                           'active_exception_type': type(active).__name__,
                           'active_exception_message': str(active),
                           'stack': [frame.name for frame in traceback.extract_stack()]})
            raise error
        return original_write(self, name, value)
    monkeypatch.setattr(RunRecorder, '_write_once', write)

    output = tmp_path / 'run'
    def run():
        report = repair_case(public, tmp_path / 'tools', 'config-binding', output,
                             provider, public_demo.count_tokens, config, public_runner=runner,
                             consumer=ModifyOnlyConsumer())
        trace = json.loads((output / 'repair.trace.json').read_bytes())
        calls = json.loads((output / 'model_calls.json').read_bytes())
        (tmp_path / 'probe.json').write_text(json.dumps({
            'report_status': report['status'], 'report_errors': report['errors'],
            'trace_errors': trace['errors'], 'recording_errors': report.get('recording_errors', []),
            'provider_dispatches': len(provider.requests), 'actual_calls': calls,
            'faults': [{key: value for key, value in fault.items() if key != 'exception'}
                       for fault in faults],
        }, sort_keys=True, indent=2) + '\n')
        assert len(faults) == 1
        assert 'generate_nonrelation' in faults[0]['stack']
        assert 'record_dispatch' in faults[0]['stack']
        assert report['errors'] == trace['errors']
        return report, calls
    return provider, journals, config, faults, run


@pytest.mark.parametrize('kind', ['timeout', 'budget'])
def test_current_direct_plan_primary_survives_actual_dispatch_receipt_write_failure(direct_plan_case, kind):
    provider, journals, config, faults, run = direct_plan_case
    if kind == 'timeout':
        provider.error = TimeoutError('fixture actual DirectPlan provider timeout')
    else:
        config['max_total_tokens'] = 1  # Real BudgetedProvider admission, no fake budget exception.
    report, calls = run()
    if kind == 'timeout':
        assert len(provider.requests) == len(calls) == 1
        assert calls[0]['error'] == 'TimeoutError: fixture actual DirectPlan provider timeout'
        assert faults[0]['active_exception_type'] == 'TimeoutError'
        assert any('TimeoutError: fixture actual DirectPlan provider timeout' in row for row in report['errors'])
        assert any('dispatch receipt I/O failure' in row for row in provider.error.recording_errors)
        assert report['recording_errors'] == list(provider.error.recording_errors)
    else:
        assert provider.requests == [] and calls == []
        assert faults[0]['active_exception_type'] == 'BudgetExceeded'
        assert report['status'] == 'BUDGET_EXHAUSTED'
        assert any('dispatch receipt I/O failure' in row for row in report['recording_errors'])


def test_successful_direct_plan_receipt_failure_is_not_hidden_by_stale_or_caller_primary(direct_plan_case):
    provider, journals, config, faults, run = direct_plan_case
    try:
        raise TimeoutError('unrelated previous primary')
    except TimeoutError as earlier:
        provider.before_response = lambda: setattr(journals[0], 'primary_error', earlier)
        report, calls = run()
    assert len(provider.requests) == len(calls) == 1
    assert 'response_content' in calls[0] and not calls[0].get('error')
    assert report['status'] == 'INFRASTRUCTURE_ERROR'
    assert any('OSError: fixture DirectPlan dispatch receipt I/O failure' in row for row in report['errors'])
    assert not report.get('recording_errors')
