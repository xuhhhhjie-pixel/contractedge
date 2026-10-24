"""Native writes need durable ownership before the first source replacement."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from contractedge.development.public_demo import freeze_example, default_config, PublicExampleRunner
from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot


CHILD = '''from pathlib import Path
import json, os, sys
from contractedge.development.public_demo import PublicExampleRunner,SyntheticExampleProvider,count_tokens
from contractedge.orchestration.repair import repair_case
from contractedge.execution import literal
root=Path(sys.argv[1]);config=json.loads((root/'config.json').read_text())
public=root/'frozen-public/public';runner=PublicExampleRunner(public,'config-binding')
replace=literal.os.replace
def stop_after_replace(source,destination,*args,**kwargs):
 result=replace(source,destination,*args,**kwargs)
 if Path(destination)==root/'run/workspace/source/repo/app.py':os._exit(73)
 return result
literal.os.replace=stop_after_replace
repair_case(public,root/'tools','config-binding',root/'run',SyntheticExampleProvider('config-binding',runner),
 count_tokens,config,public_runner=runner)
'''


@pytest.mark.parametrize('external_change', [False, True])
def test_native_post_replacement_exit_restores_only_owned_edits_without_model_replay(tmp_path, external_change):
    public = freeze_example('config-binding', tmp_path)
    config = {**default_config('config-binding'), 'execution_backend': 'topomen_agent',
              'max_rounds': 1, 'run_id': 'native-edit-window',
              'topomen_source_root': str(Path(__file__).resolve().parents[1]/'vendor/topomen')}
    (tmp_path/'config.json').write_text(json.dumps(config))
    child = tmp_path/'native-write-child.py'; child.write_text(CHILD)
    result = subprocess.run([sys.executable, '-B', str(child), str(tmp_path)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 73, result.stderr + result.stdout
    run = tmp_path/'run'; source = run/'workspace/source/repo'
    saved = json.loads((run/'journal/recovery.identity.json').read_text())
    events = [json.loads(p.read_text()) for p in sorted((run/'journal/trials/round-0').glob('[0-9]*.json'))]
    assert 'EDIT_INTENT' in [event['kind'] for event in events]
    assert 'APPLIED' not in [event['kind'] for event in events]
    assert compute_snapshot(source).snapshot_id != saved['initial_snapshot']
    assert not list(source.rglob('.ce-*'))
    ledger = (run/'model_calls.json').read_bytes()

    class ForbiddenProvider:
        def complete_json(self, *args, **kwargs):
            raise AssertionError('Recovery must not replay a native model response')

    def resume():
        return repair_case(public, tmp_path/'tools', 'config-binding', run, ForbiddenProvider(), None,
                           config, public_runner=PublicExampleRunner(public, 'config-binding'), resume=True)

    if external_change:
        (source/'external.txt').write_text('unowned edit\n')
        before = compute_snapshot(source).snapshot_id
        with pytest.raises(ValueError, match='External workspace source change'):
            resume()
        assert compute_snapshot(source).snapshot_id == before
        assert (source/'external.txt').read_text() == 'unowned edit\n'
    else:
        report = resume()
        assert report['status'] == 'RECOVERED_INCUMBENT'
        assert report['final_snapshot_id'] == saved['initial_snapshot']
        assert report['candidate']['patch_bytes'] == 0
        assert report['last_feedback'] is None
        assert resume() == report
    assert (run/'model_calls.json').read_bytes() == ledger
