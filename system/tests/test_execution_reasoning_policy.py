from copy import deepcopy
import pytest

from contractedge.execution.topomen import build_execution_request,PlanRejected
from tests.test_repair_execution import bound_plan


def fixture(root):
    (root/'templates').mkdir();(root/'templates/serviceaccount.yaml').write_text('name: default\n')
    plan=bound_plan(root)
    config=dict(model='fixture',provider='openai_compatible',max_context_tokens=50000,
        execution_max_output_tokens=16384,execution_thinking='disabled',
        execution_reasoning_policy='complex_plan')
    return plan,config


def request(root,plan,config,**kw):
    return build_execution_request(plan,root,{'allowed_paths':['templates/**']},len,config,requirement='Repair',**kw)


def test_multioperation_plan_uses_reasoning_without_raising_output_or_call_cap(tmp_path):
    plan,config=fixture(tmp_path)
    second=deepcopy(plan['operations'][0]);second['change_id']='C2';plan['operations'].append(second)
    result=request(tmp_path,plan,config)
    assert result.thinking=='enabled'
    assert result.max_output_tokens==16384
    assert result.metadata['execution_thinking_policy']=='COMPLEX_PLAN_REASONING'


def test_simple_plan_keeps_explicit_stage_setting(tmp_path):
    plan,config=fixture(tmp_path)
    assert request(tmp_path,plan,config).thinking=='disabled'


def test_explicit_runtime_prerequisite_uses_reasoning_for_one_operation(tmp_path):
    plan,config=fixture(tmp_path)
    plan['precondition_review']=[dict(disposition='REQUIRED',source_evidence_ref='unused')]
    # Source on target is supplied in full, independent of read-only support.
    plan['coordination_context']={'source_support':[dict(id='unused',snapshot_id=plan['snapshot_id'],
        locator={'path':'templates/serviceaccount.yaml'},excerpt='') ]}
    assert request(tmp_path,plan,config).thinking=='enabled'


def test_existing_empty_reasoning_fallback_remains_higher_priority(tmp_path):
    plan,config=fixture(tmp_path)
    second=deepcopy(plan['operations'][0]);second['change_id']='C2';plan['operations'].append(second)
    config['execution_empty_reasoning_retry']='disable_thinking'
    previous=dict(source_snapshot=plan['snapshot_id'],model_response='',
        response_metadata=dict(finish_reason='length',usage=dict(completion_tokens=4,completion_tokens_details={'reasoning_tokens':4})))
    assert request(tmp_path,plan,config,previous_attempt=previous).thinking=='disabled'


def test_unknown_policy_fails_before_model_call(tmp_path):
    plan,config=fixture(tmp_path);config['execution_reasoning_policy']='unknown'
    with pytest.raises(PlanRejected,match='reasoning policy'):
        request(tmp_path,plan,config)


def test_failed_trial_only_keeps_code_output_budget_for_initial_precondition_plan(tmp_path):
    plan,config=fixture(tmp_path)
    config['execution_reasoning_policy']='failed_trial_only'
    plan['precondition_review']=[dict(disposition='REQUIRED',source_evidence_ref='unused')]
    plan['coordination_context']={'source_support':[dict(id='unused',snapshot_id=plan['snapshot_id'],
        locator={'path':'templates/serviceaccount.yaml'},excerpt='')]}
    result=request(tmp_path,plan,config)
    assert result.thinking=='disabled' and result.max_output_tokens==16384


def test_failed_trial_only_still_reasons_on_a_bound_behavior_regression(tmp_path):
    plan,config=fixture(tmp_path);config['execution_reasoning_policy']='failed_trial_only'
    result=request(tmp_path,plan,config,previous_attempt=rejected_predicate_attempt(plan))
    assert result.thinking=='enabled'
    assert result.metadata['execution_thinking_policy']=='FAILED_TRIAL_REASONING'


def test_request_explains_multiple_edits_reuse_the_same_semantic_change_id(tmp_path):
    import json
    plan,config=fixture(tmp_path)
    body=json.loads(request(tmp_path,plan,config).messages[1]['content'])
    assert body['edit_batch_rules']['multiple_edits_per_change_id'] is True
    assert body['edit_batch_rules']['numbered_fields_allowed'] is False
    assert set(body['edit_batch_rules']['allowed_edit_fields'])=={'change_id','path','operation','old_text','new_text'}


def rejected_predicate_attempt(plan):
    from tests.test_failure_tasks import predicate_record
    from contractedge.orchestration.feedback import feedback_prompt_view
    return dict(source_snapshot=plan['snapshot_id'],model_response='{"edits":[]}',
        rejected_trial=dict(snapshot_id='TRIAL',decision=dict(decision='REJECT_REGRESSION',
            from_snapshot=plan['snapshot_id'],trial_snapshot='TRIAL'),
            feedback=feedback_prompt_view(dict(to_snapshot='TRIAL',public_observations=[predicate_record()]))))


@pytest.mark.parametrize('decision_key',['decision','candidate_decision'])
def test_single_operation_observed_regression_enables_existing_repair_reasoning(tmp_path,decision_key):
    plan,config=fixture(tmp_path);previous=rejected_predicate_attempt(plan)
    if decision_key!='decision':
        trial=previous['rejected_trial'];trial[decision_key]=trial.pop('decision')
    result=request(tmp_path,plan,config,previous_attempt=previous)
    assert result.thinking=='enabled' and result.max_output_tokens==16384
    assert result.metadata['execution_thinking_policy']=='FAILED_TRIAL_REASONING'
    import json
    body=json.loads(result.messages[1]['content'])
    assert body['previous_execution_attempt']['rejected_trial']['feedback']['failure_tasks']['failed_predicates'][0]['observed']['exists'] is False


@pytest.mark.parametrize('change',['old_current','wrong_trial','unbound_fact','no_failure','policy_off'])
def test_unbound_or_disabled_trial_cannot_change_stage_reasoning(tmp_path,change):
    plan,config=fixture(tmp_path);previous=rejected_predicate_attempt(plan)
    if change=='old_current':previous['source_snapshot']='OLD'
    elif change=='wrong_trial':previous['rejected_trial']['decision']['trial_snapshot']='OTHER'
    elif change=='unbound_fact':previous['rejected_trial']['feedback']['failure_tasks']['failed_predicates'][0]['snapshot_id']='OLD'
    elif change=='no_failure':previous['rejected_trial']['feedback']['failure_tasks']['failed_predicates']=[]
    else:config.pop('execution_reasoning_policy')
    assert request(tmp_path,plan,config,previous_attempt=previous).thinking=='disabled'


def test_conflicting_shared_harness_decisions_never_enable_trial_reasoning(tmp_path):
    plan,config=fixture(tmp_path);previous=rejected_predicate_attempt(plan)
    previous['rejected_trial']['candidate_decision']={'decision':'PROMOTE'}
    assert request(tmp_path,plan,config,previous_attempt=previous).thinking=='disabled'
