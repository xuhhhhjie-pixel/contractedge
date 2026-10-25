"""Source compiler diagnostics permit bounded recovery, never behavioral PASS."""
import json
from pathlib import Path
import pytest
from contractedge.execution import public
from contractedge.source.snapshot import compute_snapshot


def fixture(tmp_path,diagnostic=None):
    root=tmp_path/'source';root.mkdir();(root/'main.go').write_text('package main\nvar n int = "bad"\n')
    snap=compute_snapshot(root);raw=tmp_path/'raw';raw.mkdir()
    record={'status':'EXECUTION_FAILED','private_materials_loaded':False,'scenarios':[
        {'scenario':'package-build','exit_code':1,'timed_out':False,'output_limited':False}]}
    (raw/'public_execution.json').write_text(json.dumps(record))
    (raw/'package-build.stderr').write_text(diagnostic or 'main.go:2:13: cannot use "bad" (untyped string constant) as int value in variable declaration\n')
    return raw,snap,record


def classify(raw,snapshot):
    fn=getattr(public,'classify_candidate_build_failures',None)
    assert callable(fn),'Current-source compiler diagnostic classification is missing'
    return fn(raw,snapshot)


def test_current_source_go_type_error_is_bound_to_raw_log_and_snapshot(tmp_path):
    raw,snapshot,_=fixture(tmp_path)
    facts=classify(raw,snapshot)
    assert len(facts)==1
    f=facts[0]
    assert f['operation']=='PRODUCTION_COMPILE' and f['compiler']=='go'
    assert f['snapshot_id']==snapshot.snapshot_id
    assert f['source_locations'][0]['path']=='main.go'
    assert f['source_locations'][0]['file_sha256']==snapshot.file_digests['main.go']
    report={'status':'EXECUTION_FAILED','snapshot_id':snapshot.snapshot_id,'candidate_build_failures':facts}
    assert public.has_candidate_execution_failure(report)
    assert public.public_failure_diagnostics(report)[0]['kind']=='CANDIDATE_BUILD_FAILURE'
    report['snapshot_id']='other'
    assert not public.has_candidate_execution_failure(report)


@pytest.mark.parametrize('diagnostic', [
    'main.go:2:13: tree.lookup undefined (type *Tree has no field or method lookup)\n',
    'main.go:2:13: value.Name undefined (type Item has no field or method Name)\n',
])
def test_missing_selector_becomes_source_bound_repair_task(tmp_path, diagnostic):
    from contractedge.orchestration.feedback import make_feedback, feedback_prompt_view
    raw, snapshot, _ = fixture(tmp_path, diagnostic)
    facts = classify(raw, snapshot)
    assert len(facts) == 1
    report = dict(status='EXECUTION_FAILED', snapshot_id=snapshot.snapshot_id,
                  candidate_build_failures=facts)
    feedback = make_feedback(case_id='CASE', run_id='run', input_digest='a'*64, round_id=0,
        from_snapshot='before', to_snapshot=snapshot.snapshot_id, patch_digest='b'*64,
        execution_status='EXECUTION_FAILED', observations=[], plan={},
        observation_diagnostics=public.public_failure_diagnostics(report))
    task = feedback_prompt_view(feedback)['failure_tasks']['shared_execution_errors'][0]
    assert task['actual_error'] == diagnostic.strip()
    assert task['error_field'] == 'production_compile'
    assert task['source_locations'][0]['file_sha256'] == snapshot.file_digests['main.go']
    assert feedback['semantic_outcome'] == 'UNKNOWN'


@pytest.mark.parametrize('diagnostic', [
    'main.go:2:13: tree.lookup undefined\n',
    'main.go:2:13: tree.lookup undefined (dependency download failed)\n',
    '../external.go:2:13: tree.lookup undefined (type *Tree has no field or method lookup)\n',
])
def test_selector_like_unrecognized_or_foreign_errors_remain_unclassified(tmp_path, diagnostic):
    raw, snapshot, _ = fixture(tmp_path, diagnostic)
    assert classify(raw, snapshot) == []


def test_source_build_failure_cannot_replace_runnable_incumbent_without_preserve_ids(tmp_path):
    from contractedge.orchestration.candidate_guard import compare_candidate_checks
    raw, snapshot, _ = fixture(tmp_path)
    incumbent = dict(status='EXECUTION_PASS', snapshot_id='before', observations=[],
                     input_digest='input', environment_digest='environment')
    trial = dict(incumbent, status='EXECUTION_FAILED', snapshot_id=snapshot.snapshot_id,
                 candidate_build_failures=classify(raw, snapshot))
    decision = compare_candidate_checks(incumbent, trial, [])
    assert decision['decision'] == 'UNCOMPARABLE'
    assert decision['uncomparable'][0]['check_id'] == 'production_execution'
    assert not decision['regressions']  # A compiler failure is not a semantic FAIL.


