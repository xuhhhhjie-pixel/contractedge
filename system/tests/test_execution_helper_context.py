import json
import tempfile
from pathlib import Path

from contractedge.execution.topomen import build_execution_request
from contractedge.source.snapshot import compute_snapshot
from tests.test_repair_execution import bound_plan


def test_local_import_helper_contract_is_fresh_read_only_and_budgeted():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); (root/'templates').mkdir()
        (root/'templates/serviceaccount.yaml').write_text('name: default\n')
        (root/'go.mod').write_text('module example.org/demo\n')
        (root/'main.go').write_text('package main\nimport r "example.org/demo/runtime"\nfunc main(){ r.Merge(items) }\n')
        (root/'runtime').mkdir()
        helper = root/'runtime/utils.go'
        helper.write_text('package runtime\n// Merge requires assigned IDs before merging.\nfunc Merge(items []string) []string { return items }\nfunc Unused(){}\n')
        (root/'runtime/utils_test.go').write_text('package runtime\nfunc Merge(){}\n')
        plan = bound_plan(root); snap = compute_snapshot(root)
        plan['operations'][0]['target'].update(path='main.go', content_digest=snap.file_digests['main.go'],
            locator={'start_line':3, 'end_line':3})
        config = {'model':'test', 'provider':'openai_compatible', 'max_context_tokens':50000,
                  'execution_max_output_tokens':1000, 'execution_helper_support_enabled':True,
                  'max_execution_support_bytes':24000, 'execution_thinking':'disabled'}
        request = build_execution_request(plan,root,{'allowed_paths':['main.go']},len,config,requirement='Merge inputs')
        body = json.loads(request.messages[1]['content'])
        support = body['read_only_source_support']
        assert len(support) == 1
        assert support[0]['locator']['path'] == 'runtime/utils.go'
        assert 'requires assigned IDs' in support[0]['excerpt']
        assert 'Unused' not in support[0]['excerpt']
        assert support[0]['snapshot_id'] == snap.snapshot_id
        assert {s['path'] for s in body['current_target_files']} == {'main.go'}
        assert request.thinking == 'disabled'
        limited = build_execution_request(plan,root,{'allowed_paths':['main.go']},len,
                    {**config,'max_execution_support_bytes':10},requirement='Merge inputs')
        assert json.loads(limited.messages[1]['content'])['read_only_source_support'] == []


def test_historical_boundary_conflict_stays_unobservable():
    from contractedge.orchestration.feedback import review_plan_with_retained
    base = {'statement':'preserve', 'requirement_refs':['O1'], 'verification_kind':'PUBLIC_BEHAVIOR'}
    plan = {'postconditions':[{**base, 'id':'P1', 'verification_scope':{'scenario':'new'}}],
            'preserve_constraints':[], 'open_obligations':[]}
    prior = {'retained_constraints':[{**base,'source_snapshot':'old', 'verification_scope':{'scenario':'old'}}]}
    assert review_plan_with_retained(plan,prior)['postconditions'][0]['verification_kind'] == 'NOT_PUBLICLY_OBSERVABLE'


def test_python_local_import_helper_precondition_is_readonly_context():
    from contractedge.execution import helper_context
    assert hasattr(helper_context,'local_helper_support'), 'Language-neutral local helper support is absent'
    import tempfile
    from pathlib import Path
    from contractedge.source.snapshot import compute_snapshot
    with tempfile.TemporaryDirectory() as td:
        p=Path(td);(p/'pkg').mkdir();(p/'pkg/main.py').write_text('from .helpers import merge\n\ndef run(items):\n    return merge(items)\n')
        (p/'pkg/helpers.py').write_text('def merge(items):\n    """Requires stable resource identities before merging."""\n    return items\n')
        snapshot=compute_snapshot(p);targets=[{'path':'pkg/main.py','content':(p/'pkg/main.py').read_text()}]
        operations=[{'target':{'path':'pkg/main.py','locator':{'start_line':3,'end_line':4}}}]
        records,audit=helper_context.local_helper_support(snapshot,targets,operations,max_bytes=24000)
        assert len(records)==1
        assert 'stable resource identities' in records[0]['excerpt']
        assert records[0]['locator']['path']=='pkg/helpers.py'
        assert 'read-only' in records[0]['origin']


def test_budget_keeps_helper_referenced_by_rejected_trial_conflict(tmp_path):
    root=tmp_path;(root/'templates').mkdir();(root/'templates/serviceaccount.yaml').write_text('name: default\n')
    (root/'go.mod').write_text('module example.org/demo\n');(root/'runtime').mkdir()
    (root/'main.go').write_text('package main\nimport r "example.org/demo/runtime"\nfunc main(){ r.Other(items); r.Merge(items) }\n')
    (root/'runtime/other.go').write_text('package runtime\n//'+'x'*6000+'\nfunc Other(items []string) []string { return items }\n')
    (root/'runtime/utils.go').write_text('package runtime\n// Merge requires stable IDs.\nfunc Merge(items []string) []string { return items }\n')
    plan=bound_plan(root);snap=compute_snapshot(root)
    plan['operations'][0]['target'].update(path='main.go',content_digest=snap.file_digests['main.go'],locator={'start_line':3,'end_line':3})
    cfg=dict(model='test',provider='openai_compatible',max_context_tokens=50000,execution_max_output_tokens=1000,
        execution_helper_support_enabled=True,max_execution_support_bytes=24000)
    initial=build_execution_request(plan,root,{'allowed_paths':['main.go']},len,cfg,requirement='Fix failed merge')
    support=json.loads(initial.messages[1]['content'])['read_only_source_support']
    merge=next(r['id'] for r in support if 'func Merge(' in r['excerpt'])
    previous=dict(source_snapshot=snap.snapshot_id,rejected_trial=dict(public_check_results=[dict(rationale='x'*15000)],
        feedback=dict(full_feedback_digest='a'*64,helper_preconditions=dict(checks=[dict(status='CONFLICT',helper_evidence_ref=merge)]))))
    request=build_execution_request(plan,root,{'allowed_paths':['main.go']},len,{**cfg,'max_context_tokens':10000},
        requirement='Fix failed merge',previous_attempt=previous)
    body=json.loads(request.messages[1]['content'])
    assert merge in {r['id'] for r in body['read_only_source_support']}
    assert all('func Other(' not in r['excerpt'] for r in body['read_only_source_support'])
    assert 'public_check_results' not in body['previous_execution_attempt']['rejected_trial']
    assert previous['rejected_trial']['public_check_results'][0]['rationale']=='x'*15000
