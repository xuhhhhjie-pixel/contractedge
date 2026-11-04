"""Selection/recovery consume durable dispatch facts, not missing-content guesses."""
import copy
import hashlib
import json

import pytest

from contractedge.orchestration.recorder import RunRecorder
from contractedge.orchestration.trial import TrialSession, ledger_facts
from contractedge.source.snapshot import compute_snapshot


def recorder_with_call(tmp_path, call):
    recorder = RunRecorder(tmp_path / 'run', run_id='dispatch-facts', input_digest='a' * 64)
    recorder.update_ledger([call])
    recorder.seal_ledger()
    return recorder


@pytest.mark.parametrize('call,unknown', [
    ({'dispatch_state': 'not_sent'}, False),
    ({'dispatch_state': 'response_unknown'}, True),
    ({'dispatch_state': 'response_unknown', 'response_content': 'partial'}, True),
    ({'dispatch_state': 'response_known', 'response_content': '{}'}, False),
    ({'response_content': '{}'}, False),
    ({}, True),
])
def test_ledger_uses_explicit_state_and_conservative_legacy_fallback(tmp_path, call, unknown):
    original = copy.deepcopy(call)
    recorder = recorder_with_call(tmp_path, call)
    before = recorder._path('journal/ledger/segment-000000.inflight.json').read_bytes()
    facts = ledger_facts(recorder)
    assert facts['unknown_dispatch'] is unknown
    assert facts['calls'] == [original]
    assert recorder._path('journal/ledger/segment-000000.inflight.json').read_bytes() == before


@pytest.mark.parametrize('call', [
    {'dispatch_state': 'response_known'},
    {'dispatch_state': 'not_sent', 'response_content': '{}'},
    {'dispatch_state': 'invented'},
])
def test_inconsistent_explicit_dispatch_facts_refuse_consumption(tmp_path, call):
    recorder = recorder_with_call(tmp_path, call)
    with pytest.raises(ValueError):
        ledger_facts(recorder)


@pytest.mark.parametrize('state', ['not_sent', 'response_unknown'])
def test_selection_allows_confirmed_unsent_but_blocks_unknown(tmp_path, state):
    recorder = recorder_with_call(tmp_path, {'dispatch_state': state})
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'app.py').write_text('value = 1\n')
    trial = TrialSession.begin(recorder, 'round-0', source, {'operations': []}, {},
        plan_path='plan.json', target_paths=['app.py'])
    patch_path = recorder.root / 'candidate.patch'
    patch_path.write_text('synthetic public candidate')
    patch = {'patch_path': str(patch_path),
             'patch_sha256': hashlib.sha256(patch_path.read_bytes()).hexdigest()}
    feedback = {'to_snapshot': compute_snapshot(source).snapshot_id, 'semantic_outcome': 'UNKNOWN'}
    if state == 'response_unknown':
        with pytest.raises(ValueError, match='Unknown dispatch'):
            trial.select({}, feedback, patch)
        assert not recorder._path('selected-candidate.json').exists()
    else:
        trial.select({}, feedback, patch)
        assert trial.events()[-1]['kind'] == 'SELECTION_COMPLETED'
        assert trial.events()[-2]['public_semantic_status'] == 'UNKNOWN'


def test_terminal_recovery_keeps_confirmed_unsent_reservation_without_new_dispatch(tmp_path):
    from tests.test_trial_transactions import hard_exit, resume
    public = hard_exit(tmp_path, 'APPLIED')
    run = tmp_path / 'run'
    identity = json.loads((run / 'journal/run.identity.json').read_bytes())
    recorder = RunRecorder(run, run_id=identity['run_id'], input_digest=identity['input_digest'])
    calls = recorder._pending(0)['calls']
    # A subsequent admission was durably recorded but never reached transport.
    calls.append({'dispatch_state': 'not_sent', 'prompt_tokens_reserved': 10,
                  'output_tokens_reserved': 5, 'model': 'fixture'})
    recorder.update_ledger(calls)
    recorder.seal_ledger()
    before = recorder._path('journal/ledger/segment-000000.inflight.json').read_bytes()
    report = resume(tmp_path, public, consume_received_response=True)
    assert report['status'] == 'RECOVERED_INCUMBENT'
    assert report['recovery']['dispatches'] == 0
    assert report['recovery']['unknown_reservations_retained'] is False
    assert report['budget']['reserved_tokens'] == sum(
        call['prompt_tokens_reserved'] + call['output_tokens_reserved'] for call in calls)
    assert recorder._path('journal/ledger/segment-000000.inflight.json').read_bytes() == before
