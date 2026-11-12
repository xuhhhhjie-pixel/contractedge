"""Behaviour method inside ContractGraphAgent: the full chain, reminders, failures, budgets and the off path."""
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

minisweagent = pytest.importorskip('minisweagent')
from minisweagent.models.test_models import DeterministicModel  # noqa: E402

import conftest  # noqa: E402
from conftest import git  # noqa: E402
from contractedge.graph_repair import behavior  # noqa: E402
from contractedge.graph_repair.agent import ContractGraphAgent  # noqa: E402
from contractedge.graph_repair.behavior_loop import BehaviorLoop  # noqa: E402
from contractedge.graph_repair.config import VARIANTS, Variant  # noqa: E402
from contractedge.graph_repair.prompts.build import END, START, agent_config  # noqa: E402
from test_agent_integration import LIVE, LocalEnv, act, events  # noqa: E402

FIXTURES = Path(__file__).with_name('gotest_fixtures') / 'go1.26'
TASK = 'Fix calc.Add: Add must return the sum of its arguments.'
FIX = act("sed -i 's/a - b/a + b/' /testbed/calc/calc.go")
SUBMIT = act('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')
READ_TEST = act('cat /testbed/calc/calc_test.go')
REQUIREMENTS = json.dumps({'requirements': [
    {'kind': 'target', 'condition': 'two numbers are added', 'expected': 'Add returns their sum',
     'source_refs': [{'source': 'task', 'quote': 'Add must return the sum of its arguments'}]},
    {'kind': 'preserve', 'condition': 'any input', 'expected': 'Add never panics',
     'source_refs': [{'source': 'task', 'quote': 'Add never panics on any input'}]}]})
PLAN = '```json\n' + json.dumps({'checks': [{'obligations': ['O1'], 'scenario': 'sum', 'package': './calc',
                                             'run': '^TestAdd$'}], 'requirements': []}) + '\n```'


class WorkEnv(LocalEnv):
    """ce-run's runtime sees the work tree at /work."""

    def execute(self, command, cwd='', *, timeout=None):
        return super().execute(command.replace('/work', '/testbed'), cwd, timeout=timeout)


@pytest.fixture
def go_ws(monkeypatch, request):
    monkeypatch.setattr(conftest, 'CHART', {
        'calc/calc.go': 'package calc\n\nfunc Add(a, b int) int { return a - b }\n',
        'calc/calc_test.go': 'package calc\n\nimport "testing"\n\n// Add must return the sum of its arguments\n'
                             'func TestAdd(t *testing.T) {}\n',
        'README.md': 'demo\n'})
    w = request.getfixturevalue('workspace')
    fake_go = w.bin / 'go'
    fake_go.write_text(f'#!/bin/sh\nif grep -q "a + b" "$CE_WORKSPACE/calc/calc.go"; then cat {FIXTURES}/pass.out; exit 0; fi\n'
                       f'cat {FIXTURES}/fail.out; exit 1\n')
    fake_go.chmod(0o755)
    (w.bin / 'ce-public').write_text('#!/bin/sh\ngrep -q "a + b" "$CE_WORKSPACE/calc/calc.go" && echo PUBLIC_OK || '
                                     '{ echo PUBLIC_FAIL; exit 1; }\n')
    return w


def make(w, variant, outputs, **extra):
    cfg = {k: v for k, v in agent_config(LIVE.read_text(), variant)['agent'].items()
           if k not in ('mode', 'confirm_exit', 'whitelist_actions')}
    cfg.update(step_limit=0, cost_limit=0)
    (w.inputs / 'run.json').write_text(json.dumps({
        'variant': variant.to_json(), 'baseline_commit': w.base, 'tool_timeout_seconds': 60,
        'policy': {'patchable_paths': ['calc/**', 'chart/**', 'app.txt'], 'forbidden_paths': ['tests/**']}}))
    tree = git(w.root, 'rev-parse', w.base + '^{tree}')
    return ContractGraphAgent(DeterministicModel(outputs=outputs), WorkEnv(w), ce_variant=variant, ce_state=w.state,
                              ce_wire_usage=None, ce_tool_timeout=60, ce_baseline_tree=tree, **cfg, **extra)


def go(agent):
    return agent.run(TASK, system='Linux', release='', version='', machine='x86_64')


def notes(agent):
    return [m['content'] for m in agent.messages[2:] if m['role'] == 'user' and '[ce-behavior]' in m['content']]


