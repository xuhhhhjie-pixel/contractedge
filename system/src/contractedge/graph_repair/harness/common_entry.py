"""Formal local entry for the common Live loop and its current-source delivery."""
import argparse
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import minisweagent
from minisweagent.agents.default import AgentConfig

from contractedge.dataset.benchmark import checked_public_file, editable_policy, file_sha, public_task
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.graph_repair import agent
from contractedge.graph_repair.config import variant
from contractedge.graph_repair.prompts.build import agent_config
from contractedge.graph_repair.state import write_json
from contractedge.graph_repair.harness import common_model, common_runtime
from contractedge.graph_repair.harness.common_runtime import remaining


def tool_identity(root):
    files = {}
    for path in sorted(root.rglob('*')):
        parts = path.relative_to(root).parts
        if '__pycache__' in parts or path.suffix == '.pyc':
            continue
        if path.is_symlink():
            raise ValueError('Public tools contain a symlink')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = file_sha(path)
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def load_profile(path):
    profile = json.loads(path.read_text())
    if profile['schema'] != 'contractedge.common_live_local.v1':
        raise ValueError('Unsupported common entry profile')
    for group, names in {
        'model': ('request_seconds', 'max_output_tokens'),
        'budget': ('calls', 'tokens', 'wall_seconds', 'finalization_seconds'),
        'tools': ('public_seconds', 'shell_seconds'),
        'resources': ('cpus', 'memory_bytes', 'pids'),
    }.items():
        for name in names:
            value = profile[group][name]
            if type(value) is not int or value <= 0:
                raise ValueError(f'{group}.{name} must be a positive integer')
    if profile['budget']['finalization_seconds'] >= profile['budget']['wall_seconds']:
        raise ValueError('The action window must leave a finalization reserve')
    if not isinstance(profile['model']['id'], str) or not profile['model']['id'].strip():
        raise ValueError('An explicit model ID is required')
    temperature = profile['model']['temperature']
    if type(temperature) not in (int, float) or not math.isfinite(temperature) or temperature < 0:
        raise ValueError('Invalid model temperature')
    if not re.fullmatch('sha256:[0-9a-f]{64}', profile['method_image']):
        raise ValueError('The method container image ID must be pinned')
    if profile['pricing']['usage_kind'] != 'SYNTHETIC_LOCAL':
        raise ValueError('The local profile must label usage as synthetic')
    return profile


def docker_local(deadline):
    selected = os.environ.get('DOCKER_HOST')
    if not selected or not selected.startswith('unix:///') or os.environ.get('DOCKER_CONTEXT'):
        raise ValueError('Select the local rootless Docker socket explicitly via DOCKER_HOST')
    if not Path(selected.removeprefix('unix://')).is_socket():
        raise ValueError('The selected local Docker socket does not exist')
    result = subprocess.check_output(['docker', 'info', '--format', '{{json .SecurityOptions}}'],
                                     timeout=min(10, remaining(deadline)), text=True)
    if 'name=rootless' not in json.loads(result):
        raise ValueError('The common method requires rootless Docker')
    return selected


def git(source, deadline, *args, index=None, data=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1', GIT_OPTIONAL_LOCKS='0')
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(source), *args],
                          input=data, timeout=remaining(deadline), env=env,
                          capture_output=True, check=True).stdout


def current_tree(source, index, baseline, deadline):
    git(source, deadline, 'read-tree', baseline, index=index)
    git(source, deadline, 'add', '-A', '--', '.', index=index)
    return git(source, deadline, 'write-tree', index=index).decode().strip()


def validate_patch(patch, tools, policy):
    sys.path.append(str(tools))
    try:
        import scorer.patches as validator
        if Path(validator.__file__).resolve() != tools / 'scorer' / 'patches.py':
            raise ValueError('The published patch validator was shadowed')
        return validator.validate_submission(patch, SimpleNamespace(
            patchable_paths=policy['allowed_paths'], forbidden_paths=policy['forbidden_paths'],
        ))
    finally:
        sys.path.remove(str(tools))


