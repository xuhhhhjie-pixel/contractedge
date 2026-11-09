"""ContractGraphAgent end to end with mini-swe-agent's DeterministicModel and a local shell."""
import json
import os
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

minisweagent = pytest.importorskip('minisweagent')
from minisweagent.agents.default import DefaultAgent  # noqa: E402
from minisweagent.models.test_models import DeterministicModel  # noqa: E402

from conftest import git, run_tool  # noqa: E402
from contractedge.graph_repair.agent import ContractGraphAgent  # noqa: E402
from contractedge.graph_repair.config import VARIANTS  # noqa: E402

LIVE = Path(__file__).with_name('livesweagent.fixture.yaml')


@dataclass
class EnvConfig:
    timeout: int = 30


class LocalEnv:
    """Runs commands like DockerEnvironment would, with /testbed and /ce-state mapped to temp dirs."""

    def __init__(self, w):
        self.w, self.config, self.commands = w, EnvConfig(), []

    def execute(self, command, cwd='', *, timeout=None):
        self.commands.append(command)
        mapped = command.replace('/testbed', str(self.w.root)).replace('/ce-state', str(self.w.state))
        done = subprocess.run(['bash', '-c', mapped], cwd=self.w.root, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout or self.config.timeout,
                              env={**os.environ, **self.w.env})
        return {'output': done.stdout, 'returncode': done.returncode}

    def get_template_vars(self):
        return {}


def act(command):
    return f'THOUGHT: step\n\n```bash\n{command}\n```'


FIX = act("sed -i 's/broken/fixed/' /testbed/app.txt")
SUBMIT = act('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')


def agent_for(w, variant, outputs, **extra):
    import yaml
    from contractedge.graph_repair.prompts.build import agent_config
    cfg = {k: v for k, v in agent_config(LIVE.read_text(), VARIANTS[variant])['agent'].items()
           if k not in ('mode', 'confirm_exit', 'whitelist_actions')}
    cfg.update(step_limit=0, cost_limit=0)
    w.configure(variant)
    tree = git(w.root, 'rev-parse', w.base + '^{tree}')
    agent = ContractGraphAgent(DeterministicModel(outputs=outputs), LocalEnv(w), ce_variant=VARIANTS[variant],
                               ce_state=w.state, ce_wire_usage=None, ce_tool_timeout=60, ce_baseline_tree=tree,
                               **cfg, **extra)
    return agent


def run(agent):
    return agent.run('Fix app.txt so the public check passes.', system='Linux', release='', version='', machine='x86_64')


def events(w):
    return [json.loads(l) for l in (w.state / 'agent-events.jsonl').read_text().splitlines()]


def test_fix_then_submit_passes_the_stop_rule(workspace):
    agent = agent_for(workspace, 'full', [FIX, SUBMIT])
    status, _ = run(agent)
    assert status == 'Submitted' and agent.ce_forced is None
    assert len(agent.ce_checkpoints) == 1
    assert [e['event'] for e in events(workspace)].count('submit_check') == 1
    assert git(workspace.root, 'rev-parse', 'HEAD') == workspace.base


def test_unmet_items_are_returned_then_submit(workspace):
    agent = agent_for(workspace, 'full', [SUBMIT, FIX, SUBMIT])
    status, _ = run(agent)
    assert status == 'Submitted'
    blocked = [m['content'] for m in agent.messages if 'Submission not accepted yet' in m['content']]
    assert len(blocked) == 1 and '[public_check] PUBLIC' in blocked[0] and '[policy] PATCH' in blocked[0]


def test_repeated_unmet_forces_best_candidate(workspace):
    outputs = [FIX, act('ce-public'), act("sed -i 's/fixed/worse/' /testbed/app.txt"), SUBMIT, SUBMIT]
    agent = agent_for(workspace, 'full', outputs)
    status, message = run(agent)
    assert status == 'Submitted' and 'FORCED_SUBMISSION (REPEATED_UNMET)' in message
    assert (workspace.root / 'app.txt').read_text() == 'state=fixed\n'     # rolled back to the passing checkpoint
    assert any(e['event'] == 'force_rollback' and e['checkpoint'] == 1 for e in events(workspace))


def test_wall_clock_forces_submission_once_a_candidate_exists(workspace):
    agent = agent_for(workspace, 'full', [act('ls'), FIX, act('ls')])
    agent.ce_started = time.time() - 0.86 * 1800
    status, message = run(agent)
    assert status == 'Submitted' and 'WALL_85' in message
    names = [e['event'] for e in events(workspace)]
    assert names.index('force_declined') < names.index('forced_submission')
    assert agent.model.n_calls == 3


def test_attribution_is_appended_to_failed_public_checks(workspace):
    run_tool(workspace, 'ce-graph', 'add-node', '--role', 'PRODUCTION_CONSUMER', '--path', 'app.txt', '--span', '1-1', check=True)
    run_tool(workspace, 'ce-graph', 'add-node', '--role', 'CONFIG_DECLARATION', '--path', 'chart/values.yaml', '--span', '1-2', check=True)
    run_tool(workspace, 'ce-graph', 'add-edge', '--type', 'BINDING', '--from', 'N2', '--to', 'N1', '--status', 'VIOLATED', check=True)
    agent = agent_for(workspace, 'full', [act('ce-public'), FIX, SUBMIT])
    run(agent)
    observation = [m['content'] for m in agent.messages if 'PUBLIC_FAIL' in m['content']][0]
    assert '[ce-attribution]' in observation and 'app.txt:1 -> N1' in observation and 'E1 BINDING' in observation


def test_no_stop_rule_submits_like_live(workspace):
    agent = agent_for(workspace, 'no-stop-rule', [SUBMIT])
    status, _ = run(agent)
    assert status == 'Submitted' and not (workspace.state / 'submit-checks.jsonl').exists()


def strip(messages):
    return [(m['role'], m['content']) for m in messages]


def test_live_equivalent_matches_default_agent(workspace, tmp_path):
    outputs = [act('cat app.txt'), FIX, act('ce-public'), SUBMIT]
    graph_agent = agent_for(workspace, 'live-equivalent', outputs)
    status_a, sub_a = run(graph_agent)
    commands_a = list(graph_agent.env.commands)
    git(workspace.root, 'checkout', '--', 'app.txt')
    import yaml
    cfg = {k: v for k, v in yaml.safe_load(LIVE.read_text())['agent'].items()
           if k not in ('mode', 'confirm_exit', 'whitelist_actions')}
    cfg.update(step_limit=0, cost_limit=0)
    plain = DefaultAgent(DeterministicModel(outputs=outputs), LocalEnv(workspace), **cfg)
    status_b, sub_b = run(plain)
    assert (status_a, sub_a) == (status_b, sub_b)
    assert strip(graph_agent.messages) == strip(plain.messages)
    assert commands_a == plain.env.commands          # no checkpoint or check commands were added
    assert not (workspace.state / 'agent-events.jsonl').exists() or not events(workspace)


def test_token_budget_pressure_forces_submission(workspace, tmp_path):
    wire = tmp_path / 'usage.jsonl'
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    rows = [{'phase': 'request_started', 'request_started_utc': now}] + \
        [{'phase': 'request_finished', 'usage': {'total_tokens': 400_000}} for _ in range(11)]
    wire.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    agent = agent_for(workspace, 'full', [FIX, act('ls'), act('ls')])
    agent.ce_wire = wire
    status, message = run(agent)
    assert status == 'Submitted' and 'TOKEN_BUDGET' in message
    assert (workspace.root / 'app.txt').read_text() == 'state=fixed\n'
