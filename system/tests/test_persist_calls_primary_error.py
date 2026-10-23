"""Actual provider callbacks keep failed dispatches distinct from recording errors."""
from dataclasses import asdict
from pathlib import Path
import json

import pytest

from contractedge.development import public_demo
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration.recorder import ProviderJournal, RunRecorder


@pytest.fixture
def callback_case(tmp_path, monkeypatch):
    public = public_demo.freeze_example('config-binding', tmp_path)
    runner = public_demo.PublicExampleRunner(public, 'config-binding')
    config = {**public_demo.default_config('config-binding'), 'execution_backend': 'direct',
              'max_rounds': 1, 'run_id': 'settlement-error-fixture'}
    config.pop('topomen_source_root', None)
    config.pop('topomen_source_sha256', None)
    journals = []
    attach = ProviderJournal.attach
    def capture(self, provider):
        journals.append(self)
        return attach(self, provider)
    monkeypatch.setattr(ProviderJournal, 'attach', capture)

    class Provider(public_demo.SyntheticExampleProvider):
        def __init__(self):
            super().__init__('config-binding', runner)
            self.requests = []
            self.error = None
            self.before_response = None
        def complete_json(self, request):
            self.requests.append(asdict(request))
            if self.error is not None:
                raise self.error
            response = super().complete_json(request)
            if self.before_response is not None:
                self.before_response()
            return response

    provider = Provider()
    output = tmp_path / 'run'
    def run():
        report = repair_case(public, tmp_path / 'tools', 'config-binding', output,
                             provider, public_demo.count_tokens, config, public_runner=runner)
        calls = json.loads((output / 'model_calls.json').read_bytes())
        trace = json.loads((output / 'repair.trace.json').read_bytes())
        (tmp_path / 'probe.json').write_text(json.dumps({
            'dispatches': len(provider.requests), 'ledger': calls,
            'report_errors': report['errors'], 'trace_errors': trace['errors'],
            'recording_errors': report.get('recording_errors', []),
            'journal_primary_type': type(journals[0].primary_error).__name__,
            'journal_primary_message': str(journals[0].primary_error),
        }, sort_keys=True, indent=2) + '\n')
        return report, trace, calls
    return provider, journals, run


def fail_seal_once(monkeypatch, predicate, message):
    original = RunRecorder.seal_ledger
    failed = []
    def seal(self, *args, **kwargs):
        calls = self._pending(kwargs.get('segment_index', 0))['calls']
        if not failed and calls and predicate(calls[-1]):
            error = OSError(message)
            failed.append(error)
            raise error
        return original(self, *args, **kwargs)
    monkeypatch.setattr(RunRecorder, 'seal_ledger', seal)
    return failed


def test_failed_dispatch_keeps_original_exception_when_its_settlement_seal_fails(callback_case, monkeypatch):
    provider, journals, run = callback_case
    primary = provider.error = TimeoutError('actual fixture provider timeout')
    failed = fail_seal_once(monkeypatch, lambda row: bool(row.get('error')),
                            'actual fixture settlement seal I/O failure')
    report, trace, calls = run()
    assert len(failed) == 1 and len(provider.requests) == len(calls) == 1
    assert calls[0]['error'] == 'TimeoutError: actual fixture provider timeout'
    assert 'response_content' not in calls[0]
    assert journals[0].primary_error is primary
    assert report['errors'] == trace['errors']
    assert any('TimeoutError: actual fixture provider timeout' in message for message in trace['errors'])
    assert any('settlement seal I/O failure' in message for message in primary.recording_errors)
    assert report['recording_errors'] == list(primary.recording_errors)
    assert report['budget']['model_calls'] == 1


def test_predispatch_seal_failure_still_refuses_before_any_provider_request(callback_case, monkeypatch):
    provider, journals, run = callback_case
    failed = fail_seal_once(monkeypatch, lambda row: 'response_content' not in row and not row.get('error'),
                            'actual fixture predispatch seal failure')
    report, trace, calls = run()
    assert len(failed) == 1 and provider.requests == []
    assert len(calls) == 1 and 'response_content' not in calls[0]
    assert journals[0].primary_error is failed[0]
    assert report['status'] == 'INFRASTRUCTURE_ERROR'
    assert report['errors'] == trace['errors']
    assert any('predispatch seal failure' in message for message in trace['errors'])


def test_success_settlement_seal_failure_is_not_hidden_by_stale_primary_or_caller_exception(callback_case, monkeypatch):
    provider, journals, run = callback_case
    failed = fail_seal_once(monkeypatch,
                            lambda row: isinstance(row.get('response_content'), str) and not row.get('error'),
                            'actual fixture success settlement seal failure')
    # A caller may already be handling another error. Neither that exception nor
    # a stale journal primary authorizes swallowing this successful call's failure.
    try:
        raise TimeoutError('unrelated earlier primary')
    except TimeoutError as earlier:
        provider.before_response = lambda: setattr(journals[0], 'primary_error', earlier)
        report, trace, calls = run()
        assert journals[0].primary_error is failed[0]
    assert len(failed) == 1 and len(provider.requests) == len(calls) == 1
    assert 'response_content' in calls[0] and not calls[0].get('error')
    assert report['status'] == 'INFRASTRUCTURE_ERROR'
    assert report['errors'] == trace['errors']
    assert any('success settlement seal failure' in message for message in trace['errors'])
