"""Unobserved readiness questions prerequisite intent; it does not prove its cause."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from contractedge.source.snapshot import compute_snapshot
from tests.test_execution_runtime_reasoning import inputs


def test_verified_readiness_after_rejection_requests_plan_review(tmp_path):
    from contractedge.orchestration.repair import plan_reassessment_required
    source,plan,config,previous=inputs(tmp_path)
    plan['precondition_review']=[dict(disposition='REQUIRED',source_evidence_ref='current-source')]
    before=deepcopy(previous)
    assert plan_reassessment_required(plan,compute_snapshot(source).snapshot_id,previous)
    assert previous==before
    assert previous['rejected_trial']['feedback']['semantic_outcome']=='UNKNOWN'


@pytest.mark.parametrize('mutation',['old_current','wrong_from','wrong_trial','old_fact','wrong_program',
    'no_facts','not_required','no_plan_prerequisites','no_rejection','conflicting_decisions','unknown_error','startup_panic'])
def test_unrelated_or_unverified_failure_keeps_execution_route(tmp_path,mutation):
    from contractedge.orchestration.repair import plan_reassessment_required
    source,plan,config,previous=inputs(tmp_path,readiness=mutation!='startup_panic')
    plan['precondition_review']=[dict(disposition='REQUIRED',source_evidence_ref='current-source')]
    trial=previous['rejected_trial'];fact=trial['feedback']['observation_diagnostics'][0]
    if mutation=='old_current':previous['source_snapshot']='OLD'
    elif mutation=='wrong_from':trial['decision']['from_snapshot']='OTHER'
    elif mutation=='wrong_trial':trial['decision']['trial_snapshot']='OTHER'
    elif mutation=='old_fact':fact['snapshot_id']='OTHER'
    elif mutation=='wrong_program':fact['probe_sha256']='f'*64
    elif mutation=='no_facts':trial['feedback'].pop('observation_diagnostics')
    elif mutation=='not_required':plan['precondition_review'][0]['disposition']='NOT_APPLICABLE'
    elif mutation=='no_plan_prerequisites':plan.pop('precondition_review')
    elif mutation=='no_rejection':trial['decision']['decision']='PROMOTE'
    elif mutation=='conflicting_decisions':trial['candidate_decision']={'decision':'PROMOTE'}
    elif mutation=='unknown_error':fact['operation']='GENERIC_TIMEOUT'
    assert not plan_reassessment_required(plan,compute_snapshot(source).snapshot_id,previous)


def test_plan_revision_receives_historical_failure_and_current_sources(tmp_path):
    from tests.test_stage_delivery_admission import ready_front,config
    from contractedge.orchestration.repair import revise_plan
    from tests.test_execution_runtime_reasoning import runtime_attempt
    method,source,trace=ready_front(tmp_path)
    observed=tmp_path/'observed';observed.mkdir()
    previous=runtime_attempt(observed,dict(snapshot_id=trace['snapshot_id']))
    before=deepcopy(trace)
    class Captured(BaseException):pass
    class Provider:
        def complete_json(self,request):
            payload=json.loads(request.messages[1]['content'])
            feedback=payload['validation_feedback']
            assert feedback[0]['code']=='PUBLIC_EXECUTION_PLAN_FEEDBACK'
            history=json.loads(feedback[1]['message'])
            assert history['rejected_trial']['snapshot_id']!=trace['snapshot_id']
            assert history['current_snapshot']==trace['snapshot_id']
            assert history['rejected_trial']['feedback']['semantic_outcome']=='UNKNOWN'
            assert 'not proof' in history['instruction']
            assert request.messages[2]['role']=='assistant'
            assert 'read_only_source_support' in payload
            raise Captured()
    with pytest.raises(Captured):
        revise_plan(trace,method,source,{'to_snapshot':trace['snapshot_id']},Provider(),len,config(),
            rejected_trial=previous['rejected_trial'])
    assert trace==before


def test_stale_trial_rejected_before_planning_dispatch(tmp_path):
    from tests.test_stage_delivery_admission import ready_front,config
    from contractedge.orchestration.repair import revise_plan
    method,source,trace=ready_front(tmp_path)
    class Provider:
        def complete_json(self,request):pytest.fail('No uncertain dispatch allowed')
    with pytest.raises(ValueError,match='[Tt]rial|[Ss]napshot'):
        revise_plan(trace,method,source,{'to_snapshot':trace['snapshot_id']},Provider(),len,config(),
            rejected_trial={'snapshot_id':'OLD','decision':{'from_snapshot':'OTHER','trial_snapshot':'OLD'},'feedback':{'to_snapshot':'OLD'}})


def test_seed_reassessment_reuses_revision_and_translates_new_intent(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from contractedge.orchestration import repair
    from contractedge.changeset import topomen
    from tests.test_isolated_worker_entry import worker
    source,plan,config,previous=inputs(tmp_path)
    plan['precondition_review']=[dict(disposition='REQUIRED')]
    front=dict(changeset=plan,diagnosis={},run_context={'retrieval_config':{}},status='READY')
    new_semantic={**plan,'goal':'Use producer-backed completion'}
    revised={**front,'changeset':new_semantic}
    seen=[]
    def revision(*args,**kw):
        seen.append(kw['rejected_trial']);return revised
    monkeypatch.setattr(repair,'revise_plan',revision)
    monkeypatch.setattr(topomen,'translate_for_topomen',lambda semantic,*a,**kw:
        {'operations':[{'target':{'kind':'EXISTING','path':'templates/serviceaccount.yaml'}}],'goal':semantic['goal']})
    config['public_execution_capabilities']={}
    result,translated=worker().reassess_seed_plan(front,SimpleNamespace(),source,{},previous,None,len,config,
        ['templates/serviceaccount.yaml'])
    assert result is revised and translated['goal']=='Use producer-backed completion'
    assert seen==[previous['rejected_trial']]


@pytest.mark.parametrize('failure',['unresolved','outside_checkpoint'])
def test_seed_reassessment_never_falls_back_to_old_plan(tmp_path,monkeypatch,failure):
    from types import SimpleNamespace
    from contractedge.orchestration import repair
    from contractedge.changeset import topomen
    from contractedge.execution.topomen import PlanRejected
    from tests.test_isolated_worker_entry import worker
    source,plan,config,previous=inputs(tmp_path);plan['precondition_review']=[dict(disposition='REQUIRED')]
    front=dict(changeset=plan,diagnosis={},run_context={'retrieval_config':{}},status='READY')
    monkeypatch.setattr(repair,'revise_plan',lambda *a,**kw:{**front,'status':'NEEDS_EVIDENCE' if failure=='unresolved' else 'READY'})
    monkeypatch.setattr(topomen,'translate_for_topomen',lambda *a,**kw:
        {'operations':[{'target':{'kind':'EXISTING','path':'uncheckpointed.go'}}]})
    config['public_execution_capabilities']={}
    with pytest.raises(PlanRejected,match='unresolved|checkpoint'):
        worker().reassess_seed_plan(front,SimpleNamespace(),source,{},previous,None,len,config,
            ['templates/serviceaccount.yaml'])
from contractedge.orchestration import repair
from tests.test_isolated_worker_entry import worker


def test_legacy_method_without_reassessment_keeps_its_own_existing_route(tmp_path,monkeypatch):
    entry=worker();monkeypatch.delattr(repair,'plan_reassessment_required')
    front={'changeset':{}}
    assert entry.reassess_seed_plan(front,None,tmp_path,{},None,None,len,{},[])==(front,None)
