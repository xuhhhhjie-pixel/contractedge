from copy import deepcopy
import hashlib
import json
import pytest

from tests.test_check_coverage import obs
from tests.test_necessary_coverage_review import obligation


def values():
    plan=dict(postconditions=[obligation()],precondition_review=[{'disposition':'REQUIRED'}])
    records=[obs('a','FAIL'),obs('b','PASS')]
    for row in records:
        key=row['id'];status=row['behavior_check']['status']
        row['excerpt']=json.dumps(dict(probe_id='generic',id=key,status=status,
            statement='Required state stays visible',observed={'visible':status=='PASS'}))
        row['content_digest']=hashlib.sha256(row['excerpt'].encode()).hexdigest()
        row['locator'].update(truncated=False,whole_file_read=True)
        row['behavior_check']['id']='generic:'+key
    plan['postconditions'][0]['behavior_check_refs']=['generic:a','generic:b']
    return plan,dict(to_snapshot='S1',semantic_outcome='PUBLIC_REVIEW_FAIL',public_observations=records)


def test_necessary_failure_requests_review_even_without_a_runtime_exception():
    from contractedge.orchestration.repair import behavior_plan_reassessment_required
    plan,feedback=values();before=deepcopy(feedback)
    assert behavior_plan_reassessment_required(plan,'S1',feedback)
    assert feedback==before


@pytest.mark.parametrize('change',['old_feedback','old_observation','wrong_scenario','supporting','no_prerequisite',
    'passed','execution_only','unknown_semantics','wrong_binding','no_observations'])
def test_unbound_or_supporting_failures_do_not_request_replanning(change):
    from contractedge.orchestration.repair import behavior_plan_reassessment_required
    plan,f=values()
    if change=='old_feedback':f['to_snapshot']='OLD'
    elif change=='old_observation':f['public_observations'][0]['snapshot_id']='OLD'
    elif change=='wrong_scenario':f['public_observations'][0]['scenario']='control'
    elif change=='supporting':plan['postconditions'][0]['check_coverage']='SUPPORTING'
    elif change=='no_prerequisite':plan['precondition_review']=[]
    elif change=='passed':f['public_observations']=[obs('a','PASS'),obs('b','PASS')]
    elif change=='execution_only':f['public_observations'][0]['evidence_kind']='EXECUTION_ONLY'
    elif change=='unknown_semantics':f['semantic_outcome']='UNKNOWN'
    elif change=='wrong_binding':plan['postconditions'][0]['behavior_check_refs']=['other']
    else:f['public_observations']=[]
    assert not behavior_plan_reassessment_required(plan,'S1',f)


def test_seed_behavior_review_uses_fresh_source_and_original_shared_budget(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from contractedge.orchestration import runner
    from contractedge.changeset import topomen
    from contractedge.source.snapshot import compute_snapshot
    from tests.test_isolated_worker_entry import worker
    source=tmp_path/'source';source.mkdir();(source/'a.go').write_text('candidate code')
    plan,feedback=values();sid=compute_snapshot(source).snapshot_id
    feedback['to_snapshot']=sid
    for row in feedback['public_observations']:row['snapshot_id']=sid
    front=dict(run_id='R',changeset=plan,snapshot_id='OLD')
    budget=object();seen=[]
    def fresh(method,workspace,provider,cfg,counter,**kw):
        seen.append(kw);assert provider is budget and workspace==source
        assert cfg['run_id']=='R' and kw['execution_feedback']['to_snapshot']==sid
        assert kw['execution_feedback']['route']=='DIAGNOSIS'
        assert kw['public_observations']==tuple(feedback['public_observations'])
        return SimpleNamespace(to_wire=lambda:dict(status='READY',changeset={},diagnosis={},run_context={'retrieval_config':{}}))
    monkeypatch.setattr(runner,'run_front_half',fresh)
    monkeypatch.setattr(topomen,'translate_for_topomen',lambda *a,**kw:{'operations':[{'target':{'kind':'EXISTING','path':'a.go'}}]})
    method=SimpleNamespace(source_root='.',requirement_text='Keep state')
    config=dict(model='fixture',provider='fixture',schema_in_prompt=False,max_public_evidence_tokens=10000,public_execution_capabilities={})
    revised,translated=worker().reassess_seed_plan(front,method,source,feedback,None,budget,len,config,['a.go'])
    assert len(seen)==1 and translated and revised['status']=='READY'
