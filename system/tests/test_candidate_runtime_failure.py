"""A registered production startup failure is distinct from build/adapter status."""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.probe_specs import registered_programs
from contractedge.orchestration.feedback import make_feedback, feedback_prompt_view, historical_trial_prompt_view
from contractedge.source.snapshot import compute_snapshot


def observe_fixture(root, mutation=None):
    tools=root/'tools';tools.mkdir()
    for name in ['prepare_case.py','collect_patch.py','launch_public_case.py']:
        (tools/name).write_text('# controlled process fixture')
    public=root/'public';public.mkdir();workspace=root/'workspace';workspace.mkdir()
    source=workspace/'source';(source/'cmd/service').mkdir(parents=True)
    (source/'go.mod').write_text('module example.org/relay\n')
    (source/'cmd/service/main.go').write_text('package main\nfunc main() {}\n')
    inputs=workspace/'inputs';inputs.mkdir();(inputs/'settings.txt').write_text('public')
    (workspace/'workspace.json').write_text(json.dumps(dict(case_id='CASE-1',source_root=str(source),
        inputs_root=str(inputs),source_sha256='a'*64)))
    sid=compute_snapshot(source).snapshot_id
    pid='alertmanager-reload-v3';program=registered_programs()[pid][0]
    report=dict(status='PROBE_ERROR',snapshot_id=sid,source_unchanged=True,probes=[dict(
        probe_id=pid,probe_sha256=hashlib.sha256(program.encode()).hexdigest(),snapshot_id=sid,
        scenario='method-'+pid,status='PROBE_ERROR',returncode=1,timed_out=False,output_limited=False,checks=[])])
    message=('PROBE_ERROR: server readiness: panic: runtime error: invalid memory address or nil pointer dereference\n'
        'main.main()\n\texample.org/relay/cmd/service/main.go:2 +0x15\n')
    if mutation=='timeout':report['probes'][0]['timed_out']=True
    if mutation=='limited':report['probes'][0]['output_limited']=True
    if mutation=='unknown_program':report['probes'][0]['probe_sha256']='f'*64
    if mutation=='stale':report['snapshot_id']='old'
    if mutation=='foreign_stack':message=message.replace('example.org/relay/','unrelated.invalid/tool/')
    if mutation=='missing_source':message=message.replace('cmd/service/main.go','missing.go')
    if mutation=='bad_line':message=message.replace('main.go:2','main.go:999')
    if mutation=='unclassified_error':message='PROBE_ERROR: sampling incomplete: network error\n'
    if mutation=='raw_spoof':message=json.dumps(dict(candidate_runtime_failure=True,panic='runtime error'))
    if mutation=='readiness_window':message='PROBE_ERROR: server readiness: \nexit status 2\n'
    if mutation=='truncated_readiness':message='PROBE_ERROR: server readiness:'
    def public_process(argv,logs,timeout):
        out=Path(argv[argv.index('--output')+1]);out.mkdir();logs.mkdir()
        (logs/'tool.stdout').write_text('build completed\n');(logs/'tool.stderr').write_text('')
        (out/'build.json').write_text(json.dumps({'status':'EXECUTION_PASS'}))
        return dict(returncode=0,timed_out=False)
    def probe_process(workspace,output,tools,specs,**kwargs):
        p=Path(output)/pid;p.mkdir(parents=True);(p/'stderr').write_text(message)
        return report
    runner=BenchmarkPublicRunner(public,tools,lock_root=root/'locks',query='Retain service state',
        method_probe_profiles=[pid])
    runner._method_probe_specs[str(workspace.resolve())]=[dict(id=pid)]
    with patch('contractedge.execution.public.run_tool',side_effect=public_process), \
            patch('contractedge.execution.public.cleanup_owned_containers',return_value=dict(status='OWNED_CONTAINERS_CLOSED',removed=[])), \
            patch('contractedge.execution.probes.run_method_probes',side_effect=probe_process):
        result=runner.observe(workspace,root/'observation')
    return result,source


