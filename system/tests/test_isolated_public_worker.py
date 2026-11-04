"""Trusted workspace lifecycle; the candidate supplies source bytes only."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from contractedge.source.snapshot import compute_snapshot


def load():
    scripts=Path(__file__).resolve().parents[1]/'scripts';path=scripts/'isolated_public_worker.py'
    assert path.is_file(), 'trusted public execution session is not implemented'
    spec=importlib.util.spec_from_file_location('isolated_public_worker',path)
    module=importlib.util.module_from_spec(spec)
    sys.path.insert(0,str(scripts))
    try:spec.loader.exec_module(module)
    finally:sys.path.pop(0)
    return module


class LocalPublicTools:
    """A real Git/filesystem producer; external Docker behavior is out of scope."""
    timeout_seconds=30
    def __init__(self):self._method_probe_specs={};self._observation_adapters={}
    def prepare(self,case,workspace,log):
        (workspace/'source').mkdir(parents=True);(workspace/'inputs').mkdir()
        (workspace/'source/main.go').write_text('broken\n')
        (workspace/'inputs/fixed-input').write_text('original input')
        source=workspace/'source'
        def git(*args):return subprocess.check_output(['git','-C',str(source),*args],stderr=subprocess.STDOUT)
        git('init','-q');git('add','.')
        git('-c','user.name=Test','-c','user.email=test@localhost','commit','-qm','Broken')
        baseline=git('rev-parse','HEAD').decode().strip()
        record=dict(case_id=case,source_root=str(source),inputs_root=str(workspace/'inputs'),
                    private_materials_loaded=False,git_initialized=True,baseline_commit=baseline)
        (workspace/'workspace.json').write_text(json.dumps(record))
        log.mkdir();(log/'tool.stdout').write_text('prepared')
        self._method_probe_specs[str(workspace)]=[{'id':'frozen-on-S0','sha256':'fixed'}]
        self._observation_adapters[str(workspace)]=['original adapter']
        return record
    def observe(self,workspace,output):
        record=json.loads((workspace/'workspace.json').read_text());output.mkdir(parents=True)
        result={'source':(Path(record['source_root'])/'main.go').read_text(),
                'input':(Path(record['inputs_root'])/'fixed-input').read_text(),
                'probe':self._method_probe_specs[str(workspace)][0]['id']}
        (output/'observation.json').write_text(json.dumps(result))
        return result
    def export(self,workspace,patch,log):
        record=json.loads((workspace/'workspace.json').read_text())
        raw=subprocess.check_output(['git','-C',record['source_root'],'diff',record['baseline_commit'],'--'])
        patch.write_bytes(raw);log.mkdir();return {'patch_path':str(patch),'patch_bytes':len(raw)}


def session(tmp_path):
    module=load();transfer=tmp_path/'transfer';transfer.mkdir()
    producer=tmp_path/'producer';producer.mkdir()
    return module.PublicSession(producer,transfer,'CASE',LocalPublicTools(),max_timeout=30),transfer


def test_same_trusted_baseline_inputs_and_s0_probe_survive_multiple_source_candidates(tmp_path):
    producer,transfer=session(tmp_path)
    original=producer.prepare({'case_id':'CASE','timeout_seconds':30})['record']
    baseline=original['baseline_commit']
    for index,content in enumerate(['candidate one\n','candidate two\n']):
        stage=f'{index:032x}';source=transfer/stage;source.mkdir();(source/'main.go').write_text(content)
        (source/'.git').write_text('malicious Git is ignored')
        request=dict(stage_id=stage,snapshot_id=compute_snapshot(source).snapshot_id,timeout_seconds=10)
        observed=producer.observe(request)
        assert observed['report']==dict(source=content,input='original input',probe='frozen-on-S0')
        exported=producer.export(request)
        patch=Path(exported['result']['patch_path']).read_text()
        assert '-broken\n' in patch and '+'+content in patch
        assert json.loads((producer.workspace/'workspace.json').read_text())['baseline_commit']==baseline
    assert producer.runner._observation_adapters[str(producer.workspace)]==['original adapter']


@pytest.mark.parametrize('mutation',['cross-case','unknown-stage','wrong-snapshot','source-link'])
def test_public_worker_refuses_unregistered_or_incomplete_source_without_changing_selected(tmp_path,mutation):
    producer,transfer=session(tmp_path)
    producer.prepare({'case_id':'CASE','timeout_seconds':30})
    source=Path(producer.record['source_root']);before=compute_snapshot(source).snapshot_id
    stage='a'*32;(transfer/stage).mkdir();(transfer/stage/'main.go').write_text('candidate')
    request=dict(stage_id=stage,snapshot_id=compute_snapshot(transfer/stage).snapshot_id,timeout_seconds=10)
    if mutation=='cross-case':request['case_id']='OTHER'
    elif mutation=='unknown-stage':request['stage_id']='../private'
    elif mutation=='wrong-snapshot':request['snapshot_id']='wrong'
    else:(transfer/stage/'steal').symlink_to(tmp_path/'private')
    with pytest.raises(ValueError):producer.observe(request)
    assert compute_snapshot(source).snapshot_id==before


def test_public_session_does_not_accept_a_second_prepare_or_unbounded_timeout(tmp_path):
    producer,_=session(tmp_path)
    with pytest.raises(ValueError):producer.prepare({'case_id':'OTHER','timeout_seconds':30})
    with pytest.raises(ValueError):producer.prepare({'case_id':'CASE','timeout_seconds':float('nan')})
    producer.prepare({'case_id':'CASE','timeout_seconds':30})
    with pytest.raises(ValueError):producer.prepare({'case_id':'CASE','timeout_seconds':30})


def test_bridge_ignores_poisoned_child_metadata_git_and_inputs(tmp_path):
    producer,transfer=session(tmp_path)
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    sys.path.insert(0,str(scripts))
    try:
        import run_isolated_method as isolated
        import isolated_protocol as rpc
    finally:sys.path.pop(0)
    child=tmp_path/'child';child.mkdir();(child/'CASE').mkdir()
    visible=tmp_path/'visible-cases'
    handlers={'public.prepare':producer.prepare,'public.observe':producer.observe,'public.export':producer.export}
    with rpc.JsonRpcServer(tmp_path/'private.sock','producer-auth-8a70',rpc.Journal(tmp_path/'j'),handlers) as server:
        bridge=isolated.PublicBridge(child,visible,producer.root,transfer,'CASE',
            rpc.RpcClient(server.path,'producer-auth-8a70'),max_timeout=30)
        workspace=str(visible/'CASE/workspace')
        record=bridge.prepare(dict(case_id='CASE',workspace=workspace,log=str(visible/'CASE/prepare'),timeout_seconds=30))
        assert record['source_root']==str(visible/'CASE/workspace/source')
        actual=child/'CASE/workspace';(actual/'inputs/fixed-input').write_text('poisoned child input')
        (actual/'workspace.json').unlink();(actual/'workspace.json').symlink_to(tmp_path/'private.json')
        source=actual/'source';(source/'main.go').write_text('fixed\n')
        subprocess.run(['git','-C',str(source),'-c','user.name=Test','-c','user.email=test@localhost',
                        'commit','-qam','malicious replacement baseline'],check=True,capture_output=True)
        sid=compute_snapshot(source).snapshot_id
        report=bridge.observe(dict(workspace=workspace,snapshot_id=sid,output=str(visible/'CASE/public'),timeout_seconds=10))
        assert report==dict(source='fixed\n',input='original input',probe='frozen-on-S0')
        exported=bridge.export(dict(workspace=workspace,snapshot_id=sid,patch=str(visible/'CASE/final.patch'),
                                   log=str(visible/'CASE/export'),timeout_seconds=10))
        assert exported['patch_path']==str(visible/'CASE/final.patch')
        assert '-broken\n' in (child/'CASE/final.patch').read_text()
        assert '+fixed\n' in (child/'CASE/final.patch').read_text()
        assert json.loads((child/'CASE/public/observation.json').read_text())==report


@pytest.mark.parametrize('output',['/tmp/other-case/report','CASE/../OTHER/public','CASE/workspace/source/report'])
def test_bridge_rejects_cross_case_or_source_output_paths_before_execution(tmp_path,output):
    producer,transfer=session(tmp_path)
    scripts=Path(__file__).resolve().parents[1]/'scripts';sys.path.insert(0,str(scripts))
    try:import run_isolated_method as isolated
    finally:sys.path.pop(0)
    child=tmp_path/'child';child.mkdir();visible=tmp_path/'visible'
    bridge=isolated.PublicBridge(child,visible,producer.root,transfer,'CASE',None,max_timeout=30)
    request=dict(workspace=str(visible/'CASE/workspace'),snapshot_id='a'*64,
                 output=output if output.startswith('/') else str(visible/output),timeout_seconds=10)
    with pytest.raises(ValueError):bridge.observe(request)
