"""Combined stage hooks and isolated-observer gates at mock runtime boundaries.

No test here opens Docker or a model API. Host fixture coverage is separate.
"""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from contractedge.development import public_demo
from contractedge.contracts.validation import canonical_digest
from contractedge.execution import public_observer
from contractedge.execution.review import ModelPublicReviewer
from contractedge.execution.topomen import PlanRejected
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.orchestration.recorder import RunRecorder
from tests.test_public_isolated_demo import BoundarySessions, MOCK_RUNTIME, NeverDispatch


def read(path):
    return json.loads(Path(path).read_bytes())


def receipts(root, stage):
    return [read(p) for p in sorted((root / 'journal/stages' / stage).glob('*/*.json'))]


def run_fixture(tmp_path, monkeypatch, boundary, *, provider=None):
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    settings = tmp_path / 'synthetic.json'
    settings.write_text(json.dumps({'provider':'synthetic', 'max_rounds':1,
                                   'public_runtime_config':MOCK_RUNTIME}))
    if provider is None:
        result = public_demo.run_demo('config-binding', tmp_path/'run', provider_config=settings, runtime='isolated')
    else:
        with patch.object(public_demo, '_provider', return_value=(provider, public_demo.count_tokens)):
            result = public_demo.run_demo('config-binding', tmp_path/'run', provider_config=settings, runtime='isolated')
    root = tmp_path/'run/repair'
    identity = read(root/'journal/run.identity.json')
    recorder = RunRecorder(root, run_id=identity['run_id'], input_digest=identity['input_digest'])
    for path in (root/'journal/stages').glob('*/*/*.json'):
        value = read(path)
        for field in ('input_refs','output_refs','projection_refs'):
            for ref in value[field]:recorder.verify_artifact(ref)
        for ref in value['call_refs']:recorder.verify_call(ref)
    return result, root, read(root/'repair.trace.json')


def test_initial_unverified_observation_has_receipt_and_zero_dispatch(tmp_path, monkeypatch):
    provider = NeverDispatch()
    result, root, trace = run_fixture(tmp_path, monkeypatch,
        BoundarySessions(candidate_status='INFRASTRUCTURE_ERROR'), provider=provider)
    assert provider.requests == []
    initial = receipts(root, 'observe_initial')
    assert len(initial) == 1
    assert initial[0]['snapshot_id'] == trace['initial_snapshot_id']
    assert initial[0]['status'] != 'EXECUTION_PASS'
    assert initial[0]['call_refs'] == []
    assert not receipts(root,'review')
    assert receipts(root,'selection')[-1]['snapshot_id'] == trace['initial_snapshot_id']


def test_incomplete_candidate_skips_paid_review_records_downgrade_and_rollback(tmp_path, monkeypatch):
    boundary = BoundarySessions(check_sequences=[
        {'selected':'FAIL','disabled':'PASS','stable':'PASS'},
        {'selected':'PASS','disabled':'PASS','stable':'PASS'}],
        close_sequences=['CLOSED','INFRASTRUCTURE_ERROR'])
    result, root, trace = run_fixture(tmp_path, monkeypatch, boundary)
    assert result['model_calls'] == 3
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id']
    assert not any('UnboundLocalError' in error for error in trace['errors'])
    review = receipts(root,'review')[-1]
    assert review['status'] == 'UNKNOWN' and review['call_refs'] == []
    output = read(root/review['output_refs'][0]['relative_path'])
    assert output['required_public_profile']['binding_valid'] is False
    rollback = receipts(root,'rollback')[-1]
    assert rollback['status'] == 'RESTORED'
    assert rollback['snapshot_id'] == trace['initial_snapshot_id']
    assert receipts(root,'observe')[-1]['snapshot_id'] != trace['initial_snapshot_id']
    assert receipts(root,'selection')[-1]['snapshot_id'] == trace['initial_snapshot_id']
    assert (root/'candidate.patch').read_bytes() == b''


def test_review_receipt_captures_enforced_fail_after_model_pass(tmp_path, monkeypatch):
    boundary = BoundarySessions(check_sequences=[
        {'selected':'FAIL','disabled':'PASS','stable':'PASS'},
        {'selected':'PASS','disabled':'PASS','stable':'FAIL'}])
    monkeypatch.setattr(ModelPublicReviewer,'review',lambda *args,**kwargs: {
        'decision':'PASS','checks':[],'failure_kind':'UNKNOWN','rationale':'Explicit mock model PASS'})
    result, root, trace = run_fixture(tmp_path, monkeypatch, boundary)
    review = receipts(root,'review')[-1]
    assert review['status'] == 'FAIL'
    actual = read(root/review['output_refs'][0]['relative_path'])
    assert actual['decision'] == actual['required_public_profile']['decision'] == 'FAIL'
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id']
    assert receipts(root,'rollback')[-1]['status'] == 'RESTORED'