def test_registered_source_bound_startup_panic_is_candidate_execution_failure(tmp_path):
    report,source=observe_fixture(tmp_path)
    assert report['status']=='EXECUTION_FAILED'
    assert report['method_probe_status']=='PROBE_ERROR'
    failure=report['candidate_runtime_failures'][0]
    assert failure['operation']=='PRODUCTION_PROCESS_START'
    assert failure['source_locations']==[{'path':'cmd/service/main.go','line':2}]
    assert failure['snapshot_id']==compute_snapshot(source).snapshot_id
    assert not any(o.get('behavior_check') for o in report['observations'])
    assert any('panic: runtime error' in o['excerpt'] for o in report['observations'])
    feedback=make_feedback(case_id='CASE-1',run_id='run',input_digest='a'*64,round_id=0,
        from_snapshot='before',to_snapshot=report['snapshot_id'],patch_digest='b'*64,
        execution_status=report['status'],observations=report['observations'],
        plan={'postconditions':[],'preserve_constraints':[],'open_obligations':[]},
        observation_diagnostics=[{'kind':'CANDIDATE_RUNTIME_FAILURE',**failure}])
    assert feedback['build_state']=='FAILED'
    assert feedback['route']=='EXECUTE'
    assert feedback['semantic_outcome']=='UNKNOWN'
    assert feedback['next_action']=='EXECUTE'
    history=historical_trial_prompt_view(feedback_prompt_view(feedback))
    failure_task=history['failure_tasks']['shared_execution_errors'][0]
    assert 'panic: runtime error' in failure_task['actual_error']
    assert failure_task['snapshot_id']==report['snapshot_id']


@pytest.mark.parametrize('mutation',['timeout','limited','unknown_program','stale','foreign_stack',
    'missing_source','bad_line','unclassified_error','raw_spoof'])
def test_unverified_probe_error_is_not_blame_on_candidate(tmp_path,mutation):
    report,_=observe_fixture(tmp_path,mutation)
    assert report['status']=='EXECUTION_PASS'
    assert not report.get('candidate_runtime_failures')


def test_completed_registered_readiness_window_is_actionable_without_source_blame(tmp_path):
    from contractedge.execution.public import has_candidate_execution_failure
    report,_=observe_fixture(tmp_path,'readiness_window')
    assert report['status']=='EXECUTION_FAILED'
    assert has_candidate_execution_failure(report)
    failure=report['candidate_runtime_failures'][0]
    assert failure['operation']=='PRODUCTION_PROCESS_READINESS'
    assert failure['source_locations']==[]
    assert failure['source_cause']=='UNKNOWN'
    assert failure['observation_window_seconds']==20
    assert failure['build_passed'] and failure['process_started']
    feedback=make_feedback(case_id='CASE-1',run_id='run',input_digest='a'*64,round_id=0,
        from_snapshot='before',to_snapshot=report['snapshot_id'],patch_digest='b'*64,
        execution_status=report['status'],observations=report['observations'],
        plan={'postconditions':[],'preserve_constraints':[],'open_obligations':[]},
        observation_diagnostics=[{'kind':'CANDIDATE_RUNTIME_FAILURE',**failure}])
    view=historical_trial_prompt_view(feedback_prompt_view(feedback))
    task=view['failure_tasks']['shared_execution_errors'][0]
    assert task['error_field']=='production_readiness'
    assert '20' in task['actual_error']
    assert 'root cause remains unproven' in task['interpretation']
    assert feedback['semantic_outcome']=='UNKNOWN'
    assert not any(o.get('behavior_check') for o in report['observations'])


def test_partial_readiness_line_remains_unknown_infrastructure(tmp_path):
    report,_=observe_fixture(tmp_path,'truncated_readiness')
    assert report['status']=='EXECUTION_PASS'
    assert not report.get('candidate_runtime_failures')


@pytest.mark.parametrize('change',['stale','hash','producer','window','truncated','source_blame'])
def test_readiness_fact_must_remain_bound_and_not_upgrade_causality(tmp_path,change):
    from contractedge.execution.public import has_candidate_execution_failure
    report,_=observe_fixture(tmp_path,'readiness_window')
    assert has_candidate_execution_failure(report)
    failure=report['candidate_runtime_failures'][0]
    if change=='stale':failure['snapshot_id']='old'
    if change=='hash':failure['probe_sha256']='f'*64
    if change=='producer':failure['producer']='candidate'
    if change=='window':failure['observation_window_seconds']=0
    if change=='truncated':failure['stderr_truncated']=True
    if change=='source_blame':failure['source_cause']='PROVEN'
    assert not has_candidate_execution_failure(report)