def summary(agent):
    return json.loads((agent.ce_behavior_dir / 'behavior-summary.json').read_text())


def test_requirement_check_failure_fix_recheck_submit(go_ws):
    agent = make(go_ws, VARIANTS['behavior'], [REQUIREMENTS, act('cat /testbed/calc/calc.go'), READ_TEST, PLAN, FIX, SUBMIT])
    status, message = go(agent)
    assert status == 'Submitted' and '[ce-behavior]' not in message
    shown = notes(agent)
    assert 'O1 [target] when two numbers are added' in shown[0]
    assert 'Unresolved (no checked source, not used): O2' in shown[0]             # the invented quote is not used
    assert 'SEMANTIC_FAIL' in shown[1] and 'Next: REPAIR O1' in shown[1]           # reproduced on the original version
    assert len(shown) == 3 and 'Earlier results for O1 no longer apply after this edit' in shown[2]
    assert 'Check C1' in shown[2] and ': PASS (' in shown[2]                       # re-checked on the fixed version
    s = summary(agent)
    assert [(p['id'], p['calibrated']) for p in s['observed_pass']] == [('O1', True)]
    assert s['unresolved'] == ['O2'] and s['exit_status'] == 'Submitted'
    assert s['submitted_identity'] == agent.ce_last_tree
    assert agent.model.n_calls == 6 and agent.ce_steps == 4                       # 2 proposals, 4 steps, no extra ticks
    assert not any('Reply with one JSON object' in m['content'] for m in agent.messages)
    assert not str(agent.ce_behavior_dir).startswith(str(go_ws.state))           # not under the mounted /ce-state
    assert git(go_ws.root, 'status', '--porcelain') == 'M calc/calc.go'          # no state or logs in the patch


def test_unseen_failure_blocks_submission_once(go_ws):
    variant = replace(VARIANTS['behavior'], behavior_checks_per_tick=0)
    agent = make(go_ws, variant, [REQUIREMENTS, READ_TEST, PLAN, SUBMIT, SUBMIT, FIX, SUBMIT])
    status, _ = go(agent)
    assert status == 'Submitted'
    reminders = [m for m in agent.messages if 'Before submitting: checks run on this version' in m['content']]
    assert len(reminders) == 1 and 'O1' in reminders[0]['content'] and 'shown once' in reminders[0]['content']
    blocked = [m for m in agent.messages if 'Submission not accepted yet' in m['content']]
    assert len(blocked) == 1                                                       # second submit went to the stop rule
    assert [p['id'] for p in summary(agent)['observed_pass']] == ['O1']


def test_bad_proposal_reply_leaves_the_executor_running(go_ws):
    agent = make(go_ws, VARIANTS['behavior'], ['I think the requirement is obvious.', '', FIX, SUBMIT])
    status, _ = go(agent)
    assert status == 'Submitted' and agent.model.n_calls == 4                     # failed proposals are still counted
    rows = [json.loads(l) for l in (agent.ce_behavior_dir / 'behavior-events.jsonl').read_text().splitlines()]
    assert [(r['event'], r['status']) for r in rows[:2]] == [('proposal', 'RETRY'), ('proposal', 'PARSE_ERROR')]
    assert summary(agent)['observed_pass'] == [] and notes(agent) == []


def test_an_empty_proposal_reply_is_asked_once_more(go_ws):
    agent = make(go_ws, VARIANTS['behavior'], ['', REQUIREMENTS, act('cat /testbed/calc/calc.go'), FIX, PLAN, SUBMIT])
    assert go(agent)[0] == 'Submitted'
    assert 'O1 [target] when two numbers are added' in notes(agent)[0]


def test_transport_error_is_recorded_and_ignored(go_ws):
    class Failing(DeterministicModel):
        def query(self, messages, **kwargs):
            if messages[0]['content'].startswith('You extract'):
                raise TimeoutError('proposal timed out')
            return super().query(messages, **kwargs)
    agent = make(go_ws, VARIANTS['behavior'], [FIX, SUBMIT])
    agent.model = Failing(outputs=[FIX, SUBMIT])
    assert go(agent)[0] == 'Submitted'
    assert any(e['event'] == 'behavior_proposal' and e['status'] == 'ERROR' for e in events(go_ws))


