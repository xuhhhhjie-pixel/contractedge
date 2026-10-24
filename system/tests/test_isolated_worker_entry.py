"""Parent preflight is checked once; existing repair APIs stay in control."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import hashlib
import subprocess

import pytest


def worker():
    scripts=Path(__file__).resolve().parents[1]/'scripts';path=scripts/'run_reliability_worker_v10.py'
    assert path.is_file(), 'isolated worker entry is not implemented'
    spec=importlib.util.spec_from_file_location('worker_v10',path)
    value=importlib.util.module_from_spec(spec);sys.path.insert(0,str(scripts))
    try:spec.loader.exec_module(value)
    finally:sys.path.pop(0)
    return value


@pytest.mark.parametrize('current',[True,False])
def test_current_and_historical_public_runner_injection_keep_preflight_and_time(current,tmp_path):
    entry=worker();runner=object();provider=object()
    preflight={'status':'PASS','runtime_verified':True}
    def legacy(public,tools,case,output,received_provider,counter,config,*,public_runner):
        assert public_runner is runner and received_provider is provider
        assert config['max_model_calls']==12 and config['runtime_preflight'] is None
        return {'status':'LEGACY_CALLED','case_id':case}
    def modern(public,tools,case,output,received_provider,counter,config,*,public_runner,
               _runtime_preflight,_runtime_started):
        assert _runtime_preflight is preflight and _runtime_started==123.0
        return {**legacy(public,tools,case,output,received_provider,counter,config,public_runner=public_runner),
                'status':'CURRENT_CALLED'}
    api=SimpleNamespace(repair_case=legacy,**({'_repair_case_impl':modern} if current else {}))
    result=entry.invoke_repair(api,tmp_path,'CASE',tmp_path/'out',provider,len,
        {'max_model_calls':12,'runtime_preflight':None},runner,preflight,123.0)
    assert result=={'status':'CURRENT_CALLED' if current else 'LEGACY_CALLED','case_id':'CASE'}


@pytest.mark.parametrize('receipt',[None,{'status':'PASS','runtime_verified':False},{'status':'INFRA_ERROR','runtime_verified':False}])
def test_unverified_parent_preflight_never_starts_method(receipt,tmp_path):
    entry=worker()
    with pytest.raises(ValueError,match='preflight'):
        entry.invoke_repair(SimpleNamespace(),tmp_path,'CASE',tmp_path/'out',None,len,{},None,receipt,123)
    assert not (tmp_path/'out').exists()


def recovery_fixture(tmp_path, monkeypatch, *, front_error=None):
    """Only substitute public/model services; exercise actual git seed application."""
    from contractedge.contracts.validation import canonical_digest
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.dataset import benchmark
    from contractedge.execution import probe_specs, observations
    from contractedge.orchestration import runner
    from tests.test_contracts import valid_method_input
    entry=worker();source=tmp_path/'source';source.mkdir()
    (source/'main.go').write_text('broken\n')
    for arguments in [['init','-q'],['add','.'],['-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','S0']]:
        subprocess.run(['git',*arguments],cwd=source,check=True,capture_output=True)
    seed=tmp_path/'seed.patch'
    seed.write_text('diff --git a/main.go b/main.go\n--- a/main.go\n+++ b/main.go\n@@ -1 +1 @@\n-broken\n+seeded\n')
    value=valid_method_input();value['initial_snapshot_id']=compute_snapshot(source).snapshot_id
    value['input_digest']=canonical_digest({k:v for k,v in value.items() if k!='input_digest'})
    monkeypatch.setattr(benchmark,'method_input_from_task',lambda *a:(value,{'public_execution_capabilities':{'editable_policy':None}}))
    monkeypatch.setattr(probe_specs,'select_probes',lambda *a:[])
    monkeypatch.setattr(observations,'select_observation_adapters',lambda *a:[])
    def front(*a,**kw):
        if front_error is not None:raise front_error
        return SimpleNamespace(to_wire=lambda:{'status':'NEEDS_EVIDENCE','errors':['saved unsupported plan']})
    monkeypatch.setattr(runner,'run_front_half',front)
    class Public:
        def prepare(self,case,workspace,log):
            workspace.mkdir();return {'source_root':str(source)}
        def observe(self,*a):return {'observations':[],'status':'EXECUTION_PASS'}
        def export(self,workspace,patch,log):
            data=subprocess.check_output(['git','diff'],cwd=source);Path(patch).write_bytes(data)
            return {'patch_path':str(patch),'patch_bytes':len(data),'patch_sha256':hashlib.sha256(data).hexdigest()}
    config=dict(max_total_tokens=100,max_elapsed_seconds=30,max_model_calls=12,
                method_probe_profiles=[],max_public_evidence_tokens=100,
                model='test',provider='test',schema_in_prompt=False)
    result=entry.recover(value['case_id'],tmp_path/'case',object(),len,config,tmp_path,seed,public_runner=Public())
    return result,source,seed


def test_seeded_entry_applies_real_public_patch_before_unsupported_plan_result(tmp_path,monkeypatch):
    result,source,seed=recovery_fixture(tmp_path,monkeypatch)
    assert result['status']=='RECOVERY_PLAN_UNSUPPORTED',result['errors']
    assert (source/'main.go').read_text()=='seeded\n'
    assert Path(result['candidate']['patch_path']).read_bytes()==subprocess.check_output(['git','diff'],cwd=source)
    assert result['candidate']['patch_bytes']>0


@pytest.mark.parametrize('kind,category',[('budget','BUDGET'),('identity','MODEL_POLICY'),('worker','WORKER_ERROR')])
def test_recovery_preserves_typed_stop_reason(tmp_path,monkeypatch,kind,category):
    from contractedge.model.budget import BudgetExceeded
    from contractedge.model.provider import ProviderResponseError,ModelResponse
    error={'budget':BudgetExceeded('exhausted'),
           'identity':ProviderResponseError('wrong model',ModelResponse('{}',{'model_identity_status':'MISMATCH'})),
           'worker':NameError('missing worker dependency')}[kind]
    result,_,_=recovery_fixture(tmp_path,monkeypatch,front_error=error)
    assert result['status']=='RECOVERY_FAILED'
    assert result.get('recovery_failure',{}).get('category')==category
    assert result['recovery_failure']['error_type']==type(error).__name__
