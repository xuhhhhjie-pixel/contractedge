from copy import deepcopy

import pytest

from contractedge.contracts.validation import canonical_digest
from contractedge.execution.topomen import apply_edits,PlanRejected
from contractedge.orchestration.repair import rebind_execution_plan
from contractedge.source.snapshot import compute_snapshot
from tests.test_repair_execution import bound_plan


def fixture(root):
    (root/'templates').mkdir(); (root/'templates/serviceaccount.yaml').write_text('name: default\n')
    (root/'other.go').write_text('package other\nfunc old() {}\n')
    plan=bound_plan(root);snap=compute_snapshot(root)
    second=deepcopy(plan['operations'][0]);second['change_id']='C2'
    second['target'].update(path='other.go',content_digest=snap.file_digests['other.go'])
    plan['operations'].append(second)
    edits={'edits':[dict(change_id='C1',path='templates/serviceaccount.yaml',operation='MODIFY',old_text='default',new_text='selected'),
                    dict(change_id='C2',path='other.go',operation='MODIFY',old_text='old()',new_text='trial()')]}
    return plan,edits,{'allowed_paths':['templates/serviceaccount.yaml','other.go']}


def test_initial_partial_batch_is_still_rejected_atomically(tmp_path):
    plan,edits,policy=fixture(tmp_path)
    with pytest.raises(PlanRejected,match='cover exactly'):
        apply_edits(plan,tmp_path,policy,{'edits':edits['edits'][1:]})
    assert 'default' in (tmp_path/'templates/serviceaccount.yaml').read_text()


def test_continuation_keeps_prior_operation_accounted_without_forcing_a_new_edit(tmp_path):
    plan,edits,policy=fixture(tmp_path);applied=apply_edits(plan,tmp_path,policy,edits)
    rebound=rebind_execution_plan(plan,tmp_path,{},prior_execution=applied)
    result=apply_edits(rebound,tmp_path,policy,{'edits':[dict(change_id='C2',path='other.go',operation='MODIFY',old_text='trial()',new_text='fixed()')]})
    assert result['change_ids']==['C2']
    assert result['unchanged_change_ids']==['C1']
    assert {r['change_id'] for r in result['operation_coverage']}=={'C1','C2'}
    assert next(r for r in result['operation_coverage'] if r['change_id']=='C1')['edit_count']==0
    assert all('NOT_PROVEN' in r['semantic_status'] for r in result['semantic_edit_trace'])
    assert 'selected' in (tmp_path/'templates/serviceaccount.yaml').read_text()


@pytest.mark.parametrize('corrupt',['snapshot','digest','applied_ids','status','file'])
def test_stale_or_unproven_previous_execution_never_allows_omission(tmp_path,corrupt):
    plan,edits,policy=fixture(tmp_path);proof=apply_edits(plan,tmp_path,policy,edits)
    if corrupt=='snapshot':proof['to_snapshot']='old'
    if corrupt=='digest':proof['acceptance']['plan_digest']='other'
    if corrupt=='applied_ids':proof['change_ids']=[]
    if corrupt=='status':proof['status']='REJECTED'
    if corrupt=='file':proof['file_digests']['templates/serviceaccount.yaml']='wrong'
    rebound=rebind_execution_plan(plan,tmp_path,{},prior_execution=proof)
    with pytest.raises(PlanRejected,match='cover exactly'):
        apply_edits(rebound,tmp_path,policy,{'edits':[dict(change_id='C2',path='other.go',operation='MODIFY',old_text='trial()',new_text='fixed()')]})
