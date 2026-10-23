"""All four production intents retain C2 authority and reject cross-mode resume."""
import json
import subprocess
import sys

import pytest

from contractedge.development.method_panel import panel_config
from contractedge.development.method_panel_fixtures import MODES, PanelRunner, freeze_panel_case, case_definition
from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot


CHILD = r'''import json,os,sys
from pathlib import Path
from contractedge.development.method_panel import PanelProvider
from contractedge.development.method_panel_fixtures import PanelRunner
from contractedge.development.public_demo import count_tokens
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration.trial import TrialSession
root=Path(sys.argv[1]);mode=sys.argv[2];config=json.loads(sys.argv[3])
public=root/'frozen-public/public';runner=PanelRunner(public,'config-binding/base')
original=TrialSession.record
def record(self,kind,**payload):
 event=original(self,kind,**payload)
 if kind=='RESPONSE_DURABLE':os._exit(73)
 return event
TrialSession.record=record
repair_case(public,root/'unused',runner.case,root/'repair',PanelProvider('config-binding/base',mode,runner),count_tokens,config,public_runner=runner)
'''


@pytest.mark.parametrize('mode', MODES)
def test_every_mode_rejects_other_modes_then_consumes_its_own_received_bytes_once(tmp_path, mode):
    public = freeze_panel_case('config-binding/base', tmp_path)
    runner = PanelRunner(public, 'config-binding/base')
    config = panel_config(mode)
    script = tmp_path / 'interrupt.py'; script.write_text(CHILD)
    result = subprocess.run([sys.executable, '-B', str(script), str(tmp_path), mode, json.dumps(config)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 73, result.stderr + result.stdout
    root = tmp_path / 'repair'; source = root / 'workspace/source/repo'
    ledger = (root / 'model_calls.json').read_bytes(); snapshot = compute_snapshot(source).snapshot_id
    expected_count = {'full': 3, 'without_relation': 2, 'without_changeset': 2, 'direct': 1}[mode]
    assert len(json.loads(ledger)) == expected_count
    events = lambda: [json.loads(p.read_text()) for p in sorted((root / 'journal/trials/round-0').glob('[0-9]*.json'))]
    authority = next(e['authority'] for e in events() if e['kind'] == 'AUTHORITY_DURABLE')
    assert authority['intent_kind'] == {'full': 'SEMANTIC_CHANGESET', 'without_relation': 'DIRECT_PLAN',
        'without_changeset': 'RELATION_ONLY', 'direct': 'REQUIREMENT_ONLY'}[mode]
    class Forbidden:
        def complete_json(self, *args):
            raise AssertionError('Recovery must never dispatch a model request')
    for other in MODES:
        if other == mode:
            continue
        with pytest.raises(ValueError, match='identity differs'):
            repair_case(public, tmp_path / 'unused', runner.case, root, Forbidden(), None,
                {**config, 'method_mode': other}, public_runner=runner, resume=True, consume_received_response=True)
        assert (root / 'model_calls.json').read_bytes() == ledger
        assert compute_snapshot(source).snapshot_id == snapshot
        assert not any(e['kind'] == 'RESPONSE_CONSUME_INTENT' for e in events())
    report = repair_case(public, tmp_path / 'unused', runner.case, root, Forbidden(), None,
        config, public_runner=runner, resume=True, consume_received_response=True)
    assert report['status'] == 'RECOVERED_INCUMBENT'
    assert report['candidate']['patch_bytes'] == 0 and report['recovery']['dispatches'] == 0
    assert report['budget']['model_calls'] == expected_count
    assert (root / 'model_calls.json').read_bytes() == ledger
    assert [e['kind'] for e in events()].count('RESPONSE_CONSUME_INTENT') == 1
    assert [e['kind'] for e in events()].count('RESPONSE_CONSUME_COMPLETED') == 1
    assert [e['kind'] for e in events()].count('ROLLBACK_COMPLETED') == 1
    assert repair_case(public, tmp_path / 'unused', runner.case, root, Forbidden(), None,
        config, public_runner=runner, resume=True, consume_received_response=True) == report