def test_no_time_left_means_no_proposal(go_ws):
    agent = make(go_ws, VARIANTS['behavior'], [FIX, SUBMIT])
    agent.ce_started = time.time() - 1800 + 100                  # 100 s left, below reserve + proposal timeout
    status, message = go(agent)
    assert status == 'Submitted' and agent.model.n_calls == 2
    rows = [json.loads(l) for l in (agent.ce_behavior_dir / 'behavior-events.jsonl').read_text().splitlines()]
    assert rows[0]['status'] == 'BUDGET'


def test_corrupt_state_disables_the_method_without_resetting_it(go_ws, tmp_path):
    state_dir = tmp_path / 'host-behavior'
    state_dir.mkdir()
    (state_dir / 'behavior-state.json').write_text('{half written')
    agent = make(go_ws, VARIANTS['behavior'], [FIX, SUBMIT], ce_behavior_dir=state_dir)
    assert go(agent)[0] == 'Submitted' and agent.model.n_calls == 2
    assert agent.ce_behavior.disabled.startswith('STATE_ERROR')
    assert (state_dir / 'behavior-state.json').read_text() == '{half written'
    assert summary(agent)['disabled'].startswith('STATE_ERROR')


def test_unexpected_prepare_error_disables_the_method(go_ws, monkeypatch):
    def broken(self, task):
        raise RuntimeError('unexpected')
    monkeypatch.setattr(BehaviorLoop, 'prepare', broken)
    agent = make(go_ws, VARIANTS['behavior'], [FIX, SUBMIT])
    assert go(agent)[0] == 'Submitted' and agent.ce_behavior.disabled == 'PREPARE_ERROR: RuntimeError: unexpected'
    assert notes(agent) == [] and summary(agent)['disabled'].startswith('PREPARE_ERROR')


def test_requirements_only_shows_requirements_and_runs_nothing(go_ws):
    agent = make(go_ws, VARIANTS['behavior-requirements-only'], [REQUIREMENTS, READ_TEST, FIX, SUBMIT])
    assert go(agent)[0] == 'Submitted' and agent.model.n_calls == 4
    assert len(notes(agent)) == 1 and 'Matching existing tests' not in notes(agent)[0]
    assert not any('ce-run' in c for c in agent.env.commands)


def test_methods_off_create_no_behaviour_state(workspace):
    from test_agent_integration import FIX as APP_FIX, SUBMIT as APP_SUBMIT, agent_for, run
    agent = agent_for(workspace, 'submit-only', [APP_FIX, APP_SUBMIT])
    assert run(agent)[0] == 'Submitted' and agent.model.n_calls == 2
    assert agent.ce_behavior is None and not agent.ce_behavior_dir.exists()
    assert not any('ce-run' in c or 'behavior.index' in c for c in agent.env.commands)


def test_submission_output_is_never_annotated(go_ws, monkeypatch):
    seen = []
    monkeypatch.setattr(BehaviorLoop, 'prepare', lambda self, task: {'status': 'SKIPPED'})
    monkeypatch.setattr(BehaviorLoop, 'after_action', lambda self, command, *a: seen.append(command) or 'NOTE')
    agent = make(go_ws, VARIANTS['behavior'], [FIX, SUBMIT])
    status, message = go(agent)
    assert status == 'Submitted' and 'NOTE' not in message
    assert seen == ["sed -i 's/a - b/a + b/' /testbed/calc/calc.go"]


def test_best_candidate_uses_behaviour_rank_after_the_public_check(go_ws):
    agent = make(go_ws, VARIANTS['behavior'], [])
    agent.ce_checkpoints = [{'n': 1, 'tree': 'TA', 'changed': True}, {'n': 2, 'tree': 'TB', 'changed': True},
                            {'n': 3, 'tree': 'TC', 'changed': True}]
    (go_ws.state / 'results.json').write_text(json.dumps({'TC': {'public': {'exit_code': 0}}}))
    agent.ce_behavior = type('Rank', (), {'rank': staticmethod(lambda t: {'TA': (0, 2), 'TB': (-1, 3), 'TC': (-1, 0)}[t])})()
    assert agent._best()['tree'] == 'TC'                     # the public check still decides first
    (go_ws.state / 'results.json').write_text('{}')
    assert agent._best()['tree'] == 'TA'                     # then no confirmed regression beats more passes


