"""Preparation verifies original sources and never dispatches or reads a key."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from tests.test_fixed_panel import fixture


@pytest.mark.parametrize('changed',[False,True])
def test_prepare_cli_binds_frozen_sources_and_preserves_original_registration(tmp_path,changed):
    panel,_,_=fixture(tmp_path);script=Path(__file__).resolve().parents[1]/'scripts/prepare_fixed_panel.py'
    assert script.is_file(),'Portable fixed46 preparation entry not implemented'
    baseline=tmp_path/'baseline-reference.json';baseline.write_text(json.dumps({'baseline_source_files':panel['variants']['baseline']['source_files']}))
    candidate=tmp_path/'candidate-reference.json';candidate.write_text(json.dumps({'files':panel['variants']['candidate']['source_files']}))
    original=Path(panel['original_registration']['path']);value=json.loads(original.read_text())
    for case in value['recovery_seed_paths']:value['recovery_seed_paths'][case]=case+'.patch'
    original.write_text(json.dumps(value));before=original.read_bytes()
    if changed:(Path(panel['variants']['baseline']['root'])/'src/pkg/__init__.py').write_text('wrong baseline')
    output=tmp_path/'new-panel.json';runs=tmp_path/'run-output';deps=tmp_path/'deps';deps.mkdir()
    command=[sys.executable,str(script),'--original',str(original),'--baseline-reference',str(baseline),
        '--candidate-reference',str(candidate),'--baseline-root',panel['variants']['baseline']['root'],
        '--candidate-root',panel['variants']['candidate']['root'],'--baseline-config',panel['variants']['baseline']['config'],
        '--candidate-config',panel['variants']['candidate']['config'],'--benchmark-root',panel['benchmark_root'],
        '--seed-root',str(tmp_path),'--python',sys.executable,'--dependency-root',str(deps),
        '--run-root',str(runs),'--output',str(output),'--panel-id','prepared-test']
    result=subprocess.run(command,capture_output=True,text=True,timeout=30)
    if changed:
        assert result.returncode!=0 and not output.exists()
    else:
        assert result.returncode==0,result.stderr
        record=json.loads(output.read_text());assert len(record['slots'])==46
        assert record['variants']['baseline']['source_files']==panel['variants']['baseline']['source_files']
        assert record['continuation_protocol']['applies_to']==['baseline','candidate']
    assert original.read_bytes()==before
    assert not runs.exists() # inspect/preparation cannot create or claim a run
