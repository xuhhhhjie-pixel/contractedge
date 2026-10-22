import pytest

from contractedge.source.snapshot import compute_snapshot
from tests.test_repair_execution import bound_plan


@pytest.mark.parametrize('path,before,after,same',[
    ('a.py','def run():\n    return 1\n','def run():\n    # revised\n    return 1\n\n',True),
    ('a.py','if ready:\n    run()\ncleanup()\n','if ready:\n    run()\n    cleanup()\n',False),
    ('a.py','def run():\n    return 1\n','def run():\n    return 2\n',False),
    ('a.go','package p\nfunc run() { work() }\n','package p\n\nfunc run() { work() }\n\n',True),
    ('a.go','package p\nfunc run() { a++ }\n','package p\nfunc run() { a+ +b }\n',False),
    ('a.go','package p\nvar s = `a\n\nb`\n','package p\nvar s = `a\nb`\n',False),
    ('a.yaml','name: default\n','name: fixed\n',False),
])
def test_conservative_fingerprint_respects_indent_literals_tokens_and_unknown_formats(path,before,after,same):
    from contractedge.execution.code_change import semantics_unchanged
    assert semantics_unchanged({path:before.encode()},{path:after}) is same


def fixture(tmp_path):
    (tmp_path/'templates').mkdir();(tmp_path/'templates/serviceaccount.yaml').write_text('name: default\n')
    (tmp_path/'a.py').write_text('def run():\n    return 1\n')
    plan=bound_plan(tmp_path);snap=compute_snapshot(tmp_path)
    plan['operations'][0]['target'].update(path='a.py',content_digest=snap.file_digests['a.py'],locator={'start_line':1,'end_line':2})
    return plan,dict(edits=[dict(change_id='C1',path='a.py',operation='MODIFY',old_text='return 1',new_text='# repair\n    return 1')])


def test_behavior_noop_rejection_is_atomic_and_does_not_reject_a_functional_change(tmp_path):
    from contractedge.execution.topomen import apply_edits,PlanRejected
    plan,response=fixture(tmp_path);before=compute_snapshot(tmp_path).snapshot_id
    with pytest.raises(PlanRejected,match='semantic change'):
        apply_edits(plan,tmp_path,{'allowed_paths':['a.py']},response,require_behavior_change=True)
    assert compute_snapshot(tmp_path).snapshot_id==before
    response['edits'][0]['new_text']='return 2'
    assert apply_edits(plan,tmp_path,{'allowed_paths':['a.py']},response,require_behavior_change=True)['status']=='APPLIED'


def test_native_environment_enforces_the_same_requirement(tmp_path):
    from contractedge.execution.topomen_agent import _FileEnvironment
    from contractedge.execution.topomen import PlanRejected
    plan,response=fixture(tmp_path)
    env=_FileEnvironment(plan,tmp_path,{'allowed_paths':['a.py']},RuntimeError,require_behavior_change=True)
    with pytest.raises(PlanRejected,match='semantic change'):
        env.execute(dict(kind='apply_source_edits',body=response))
    assert (tmp_path/'a.py').read_text()=='def run():\n    return 1\n'


def test_only_current_supported_behavior_failure_enables_the_guard(tmp_path):
    from contractedge.execution.code_change import behavior_change_required
    current='S';feedback=dict(to_snapshot=current,semantic_outcome='PUBLIC_REVIEW_FAIL',
        public_observations=[dict(id='OBS',snapshot_id=current,evidence_kind='PUBLIC_BEHAVIOR')],
        public_check_results=[dict(status='FAIL',evidence=[dict(observation_id='OBS')])])
    assert behavior_change_required(feedback,current)
    assert not behavior_change_required(feedback,'different')
    feedback['public_observations'][0]['evidence_kind']='EXECUTION_ONLY'
    assert not behavior_change_required(feedback,current)
