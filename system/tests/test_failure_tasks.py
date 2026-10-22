import json

from contractedge.orchestration.feedback import feedback_prompt_view


def test_shared_public_execution_error_and_helper_conflict_survive_compact_feedback():
    rows=[]
    for index,key in enumerate(['inventory','binding','transform']):
        rows.append(dict(id='O'+str(index),snapshot_id='S1',scenario='scenario',evidence_kind='PUBLIC_BEHAVIOR',
            behavior_check=dict(id=key,status='FAIL',probe_sha256='a'*64),
            excerpt=json.dumps(dict(observed=dict(render_error='resource already exists')))))
    feedback=dict(to_snapshot='S1',public_observations=rows,helper_preconditions=dict(checks=[dict(status='CONFLICT',call='Merge')]))
    view=feedback_prompt_view(feedback);tasks=view['failure_tasks']
    assert len(tasks['shared_execution_errors'])==1
    assert tasks['shared_execution_errors'][0]['affected_checks']==['inventory','binding','transform']
    assert tasks['helper_precondition_conflicts'][0]['call']=='Merge'
    assert 'public_observations' not in view
    assert len(feedback['public_observations'])==3


def test_stale_success_or_unregistered_raw_error_never_becomes_execution_task():
    row=dict(id='O',snapshot_id='S0',scenario='scenario',evidence_kind='EXECUTION_ONLY',
        behavior_check=dict(id='check',status='FAIL',probe_sha256='a'*64),excerpt='{"observed":{"render_error":"bad"}}')
    assert 'failure_tasks' not in feedback_prompt_view(dict(to_snapshot='S1',public_observations=[row]))


def test_rejected_trial_projection_preserves_failures_helpers_and_unseen_obligation_without_duplicating_ledger():
    from copy import deepcopy
    from contractedge.orchestration.feedback import historical_trial_prompt_view
    value=dict(to_snapshot='TRIAL',full_feedback_digest='a'*64,obligation_lineage={'definitions':'x'*60000},
        pending_obligations=[dict(id='P1',statement='Known requirement'),dict(id='P2',statement='Extra historical branch')],
        public_check_results=[dict(id='P1',status='FAIL',evidence=[dict(observation_id='O1',quote='x'*4000)])],
        helper_preconditions=dict(checks=[dict(status='CONFLICT',call='Merge')]),failure_tasks=dict(shared_execution_errors=['duplicate']))
    before=deepcopy(value);view=historical_trial_prompt_view(value,canonical_text='Known requirement')
    assert value==before and 'obligation_lineage' not in view
    assert view['public_check_results'][0]['status']=='FAIL'
    assert view['helper_preconditions']==value['helper_preconditions']
    assert view['pending_obligation_refs'][1]['statement']=='Extra historical branch'
    assert view['full_feedback_digest']=='a'*64 and view['to_snapshot']=='TRIAL'
    assert len(json.dumps(view))<4000


def predicate_record(status='FAIL', snapshot='TRIAL'):
    import hashlib
    body=dict(probe_id='public-flow-v1',id='downstream.visible',status=status,
        statement='Excluded items remain available to the later public operation',
        observed=dict(items=['workload'],exists=False,render_error='<nil>'))
    excerpt=json.dumps(body)
    return dict(id='OBS-visible',snapshot_id=snapshot,kind='PUBLIC_OBSERVATION',evidence_kind='PUBLIC_BEHAVIOR',
        scenario='method-public-flow-v1',behavior_check=dict(id='public-flow-v1:downstream.visible',status=status,probe_sha256='a'*64),
        excerpt=excerpt,content_digest=hashlib.sha256(excerpt.encode()).hexdigest(),
        locator=dict(path='method-probes/public-flow-v1/check.json',truncated=False,whole_file_read=True))


def test_behavior_failure_without_exception_survives_live_and_rejected_feedback():
    from contractedge.orchestration.feedback import historical_trial_prompt_view
    from copy import deepcopy
    row=predicate_record();value=dict(to_snapshot='TRIAL',public_observations=[row]);before=deepcopy(value)
    view=feedback_prompt_view(value)
    assert 'failure_tasks' in view
    task=view['failure_tasks']['failed_predicates'][0]
    assert task['check_id']=='public-flow-v1:downstream.visible'
    assert task['observed']['exists'] is False and task['observed']['items']==['workload']
    assert task['expected_statement']==json.loads(row['excerpt'])['statement']
    assert task['snapshot_id']=='TRIAL' and task['observation_id']=='OBS-visible'
    assert task['content_digest']==row['content_digest']
    historical=historical_trial_prompt_view(view)
    assert historical['failure_tasks']==view['failure_tasks']
    assert value==before


import pytest

@pytest.mark.parametrize('change',['stale','success','unregistered','digest','truncated','partial','malformed','id_mismatch','status_mismatch'])
def test_invalid_or_inapplicable_records_never_become_concrete_failure_tasks(change):
    row=predicate_record()
    if change=='stale':row['snapshot_id']='OLD'
    elif change=='success':row=predicate_record(status='PASS')
    elif change=='unregistered':row['evidence_kind']='EXECUTION_ONLY'
    elif change=='digest':row['content_digest']='b'*64
    elif change=='truncated':row['locator']['truncated']=True
    elif change=='partial':row['locator']['whole_file_read']=False
    else:
        import hashlib
        body=json.loads(row['excerpt'])
        if change=='malformed':body['observed']='unparsed text'
        elif change=='id_mismatch':body['id']='different.check'
        else:body['status']='PASS'
        row['excerpt']=json.dumps(body);row['content_digest']=hashlib.sha256(row['excerpt'].encode()).hexdigest()
    assert not feedback_prompt_view(dict(to_snapshot='TRIAL',public_observations=[row])).get('failure_tasks',{}).get('failed_predicates')


