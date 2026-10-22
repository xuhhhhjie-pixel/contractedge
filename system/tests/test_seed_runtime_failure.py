"""Saved-failure recovery retains startup errors without relaxing promotion."""
import importlib.util
from pathlib import Path

import pytest

from contractedge.source.snapshot import compute_snapshot
from tests.test_candidate_regression_guard import run


def harness():
    spec=importlib.util.spec_from_file_location('worker_v7',Path(__file__).parents[1]/'scripts/run_reliability_worker_v7.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.mark.parametrize('kind,rounds,expected',[('confirmed',2,2),('unclassified',2,1),('stale',2,1),('confirmed',1,1)])
def test_seed_runtime_failure_retries_only_existing_round_after_restore(tmp_path,kind,rounds,expected):
    source=tmp_path/'source';source.mkdir();target=source/'a.go';target.write_text('seed')
    sid=compute_snapshot(source).snapshot_id
    initial=dict(route='EXECUTE',to_snapshot=sid)
    count=[]
    def attempt(index,feedback,observation,previous):
        count.append(index)
        if index:
            assert target.read_text()=='seed'
            assert previous['source_snapshot']==sid
            task=previous['rejected_trial']['feedback']['failure_tasks']['shared_execution_errors'][0]
            assert task['actual_error']=='panic: nil receiver at public startup'
            assert task['snapshot_id']!=sid
        target.write_text('crashed' if not index else 'fixed')
        snap=compute_snapshot(source).snapshot_id
        trial=run({} if not index else {'retain':'PASS'},snap)
        diagnostics=[]
        if not index:
            trial.update(status='EXECUTION_FAILED',method_probe_status='PROBE_ERROR')
            if kind!='unclassified':
                fact=dict(snapshot_id='old' if kind=='stale' else snap,operation='PRODUCTION_PROCESS_START',
                    status='FAILED',source_locations=[dict(path='a.go',line=1)],
                    stderr_excerpt='panic: nil receiver at public startup',stderr_sha256='a'*64)
                trial['candidate_runtime_failures']=[fact]
                diagnostics=[dict(kind='CANDIDATE_RUNTIME_FAILURE',**fact)]
        return dict(observation=trial,feedback=dict(route='EXECUTE' if not index else 'STOP',
            to_snapshot=snap,semantic_outcome='UNKNOWN',observation_diagnostics=diagnostics),
            execution_mapping=[dict(path='a.go',new_text='crashed' if not index else 'fixed')])
    result=harness().continue_seeded_repair(source,['a.go'],initial,run({'retain':'PASS'},sid),
        max_rounds=rounds,guard_enabled=True,output=tmp_path/'rounds',attempt=attempt,preservation_ids=lambda f:['retain'])
    assert len(count)==expected
    assert result['rounds'][0]['candidate_decision']['decision']=='UNCOMPARABLE'
    assert target.read_text()==('fixed' if expected==2 else 'seed')
    assert result['source_integrity_verified']


def test_new_runtime_diagnostics_never_change_historical_feedback_signature():
    def historical_feedback(*,execution_status):return execution_status
    report=dict(status='EXECUTION_FAILED',snapshot_id='s',method_probe_status='PROBE_ERROR',
        candidate_runtime_failures=[dict(snapshot_id='s',operation='PRODUCTION_PROCESS_START',status='FAILED',source_locations=[dict(path='a.go',line=1)])])
    assert harness().public_feedback_kwargs(historical_feedback,report)=={}


def test_seed_uses_same_candidate_diagnostic_classification_as_normal_loop():
    from contractedge.orchestration.feedback import make_feedback
    report=dict(status='EXECUTION_FAILED',snapshot_id='s',method_probe_status='PROBE_ERROR',
        candidate_runtime_failures=[dict(snapshot_id='s',operation='PRODUCTION_PROCESS_START',status='FAILED',source_locations=[dict(path='a.go',line=1)])])
    kwargs=harness().public_feedback_kwargs(make_feedback,report)
    assert len(kwargs['observation_diagnostics'])==1
    assert kwargs['observation_diagnostics'][0]['kind']=='CANDIDATE_RUNTIME_FAILURE'