def test_loop_counts_proposals_against_the_limit(tmp_path):
    calls = []

    class Model:
        def query(self, messages, **kwargs):
            calls.append(kwargs)
            return {'content': '{"requirements": []}'}
    loop = BehaviorLoop(replace(VARIANTS['behavior'], behavior_max_proposals=1), model=Model(),
                        run=lambda c, t: {'output': '', 'returncode': 1}, directory=tmp_path, remaining=lambda: 1000,
                        baseline_tree='T0', env_identity='img', log=lambda *a, **k: None)
    snap = behavior.empty()
    assert loop.propose(snap, 'requirements', 'x')['status'] == 'EMPTY'
    assert loop.propose(snap, 'check_plan', 'x')['status'] == 'EMPTY'      # requirements do not use the plan limit
    assert loop.propose(snap, 'check_plan', 'x')['status'] == 'BUDGET'
    assert calls == [{'timeout': 30}] * 2 and snap['counters'] == {'proposals': 2, 'check_plans': 1}


def test_prepare_reads_the_requirement_file_the_task_names(tmp_path):
    seen = []

    class Model:
        def query(self, messages, **kwargs):
            seen.append(messages[1]['content'])
            return {'content': json.dumps({'requirements': [
                {'kind': 'target', 'condition': 'repeated reconciliation', 'expected': 'stable external name',
                 'source_refs': [{'source': 'file', 'path': '/inputs/requirement.md',
                                  'quote': 'must not alternate between identifiers'}]}]})}
    files = {'/inputs/requirement.md': 'The `crossplane.io/external-name` value must not alternate between identifiers.\n'}

    def run(command, timeout):
        for path, text in files.items():
            if command.startswith('test -f ' + path):
                return {'output': text, 'returncode': 0}
        return {'output': '', 'returncode': 1}
    loop = BehaviorLoop(VARIANTS['behavior'], model=Model(), run=run, directory=tmp_path, remaining=lambda: 1000,
                        baseline_tree='T0', env_identity='img', log=lambda *a, **k: None)
    result = loop.prepare('Repair the repository.\nrequirement_file: requirement.md\nexecution_config: environment.json\n')
    assert result['delta']['added'] == ['O1'] and 'Requirement file /inputs/requirement.md' in seen[0]
    assert loop.store.load()['obligations']['O1']['source_refs'][0]['path'] == '/inputs/requirement.md'
    loop2 = BehaviorLoop(VARIANTS['behavior'], model=Model(), run=run, directory=tmp_path / 'b', remaining=lambda: 1000,
                         baseline_tree='T0', env_identity='img', log=lambda *a, **k: None)
    loop2.prepare('requirement_file: ../../etc/passwd\n')
    assert 'Requirement file' not in seen[1]


def test_prepare_offers_small_public_fixtures_and_grounds_their_quotes(tmp_path):
    seen = []
    trigger = '"external_name": "vpce-1/subnet-2"'
    control = '"resource": "aws_vpc_endpoint_security_group_association"'

    class Model:
        def query(self, messages, **kwargs):
            seen.append(messages[1]['content'])
            ref = lambda q: [{'source': 'file', 'path': '/inputs/inputs/replay-input.json', 'quote': q}]
            return {'content': json.dumps({'requirements': [
                {'kind': 'target', 'condition': 'tfstate id a-vpce-1', 'expected': 'external name vpce-1/subnet-2',
                 'source_refs': ref(trigger)},
                {'kind': 'preserve', 'condition': 'security group association', 'expected': 'external name is the id',
                 'source_refs': ref(control)},
                {'kind': 'target', 'condition': 'x', 'expected': 'y', 'source_refs': ref('"not": "in the fixture"')}]})}
    files = {'/inputs/requirement.md': 'The external name must not alternate.\n',
             '/inputs/inputs/replay-input.json': '{"trigger": {' + trigger + '},\n "control": {' + control + '}}\n',
             '/inputs/task.yaml': 'requirement_file: requirement.md\n', '/inputs/environment.json': '{}',
             '/inputs/gomod.tar.gz': 'BINARY'}

    def run(command, timeout):
        if command.startswith('find /inputs'):
            return {'output': '\n'.join(sorted(files)) + '\n/testbed/x.json\n', 'returncode': 0}
        for path, text in files.items():
            if command.startswith('test -f ' + path):
                return {'output': text, 'returncode': 0}
        return {'output': '', 'returncode': 1}
    loop = BehaviorLoop(VARIANTS['behavior'], model=Model(), run=run, directory=tmp_path, remaining=lambda: 1000,
                        baseline_tree='T0', env_identity='img', log=lambda *a, **k: None)
    result = loop.prepare('Repair it.\nrequirement_file: requirement.md\n')
    assert 'Public input file /inputs/inputs/replay-input.json' in seen[0]
    for skipped in ('task.yaml', 'environment.json', 'gomod.tar.gz', '/testbed/x.json', 'Public input file /inputs/requirement.md'):
        assert skipped not in seen[0]
    assert result['delta']['added'] == ['O1', 'O2'] and result['delta']['unresolved'] == ['O3']
    obligations = loop.store.load()['obligations']
    assert obligations['O2']['kind'] == 'preserve' and obligations['O1']['source_refs'][0]['line'] == 1


