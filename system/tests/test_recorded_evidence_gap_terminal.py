"""A known closed pre-edit evidence gap may be audited without rerunning it."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_isolated_coordinator import load


def recorded(tmp_path):
    state=tmp_path/'.isolated-recorded';state.mkdir()
    case=tmp_path/'CASE';case.mkdir()
    journal=state/'method-journal';journal.mkdir()
    program=tmp_path/'run_isolated_method.py'
    program.write_bytes((Path(__file__).parent/'fixtures/isolated_method_pre_gap_v2.py').read_bytes())
    ref={'path':str(program),'sha256':hashlib.sha256(program.read_bytes()).hexdigest()}
    patch=case/'candidate.patch';patch.write_bytes(b'')
    candidate={'patch_path':str(patch),'patch_sha256':hashlib.sha256(b'').hexdigest(),'patch_bytes':0}
    trace={'case_id':'CASE','status':'EVIDENCE_GAP','initial_snapshot_id':'S1',
           'final_snapshot_id':'S1','candidate':candidate,'errors':[],'rounds':[]}
    receipt={'case_id':'CASE','status':'INFRASTRUCTURE_STOP','state_root':str(state),
             'identity':{'output_path':str(case),'harness_files':{'run_isolated_method.py':ref['sha256']}},
             'method_returncode':0,'method_source_unchanged':True,
             'cleanup':{'status':'OWNED_CONTAINERS_CLOSED'},
             'terminal_validation':{'classification':'INFRASTRUCTURE_STOP','method_status':'EVIDENCE_GAP',
                 'unknown_requests':0,'not_dispatched_requests':0,'candidate_verified':True,
                 'coordinator_policy_failures':[],'recovery_failure':None,'semantic_outcome_upgraded':False}}
    for name,op,result in [('observe','public.observe',{'status':'EXECUTION_PASS'}),
                           ('export','public.export',candidate),('model','model.complete',{})]:
        (journal/(name+'.json')).write_text(json.dumps({'op':op,'status':'DONE','result':result}))
    (case/'repair.trace.json').write_text(json.dumps(trace));p=state/'receipt.json';p.write_text(json.dumps(receipt))
    return p,ref,receipt,trace


def test_known_terminal_is_interpreted_read_only_and_keeps_failure(tmp_path):
    p,ref,raw,trace=recorded(tmp_path)
    before={str(f):f.read_bytes() for f in tmp_path.rglob('*') if f.is_file()}
    assert hasattr(load(),'read_recorded_terminal'), 'No bounded terminal adjudication exists'
    result=load().read_recorded_terminal(p,recorded_program=ref)
    assert result['receipt']['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING'
    assert result['receipt']['terminal_validation']['classification']=='METHOD_RESULT'
    assert result['receipt']['terminal_validation']['method_status']=='EVIDENCE_GAP'
    assert result['raw_receipt']==raw and result['model_calls_added']==0
    assert result['interpretation_policy']=='ce.pre_edit_evidence_gap_terminal.v1'
    assert before=={str(f):f.read_bytes() for f in tmp_path.rglob('*') if f.is_file()}


@pytest.mark.parametrize('mutation', ['pending','not-dispatched','policy-failure','unknown-call',
    'public-infrastructure','source-integrity','source-mutated','cleanup-open','wrong-method-status',
    'nonempty-patch','different-snapshot','modified-program','wrong-program-binding','unverified-candidate'])
def test_uncertain_or_different_failures_cannot_be_reclassified(tmp_path,mutation):
    p,ref,raw,trace=recorded(tmp_path);state=p.parent;case=Path(raw['identity']['output_path'])
    if mutation in {'pending','not-dispatched'}:
        f=state/'method-journal/model.json';d=json.loads(f.read_text());d['status']='PENDING' if mutation=='pending' else 'NOT_DISPATCHED';f.write_text(json.dumps(d))
    elif mutation=='policy-failure':raw['terminal_validation']['coordinator_policy_failures']=['MODEL_MISMATCH']
    elif mutation=='unknown-call':raw['terminal_validation']['unknown_requests']=1
    elif mutation=='public-infrastructure':
        f=state/'method-journal/observe.json';d=json.loads(f.read_text());d['result']['status']='INFRA_ERROR';f.write_text(json.dumps(d))
    elif mutation=='source-integrity':trace['source_integrity_verified']=False
    elif mutation=='source-mutated':raw['method_source_unchanged']=False
    elif mutation=='cleanup-open':raw['cleanup']['status']='UNKNOWN'
    elif mutation=='wrong-method-status':trace['status']='INFRASTRUCTURE_ERROR'
    elif mutation=='nonempty-patch':(case/'candidate.patch').write_bytes(b'changed')
    elif mutation=='different-snapshot':trace['final_snapshot_id']='S2'
    elif mutation=='modified-program':Path(ref['path']).write_text('different implementation')
    elif mutation=='wrong-program-binding':raw['identity']['harness_files']['run_isolated_method.py']='b'*64
    elif mutation=='unverified-candidate':raw['terminal_validation']['candidate_verified']=False
    p.write_text(json.dumps(raw));(case/'repair.trace.json').write_text(json.dumps(trace))
    assert hasattr(load(),'read_recorded_terminal'), 'No bounded terminal adjudication exists'
    with pytest.raises(ValueError):load().read_recorded_terminal(p,recorded_program=ref)