def seal(out, runner, prepared, workspace, tools, policy, deadline, closed):
    source = Path(prepared['source_root'])
    base = prepared['baseline_commit']
    base_tree = git(source, deadline, 'rev-parse', base + '^{tree}').decode().strip()
    index = out / 'delivery.index'
    before = current_tree(source, index, base, deadline)
    prepatch = out / 'pre_clean.patch'
    runner.timeout_seconds = remaining(deadline)
    runner.export(workspace, prepatch, out / 'export-before')
    raw = git(source, deadline, 'diff', '--raw', '-z', '--no-renames', base_tree, before).split(b'\0')
    if any(any(mode in entry.split()[0:2] for mode in (b':120000', b'120000', b':160000', b'160000'))
           for entry in raw[::2]):
        raise ValueError('Changed symlinks or nested repositories cannot be automatically sealed')
    clean_root = out / 'cleanup-input'
    clean_root.mkdir()
    (out / 'cleanup-state').mkdir()
    write_json(clean_root / 'run.json', {'baseline_commit': base, 'policy': {
        'patchable_paths': policy['allowed_paths'], 'forbidden_paths': policy['forbidden_paths'],
    }})
    env = {**os.environ, 'CE_WORKSPACE': str(source), 'CE_STATE': str(out / 'cleanup-state'),
           'CE_INPUTS': str(clean_root)}
    cleanup = subprocess.check_output([
        sys.executable, '-c', 'import json; from contractedge.graph_repair.submit import clean; print(json.dumps(clean()))',
    ], env=env, timeout=remaining(deadline), text=True)
    cleaned = json.loads(cleanup)
    write_json(out / 'cleanup.json', {'before_tree': before, **cleaned})
    candidate = current_tree(source, index, base, deadline)
    if candidate != cleaned['tree']:
        raise ValueError('Cleanup tree differs from the candidate')
    pending = out / 'candidate.pending.patch'
    runner.timeout_seconds = remaining(deadline)
    runner.export(workspace, pending, out / 'export-after')
    validated = validate_patch(pending.read_bytes(), tools, policy)
    replay_workspace = out / 'clean-broken'
    runner.timeout_seconds = remaining(deadline)
    replay = runner.prepare(prepared['case_id'], replay_workspace, out / 'prepare-replay')
    replay_source = Path(replay['source_root'])
    if git(replay_source, deadline, 'rev-parse', 'HEAD^{tree}').decode().strip() != base_tree:
        raise ValueError('Replay is not the frozen Broken tree')
    if validated.raw:
        git(replay_source, deadline, 'apply', '--index', '--whitespace=nowarn', '-', data=validated.raw)
    replayed_tree = git(replay_source, deadline, 'write-tree').decode().strip()
    if replayed_tree != candidate or current_tree(source, index, base, deadline) != candidate:
        raise ValueError('Final candidate or replay tree mismatch')
    remaining(deadline)
    pending.rename(out / 'candidate.patch')
    receipt = {'status': 'SEALED' if validated.raw else 'EMPTY_PATCH', 'selection': 'CURRENT_AFTER_WRITER_CLOSURE',
               'baseline_tree': base_tree, 'before_cleanup_tree': before, 'candidate_tree': candidate,
               'replay_tree': replayed_tree, 'paths': validated.changed_paths, 'patch_sha256': validated.sha256,
               'patch_bytes': len(validated.raw), 'sealed_at_monotonic': time.monotonic(),
               'writer_closure_at_monotonic': closed['confirmed_at_monotonic'], 'behavior_scoring': 'NOT_RUN'}
    write_json(out / 'delivery.json', receipt)
    return receipt