def test_self_consistent_old_observation_cannot_be_relabelled_as_current(tmp_path, monkeypatch):
    boundary = BoundarySessions(check_sequences=[
        {'selected':'FAIL','disabled':'PASS','stable':'PASS'},
        {'selected':'PASS','disabled':'PASS','stable':'PASS'}])
    original = public_demo.IsolatedPublicExampleRunner.observe
    observed = []
    def stale(self,*args,**kwargs):
        result = original(self,*args,**kwargs)
        observed.append(result['snapshot_id'])
        if len(observed) == 1:return result
        old, current = observed[0], observed[-1]
        def replace(value):
            if isinstance(value,dict):return {key:replace(item) for key,item in value.items()}
            if isinstance(value,list):return [replace(item) for item in value]
            return old if value == current else value
        result = replace(copy.deepcopy(result))
        result['required_public_profile']['observations_digest'] = canonical_digest(result['observations'])
        assert public_observer.isolated_observation_complete(result)
        return result
    monkeypatch.setattr(public_demo.IsolatedPublicExampleRunner,'observe',stale)
    result, root, trace = run_fixture(tmp_path,monkeypatch,boundary)
    assert result['model_calls'] == 3
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id']
    receipt = receipts(root,'observe')[-1]
    assert receipt['status'] == 'INVALID_OBSERVATION'
    assert receipt['snapshot_id'] == observed[1]
    output = next(ref for ref in receipt['output_refs'] if ref['relative_path'].endswith('/public-observation.json'))
    assert output['snapshot_id'] == observed[0]
    assert read(root/output['relative_path'])['snapshot_id'] == observed[0]
    assert receipts(root,'review')[-1]['call_refs'] == []
    assert receipts(root,'rollback')[-1]['snapshot_id'] == observed[0]


def test_observer_exception_records_not_run_review_and_emergency_restore(tmp_path, monkeypatch):
    original = public_demo.IsolatedPublicExampleRunner.observe
    calls = []
    def failed(self,*args,**kwargs):
        calls.append(args)
        if len(calls) == 2:raise RuntimeError('explicit mock observation failure')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(public_demo.IsolatedPublicExampleRunner,'observe',failed)
    result,root,trace = run_fixture(tmp_path,monkeypatch,BoundarySessions())
    assert result['model_calls'] == 3
    assert 'explicit mock observation failure' in ' '.join(trace['errors'])
    assert receipts(root,'observe')[-1]['status'] == 'ERROR'
    assert receipts(root,'review')[-1]['status'] == 'NOT_RUN'
    assert receipts(root,'review')[-1]['call_refs'] == []
    assert receipts(root,'rollback')[-1]['attempt_id'] == 'incomplete-trial'
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id']
    assert (root/'candidate.patch').read_bytes() == b''


def test_rejected_edit_records_skipped_observation_and_review_without_fake_calls(tmp_path,monkeypatch):
    def reject(*args,**kwargs):raise PlanRejected('explicit mock concrete edit rejection')
    monkeypatch.setattr(TopoMenAgentExecutor,'execute',reject)
    result,root,trace = run_fixture(tmp_path,monkeypatch,BoundarySessions())
    assert result['model_calls'] == 2
    for stage in ('observe','review'):
        receipt = receipts(root,stage)[-1]
        assert receipt['status'] == 'NOT_RUN'
        assert receipt['call_refs'] == []
    assert not (root/'rounds/0/public-review.json').exists()
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id']
    assert (root/'candidate.patch').read_bytes() == b''


def test_failed_restore_records_failure_and_prohibits_export(tmp_path,monkeypatch):
    from contractedge.orchestration import candidate_guard
    def fail(*args,**kwargs):raise OSError('explicit mock restore failure')
    monkeypatch.setattr(candidate_guard,'restore_candidate',fail)
    boundary = BoundarySessions(check_sequences=[
        {'selected':'FAIL','disabled':'PASS','stable':'PASS'},
        {'selected':'PASS','disabled':'PASS','stable':'FAIL'}])
    result,root,trace = run_fixture(tmp_path,monkeypatch,boundary)
    assert trace['candidate'] is None
    assert trace['status'] == 'INFRASTRUCTURE_ERROR'
    assert 'export prohibited' in ' '.join(trace['errors'])
    rollbacks = receipts(root,'rollback')
    assert rollbacks and all(receipt['status'] == 'RESTORE_FAILED' for receipt in rollbacks)
    assert receipts(root,'selection')[-1]['snapshot_id'] == trace['final_snapshot_id']
    assert not (root/'candidate.patch').exists()
