"""Saved public failure and protocol counterexamples for the next source freeze."""
import json
from pathlib import Path
from contractedge.orchestration.candidate_guard import compare_candidate_checks
from tests.test_isolated_scoring import load

ROOT=Path('/home/xhj/contractedge-diagnostic-source054-v2-20261026/methods/RW-ALERTMANAGER-4705/RW-ALERTMANAGER-4705')

def test_actual_registered_measurement_loss_does_not_promote():
    read=lambda name:json.loads((ROOT/name).read_text())
    old=read('rounds/0/public-observation.json')
    trial=read('rounds/1/public-observation.json')
    assert old['method_probe_status']=='COMPLETE' and trial['method_probe_status']=='PROBE_ERROR'
    decision=compare_candidate_checks(old,trial,[])
    assert decision['decision']=='UNCOMPARABLE'
    assert not decision['regressions']

def test_native_execution_failure_is_a_negative_exit_not_scorer_failure():
    assert load().score_outcome({'returncode':1,'timed_out':False},{'status':'EXECUTION_FAIL'})=='EXECUTION_FAIL'
