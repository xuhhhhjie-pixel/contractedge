"""Dispatch admission, session ownership and explicit mount planning."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


def load():
    scripts=Path(__file__).resolve().parents[1]/'scripts';path=scripts/'run_isolated_method.py'
    spec=importlib.util.spec_from_file_location('isolated_coordinator',path)
    module=importlib.util.module_from_spec(spec);sys.path.insert(0,str(scripts))
    try:spec.loader.exec_module(module)
    finally:sys.path.pop(0)
    return module


def test_paid_registration_binds_all_source_config_harness_and_never_claims_panel(tmp_path):
    isolated=load()
    identity={'case_id':'CASE','config_sha256':'abc','method_files':{'x.py':'a'},'harness_files':{'worker.py':'b'}}
    record={**identity,'role':'ISOLATED_DEVELOPMENT_VALIDATION','allow_paid':True,'counts_in_46_slot_panel':False}
    path=tmp_path/'registration.json';path.write_text(json.dumps(record))
    isolated.check_registration(path,identity)
    for changed in [dict(record,case_id='OTHER'),dict(record,allow_paid=False),
                    dict(record,role='FORMAL150'),dict(record,counts_in_46_slot_panel=True),
                    dict(record,method_files={'x.py':'changed'})]:
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError):isolated.check_registration(path,identity)


def test_docker_wrapper_labels_only_owned_runs_and_keeps_all_other_arguments(tmp_path):
    isolated=load();real=tmp_path/'real-docker';log=tmp_path/'docker.json'
    real.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\nPath('+repr(str(log))+').write_text(json.dumps(sys.argv[1:]))\n')
    real.chmod(0o755)
    wrapper=tmp_path/'docker';isolated.write_docker_wrapper(wrapper,str(real),'a'*32)
    result=subprocess.run([str(wrapper),'run','--rm','image','command'],capture_output=True,text=True)
    assert result.returncode==0
    assert json.loads(log.read_text())==['run','--label','contractedge.isolated.session='+'a'*32,'--rm','image','command']
    subprocess.run([str(wrapper),'inspect','container'],check=True)
    assert json.loads(log.read_text())==['inspect','container']


def test_cleanup_filters_exact_session_and_refuses_foreign_or_invalid_container_ids(tmp_path):
    isolated=load();real=tmp_path/'docker';log=tmp_path/'removed'
    session='a'*32;owned='a'*64;foreign='b'*64
    real.write_text('''#!/usr/bin/python3
import sys,json
from pathlib import Path
args=sys.argv[1:]
if args[0]=='ps':
 assert args==['ps','-aq','--filter','label=contractedge.isolated.session=SESSION']
 print('OWNED');print('FOREIGN')
elif args[0]=='inspect':
 print(json.dumps({'contractedge.isolated.session':'SESSION' if args[-1]=='OWNED' else 'different'}))
elif args[0]=='rm':Path(LOG).write_text(args[-1])
else:sys.exit(2)
'''.replace('SESSION',session).replace('OWNED',owned).replace('FOREIGN',foreign).replace('LOG',repr(str(log))))
    real.chmod(0o755)
    result=isolated.cleanup_session(str(real),session,environment={})
    assert result['removed']==[owned] and result['status']=='CLEANUP_UNCONFIRMED'
    assert log.read_text()==owned


def test_inspect_cli_does_not_read_key_dispatch_or_create_output(tmp_path):
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    method=tmp_path/'method';(method/'src').mkdir(parents=True)
    (method/'src/public.py').write_text('public source')
    config=tmp_path/'config.json';config.write_text('{}')
    output=tmp_path/'outputs/CASE'
    result=subprocess.run([sys.executable,str(scripts/'run_isolated_method.py'),'--inspect',
        '--method-root',str(method),'--config',str(config),'--benchmark-root',str(tmp_path/'benchmark'),
        '--output',str(output),'--case','CASE','--python',sys.executable,'--dependency-root',str(tmp_path/'deps'),
        '--api-key-file',str(tmp_path/'key-does-not-exist')],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    record=json.loads(result.stdout)
    assert record['case_id']=='CASE' and record['allow_paid'] is False
    assert record['counts_in_46_slot_panel'] is False and record['method_files']['public.py']
    assert not output.parent.exists()


def test_deep_audit_directory_keeps_real_local_transport_usable(tmp_path):
    isolated=load()
    scripts=Path(__file__).resolve().parents[1]/'scripts';sys.path.insert(0,str(scripts))
    try:import isolated_protocol as rpc
    finally:sys.path.pop(0)
    audit=tmp_path/('deep-audit-'*12);audit.mkdir()
    channel=isolated.make_channel_root()
    try:
        with rpc.JsonRpcServer(channel/'method.sock','auth-8d7c',rpc.Journal(audit/'journal'),
                               {'budget.summary':lambda p:{'limit':150}}) as server:
            assert rpc.RpcClient(server.path,'auth-8d7c').call('budget.summary',{})=={'limit':150}
        assert list((audit/'journal').glob('*.json'))
    finally:
        import shutil
        shutil.rmtree(channel)


def test_public_tool_or_release_drift_invalidates_paid_registration(tmp_path):
    from types import SimpleNamespace
    isolated=load()
    method=tmp_path/'method';(method/'src').mkdir(parents=True);(method/'src/a.py').write_text('source')
    benchmark=tmp_path/'benchmark';(benchmark/'tools').mkdir(parents=True);(benchmark/'public').mkdir()
    (benchmark/'partition').mkdir();(benchmark/'partition/member_groups.jsonl').write_text('partition')
    tool=benchmark/'tools/prepare_case.py';tool.write_text('fixed tool')
    manifest=benchmark/'public/manifest.jsonl';manifest.write_text('fixed release')
    config=tmp_path/'config.json';config.write_text('{}')
    args=SimpleNamespace(method_root=str(method),benchmark_root=str(benchmark),config=str(config),case='CASE',
        python=sys.executable,dependency_root=str(tmp_path/'deps'),seed=None,runtime_admission=None,
        output=str(tmp_path/'results/CASE'),attempt_id='release-drift')
    initial=isolated.execution_identity(args)
    registration=tmp_path/'registration.json';registration.write_text(json.dumps({**initial,
        'role':'ISOLATED_DEVELOPMENT_VALIDATION','allow_paid':True,'counts_in_46_slot_panel':False}))
    isolated.check_registration(registration,initial)
    tool.write_text('changed public producer')
    with pytest.raises(ValueError):isolated.check_registration(registration,isolated.execution_identity(args))
    tool.write_text('fixed tool');manifest.write_text('changed release')
    with pytest.raises(ValueError):isolated.check_registration(registration,isolated.execution_identity(args))


@pytest.mark.parametrize('status,want',[('PUBLIC_EXECUTION_COMPLETE','METHOD_RESULT'),
    ('EVIDENCE_GAP','METHOD_RESULT'),
    ('INVALID_PLAN','METHOD_RESULT'),('STOPPED_UNCERTAIN','METHOD_RESULT'),
    ('INFRASTRUCTURE_ERROR','INFRASTRUCTURE_STOP'),('BUDGET_EXHAUSTED','METHOD_RESULT'),
    ('RECOVERY_FAILED','INFRASTRUCTURE_STOP')])
def test_queue_classifies_method_failure_separately_from_infrastructure_stop(tmp_path,status,want):
    from types import SimpleNamespace
    isolated=load();(tmp_path/'CASE').mkdir();journal=tmp_path/'journal';journal.mkdir()
    (tmp_path/'CASE/repair.trace.json').write_text(json.dumps(dict(case_id='CASE',status=status,
                                                               candidate=None,source_integrity_verified=True)))
    bridge=SimpleNamespace(public_infrastructure_failed=False,exports={})
    assert isolated.validate_method_result(tmp_path,'CASE',bridge,journal)['classification']==want


def test_unknown_request_blocks_completion_even_if_child_writes_success(tmp_path):
    from types import SimpleNamespace
    isolated=load();(tmp_path/'CASE').mkdir();journal=tmp_path/'journal';journal.mkdir()
    (tmp_path/'CASE/repair.trace.json').write_text(json.dumps(dict(case_id='CASE',status='PUBLIC_REVIEW_COMPLETE',candidate=None)))
    (journal/'pending.json').write_text(json.dumps({'status':'PENDING','op':'model.complete'}))
    bridge=SimpleNamespace(public_infrastructure_failed=False,exports={})
    result=isolated.validate_method_result(tmp_path,'CASE',bridge,journal)
    assert result['classification']=='INFRASTRUCTURE_STOP' and result['unknown_requests']==1


def test_candidate_must_match_trusted_export_snapshot_and_actual_patch_bytes(tmp_path):
    from types import SimpleNamespace
    import hashlib
    isolated=load();(tmp_path/'CASE').mkdir();journal=tmp_path/'journal';journal.mkdir()
    patch=tmp_path/'CASE/candidate.patch';patch.write_bytes(b'public patch')
    visible='/visible/CASE/candidate.patch';digest=hashlib.sha256(b'public patch').hexdigest()
    candidate={'patch_path':visible,'patch_sha256':digest,'patch_bytes':12}
    trace=dict(case_id='CASE',status='PUBLIC_EXECUTION_COMPLETE',candidate=candidate,
               final_snapshot_id='S1',source_integrity_verified=True)
    (tmp_path/'CASE/repair.trace.json').write_text(json.dumps(trace))
    bridge=SimpleNamespace(public_infrastructure_failed=False,
        exports={visible:dict(relative='CASE/candidate.patch',patch_sha256=digest,patch_bytes=12,snapshot_id='S1')})
    assert isolated.validate_method_result(tmp_path,'CASE',bridge,journal)['candidate_verified'] is True
    patch.write_bytes(b'corrupted')
    with pytest.raises(ValueError):isolated.validate_method_result(tmp_path,'CASE',bridge,journal)


def test_failed_attempt_cannot_create_new_session_or_reset_journal(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from contractedge.dataset import admission,benchmark
    from contractedge.execution import topomen_agent
    isolated=load();output=tmp_path/'results/CASE';key=tmp_path/'key';key.write_text('never read')
    registration=tmp_path/'registration.json'
    identity={'case_id':'CASE','output_path':str(output),'attempt_id':'dev-attempt-1'}
    registration.write_text(json.dumps({**identity,'role':'ISOLATED_DEVELOPMENT_VALIDATION',
        'allow_paid':True,'counts_in_46_slot_panel':False}))
    args=SimpleNamespace(output=str(output),method_root=str(Path(__file__).resolve().parents[1]),
        benchmark_root=str(tmp_path/'benchmark'),case='CASE',execute=True,registration=str(registration),
        api_key_file=str(key),attempt_id='dev-attempt-1',runtime_admission=None)
    config={'money_budget':{'path':str(tmp_path/'money.json')},
            'topomen_source_root':str(tmp_path/'native'),'topomen_source_sha256':'native'}
    monkeypatch.setattr(admission,'check_case_admission',lambda *a:None)
    monkeypatch.setattr(benchmark,'method_input_from_task',lambda *a:({'initial_snapshot_id':'S0'},{}))
    monkeypatch.setattr(topomen_agent,'source_identity',lambda *a:{'source_sha256':'native'})
    reached=[]
    def interrupted(*a):
        reached.append(True)
        state=next(output.parent.glob('.isolated-*'));journal=state/'method-journal';journal.mkdir(exist_ok=True)
        (journal/'sent.json').write_text(json.dumps({'status':'PENDING'}))
        raise OSError('simulated interrupted run')
    monkeypatch.setattr(isolated,'_runtime_admission',interrupted)
    with pytest.raises(OSError,match='interrupted'):isolated.run_session(args,config,identity)
    assert not output.exists()
    with pytest.raises(ValueError,match='already claimed'):isolated.run_session(args,config,identity)
    assert len(reached)==1 and len(list(output.parent.glob('.isolated-*')))==1
    # Rewriting registration with a new attempt ID must not recycle the same output.
    args.attempt_id='dev-attempt-2';identity={**identity,'attempt_id':args.attempt_id}
    registration.write_text(json.dumps({**identity,'role':'ISOLATED_DEVELOPMENT_VALIDATION',
        'allow_paid':True,'counts_in_46_slot_panel':False}))
    with pytest.raises(ValueError,match='already claimed'):isolated.run_session(args,config,identity)
    assert len(reached)==1


def test_trusted_policy_failure_cannot_be_overridden_by_child_success(tmp_path):
    from types import SimpleNamespace
    isolated=load();(tmp_path/'CASE').mkdir();journal=tmp_path/'journal';journal.mkdir()
    (tmp_path/'CASE/repair.trace.json').write_text(json.dumps(dict(case_id='CASE',status='PUBLIC_REVIEW_COMPLETE',candidate=None)))
    bridge=SimpleNamespace(public_infrastructure_failed=False,exports={})
    broker=SimpleNamespace(policy_failures=[{'category':'MODEL_POLICY','error_type':'ProviderResponseError'}])
    assert isolated.validate_method_result(tmp_path,'CASE',bridge,journal,broker=broker)['classification']=='INFRASTRUCTURE_STOP'


@pytest.mark.parametrize('package_file',['example.py','native.so','example.pyc','data.json'])
def test_installed_package_bytes_are_part_of_registered_runtime(tmp_path,package_file):
    from types import SimpleNamespace
    isolated=load();venv=tmp_path/'venv';(venv/'bin').mkdir(parents=True)
    python=venv/'bin/python';python.write_bytes(b'pinned interpreter')
    (venv/'pyvenv.cfg').write_text('home = /usr/bin\n')
    site=venv/'lib/python3.10/site-packages';site.mkdir(parents=True)
    metadata=site/'example-1.dist-info/METADATA';metadata.parent.mkdir();metadata.write_text('Version: 1\n')
    executable=site/package_file;executable.write_bytes(b'original installed bytes')
    method=tmp_path/'method';(method/'src').mkdir(parents=True)
    config=tmp_path/'config.json';config.write_text('{}')
    args=SimpleNamespace(method_root=str(method),benchmark_root=str(tmp_path/'benchmark'),config=str(config),case='CASE',
        python=str(python),dependency_root=str(tmp_path/'deps'),seed=None,runtime_admission=None,
        output=str(tmp_path/'results/CASE'),attempt_id='runtime-drift')
    initial=isolated.execution_identity(args)
    registration=tmp_path/'registration.json';registration.write_text(json.dumps({**initial,
        'role':'ISOLATED_DEVELOPMENT_VALIDATION','allow_paid':True,'counts_in_46_slot_panel':False}))
    executable.write_bytes(b'changed installed bytes')
    assert metadata.read_text()=='Version: 1\n'
    with pytest.raises(ValueError):isolated.check_registration(registration,isolated.execution_identity(args))
