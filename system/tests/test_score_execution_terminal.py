"""Exact execution failure is terminal, with read-only old-result reconciliation."""
import importlib.util,json,sys
from pathlib import Path
import pytest
from tests.test_isolated_scoring import load,fixture
from tests.test_fixed_panel import load as load_panel

def test_execution_fail_is_a_native_terminal_negative():
    receipt={'returncode':1,'timed_out':False}
    assert load().score_outcome(receipt,{'status':'EXECUTION_FAIL'})=='EXECUTION_FAIL'
    assert 'EXECUTION_FAIL' in load_panel().TERMINAL_OUTCOMES

@pytest.mark.parametrize('status,exit_code',[('EXECUTION_FAIL',0),('EXECUTION_FAIL',2),('INFRA_ERROR',1),('UNKNOWN_STATE',1)])
def test_unrecognized_or_inconsistent_outcome_stays_error(status,exit_code):
    assert load().score_outcome({'returncode':exit_code,'timed_out':False},{'status':status})=='SCORER_ERROR'

def test_v054_execution_fail_reconciles_without_dispatch_or_overwrite(tmp_path,monkeypatch):
    receipt,benchmark,out,_=fixture(tmp_path,exit_code=1)
    tool=benchmark/'tools/score_case.py';tool.write_text(tool.read_text().replace("'status':'PASS'","'status':'EXECUTION_FAIL'"))
    program=Path(__file__).parent/'fixtures/isolated_score_v054.py'
    spec=importlib.util.spec_from_file_location('frozen_v054_score',program);legacy=importlib.util.module_from_spec(spec)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));spec.loader.exec_module(legacy)
    original=legacy.run(receipt,benchmark,out);assert original['status']=='SCORER_ERROR'
    before={str(p):p.read_bytes() for p in out.rglob('*') if p.is_file()}
    scoring=load();monkeypatch.setattr(scoring.subprocess,'run',lambda *a,**k:pytest.fail('Cannot dispatch completed grader'))
    recorded={'path':str(program),'sha256':scoring.sha(program)}
    result=scoring.read_recorded_score(receipt,benchmark,out,recorded_program=recorded)
    assert result['raw']==original and result['interpreted_status']=='EXECUTION_FAIL'
    assert before=={str(p):p.read_bytes() for p in out.rglob('*') if p.is_file()}
    (out/'result.json').write_text(json.dumps({**original,'status':'PASS'}))
    with pytest.raises(ValueError):scoring.read_recorded_score(receipt,benchmark,out,recorded_program=recorded)
