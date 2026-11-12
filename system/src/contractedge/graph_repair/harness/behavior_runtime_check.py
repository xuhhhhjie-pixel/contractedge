"""Model-free check of the behaviour method in a real case runtime.

Copies a prepared case's smoke workspace into SCRATCH, starts the case's own public runtime
server (so ce-run / ce-public run in the published runtime image) and a method container with
the mounts run_native.graph() uses (network none), then either

  raw CMD...   runs commands in the method container and prints their results, or
  agent SPEC   runs ContractGraphAgent with a scripted model: proposal calls are answered from
               SPEC['proposals'][purpose], executor steps from SPEC['actions'] in order.
  real SPEC    runs ContractGraphAgent with the real model through the controller's metered transport
               (deepseek_transport: same model, request/token limits and usage ledger as run_native.graph());
               SPEC gives the variant and overrides only. Paid; billed into SCRATCH/billing_summary.json.

raw and agent make no model request. The experiment root and the case are only read; everything
written goes to SCRATCH and the per-run socket directory, and both containers are removed at the end.
Usage: python behavior_runtime_check.py ACTIVE_CASE_DIR EXPERIMENT_ROOT SCRATCH {raw CMD...|agent SPEC.json|real SPEC.json}
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

P = Path('/tmp/contractedge-deepseek-preflight-20261019')
SRC = Path(__file__).resolve().parents[3]          # system/src
CREDENTIALS = '/home/zyw/.config/contractedge-deepseek-official/api.env'
MODEL, BASE = 'deepseek-flash', 'https://api.deepseek.com/v1'      # as run_native.graph()


CACHE_FROM, CACHE_TO = '--env=GOCACHE=/work/.cache/gocache', '--env=GOCACHE=/work/.contractedge-deps/gocache'


def persistent_public_cache(tools_root: str, scratch: Path) -> str:
    """A copy of the case's public runtime tools whose Go build cache survives between ce-run calls.

    The published runtime points GOCACHE at a tmpfs dropped after every command (a cold build of
    provider-upjet-aws takes about 250 s); the copy points it at the work tree's git-excluded
    dependency directory instead. A shared environment fix: use it for every compared group alike."""
    copy = scratch / 'public-tools'
    shutil.copytree(tools_root, copy, symlinks=True)
    for name in ('scorer/executor.py', 'scorer/isolation.py'):
        path = copy / name
        text = path.read_text()
        assert text.count(CACHE_FROM) == 1, f'{name}: expected one GOCACHE setting'
        path.chmod(0o644)                     # package files are read-only; copytree keeps their mode
        path.write_text(text.replace(CACHE_FROM, CACHE_TO))
    return str(copy)


def setup(active: Path, root: Path, scratch: Path, public_cache: bool = False) -> dict:
    config = json.loads((active / 'case-config.json').read_text())
    ready = json.loads((active.parent / 'runtime/READY.json').read_text())
    template = json.loads((active / 'smoke/contractedge-graph/profile.json').read_text())
    assert not scratch.exists(), 'use a fresh scratch directory'
    scratch.mkdir(parents=True)
    os.environ.update(DOCKER_HOST=ready['socket'], TMPDIR=str(scratch / 'tmp'),
                      PATH=str(Path(ready['docker']).parent) + ':' + str(P / 'venv312/bin') + ':' + os.environ['PATH'])
    (scratch / 'tmp').mkdir()
    source = scratch / 'source'
    subprocess.run(['cp', '-a', template['source_root'], str(source)], check=True)
    for mount in template['public_dependency_mounts']:
        (source / mount['target'].replace('/work/', '', 1)).mkdir(parents=True, exist_ok=True)
    with (source / '.git/info/exclude').open('a') as exclude:
        exclude.write('\n/.contractedge-deps/\n/.ce75-public-logs/\n')
    subprocess.run(['chmod', '-R', 'a+rwX', str(source)], check=True)
    run_id = 'bhvchk-' + uuid.uuid4().hex[:8]
    socket = Path('/run/user') / str(os.getuid()) / 'cep5-public' / run_id
    socket.mkdir(parents=True)
    socket.chmod(0o755)
    task = scratch / 'task.txt'
    task.write_bytes(Path(template['task_path']).read_bytes())
    profile = {**template, 'source_root': str(source), 'task_path': str(task), 'public_logs': str(scratch / 'public-logs'),
               'public_socket_dir': str(socket), 'public_container_prefix': run_id + '-public-'}
    if public_cache:
        profile['tools_root'] = persistent_public_cache(template['tools_root'], scratch)
    (scratch / 'profile.json').write_text(json.dumps(profile, indent=2))
    tools = scratch / 'ce-tools'
    subprocess.run([sys.executable, str(SRC / 'contractedge/graph_repair/harness/package_tools.py'), str(SRC), str(tools)],
                   check=True, capture_output=True)
    for name in ('ce-state', 'ce-inputs', 'ce-behavior'):
        (scratch / name).mkdir()
    (scratch / 'ce-state').chmod(0o777)
    server_log = (scratch / 'public-server.log').open('wb')
    server = subprocess.Popen([str(P / 'venv312/bin/python3'), str(root / 'controller/public_runtime_server.py'),
                               '--profile', str(scratch / 'profile.json')], stdout=server_log, stderr=subprocess.STDOUT,
                              start_new_session=True)
    for _ in range(200):
        if (socket / 'runner.sock').exists():
            break
        assert server.poll() is None, 'public server exited; see public-server.log'
        time.sleep(0.1)
    else:
        raise RuntimeError('public server did not start')
    mounts = {str(source): ('/testbed', 'rw'), template['inputs_root']: ('/inputs', 'ro'),
              str(socket): ('/ce-public', 'ro'), config['public_client']: ('/usr/local/bin/ce-run', 'ro'),
              str(tools.resolve()): ('/ce-tools', 'ro'), str(scratch / 'ce-inputs'): ('/ce-inputs', 'ro'),
              str(scratch / 'ce-state'): ('/ce-state', 'rw')}
    for folder in ('venv312', 'python', 'repos'):
        mounts[str((P / folder).resolve())] = (str(P / folder), 'ro')
    for item in template['public_dependency_mounts']:
        mounts[item['source']] = (item['target'].replace('/work/', '/testbed/', 1), 'rw' if item.get('writable') else 'ro')
    settings = config['graph_repair']
    for name in settings['commands']:
        mounts[str((tools / 'bin' / name).resolve())] = ('/usr/local/bin/' + name, 'ro')
    resources = config['native_resources']
    # --rm: there is no supervisor here to remove the container by its telemetry label.
    args = ['--rm', '--network', 'none', f"--memory={resources['memory_bytes']}", f"--cpus={resources['cpus']}",
            f"--pids-limit={resources['pids']}", '--security-opt=no-new-privileges:true', '-e', 'PAGER=cat',
            '-e', 'GIT_PAGER=cat']
    for src, (bind, mode) in mounts.items():
        args += ['-v', f'{src}:{bind}:{mode}']
    return {'config': config, 'profile': profile, 'server': server, 'server_log': server_log, 'socket': socket,
            'run_args': args, 'image': config['method_images']['contractedge-graph'], 'tools': tools}


def teardown(ctx: dict, env) -> None:
    if env is not None:
        env.cleanup()
    server = ctx['server']
    if server.poll() is None:
        os.killpg(server.pid, 15)
        server.wait(timeout=30)
    ctx['server_log'].close()
    (ctx['socket'] / 'runner.sock').unlink(missing_ok=True)
    shutil.rmtree(ctx['socket'], ignore_errors=True)


def environment(ctx: dict, live_yaml: str):
    import yaml
    from minisweagent.environments.docker import DockerEnvironment
    env = DockerEnvironment(image=ctx['image'], cwd='/testbed', run_args=ctx['run_args'], timeout=120,
                            env=yaml.safe_load(live_yaml)['environment']['env'])
    limit = ctx['config']['tool_timeout_seconds']
    original = env.execute

    def execute(command, cwd='', *, timeout=None):
        # Same rule as run_native.graph(): public commands get the case tool timeout.
        if timeout is None and re.search(r'(?<![\w./-])(?:/usr/local/bin/)?ce-(?:public|run|probe|submit)(?![\w-])', command):
            timeout = limit
        return original(command, cwd, timeout=timeout)
    env.execute = execute
    return env


class ScriptedModel:
    """Answers behaviour proposals by purpose and executor steps in order; counts like a model."""

    def __init__(self, spec: dict):
        self.proposals, self.actions = spec['proposals'], list(spec['actions'])
        self.n_calls, self.cost, self.asked = 0, 0.0, []

    def query(self, messages, **kwargs):
        self.n_calls += 1
        system = messages[0]['content']
        if system.startswith('You extract'):
            self.asked.append('requirements')
            return {'content': json.dumps(self.proposals.get('requirements', {}))}
        if system.startswith('You map'):
            self.asked.append('check_plan')
            return {'content': json.dumps(self.proposals.get('check_plan', {}))}
        self.asked.append('action')
        if not self.actions:
            return {'content': 'THOUGHT: done\n\n```bash\necho COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n```'}
        return {'content': 'THOUGHT: scripted step\n\n```bash\n' + self.actions.pop(0) + '\n```'}

    def get_template_vars(self):
        return {'n_model_calls': self.n_calls, 'model_cost': self.cost}


def real_model(root: Path, scratch: Path):
    """The controller's model wiring: litellm through the metered DeepSeek transport (no key in this process' code)."""
    sys.path[:0] = [str(root / 'controller/controller'), str(root / 'controller')]
    run_id = 'bhvreal-' + uuid.uuid4().hex[:8]
    (scratch / 'coordination').mkdir()          # request settlement of this run only, not the experiment root's
    os.environ.update(CE_PREFLIGHT_CREDENTIAL_FILE=CREDENTIALS, CE_PRICING_FILE=str(root / 'controller/pricing.json'),
                      CE_API_COORDINATION=str(scratch / 'coordination'),
                      CE_RUN_ID=run_id, CE_API_USER_ID=run_id + '-real', OPENAI_API_KEY='task-local-adapter-no-upstream-secret',
                      LITELLM_LOG='ERROR')
    from deepseek_transport import AdapterError, install
    adapter = install(scratch / 'wire')
    from minisweagent.models import get_model
    from minisweagent.models.litellm_model import LitellmModel
    from tenacity import retry_if_not_exception_type
    LitellmModel._query.retry.retry &= retry_if_not_exception_type(AdapterError)
    cfg = {'model_name': 'openai/' + MODEL, 'cost_tracking': 'ignore_errors',
           'model_kwargs': {'api_key': 'task-local-adapter-no-upstream-secret', 'api_base': BASE, 'max_tokens': 16384}}
    return get_model(cfg['model_name'], cfg), adapter


def bill(root: Path, scratch: Path) -> dict:
    usage = scratch / 'wire/usage.jsonl'
    if not usage.exists():
        return {}
    from billing import build
    build(usage, root / 'controller/pricing.json', scratch)
    rows = [json.loads(line) for line in usage.read_text().splitlines() if line.strip()]
    summary = json.loads((scratch / 'billing_summary.json').read_text())
    return {'cost_cny': summary.get('calculated_cost'), 'requests': sum(r.get('phase') == 'request_started' for r in rows),
            'tokens': sum(r.get('usage', {}).get('total_tokens', 0) for r in rows if r.get('phase') == 'request_finished')}


def main(active: str, root: str, scratch: str, mode: str, *rest: str) -> int:
    active, root, scratch = Path(active), Path(root), Path(scratch)
    spec = json.loads(Path(rest[0]).read_text()) if mode in ('agent', 'real', 'extract') else {}
    ctx = setup(active, root, scratch, public_cache=bool(spec.get('public_cache')) or os.environ.get('BHV_PUBLIC_CACHE') == '1')
    sys.path.insert(0, str(ctx['tools'] / 'lib'))
    live_yaml = (P / 'repos/live-swe-agent/config/livesweagent.yaml').read_text()
    env = None
    try:
        env = environment(ctx, live_yaml)
        if mode == 'raw':
            for command in rest:
                began = time.monotonic()
                result = env.execute(command)
                print(json.dumps({'command': command, 'seconds': round(time.monotonic() - began, 1),
                                  'returncode': result.get('returncode'), 'output_tail': result.get('output', '')[-3000:]}),
                      flush=True)
            return 0
        from contractedge.graph_repair.agent import ContractGraphAgent
        from contractedge.graph_repair.config import variant as load_variant
        from contractedge.graph_repair.prompts.build import agent_config
        from dataclasses import fields, replace
        from minisweagent.agents.default import AgentConfig
        v = replace(load_variant(spec.get('variant', 'behavior')), **spec.get('variant_overrides', {}))
        policy = json.loads((active / 'PUBLIC_SUBMISSION_POLICY.json').read_text())['public']
        (scratch / 'ce-inputs/run.json').write_text(json.dumps({
            'variant': v.to_json(), 'baseline_commit': ctx['profile']['baseline_commit'],
            'policy': {k: policy[k] for k in ['patchable_paths', 'forbidden_paths']},
            'tool_timeout_seconds': ctx['config']['tool_timeout_seconds']}, indent=2))
        cfg = agent_config(live_yaml, v)['agent']
        allowed = {f.name for f in fields(AgentConfig)}
        cfg = {k: val for k, val in cfg.items() if k in allowed}
        cfg.update(step_limit=0, cost_limit=0)
        tree = env.execute('git -c safe.directory=* rev-parse ' + ctx['profile']['baseline_commit'] + '^{tree}')
        assert tree['returncode'] == 0, tree
        if mode == 'extract':
            # Extraction only: the prepare() proposal with the real model on this case's public material. Nothing
            # is edited and no driver runs; used to compare extraction prompts offline.
            from contractedge.graph_repair.behavior_loop import BehaviorLoop
            model, _ = real_model(root, scratch)

            def run(command, timeout):
                try:
                    return env.execute(command, timeout=max(5, int(timeout)))
                except Exception as exc:
                    return {'output': str(exc), 'returncode': 124}
            loop = BehaviorLoop(v, model=model, run=run, directory=scratch / 'ce-behavior', remaining=lambda: v.wall_seconds,
                                baseline_tree=tree['output'].strip(), env_identity='extract', log=lambda *a, **k: None)
            began = time.monotonic()
            loop.prepare(Path(ctx['profile']['task_path']).read_text())
            snap = loop.store.load()
            result = {'seconds': round(time.monotonic() - began, 1), 'requirements': snap.get('obligations'),
                      'examples': snap.get('examples'), **bill(root, scratch)}
            (scratch / 'EXTRACT.json').write_text(json.dumps(result, indent=1, ensure_ascii=False))
            print(json.dumps({k: result[k] for k in ('seconds', 'cost_cny')}, default=str), flush=True)
            return 0
        if mode == 'real':
            model, _ = real_model(root, scratch)
            wire = scratch / 'wire/usage.jsonl'
        else:
            model, wire = ScriptedModel(spec), None
        agent = ContractGraphAgent(model, env, ce_variant=v, ce_state=scratch / 'ce-state', ce_wire_usage=wire,
                                   ce_tool_timeout=ctx['config']['tool_timeout_seconds'],
                                   ce_baseline_tree=tree['output'].strip(), ce_behavior_dir=scratch / 'ce-behavior',
                                   ce_behavior_gold=spec.get('gold'), **cfg)
        began = time.monotonic()
        try:
            status, message = agent.run(Path(ctx['profile']['task_path']).read_text(), system='Linux', release='',
                                        version='', machine='x86_64')
        except Exception as exc:               # e.g. the transport's budget stop; keep the evidence
            status, message = type(exc).__name__, str(exc)
        patch = env.execute('cd /testbed && git -c safe.directory=* status --porcelain && git -c safe.directory=* diff --stat')
        baseline = ctx['profile']['baseline_commit']
        diff = env.execute(f'cd /testbed && git -c safe.directory=* add -N . && git -c safe.directory=* diff --binary {baseline}')
        (scratch / 'candidate.patch').write_text(diff.get('output', ''))
        result = {'status': status, 'message': message[-500:], 'seconds': round(time.monotonic() - began, 1),
                  'model_calls': model.n_calls, 'asked': getattr(model, 'asked', None), 'forced': agent.ce_forced,
                  'checkpoints': len(agent.ce_checkpoints), 'worktree': patch.get('output', '')[-800:]}
        if mode == 'real':
            result.update(bill(root, scratch))
        (scratch / 'trajectory.json').write_text(json.dumps(agent.messages, indent=1, default=str))
        (scratch / 'RESULT.json').write_text(json.dumps(result, indent=1, default=str))
        print(json.dumps(result, default=str), flush=True)
        return 0
    finally:
        teardown(ctx, env)


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
