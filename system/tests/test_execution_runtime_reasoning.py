"""Verified public runtime failures qualify for the existing failed-trial policy."""
import json

import pytest

from contractedge.orchestration.feedback import make_feedback, feedback_prompt_view
from tests.test_candidate_runtime_failure import observe_fixture
from tests.test_execution_reasoning_policy import fixture, request


def runtime_attempt(root, plan, *, readiness=True):
    report, _ = observe_fixture(root, 'readiness_window' if readiness else None)
    sid = report['snapshot_id']
    feedback = make_feedback(case_id='CASE-1', run_id='run', input_digest='a'*64, round_id=0,
        from_snapshot=plan['snapshot_id'], to_snapshot=sid, patch_digest='b'*64,
        execution_status=report['status'], observations=report['observations'],
        plan={'postconditions': [], 'preserve_constraints': [], 'open_obligations': []},
        observation_diagnostics=[{'kind': 'CANDIDATE_RUNTIME_FAILURE',
                                 **report['candidate_runtime_failures'][0]}])
    return dict(source_snapshot=plan['snapshot_id'], model_response='{}', rejected_trial=dict(
        snapshot_id=sid, decision=dict(decision='UNCOMPARABLE', from_snapshot=plan['snapshot_id'],
                                      trial_snapshot=sid), feedback=feedback_prompt_view(feedback)))


def inputs(tmp_path, *, readiness=True):
    current=tmp_path/'current';current.mkdir();plan,config=fixture(current)
    observed=tmp_path/'observed';observed.mkdir()
    config['execution_reasoning_policy']='failed_trial_only'
    return current,plan,config,runtime_attempt(observed,plan,readiness=readiness)


@pytest.mark.parametrize('readiness',[True,False])
@pytest.mark.parametrize('decision_key',['decision','candidate_decision'])
def test_bound_runtime_failure_enables_reasoning_before_feedback_compaction(tmp_path,readiness,decision_key):
    source,plan,config,previous=inputs(tmp_path,readiness=readiness)
    trial=previous['rejected_trial']
    if decision_key!='decision':trial[decision_key]=trial.pop('decision')
    result=request(source,plan,config,previous_attempt=previous)
    assert result.thinking=='enabled' and result.max_output_tokens==16384
    assert result.metadata['execution_thinking_policy']=='FAILED_TRIAL_REASONING'
    assert result.metadata['execution_reasoning_trigger']=='VERIFIED_RUNTIME_FAILURE'
    body=json.loads(result.messages[1]['content'])
    historical=body['previous_execution_attempt']['rejected_trial']['feedback']
    # Shared human-readable tasks reach the model; original typed facts decide
    # eligibility before that bounded delivery projection.
    assert historical['failure_tasks']['shared_execution_errors'][0]['snapshot_id']==trial['snapshot_id']
    assert historical['semantic_outcome']=='UNKNOWN'


@pytest.mark.parametrize('mutation',[
    'old_current','wrong_from','wrong_trial','same_snapshot','old_feedback','old_fact',
    'wrong_program','wrong_producer','truncated','no_operation','wrong_kind','no_raw_fact',
    'claims_source_cause','passed_build','promotion','conflicting_decisions','policy_off',
])
def test_unknown_unbound_or_conflicting_failures_do_not_enable_reasoning(tmp_path,mutation):
    source,plan,config,previous=inputs(tmp_path)
    trial=previous['rejected_trial'];feedback=trial['feedback'];decision=trial['decision']
    fact=feedback['observation_diagnostics'][0]
    if mutation=='old_current':previous['source_snapshot']='OLD'
    elif mutation=='wrong_from':decision['from_snapshot']='OTHER'
    elif mutation=='wrong_trial':decision['trial_snapshot']='OTHER'
    elif mutation=='same_snapshot':trial['snapshot_id']=plan['snapshot_id']
    elif mutation=='old_feedback':feedback['to_snapshot']='OTHER'
    elif mutation=='old_fact':fact['snapshot_id']='OTHER'
    elif mutation=='wrong_program':fact['probe_sha256']='f'*64
    elif mutation=='wrong_producer':fact['producer']='candidate self report'
    elif mutation=='truncated':fact['stderr_truncated']=True
    elif mutation=='no_operation':fact['operation']='UNKNOWN_PROBE_ERROR'
    elif mutation=='wrong_kind':fact['kind']='MODEL_ASSERTION'
    elif mutation=='no_raw_fact':feedback.pop('observation_diagnostics')
    elif mutation=='claims_source_cause':fact['source_cause']='PROVEN'
    elif mutation=='passed_build':feedback['build_state']='PASSED'
    elif mutation=='promotion':decision['decision']='PROMOTE'
    elif mutation=='conflicting_decisions':trial['candidate_decision']={'decision':'PROMOTE'}
    else:config.pop('execution_reasoning_policy')
    assert request(source,plan,config,previous_attempt=previous).thinking=='disabled'


def test_empty_reasoning_fallback_still_overrides_runtime_reasoning(tmp_path):
    source,plan,config,previous=inputs(tmp_path)
    config['execution_empty_reasoning_retry']='disable_thinking'
    previous.update(model_response='',response_metadata=dict(finish_reason='length',usage=dict(
        completion_tokens=4,completion_tokens_details={'reasoning_tokens':4})))
    result=request(source,plan,config,previous_attempt=previous)
    assert result.thinking=='disabled'
    assert result.metadata['execution_retry_policy']=='EMPTY_REASONING_BUDGET_EXHAUSTED_DISABLE_THINKING'