@pytest.mark.parametrize('command, files', [
    ("find controllers -name '*.go' ! -name '*_test.go'", []),
    ('grep -l Reconcile *_test.go', []),
    ('nl -ba controllers/keda/hpa_test.go | sed -n 1,40p', ['controllers/keda/hpa_test.go']),
    ('cat /testbed/calc/calc_test.go ./a.b/c_test.go', ['/testbed/calc/calc_test.go', './a.b/c_test.go']),
])
def test_only_named_test_files_are_taken_from_commands(command, files):
    from contractedge.graph_repair.behavior_loop import TEST_FILE
    assert TEST_FILE.findall(command) == files


def test_an_unchanged_plan_context_is_not_asked_again(tmp_path):
    asked = []

    class Model:
        def query(self, messages, **kwargs):
            asked.append(messages[0]['content'][:12])
            return {'content': '{"checks": [], "requirements": []}'}
    loop = BehaviorLoop(VARIANTS['behavior'], model=Model(), run=lambda c, t: {'output': '', 'returncode': 1},
                        directory=tmp_path, remaining=lambda: 1000, baseline_tree='T0', env_identity='img',
                        log=lambda *a, **k: None)
    snap = behavior.empty()
    behavior.apply_proposals(snap, [{'kind': 'target', 'condition': 'c', 'expected': 'Add returns the sum',
                                     'source_refs': [{'source': 'task', 'quote': 'Add returns the sum'}]}],
                             behavior.Sources('Add returns the sum', lambda p: None), 'task')
    snap['packages'] = {'./calc': ['TestAdd']}
    loop._plan_checks(snap, ['./calc'], None)
    loop._plan_checks(snap, ['./calc'], None)                   # same tests, same open requirement
    snap['packages']['./other'] = ['TestOther']
    loop._plan_checks(snap, ['./other'], None)                  # a new test is offered
    assert len(asked) == 2 and snap['counters']['check_plans'] == 2


@pytest.mark.parametrize('fields, message', [
    ({'behavior_feedback': True}, 'needs behavior_requirements'),
    ({'behavior_impact': True, 'behavior_requirements': True}, 'not implemented'),
    ({'behavior_requirements': True, 'checkpoint': False}, 'need checkpoint'),
    ({'behavior_requirements': True, 'closure': True}, "turn off ['closure']"),
])
def test_invalid_switch_combinations_are_refused(fields, message):
    base = {**VARIANTS['submit-only'].to_json(), 'name': 'x', **fields}
    with pytest.raises(ValueError, match=message.replace('[', r'\[').replace(']', r'\]')):
        Variant.from_json(base)


def test_behaviour_variants_add_only_their_switches():
    base, method = VARIANTS['submit-only'].to_json(), VARIANTS['behavior'].to_json()
    assert {k for k in base if base[k] != method[k]} == {'name', 'behavior_requirements', 'behavior_feedback'}
    assert VARIANTS['behavior'].any_mechanism and not VARIANTS['live-equivalent'].any_mechanism


def test_prompt_keeps_live_workflow_and_adds_a_section():
    live = agent_config(LIVE.read_text(), VARIANTS['submit-only'])['agent']['instance_template']
    method = agent_config(LIVE.read_text(), VARIANTS['behavior'])['agent']['instance_template']
    assert live == agent_config(LIVE.read_text(), VARIANTS['live-equivalent'])['agent']['instance_template']
    assert method.replace(method[method.index('## Behaviour requirements'):method.index(END)], '') == live
    assert method.index(START) < method.index('## Behaviour requirements') < method.index(END)
    assert 'cross-artifact' not in method