def run(args):
    started = time.monotonic()
    if str(minisweagent.__version__) != '1.17.5':
        raise ValueError('The formal Live entry requires mini-swe-agent 1.17.5')
    profile = load_profile(args.profile)
    out, tools, public = args.out.resolve(), args.tools.resolve(strict=True), args.public.resolve(strict=True)
    if any(out.is_relative_to(root) for root in (tools, public, Path(__file__).resolve().parents[5])):
        raise ValueError('The output must be outside tools, data and the system worktree')
    if file_sha(args.live_yaml) != profile['live_yaml_sha256'] or tool_identity(tools) != profile['tools_sha256']:
        raise ValueError('Live template or public tools differ from the frozen profile')
    case = profile['case']
    row, directory, task, manifest = public_task(public, case['id'])
    if any(row[key] != case[key] for key in ('task_manifest_sha256', 'source_sha256')):
        raise ValueError('The public case differs from the frozen accepted identity')
    requirement = checked_public_file(directory, manifest, task['requirement_file']).read_text()
    inputs = json.loads(checked_public_file(directory, manifest, task['environment_file']).read_text())
    policy = editable_policy(task, inputs)
    if not policy['allowed_paths']:
        raise ValueError('An explicit public edit allowlist is required')

    end = started + profile['budget']['wall_seconds']
    action_end = end - profile['budget']['finalization_seconds']
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_json(out / 'identity.json', {
        'profile': profile, 'profile_sha256': file_sha(args.profile), 'runtime_case': row,
        'public_file_sha256': manifest['file_sha256'], 'edit_policy': policy,
        'python': sys.executable, 'mini_swe_agent': str(minisweagent.__version__), 'imports': {
            mod.__name__: {'path': mod.__file__, 'sha256': file_sha(Path(mod.__file__))}
            for mod in (minisweagent, agent, common_model, common_runtime, sys.modules[__name__])
        },
        'started_monotonic': started, 'action_deadline_monotonic': action_end, 'final_deadline_monotonic': end,
        'usage_kind': 'SYNTHETIC_LOCAL', 'real_provider_usage': None,
        'real_billing': None, 'cache_attribution': None,
        'variant': variant('live-equivalent').to_json(),
    })
    workspace = out / 'candidate'
    runner = BenchmarkPublicRunner(public, tools, lock_root=out / 'public-lock',
                                               timeout_seconds=remaining(action_end))
    run_status = 'INFRA_ERROR'
    detail = None
    delivery = {'status': 'UNCONFIRMED'}
    env = model = None
    prepared = None
    safe_to_deliver = False
    try:
        docker_local(action_end)
        prepared = runner.prepare(case['id'], workspace, out / 'prepare')
        source, inputs_path = Path(prepared['source_root']), Path(prepared['inputs_root'])
        if (git(source, action_end, 'rev-list', '--count', '--all').strip() != b'1'
                or git(source, action_end, 'remote').strip()
                or git(source, action_end, 'fsck', '--full', '--no-reflogs', '--unreachable').strip()):
            raise ValueError('Broken Git custody check failed')
        if (inputs_path / task['requirement_file']).read_text() != requirement:
            raise ValueError('Prepared task differs from the verified public requirement')
        active = variant('live-equivalent')
        config = agent_config(args.live_yaml.read_text(), active)
        fields = {f.name for f in dataclasses.fields(AgentConfig)}
        settings = {k: v for k, v in config['agent'].items() if k in fields}
        settings.update(step_limit=0, cost_limit=0)
        model = common_model.LiveModel(profile, args.endpoint, out, action_end)
        env = common_runtime.CommonEnvironment(
            image=profile['method_image'], source=source, inputs=inputs_path, runner=runner,
            workspace=workspace, out=out, shell_env=config['config']['environment']['env'],
            profile=profile, action_deadline=action_end, final_deadline=end,
        )
        platform_info = env.start()
        live = agent.ContractGraphAgent(model, env, ce_variant=active, ce_state=out / 'agent-state', **settings)
        try:
            run_status, detail = live.run(requirement, **platform_info)
            safe_to_deliver = run_status in {'Submitted', 'BudgetStop', 'ProviderStop', 'ToolStop'}
        finally:
            write_json(out / 'trajectory.json', live.messages)
    except Exception as exc:
        detail = f'{type(exc).__name__}: {exc}'
        safe_to_deliver = False
    finally:
        if env is not None:
            try:
                closed = env.stop()
            except Exception as exc:
                delivery['error'] = f'WRITER_CLOSURE_UNCONFIRMED: {type(exc).__name__}: {exc}'
            else:
                if safe_to_deliver:
                    try:
                        delivery = seal(out, runner, prepared, workspace, tools, policy, end, closed)
                    except Exception as exc:
                        delivery['error'] = f'{type(exc).__name__}: {exc}'
        write_json(out / 'result.json', {'run_status': run_status, 'detail': detail, 'delivery': delivery,
                                        'elapsed_seconds': time.monotonic() - started,
                                        'local_accounting': model.money.summary() if model else None,
                                        'real_provider_called': False})
    return 0 if delivery['status'] in {'SEALED', 'EMPTY_PATCH'} else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('profile', 'public', 'tools', 'live-yaml', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--endpoint', required=True)
    # The published setup and export commands invoke Git themselves.
    for key in list(os.environ):
        if key.startswith('GIT_'):
            del os.environ[key]
    os.environ.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1', GIT_OPTIONAL_LOCKS='0')
    return run(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