def test_predicate_projection_budget_is_explicit_and_keeps_objects_parseable():
    import hashlib
    rows=[]
    for i in range(12):
        row=predicate_record();row['id']='OBS-'+str(i);rows.append(row)
    view=feedback_prompt_view(dict(to_snapshot='TRIAL',public_observations=rows))['failure_tasks']
    assert len(view['failed_predicates'])==8
    assert view['omitted_failed_predicate_count']==4
    assert all(isinstance(row['observed'],dict) for row in view['failed_predicates'])
    row=predicate_record();body=json.loads(row['excerpt']);body['observed']['payload']='x'*9000
    row['excerpt']=json.dumps(body);row['content_digest']=hashlib.sha256(row['excerpt'].encode()).hexdigest()
    tasks=feedback_prompt_view(dict(to_snapshot='TRIAL',public_observations=[row]))['failure_tasks']
    assert tasks['omitted_failed_predicate_count']==1 and not tasks['failed_predicates']


def test_supplied_observation_identity_is_referenced_once_without_losing_facts():
    from contractedge.contracts.validation import canonical_digest
    from copy import deepcopy
    row=predicate_record();value=dict(to_snapshot='TRIAL',public_observations=[row],pending_obligations=[{'id':'P','statement':'Still open'}]);before=deepcopy(value)
    view=feedback_prompt_view(value,supplied_observations=[row])
    ref=view['public_observation_refs'][0]
    assert ref=={'id':'OBS-visible','public_observation_record_ref':'/public_observations/0'}
    assert view['failure_tasks']['failed_predicates'][0]['observed']['exists'] is False
    assert view['full_feedback_digest']==canonical_digest(value)
    assert view['pending_obligations']==value['pending_obligations'] and value==before
    assert feedback_prompt_view(value)['public_observation_refs'][0]['content_digest']==row['content_digest']


@pytest.mark.parametrize('change',['snapshot','digest','locator','duplicate','missing_excerpt'])
def test_ambiguous_or_different_observation_keeps_full_feedback_reference(change):
    from copy import deepcopy
    row=predicate_record();supplied=deepcopy(row)
    if change=='snapshot':supplied['snapshot_id']='CURRENT'
    elif change=='digest':supplied['content_digest']='b'*64
    elif change=='locator':supplied['locator']['path']='another.json'
    elif change=='missing_excerpt':supplied.pop('excerpt')
    rows=[supplied,supplied] if change=='duplicate' else [supplied]
    view=feedback_prompt_view(dict(to_snapshot='TRIAL',public_observations=[row]),supplied_observations=rows)
    ref=view['public_observation_refs'][0]
    assert 'public_observation_record_ref' not in ref
    assert ref['content_digest']==row['content_digest'] and ref['snapshot_id']=='TRIAL'


def test_actual_execution_request_uses_bound_observation_reference(tmp_path):
    from tests.test_execution_reasoning_policy import fixture,request
    import json
    plan,config=fixture(tmp_path);row=predicate_record(snapshot=plan['snapshot_id'])
    feedback=dict(to_snapshot=plan['snapshot_id'],public_observations=[row])
    req=request(tmp_path,plan,config,feedback=feedback,observations=[row])
    body=json.loads(req.messages[1]['content'])
    ref=body['execution_feedback']['public_observation_refs'][0]
    assert ref['public_observation_record_ref']=='/public_observations/0'
    assert body['public_observations'][0]==row
    assert body['execution_feedback']['failure_tasks']['failed_predicates'][0]['observed']['exists'] is False


@pytest.mark.parametrize('field',['obligation_lineage','public_execution_capabilities'])
def test_execution_feedback_reuses_only_exact_accepted_context(tmp_path,field):
    from tests.test_execution_reasoning_policy import fixture,request
    from contractedge.contracts.validation import canonical_digest
    from copy import deepcopy
    plan,config=fixture(tmp_path);shared={'public_definition':{'enabled':True},'source':'registered'}
    plan[field]=deepcopy(shared);feedback=dict(to_snapshot=plan['snapshot_id'],**{field:deepcopy(shared)})
    req=request(tmp_path,plan,config,feedback=feedback);body=json.loads(req.messages[1]['content'])
    assert body['execution_feedback'][field+'_record_ref']=='/accepted_plan/'+field
    assert field not in body['execution_feedback']
    assert body['accepted_plan'][field]==feedback[field]
    assert body['execution_feedback_digest']==canonical_digest(feedback)
    assert feedback[field]==shared


@pytest.mark.parametrize('field',['obligation_lineage','public_execution_capabilities'])
def test_distinct_context_and_boolean_numeric_difference_never_share_identity(tmp_path,field):
    from tests.test_execution_reasoning_policy import fixture,request
    plan,config=fixture(tmp_path);plan[field]={'enabled':True}
    feedback=dict(to_snapshot=plan['snapshot_id'],**{field:{'enabled':1}})
    body=json.loads(request(tmp_path,plan,config,feedback=feedback).messages[1]['content'])
    assert body['execution_feedback'][field]=={'enabled':1}
    assert field+'_record_ref' not in body['execution_feedback']