@pytest.mark.parametrize('kind',['timeout','limited','success','foreign_path','out_of_range','environment','symlink','changed_source'])
def test_unbound_environment_incomplete_or_stale_output_cannot_authorize_repair(tmp_path,kind):
    raw,snapshot,record=fixture(tmp_path)
    if kind=='timeout':record['scenarios'][0]['timed_out']=True
    elif kind=='limited':record['scenarios'][0]['output_limited']=True
    elif kind=='success':record['scenarios'][0]['exit_code']=0;record['status']='EXECUTION_PASS'
    elif kind=='foreign_path':(raw/'package-build.stderr').write_text('../dependency/main.go:2:13: undefined: Missing\n')
    elif kind=='out_of_range':(raw/'package-build.stderr').write_text('main.go:999:1: undefined: Missing\n')
    elif kind=='environment':(raw/'package-build.stderr').write_text('go: module lookup disabled by GOPROXY=off\n')
    elif kind=='symlink':(raw/'package-build.stderr').unlink();(raw/'package-build.stderr').symlink_to(Path(snapshot.source_root)/'main.go')
    elif kind=='changed_source':(Path(snapshot.source_root)/'main.go').write_text('package changed\n')
    (raw/'public_execution.json').write_text(json.dumps(record))
    assert classify(raw,snapshot)==[]


def test_compiler_diagnostics_are_execution_tasks_and_keep_behavior_unknown(tmp_path):
    from contractedge.orchestration.feedback import make_feedback,feedback_prompt_view
    raw,snapshot,_=fixture(tmp_path);facts=classify(raw,snapshot)
    report={'status':'EXECUTION_FAILED','snapshot_id':snapshot.snapshot_id,'candidate_build_failures':facts}
    feedback=make_feedback(case_id='CASE',run_id='run',input_digest='a'*64,round_id=0,
        from_snapshot='before',to_snapshot=snapshot.snapshot_id,patch_digest='b'*64,
        execution_status='EXECUTION_FAILED',observations=[],
        plan={'postconditions':[{'id':'P','statement':'Production behavior works','verification_kind':'PUBLIC_BEHAVIOR'}]},
        observation_diagnostics=public.public_failure_diagnostics(report))
    assert feedback['route']=='EXECUTE' and feedback['next_action']=='EXECUTE'
    assert feedback['semantic_outcome']=='UNKNOWN'
    assert feedback['pending_obligations'][0]['reason_category']=='EXECUTION_BLOCKED'
    task=feedback_prompt_view(feedback)['failure_tasks']['shared_execution_errors'][0]
    assert 'cannot use' in task['actual_error'] and task['snapshot_id']==snapshot.snapshot_id
    assert task['error_field']=='production_compile'


@pytest.mark.parametrize('value',[[],{'status':'EXECUTION_FAILED','private_materials_loaded':False,'scenarios':[None]},
    {'status':'EXECUTION_FAILED','private_materials_loaded':False,'scenarios':None}])
def test_malformed_public_producer_record_is_not_a_source_failure(tmp_path,value):
    raw,snapshot,_=fixture(tmp_path)
    (raw/'public_execution.json').write_text(json.dumps(value))
    assert classify(raw,snapshot)==[]


def test_diagnostics_drop_invalid_facts_even_beside_one_valid_failure(tmp_path):
    from copy import deepcopy
    raw,snapshot,_=fixture(tmp_path);facts=classify(raw,snapshot)
    bad=deepcopy(facts[0]);bad['stderr_excerpt']='fabricated';bad['source_locations'][0]['path']='other.go'
    report={'status':'EXECUTION_FAILED','snapshot_id':snapshot.snapshot_id,'candidate_build_failures':facts+[bad]}
    assert len(public.public_failure_diagnostics(report))==1


@pytest.mark.parametrize('field',['timed_out','output_limited'])
@pytest.mark.parametrize('value',['missing',None])
def test_incomplete_scenario_flags_cannot_authorize_repair(tmp_path,field,value):
    raw,snapshot,record=fixture(tmp_path)
    scenario=record['scenarios'][0]
    if value=='missing':
        del scenario[field]
    else:
        scenario[field]=value
    (raw/'public_execution.json').write_text(json.dumps(record))
    assert classify(raw,snapshot)==[]


@pytest.mark.parametrize('invalid_first',[True,False])
def test_non_mapping_failure_is_dropped_without_hiding_valid_failure(tmp_path,invalid_first):
    raw,snapshot,_=fixture(tmp_path);facts=classify(raw,snapshot)
    values=[None,*facts] if invalid_first else [*facts,None]
    report={'status':'EXECUTION_FAILED','snapshot_id':snapshot.snapshot_id,'candidate_build_failures':values}
    assert public.has_candidate_execution_failure(report)
    diagnostics=public.public_failure_diagnostics(report)
    assert len(diagnostics)==1 and diagnostics[0]['snapshot_id']==snapshot.snapshot_id


@pytest.mark.parametrize('values',[None,{'invalid':'container'},'invalid'])
def test_non_list_failure_container_cannot_authorize_repair(tmp_path,values):
    _,snapshot,_=fixture(tmp_path)
    report={'status':'EXECUTION_FAILED','snapshot_id':snapshot.snapshot_id,'candidate_build_failures':values}
    assert not public.has_candidate_execution_failure(report)
    assert public.public_failure_diagnostics(report)==[]
