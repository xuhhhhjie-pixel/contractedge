"""Parent preflight is checked once; existing repair APIs stay in control."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

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
