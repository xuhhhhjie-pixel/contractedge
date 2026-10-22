from copy import deepcopy
import json

import pytest

from tests.test_operation_consistency import failed


def fixture():
    old=failed();new=deepcopy(old);old['snapshot_id']='S0';new['snapshot_id']='S1'
    before=dict(snapshot_id='S0',input_digest='input',environment_digest='env',observations=[old])
    after=dict(snapshot_id='S1',input_digest='input',environment_digest='env',observations=[new])
    feedback=dict(from_snapshot='S0',to_snapshot='S1',semantic_outcome='PUBLIC_REVIEW_FAIL',route='EXECUTE',failure_kind='EXECUTION',
        public_check_results=[dict(id='P1',status='FAIL',evidence=[dict(observation_id=new['id'])])])
    return before,after,feedback


def test_identical_registered_error_reassesses_cause_instead_of_repeating_narrow_plan():
    from contractedge.orchestration.feedback import reassess_unchanged_operations
    before,after,feedback=fixture();result=reassess_unchanged_operations(feedback,before,after)
    assert result['route']=='DIAGNOSIS' and result['route_confidence']=='LOW'
    assert result['semantic_outcome']=='PUBLIC_REVIEW_FAIL'
    assert result['operation_failure_comparison']['rows'][0]['exception']['message']=='Owner required'
    assert feedback['route']=='EXECUTE'


def test_operation_reassessment_does_not_append_superseded_execution_command():
    from contractedge.orchestration.feedback import reassess_unchanged_operations
    before, after, feedback = fixture()
    feedback['rationale'] = 'Execute the same intent; do not rediagnose solely from a failed postcondition.'
    result = reassess_unchanged_operations(feedback, before, after)
    assert 'do not rediagnose' not in result['rationale']
    assert result['operation_failure_comparison']['previous_rationale_sha256']


@pytest.mark.parametrize('change',['exception','resource','input','environment','expected','stale'])
def test_changed_unrelated_expected_or_uncomparable_failure_never_forces_rediagnosis(change):
    from contractedge.orchestration.feedback import reassess_unchanged_operations
    before,after,feedback=fixture()
    if change=='exception':
        value=json.loads(after['observations'][0]['excerpt']);value['exception']['message']='different error';after['observations'][0]['excerpt']=json.dumps(value)
    if change=='resource':after['observations'][0]['resource_identity']={'name':'other'}
    if change=='input':after['input_digest']='different'
    if change=='environment':before['environment_digest']=None
    if change=='expected':feedback['public_check_results'][0]['status']='PASS'
    if change=='stale':feedback['to_snapshot']='elsewhere'
    assert reassess_unchanged_operations(feedback,before,after)['route']=='EXECUTE'
