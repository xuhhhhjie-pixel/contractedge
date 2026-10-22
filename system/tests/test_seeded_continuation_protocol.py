import importlib.util
from pathlib import Path

import pytest

from contractedge.source.snapshot import compute_snapshot
from tests.test_candidate_regression_guard import run


def harness():
    spec=importlib.util.spec_from_file_location('reliability_worker',Path(__file__).parents[1]/'scripts/run_reliability_worker_v4.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_seed_is_bootstrap_and_two_actual_edits_use_fresh_feedback(tmp_path):
    source=tmp_path/'src';source.mkdir();file=source/'a.go';file.write_text('seed')
    calls=[]
    def attempt(index,feedback,observation,previous):
        calls.append((index,feedback['to_snapshot']))
        assert compute_snapshot(source).snapshot_id==feedback['to_snapshot']
        file.write_text('first' if index==0 else 'fixed')
        return dict(feedback=dict(route='EXECUTE' if index==0 else 'STOP',
            semantic_outcome='PUBLIC_REVIEW_FAIL' if index==0 else 'PUBLIC_REVIEW_PASS',
            to_snapshot=compute_snapshot(source).snapshot_id),observation={})
    initial=dict(route='EXECUTE',to_snapshot=compute_snapshot(source).snapshot_id)
    result=harness().continue_seeded_repair(source,['a.go'],initial,{},max_rounds=2,
        guard_enabled=False,output=tmp_path/'rounds',attempt=attempt)
    assert [x[0] for x in calls]==[0,1]
    assert result['feedback']['semantic_outcome']=='PUBLIC_REVIEW_PASS'
    assert len(result['rounds'])==2 and file.read_text()=='fixed'


def test_regression_rolls_back_before_second_real_edit_and_keeps_rejected_snapshot(tmp_path):
    source=tmp_path/'src';source.mkdir();file=source/'a.go';file.write_text('seed')
    initial=dict(route='EXECUTE',to_snapshot=compute_snapshot(source).snapshot_id)
    def attempt(index,feedback,observation,previous):
        if index==1:
            assert file.read_text()=='seed'
            assert previous['rejected_trial']['snapshot_id']!=feedback['to_snapshot']
            assert previous['rejected_trial']['feedback']['helper_preconditions']['checks'][0]['status']=='CONFLICT'
            assert previous['rejected_trial']['actual_edits'][0]['new_text']=='regressed'
        file.write_text('regressed' if index==0 else 'fixed')
        snap=compute_snapshot(source).snapshot_id
        return dict(feedback=dict(route='EXECUTE' if index==0 else 'STOP',to_snapshot=snap,
            semantic_outcome='PUBLIC_REVIEW_FAIL' if index==0 else 'PUBLIC_REVIEW_PASS',
            helper_preconditions=dict(checks=[dict(status='CONFLICT')])),
            observation=run({'retain':'FAIL' if index==0 else 'PASS'},snap),
            execution_mapping=[dict(path='a.go',new_text='regressed' if index==0 else 'fixed')])
    result=harness().continue_seeded_repair(source,['a.go'],initial,run({'retain':'PASS'},initial['to_snapshot']),
        max_rounds=2,guard_enabled=True,output=tmp_path/'rounds',attempt=attempt,preservation_ids=lambda f:['retain'])
    assert result['rounds'][0]['candidate_decision']['decision']=='REJECT_REGRESSION'
    assert file.read_text()=='fixed'
    assert result['feedback']['to_snapshot']==compute_snapshot(source).snapshot_id


def test_transport_failure_after_edit_restores_selected_source(tmp_path):
    source=tmp_path/'src';source.mkdir();file=source/'a.go';file.write_text('seed')
    def attempt(*args):
        file.write_text('unchecked');raise RuntimeError('review interrupted')
    result=harness().continue_seeded_repair(source,['a.go'],{'route':'EXECUTE'}, {},max_rounds=2,
        guard_enabled=True,output=tmp_path/'rounds',attempt=attempt)
    assert result['status']=='RECOVERY_FAILED' and result['source_integrity_verified']
    assert 'review interrupted' in result['errors'][0]
    assert file.read_text()=='seed'


def test_unknown_route_does_not_spend_another_call(tmp_path):
    result=harness().continue_seeded_repair(tmp_path,[],{'route':'STOP'}, {},max_rounds=2,
        guard_enabled=False,output=tmp_path/'rounds',attempt=lambda *args:pytest.fail('unexpected call'))
    assert result['rounds']==[]


def test_completed_invalid_edit_is_corrected_on_same_snapshot_without_spending_a_semantic_round(tmp_path):
    from types import SimpleNamespace
    from contractedge.execution.topomen import PlanRejected
    source=tmp_path/'src';source.mkdir();(source/'a.go').write_text('seed');budget=SimpleNamespace(calls=[])
    class Executor:
        calls=0
        def execute(self,plan,source,policy,**kw):
            self.calls+=1
            if self.calls==1:
                budget.calls.append(dict(prompt_version='topomen-source-execution.v1',response_content='{"edits":[]}',response_metadata={'finish_reason':'stop'}))
                raise PlanRejected('Missing accepted C2')
            assert kw['previous_attempt']['validation_error']=='Missing accepted C2'
            assert kw['previous_attempt']['source_snapshot']==compute_snapshot(source).snapshot_id
            (source/'a.go').write_text('fixed')
            return dict(status='APPLIED')
    result,used=harness().execute_seed_edit(Executor(),budget,{},source,{},config=dict(execution_format_retry_in_round=True,max_format_retries=1),
        kwargs={},output=tmp_path/'attempts',corrections_used=0)
    assert result['status']=='APPLIED' and result['generation_attempts']==2 and used==1


@pytest.mark.parametrize('kind',['transport','used','changed'])
def test_seed_correction_never_retries_unknown_transport_exhausted_allowance_or_changed_source(tmp_path,kind):
    from types import SimpleNamespace
    from contractedge.execution.topomen import PlanRejected
    source=tmp_path/'src';source.mkdir();(source/'a.go').write_text('seed');budget=SimpleNamespace(calls=[])
    class Executor:
        calls=0
        def execute(self,plan,source,policy,**kw):
            self.calls+=1;assert self.calls==1
            if kind!='transport':budget.calls.append(dict(prompt_version='topomen-source-execution.v1',response_content='{}'))
            if kind=='changed':(source/'a.go').write_text('unexpected')
            raise PlanRejected('bad')
    with pytest.raises(PlanRejected,match='bad'):
        harness().execute_seed_edit(Executor(),budget,{},source,{},config=dict(execution_format_retry_in_round=True,max_format_retries=1),
            kwargs={},output=tmp_path/'attempts',corrections_used=1 if kind=='used' else 0)


def test_missing_operation_error_identifies_omission_and_keeps_source_unchanged(tmp_path):
    from copy import deepcopy
    from tests.test_repair_execution import bound_plan
    from contractedge.execution.topomen import apply_edits,PlanRejected
    (tmp_path/'templates').mkdir();p=tmp_path/'templates/serviceaccount.yaml';p.write_text('name: default\n')
    plan=bound_plan(tmp_path);other=deepcopy(plan['operations'][0]);other['change_id']='C2';plan['operations'].append(other)
    edit=dict(change_id='C1',path='templates/serviceaccount.yaml',operation='MODIFY',old_text='default',new_text='fixed')
    with pytest.raises(PlanRejected,match="missing=\\['C2'\\]"):
        apply_edits(plan,tmp_path,{'allowed_paths':['templates/serviceaccount.yaml']},{'edits':[edit]})
    assert p.read_text()=='name: default\n'
