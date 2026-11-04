from types import SimpleNamespace
import copy

import pytest

from contractedge.execution.topomen import PlanRejected
from contractedge.source.snapshot import compute_snapshot
from tests.test_isolated_worker_entry import worker


def fixture(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'a.go').write_text('incumbent\n')
    before=compute_snapshot(source).snapshot_id
    call={'prompt_version':'topomen-source-execution.v1','response_content':'{"edits":[]}',
          'response_metadata':{}}
    return source,before,SimpleNamespace(calls=[call])


def test_completed_noop_rejection_is_failed_method_result_after_verified_rollback(tmp_path):
    module=worker();source,before,budget=fixture(tmp_path)
    marker=module.completed_edit_rejection(PlanRejected('Concrete edits are a no-op'),budget,0,source,before)
    assert marker['response_sha256'] and marker['snapshot_id']==before
    continuation={'status':'RECOVERY_FAILED','source_integrity_verified':True,
                  'errors':['PlanRejected: Concrete edits are a no-op']}
    result=module.classify_recovery_continuation(continuation,marker,source)
    assert result['status']=='CONCRETE_EDIT_REJECTED'
    assert result['errors']==continuation['errors']
    assert result['method_rejection']==marker and continuation['status']=='RECOVERY_FAILED'


@pytest.mark.parametrize('problem',['unknown_response','no_new_call','foreign_prompt','transport_error',
                                   'source_changed','response_error','rollback_failed','wrong_restore'])
def test_uncertainty_environment_and_source_failures_keep_infrastructure_stop(tmp_path,problem):
    module=worker();source,before,budget=fixture(tmp_path);error=PlanRejected('rejected');position=0
    if problem=='unknown_response':budget.calls[0].pop('response_content')
    elif problem=='no_new_call':position=1
    elif problem=='foreign_prompt':budget.calls[0]['prompt_version']='diagnosis.v1'
    elif problem=='transport_error':error=TimeoutError('response unknown')
    elif problem=='source_changed':(source/'a.go').write_text('partial write\n')
    elif problem=='response_error':budget.calls[0]['error']='unknown'
    marker=module.completed_edit_rejection(error,budget,position,source,before)
    value={'status':'RECOVERY_FAILED','source_integrity_verified':problem!='rollback_failed','errors':['original']}
    if problem=='wrong_restore':(source/'a.go').write_text('wrong restored state\n')
    result=module.classify_recovery_continuation(value,marker,source)
    assert result['status']=='RECOVERY_FAILED'

