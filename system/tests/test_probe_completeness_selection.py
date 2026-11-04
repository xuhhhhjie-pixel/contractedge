"""Measurement failure is a promotion blocker, not a semantic verdict."""
import pytest
from contractedge.orchestration.candidate_guard import compare_candidate_checks
from contractedge.execution.public import has_candidate_execution_failure
from tests.test_candidate_regression_guard import run

@pytest.mark.parametrize('old_probe',[None,'COMPLETE','PARTIAL','PROBE_ERROR'])
def test_probe_error_blocks_promotion_without_model_preservation_ids(old_probe):
    old=run({'target':'FAIL'},'S0');trial=run({},'S1')
    if old_probe:old['method_probe_status']=old_probe
    trial['method_probe_status']='PROBE_ERROR'
    result=compare_candidate_checks(old,trial,[])
    assert result['decision']=='UNCOMPARABLE'
    assert not result['regressions']
    assert not has_candidate_execution_failure(trial)
    assert any(row['check_id']=='method_probe_execution' for row in result['uncomparable'])

@pytest.mark.parametrize('probe',[None,'COMPLETE','PARTIAL'])
def test_complete_or_absent_probe_does_not_lock_intended_behavior(probe):
    old=run({'old_target':'PASS'},'S0');trial=run({'old_target':'FAIL'},'S1')
    if probe:trial['method_probe_status']=probe
    assert compare_candidate_checks(old,trial,[])['decision']=='PROMOTE'

@pytest.mark.parametrize('change',['input','environment','scenario','snapshot'])
def test_probe_error_does_not_claim_comparable_semantic_regression(change):
    old=run({'retain':'PASS'},'S0');trial=run({},'S1');trial['method_probe_status']='PROBE_ERROR'
    if change=='input':trial['input_digest']='other'
    if change=='environment':trial['environment_digest']='other'
    if change=='scenario':old['observations'][0]['scenario']='other'
    if change=='snapshot':old['observations'][0]['snapshot_id']='stale'
    result=compare_candidate_checks(old,trial,[])
    assert result['decision']=='UNCOMPARABLE' and not result['regressions']
