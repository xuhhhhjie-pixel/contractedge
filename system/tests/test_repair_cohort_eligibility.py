"""The actual cohort scorer must not upgrade failed methods with retained patches."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from contractedge.evaluation.repair_batch import evaluate_repair_batch


def complete_trace(candidate, *, status='PUBLIC_EXECUTION_COMPLETE', public='PUBLIC_REVIEW_PASS'):
    return dict(case_id='A', status=status, candidate=candidate, final_snapshot_id='selected',
        public_semantic_status=public,
        last_feedback=dict(to_snapshot='selected', build_state='PASSED', semantic_outcome=public),
        repair_execution_facts=[dict(trial_id='trial-0', begin_ref='begin', parent_snapshot='broken',
            execution=dict(status='APPLIED', from_snapshot='broken', to_snapshot='selected', event_ref='applied'),
            observation=dict(status='EXECUTION_PASS', snapshot_id='selected', complete=True, event_ref='observed'),
            selection=dict(snapshot_id='selected', event_ref='selected'))])


def batch_inputs(root, *, status='PUBLIC_EXECUTION_COMPLETE', public='PUBLIC_REVIEW_PASS', mutate=None):
    evaluation=root/'evaluation';evaluation.mkdir()
    (evaluation/'manifest.jsonl').write_text(json.dumps({'case_id':'A'})+'\n')
    batch=root/'batch';batch.mkdir()
    tools=root/'tools';tools.mkdir()
    patch=batch/'candidate.patch';patch.write_text('retained patch')
    candidate=dict(patch_path=str(patch), patch_bytes=patch.stat().st_size,
                   patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest())
    value=complete_trace(deepcopy(candidate),status=status,public=public)
    if mutate:
        mutate(value)
    trace=batch/'repair.trace.json';trace.write_text(json.dumps(value))
    method=dict(case_id='A',status=status,trace=trace.name,
        trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),candidate=candidate)
    (batch/'batch.json').write_text(json.dumps(dict(case_count=1,records=[method])))
    return evaluation,batch,tools,root/'scores'


def install_passing_scorer(monkeypatch):
    dispatched=[]
    def score(argv,*args):
        dispatched.append(argv)
        output=Path(argv[argv.index('--output')+1]);output.mkdir(parents=True)
        patch=Path(argv[argv.index('--patch')+1])
        (output/'score.json').write_text(json.dumps(dict(case_id='A',status='PASS',
            patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest())))
        return dict(returncode=0,timed_out=False)
    monkeypatch.setattr('contractedge.evaluation.repair_batch.run_tool',score)
    return dispatched


@pytest.mark.parametrize('status',[
    'RESPONSE_UNKNOWN','INFRASTRUCTURE_ERROR','BUDGET_EXHAUSTED',
    'INVALID_OUTPUT','PLAN_INVALID','INTERRUPTED','RECOVERED_INCUMBENT',
])
def test_retained_complete_patch_does_not_hide_later_method_failure(tmp_path,monkeypatch,status):
    inputs=batch_inputs(tmp_path,status=status)
    dispatched=install_passing_scorer(monkeypatch)
    report=evaluate_repair_batch(*inputs)
    assert dispatched==[]
    assert report['case_count']==1 and report['pending_count']==0
    assert report['pass_count']==0 and report['pass_rate_fixed_denominator']==0
    record=report['records'][0]
    assert record['execution_status']=='COMPLETE'
    assert record['method_status'] in {status,'INVALID_PLAN'}
    assert record['status']!='PASS' and record['failure_kind']
    assert record['independent_score_status']==record['status']


@pytest.mark.parametrize('public',['PUBLIC_REVIEW_PASS','PUBLIC_REVIEW_FAIL','UNKNOWN'])
@pytest.mark.parametrize('status',['PUBLIC_EXECUTION_COMPLETE','ROUND_LIMIT'])
def test_complete_selected_candidate_can_be_independently_scored_despite_public_verdict(tmp_path,monkeypatch,public,status):
    inputs=batch_inputs(tmp_path,status=status,public=public)
    dispatched=install_passing_scorer(monkeypatch)
    report=evaluate_repair_batch(*inputs)
    assert len(dispatched)==1
    assert report['pass_count']==1 and report['pass_rate_fixed_denominator']==1
    assert report['records'][0]['independent_score_status']=='PASS'


@pytest.mark.parametrize('fault',['legacy','no_trials','unobserved','unselected','wrong_snapshot','rolled_back'])
def test_legacy_or_uncompleted_candidate_is_not_scorable(tmp_path,monkeypatch,fault):
    def mutate(trace):
        if fault=='legacy':
            del trace['repair_execution_facts']
        elif fault=='no_trials':
            trace['repair_execution_facts']=[]
        elif fault=='wrong_snapshot':
            trace['final_snapshot_id']='unobserved-other'
        else:
            row=trace['repair_execution_facts'][0]
            if fault=='rolled_back':
                row['rollback']=dict(status='ROLLBACK_COMPLETED',snapshot_id='broken',event_ref='rollback')
            else:
                del row['observation' if fault=='unobserved' else 'selection']
    inputs=batch_inputs(tmp_path,mutate=mutate)
    dispatched=install_passing_scorer(monkeypatch)
    report=evaluate_repair_batch(*inputs)
    assert dispatched==[]
    assert report['pass_count']==0 and report['case_count']==1
    assert report['records'][0]['status']=='NOT_SCORED_EXECUTION'


@pytest.mark.parametrize('fault',['case','status','candidate_hash','candidate_path','candidate_bytes','missing_candidate'])
@pytest.mark.parametrize('status',['PUBLIC_EXECUTION_COMPLETE','RESPONSE_UNKNOWN'])
def test_frozen_trace_and_candidate_identity_checked_even_when_method_is_excluded(tmp_path,monkeypatch,fault,status):
    def mutate(trace):
        if fault=='case':trace['case_id']='OTHER'
        elif fault=='status':trace['status']='EVIDENCE_GAP'
        elif fault=='missing_candidate':del trace['candidate']
        else:
            key={'candidate_hash':'patch_sha256','candidate_path':'patch_path','candidate_bytes':'patch_bytes'}[fault]
            trace['candidate'][key]='different'
    inputs=batch_inputs(tmp_path,status=status,mutate=mutate)
    dispatched=install_passing_scorer(monkeypatch)
    with pytest.raises(ValueError,match='identity'):
        evaluate_repair_batch(*inputs)
    assert dispatched==[]


@pytest.mark.parametrize('fault',['trace_hash','patch_hash','trace_outside_batch','patch_size'])
def test_artifact_integrity_precedes_scorer_dispatch(tmp_path,monkeypatch,fault):
    inputs=batch_inputs(tmp_path)
    evaluation,batch,tools,output=inputs
    state=json.loads((batch/'batch.json').read_text());record=state['records'][0]
    if fault=='trace_hash':
        (batch/record['trace']).write_text('{}')
    elif fault=='patch_hash':
        Path(record['candidate']['patch_path']).write_text('altered')
    elif fault=='trace_outside_batch':
        trace=batch/record['trace'];outside=tmp_path/'outside.json';outside.write_bytes(trace.read_bytes())
        record['trace']=str(outside)
    else:
        record['candidate']['patch_bytes']+=1
        trace=batch/record['trace'];value=json.loads(trace.read_text())
        value['candidate']=record['candidate'];trace.write_text(json.dumps(value))
        record['trace_sha256']=hashlib.sha256(trace.read_bytes()).hexdigest()
    (batch/'batch.json').write_text(json.dumps(state))
    dispatched=install_passing_scorer(monkeypatch)
    with pytest.raises(ValueError):
        evaluate_repair_batch(*inputs)
    assert dispatched==[]


@pytest.mark.parametrize('status',['BUDGET_EXHAUSTED','INFRASTRUCTURE_ERROR','NOT_RUN'])
@pytest.mark.parametrize('empty_patch',[False,True])
def test_no_candidate_still_projects_its_verified_trace(tmp_path,monkeypatch,status,empty_patch):
    inputs=batch_inputs(tmp_path,status=status)
    _,batch,_,_=inputs
    state=json.loads((batch/'batch.json').read_text());method=state['records'][0]
    if empty_patch:
        patch=Path(method['candidate']['patch_path']);patch.write_bytes(b'')
        method['candidate'].update(patch_bytes=0,patch_sha256=hashlib.sha256(b'').hexdigest())
    else:
        method.pop('candidate')
    trace=batch/method['trace'];value=json.loads(trace.read_text())
    value.update(candidate=method.get('candidate'),repair_execution_facts=[])
    trace.write_text(json.dumps(value));method['trace_sha256']=hashlib.sha256(trace.read_bytes()).hexdigest()
    (batch/'batch.json').write_text(json.dumps(state))
    dispatched=install_passing_scorer(monkeypatch)
    report=evaluate_repair_batch(*inputs)
    record=report['records'][0]
    assert record['execution_status']=='NOT_RUN'
    assert record['public_status']=='UNKNOWN'
    assert record['status']==('NOT_RUN' if status=='NOT_RUN' else 'NO_CANDIDATE')
    assert not dispatched and report['pass_count']==0


def test_missing_patch_does_not_bypass_an_existing_trace_hash(tmp_path,monkeypatch):
    inputs=batch_inputs(tmp_path)
    _,batch,_,_=inputs
    state=json.loads((batch/'batch.json').read_text())
    state['records'][0].pop('candidate')
    (batch/'batch.json').write_text(json.dumps(state))
    (batch/'repair.trace.json').write_text('{}')
    dispatched=install_passing_scorer(monkeypatch)
    with pytest.raises(ValueError,match='changed'):
        evaluate_repair_batch(*inputs)
    assert not dispatched
