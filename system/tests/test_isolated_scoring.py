"""Parent-only checkpoints never invoke models or repeat a completed grader."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys

import pytest


def load():
    scripts=Path(__file__).resolve().parents[1]/'scripts';path=scripts/'score_isolated_method.py'
    assert path.is_file(),'parent-only scoring checkpoint is not implemented'
    spec=importlib.util.spec_from_file_location('isolated_scoring',path)
    module=importlib.util.module_from_spec(spec);sys.path.insert(0,str(scripts))
    try:spec.loader.exec_module(module)
    finally:sys.path.pop(0)
    return module


def fixture(tmp_path,*,exit_code=0):
    write=lambda p,v:p.write_text(json.dumps(v))
    benchmark=tmp_path/'benchmark';(benchmark/'tools').mkdir(parents=True);(benchmark/'evaluation').mkdir()
    manifest=benchmark/'evaluation/manifest.jsonl';manifest.write_text('opaque frozen registry')
    write(benchmark/'RELEASE_ENTRY.json',{'evaluation_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()})
    tool=benchmark/'tools/score_case.py'
    tool.write_text('''import argparse,json,hashlib
from pathlib import Path
p=argparse.ArgumentParser()
for name in ['evaluation-root','case-id','patch','output']:p.add_argument('--'+name,required=True)
a=p.parse_args();out=Path(a.output);out.mkdir(parents=True)
(out/'score.json').write_text(json.dumps({'case_id':a.case_id,'patch_sha256':hashlib.sha256(Path(a.patch).read_bytes()).hexdigest(),'status':'PASS'}))
raise SystemExit(EXIT_CODE)
'''.replace('EXIT_CODE',str(exit_code)))
    case=tmp_path/'method/CASE';case.mkdir(parents=True);patch=case/'candidate.patch';patch.write_bytes(b'fixed public patch')
    candidate={'patch_path':str(patch),'patch_bytes':patch.stat().st_size,'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest()}
    write(case/'repair.trace.json',{'case_id':'CASE','status':'PUBLIC_EXECUTION_COMPLETE','candidate':candidate,'final_snapshot_id':'S1'})
    state=tmp_path/'state';(state/'method-journal').mkdir(parents=True)
    write(state/'method-journal/export.json',{'status':'DONE','op':'public.export','result':candidate})
    receipt=state/'receipt.json'
    write(receipt,{'case_id':'CASE','status':'METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING',
        'method_returncode':0,'identity':{'output_path':str(case),'public_release_files':{}},
        'terminal_validation':{'classification':'METHOD_RESULT','candidate_verified':True},
        'cleanup':{'status':'OWNED_CONTAINERS_CLOSED'},'state_root':str(state)})
    return receipt,benchmark,tmp_path/'independent',patch


def test_finished_score_recovers_after_parent_exit_without_rerunning_tool(tmp_path,monkeypatch):
    scoring=load();receipt,benchmark,out,patch=fixture(tmp_path)
    scoring.run(receipt,benchmark,out,timeout=10,phase='run')
    assert not (out/'result.json').exists()
    assert json.loads((out/'checkpoint.json').read_text())['status']=='TOOL_FINISHED'
    def forbidden(*a,**k):raise AssertionError('grader must never be repeated on finalization')
    monkeypatch.setattr(scoring.subprocess,'run',forbidden)
    result=scoring.run(receipt,benchmark,out,timeout=10,phase='finalize')
    assert result['status']=='PASS' and result['model_calls_added']==0
    assert result['candidate_selection_frozen'] is True
    assert scoring.run(receipt,benchmark,out,timeout=10,phase='finalize')==result
    assert patch.read_bytes()==b'fixed public patch'


@pytest.mark.parametrize('mutate',['patch','score'])
def test_finalization_rejects_changed_inputs_or_score(tmp_path,mutate):
    scoring=load();receipt,benchmark,out,patch=fixture(tmp_path)
    scoring.run(receipt,benchmark,out,timeout=10,phase='run')
    target=patch if mutate=='patch' else out/'native/score.json';target.write_bytes(b'changed bytes')
    with pytest.raises(ValueError):scoring.run(receipt,benchmark,out,timeout=10,phase='finalize')
    assert not (out/'result.json').exists()


def test_uncertain_score_execution_never_auto_restarts(tmp_path,monkeypatch):
    scoring=load();receipt,benchmark,out,_=fixture(tmp_path)
    def interrupted(*a,**kw):raise OSError('simulated parent death after dispatch')
    monkeypatch.setattr(scoring.subprocess,'run',interrupted)
    with pytest.raises(OSError):scoring.run(receipt,benchmark,out,timeout=10,phase='run')
    for phase in ['run','finalize']:
        with pytest.raises(ValueError):scoring.run(receipt,benchmark,out,timeout=10,phase=phase)
    assert json.loads((out/'checkpoint.json').read_text())['status']=='SCORING_STARTED'


def test_private_scoring_requires_closed_method_and_separate_output(tmp_path):
    scoring=load();receipt,benchmark,out,patch=fixture(tmp_path)
    with pytest.raises(ValueError):scoring.run(receipt,benchmark,patch.parent/'scores',timeout=10,phase='run')
    pending=receipt.parent/'method-journal/pending.json';pending.write_text(json.dumps({'status':'PENDING'}))
    with pytest.raises(ValueError):scoring.run(receipt,benchmark,out,timeout=10,phase='run')
    assert not out.exists()


def test_pass_json_with_failed_scorer_exit_is_not_a_pass(tmp_path):
    scoring=load();receipt,benchmark,out,_=fixture(tmp_path,exit_code=3)
    result=scoring.run(receipt,benchmark,out,timeout=10,phase='all')
    assert result['status']=='SCORER_ERROR' and result['native_score_status']=='PASS'
