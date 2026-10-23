"""Real edit-window exits and failed-rollback recovery against public synthetic inputs."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from contractedge.development.public_demo import freeze_example, default_config, PublicExampleRunner
from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot


CHILD = r'''
from pathlib import Path
import json, os, sys
from contractedge.development.public_demo import PublicExampleRunner, SyntheticExampleProvider, count_tokens
from contractedge.orchestration.repair import repair_case
from contractedge.execution import literal
from contractedge.orchestration import candidate_guard
from contractedge.orchestration.recorder import RunRecorder
root=Path(sys.argv[1]); point=sys.argv[2]; config=json.loads((root/'config.json').read_text())
public=root/'frozen-public/public'; runner=PublicExampleRunner(public,'config-binding')
if point=='staging':
    original=literal.tempfile.mkstemp
    def mkstemp(*args,**kwargs):
        # The first complete staged source bytes exist before its backup begins.
        if kwargs.get('prefix')=='.ce-before-': os._exit(73)
        return original(*args,**kwargs)
    literal.tempfile.mkstemp=mkstemp
elif point=='backup':
    original=literal.os.link
    def link(source,destination,*args,**kwargs):
        result=original(source,destination,*args,**kwargs)
        # Exit after creating the real original-file hardlink, before replacement.
        if Path(destination).name.startswith('.ce-before-'): os._exit(73)
        return result
    literal.os.link=link
elif point=='selection-rollback-failed':
    original=RunRecorder._write_pending
    def pending(self,name,value):
        if name=='selected-candidate.json' and value.get('selection')=='TRIAL':
            raise OSError('controlled selection pointer persistence failure')
        return original(self,name,value)
    RunRecorder._write_pending=pending
    def fail_rollback(*args,**kwargs):
        raise OSError('controlled emergency rollback failure')
    candidate_guard.restore_candidate=fail_rollback
report=repair_case(public,root/'tools','config-binding',root/'run',
    SyntheticExampleProvider('config-binding',runner),count_tokens,config,public_runner=runner)
(root/'original-report.json').write_text(json.dumps(report,sort_keys=True))
'''


def _start(tmp_path, point):
    public = freeze_example('config-binding', tmp_path)
    config = {**default_config('config-binding'), 'execution_backend': 'direct',
              'max_rounds': 1, 'run_id': 'edit-window-' + point}
    config.pop('topomen_source_root', None)
    config.pop('topomen_source_sha256', None)
    (tmp_path / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    program = tmp_path / 'edit-window-child.py'
    program.write_text(CHILD, encoding='utf-8')
    child = subprocess.run([sys.executable, '-B', str(program), str(tmp_path), point],
                           text=True, capture_output=True, timeout=120)
    (tmp_path / 'child.stdout.txt').write_text(child.stdout, encoding='utf-8')
    (tmp_path / 'child.stderr.txt').write_text(child.stderr, encoding='utf-8')
    assert child.returncode == (0 if point == 'selection-rollback-failed' else 73), child.stderr + child.stdout
    run = tmp_path / 'run'
    saved = json.loads((run / 'journal/recovery.identity.json').read_bytes())
    source = Path(saved['identity']['source_root'])
    events = [json.loads(path.read_bytes()) for path in
              sorted((run / 'journal/trials/round-0').glob('[0-9]*.json'))]
    return public, config, run, source, saved, events


def _resume(tmp_path, public, config):
    class ForbiddenProvider:
        def complete_json(self, *args, **kwargs):
            raise AssertionError('Recovery dispatched a model request')
    return repair_case(public, tmp_path / 'tools', 'config-binding', tmp_path / 'run',
                       ForbiddenProvider(), None, config,
                       public_runner=PublicExampleRunner(public, 'config-binding'), resume=True)


def _probe(tmp_path, **values):
    (tmp_path / 'probe.json').write_text(json.dumps(values, indent=2, sort_keys=True) + '\n', encoding='utf-8')


@pytest.mark.parametrize('point', ['staging', 'backup'])
def test_real_edit_window_exit_recovers_without_temporary_source_files(tmp_path, point):
    public, config, run, source, saved, events = _start(tmp_path, point)
    kinds = [event['kind'] for event in events]
    assert 'EDIT_INTENT' in kinds and 'APPLIED' not in kinds
    ledger = (run / 'model_calls.json').read_bytes()
    source_temps = sorted(str(path.relative_to(source)) for path in source.rglob('.ce-*'))
    trial_temps = sorted(str(path.relative_to(run)) for path in (run / 'journal/trials').rglob('.ce-*'))
    before = compute_snapshot(source).snapshot_id
    try:
        report = _resume(tmp_path, public, config)
    except Exception as error:
        _probe(tmp_path, point=point, source_temps=source_temps, trial_temps=trial_temps,
               source_snapshot=before, parent_snapshot=saved['initial_snapshot'],
               exception=type(error).__name__ + ': ' + str(error))
        raise
    _probe(tmp_path, point=point, source_temps=source_temps, trial_temps=trial_temps,
           source_snapshot=before, parent_snapshot=saved['initial_snapshot'], report=report)
    assert source_temps == []
    assert trial_temps
    assert before == saved['initial_snapshot']
    assert report['final_snapshot_id'] == saved['initial_snapshot']
    assert report['status'] == 'RECOVERED_INCUMBENT'
    assert report['candidate']['patch_bytes'] == 0
    assert report['last_feedback'] is None
    assert report['budget']['model_calls'] == 3
    assert (run / 'model_calls.json').read_bytes() == ledger
    assert _resume(tmp_path, public, config) == report


def test_external_ce_named_source_file_still_blocks_recovery(tmp_path):
    public, config, run, source, saved, events = _start(tmp_path, 'staging')
    assert not list(source.rglob('.ce-*'))
    external = source / '.ce-outside-transaction'
    external.write_text('external modification, never an owned staging file\n', encoding='utf-8')
    before = compute_snapshot(source).snapshot_id
    ledger = (run / 'model_calls.json').read_bytes()
    with pytest.raises(ValueError, match='External workspace source change'):
        _resume(tmp_path, public, config)
    assert compute_snapshot(source).snapshot_id == before
    assert external.read_text() == 'external modification, never an owned staging file\n'
    assert (run / 'model_calls.json').read_bytes() == ledger
    assert not (run / 'journal/recovery.completed.json').exists()


def test_failed_rollback_after_real_selection_intent_prohibits_recovery_export(tmp_path):
    public, config, run, source, saved, events = _start(tmp_path, 'selection-rollback-failed')
    kinds = [event['kind'] for event in events]
    assert 'SELECTION_INTENT' in kinds and 'ROLLBACK_FAILED' in kinds
    assert 'SELECTION_COMPLETED' not in kinds
    selected = next(event for event in events if event['kind'] == 'SELECTION_INTENT')
    assert compute_snapshot(source).snapshot_id == selected['selected_snapshot']
    original = json.loads((tmp_path / 'original-report.json').read_bytes())
    assert original['candidate'] is None
    ledger = (run / 'model_calls.json').read_bytes()
    before = compute_snapshot(source).snapshot_id
    try:
        report = _resume(tmp_path, public, config)
    except ValueError as error:
        _probe(tmp_path, original=original, recovery_exception=str(error), kinds=kinds)
        assert 'rollback failed' in str(error).lower()
    else:
        _probe(tmp_path, original=original, wrongly_exported_report=report, kinds=kinds)
        pytest.fail('A previously failed rollback became an exportable recovered selection')
    assert compute_snapshot(source).snapshot_id == before
    assert (run / 'model_calls.json').read_bytes() == ledger
    assert not (run / 'journal/recovery.completed.json').exists()
