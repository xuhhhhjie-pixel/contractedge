"""Shared noise fixes: cleaning a forced submission, reading DSML actions, the persistent public build cache."""
import json
import time
from dataclasses import replace

import pytest

minisweagent = pytest.importorskip('minisweagent')

from conftest import git, run_tool  # noqa: E402
from contractedge.graph_repair.config import VARIANTS  # noqa: E402
from test_agent_integration import FIX, act, agent_for, events, run  # noqa: E402


def test_clean_restores_and_deletes_what_the_rules_reject(workspace):
    w = workspace
    w.configure('submit-only')
    (w.root / 'app.txt').write_text('state=fixed\n')                       # allowed change stays
    (w.root / 'tests/app_test.txt').write_text('edited\n')                 # forbidden, existed: restored
    (w.root / '.probe').mkdir()
    (w.root / '.probe/main.go').write_text('package main\n')               # outside patchable, new: deleted
    (w.root / 'probe_diff.go').write_text('package x\n')
    done = run_tool(w, 'ce-submit', '--clean', '--json', check=True)
    value = json.loads(done.stdout.split('CE_SUBMIT_CLEAN_JSON=', 1)[1])
    assert value['restored'] == ['tests/app_test.txt']
    assert sorted(value['removed']) == ['.probe/main.go', 'probe_diff.go']
    assert git(w.root, 'status', '--porcelain') == 'M app.txt'
    done = run_tool(w, 'ce-submit', '--check', '--json')
    assert '"passed": true' in done.stdout


def test_forced_submission_is_cleaned_only_when_switched_on(workspace, monkeypatch):
    # The wall-clock check runs before each action, so the fix and the scratch file come in one step.
    fix_and_scratch = act("sed -i 's/broken/fixed/' /testbed/app.txt && mkdir -p /testbed/.probe && "
                          'echo x > /testbed/.probe/main.go')
    for name, expect in (('submit-only', True), ('submit-only-clean', False)):
        w = workspace
        if name == 'submit-only-clean':
            monkeypatch.setitem(VARIANTS, name, replace(VARIANTS['submit-only'], name=name, force_clean=True))
        agent = agent_for(w, name, [fix_and_scratch, act('ls')])
        agent.ce_started = time.time() - 0.86 * 1800
        status, message = run(agent)
        assert status == 'Submitted' and 'WALL_85' in message
        assert (w.root / '.probe/main.go').exists() == expect
        git(w.root, 'checkout', '--', '.')
        git(w.root, 'clean', '-fdq')
    assert any(e['event'] == 'force_clean' and e['result']['removed'] == ['.probe/main.go'] for e in events(w))


DSML = ('<｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name="bash"> <｜｜DSML｜｜ parameter name="command" string="true">'
        "sed -i 's/broken/fixed/' /testbed/app.txt</｜｜DSML｜｜ parameter> </｜｜DSML｜｜ invoke>")


def test_dsml_actions_are_read_only_when_switched_on(workspace, monkeypatch):
    submit = act('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
    agent = agent_for(workspace, 'submit-only', [DSML, FIX, submit])
    assert run(agent)[0] == 'Submitted'
    assert any('EXACTLY ONE' in m['content'] or 'format' in m['content'].lower() for m in agent.messages
               if m['role'] == 'user')                                     # off: a format error, as in Live
    git(workspace.root, 'checkout', '--', '.')
    monkeypatch.setitem(VARIANTS, 'salvage', replace(VARIANTS['submit-only'], name='salvage', salvage_dsml=True))
    agent = agent_for(workspace, 'salvage', [DSML, submit])
    assert run(agent)[0] == 'Submitted' and agent.model.n_calls == 2
    assert (workspace.root / 'app.txt').read_text() == 'state=fixed\n'
    assert any(e['event'] == 'dsml_salvaged' for e in events(workspace))


def test_live_equivalent_keeps_both_fixes_off():
    v = VARIANTS['live-equivalent']
    assert not v.force_clean and not v.salvage_dsml


def test_public_cache_copy_moves_only_the_build_cache(tmp_path):
    from contractedge.graph_repair.harness.behavior_runtime_check import CACHE_FROM, CACHE_TO, persistent_public_cache
    tools = tmp_path / 'tools'
    (tools / 'scorer').mkdir(parents=True)
    for name in ('executor.py', 'isolation.py'):
        (tools / 'scorer' / name).write_text(f'ARGS = ["--tmpfs=/work/.cache:rw", "{CACHE_FROM}"]\n')
    (tools / 'other.py').write_text('x = 1\n')
    copy = persistent_public_cache(str(tools), tmp_path / 'scratch')
    for name in ('executor.py', 'isolation.py'):
        text = (tmp_path / 'scratch/public-tools/scorer' / name).read_text()
        assert CACHE_TO in text and CACHE_FROM not in text
        assert CACHE_FROM in (tools / 'scorer' / name).read_text()        # the case package is untouched
    assert copy.endswith('public-tools') and (tmp_path / 'scratch/public-tools/other.py').exists()
