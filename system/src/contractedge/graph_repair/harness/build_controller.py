"""Copy the frozen baseline controller and register contractedge-graph in the copy.

Every edit is an exact, asserted text replacement, so the diff against the frozen controller is
small and reviewable. The source controller is only read.
Usage: python build_controller.py SOURCE_CONTROLLER_DIR TARGET_CONTROLLER_DIR
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

METHOD = 'contractedge-graph'

GRAPH_FUNCTION = r'''
def graph_settings():
    settings=CASE_CONFIG['graph_repair'];tools=Path(settings['tools_root'])
    from contractedge.graph_repair.config import variant as load_variant
    variant=load_variant(os.environ.get('CE_GRAPH_VARIANT','full') if not a.smoke else 'full')
    frozen=json.loads(Path(settings['variants_file']).read_text())
    assert frozen[variant.name]==variant.to_json(),'Variant differs from the frozen variant table'
    return settings,tools,variant

def graph():
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.agents.default import AgentConfig
    from minisweagent.models import get_model
    from minisweagent.run.utils.save import save_traj
    from contractedge.graph_repair.agent import ContractGraphAgent
    from contractedge.graph_repair.prompts.build import agent_config
    settings,tools,variant=graph_settings()
    built=agent_config((P/'repos/live-swe-agent/config/livesweagent.yaml').read_text(),variant);config=built['config'];cfg=built['agent']
    allowed={f.name for f in dataclasses.fields(AgentConfig)}
    removed=set(cfg)-allowed;assert removed<={'mode','confirm_exit','whitelist_actions'}
    cfg={k:v for k,v in cfg.items() if k in allowed};cfg.update(step_limit=0,cost_limit=0)
    envcfg={'image':IMAGE,'cwd':'/testbed','run_args':docker_args('none'),'timeout':120,'env':config['environment']['env']}
    modelcfg={'model_name':'openai/'+MODEL,'model_kwargs':{'api_key':'task-local-adapter-no-upstream-secret','api_base':BASE,'max_tokens':16384},'cost_tracking':'ignore_errors'}
    (out/'native-config.yaml').write_text(yaml.safe_dump({'agent':cfg,'environment':envcfg,'model':modelcfg,'removed_interactive_fields':sorted(removed),'graph_variant':variant.to_json()},sort_keys=False))
    env=DockerEnvironment(**envcfg);agent=None
    try:
        info['container']=env.container_id;inspect(env.container_id)
        original_execute=env.execute
        def execute(command,cwd='',*,timeout=None):
            if timeout is None and is_public_command(command):timeout=TOOL_TIMEOUT
            return original_execute(command,cwd,timeout=timeout)
        env.execute=execute
        if a.smoke:
            smoke_commands(env.execute)
            tool=env.execute("cd /testbed && ce-graph show >/tmp/ce-graph.out && ce-ckpt list >/dev/null && ce-links README.md >/dev/null; rc=$?; git status --porcelain; exit $rc")
            print('GRAPH_TOOLS_SMOKE',tool,flush=True);assert tool['returncode']==0 and tool['output'].strip()=='','ce-tools failed or wrote under /testbed'
            info['smoke_pass']=True;return
        tree=env.execute("git -c safe.directory=* rev-parse "+profile['baseline_commit']+"^{tree}")
        assert tree['returncode']==0
        model=get_model(modelcfg['model_name'],modelcfg)
        agent=ContractGraphAgent(model,env,ce_variant=variant,ce_state=out/'ce-state',ce_wire_usage=out/'wire/usage.jsonl',ce_tool_timeout=TOOL_TIMEOUT,ce_baseline_tree=tree['output'].strip(),**cfg)
        platform=env.execute("python -c 'import platform,json;print(json.dumps(platform.uname()._asdict()))'")
        assert platform['returncode']==0
        status,submission=agent.run(Path(profile['task_path']).read_text(),**json.loads(platform['output']))
        info.update(exit_status=status,submitted=status=='Submitted',submission=submission,graph_variant=variant.name,forced_submission=agent.ce_forced,checkpoints=len(agent.ce_checkpoints))
        (out/'native_submission.txt').write_text(submission)
    finally:
        export(env.container_id)
        if agent is not None:save_traj(agent,out/'trajectory.json',extra_info=info)
        env.cleanup()
'''

# Tool inputs/state are created before the container starts (mounts must exist when inspected).
PREPARE_STATE = r'''
if a.method=='contractedge-graph':
    _settings,_tools,_variant=graph_settings()
    if _variant.any_mechanism:
        _policy=json.loads((R/'PUBLIC_SUBMISSION_POLICY.json').read_text())['public']
        (out/'ce-inputs').mkdir(exist_ok=True);(out/'ce-state').mkdir(exist_ok=True);(out/'ce-state').chmod(0o777)
        (out/'ce-inputs/run.json').write_text(json.dumps({'variant':_variant.to_json(),'baseline_commit':profile['baseline_commit'],'policy':{k:_policy[k] for k in ['patchable_paths','forbidden_paths']},'tool_timeout_seconds':TOOL_TIMEOUT},indent=2))
'''

EDITS = {
    'controller/run_native.py': [
        ("choices=['swe-agent','openhands-codeact','live-swe-agent']",
         "choices=['swe-agent','openhands-codeact','live-swe-agent','contractedge-graph']"),
        ("os.environ['OPENAI_API_KEY']='task-local-adapter-no-upstream-secret'",
         "os.environ['OPENAI_API_KEY']='task-local-adapter-no-upstream-secret'\n"
         "if a.method=='contractedge-graph':sys.path.insert(0,str(Path(CASE_CONFIG['graph_repair']['tools_root'])/'lib'))"),
        ("    result[CASE_CONFIG['public_client']]={'bind':'/usr/local/bin/ce-run','mode':'ro'}\n    return result",
         "    result[CASE_CONFIG['public_client']]={'bind':'/usr/local/bin/ce-run','mode':'ro'}\n"
         "    if a.method=='contractedge-graph':\n"
         "        settings,tools,variant=graph_settings()\n"
         "        result[str(tools.resolve())]={'bind':'/ce-tools','mode':'ro'}\n"
         "        for name in settings['commands']:result[str((tools/'bin'/name).resolve())]={'bind':'/usr/local/bin/'+name,'mode':'ro'}\n"
         "        if variant.any_mechanism:\n"
         "            result[str((out/'ce-inputs').resolve())]={'bind':'/ce-inputs','mode':'ro'}\n"
         "            result[str((out/'ce-state').resolve())]={'bind':'/ce-state','mode':'rw'}\n"
         "    return result"),
        ("    return bool(re.search(r'(?<![\\w./-])(?:/usr/local/bin/)?ce-(?:public|run)(?![\\w-])',command))",
         "    return bool(re.search(r'(?<![\\w./-])(?:/usr/local/bin/)?ce-(?:public|run|probe|submit)(?![\\w-])',command))"),
        ("def live():", GRAPH_FUNCTION.lstrip('\n') + "\ndef live():"),
        ("try:\n    from runtime_ingress import RuntimeIngress", PREPARE_STATE.lstrip('\n') + "try:\n    from runtime_ingress import RuntimeIngress"),
        ("if a.method!='live-swe-agent' else contextlib.nullcontext()",
         "if a.method not in ('live-swe-agent','contractedge-graph') else contextlib.nullcontext()"),
        ("        elif a.method=='openhands-codeact':asyncio.run(codeact())\n        else:live()",
         "        elif a.method=='openhands-codeact':asyncio.run(codeact())\n        elif a.method=='contractedge-graph':graph()\n        else:live()"),
    ],
    'run_remote_method.py': [
        ("out=active/('smoke' if a.smoke else 'attempts')/a.method",
         "version=os.environ.get('CE_RUN_VERSION','v1');attempts='attempts' if version=='v1' else 'attempts-'+version\n"
         "out=active/('smoke' if a.smoke else attempts)/a.method"),
        ("    assert frozen['profiles'][a.method]==hashlib.sha256((out/'profile.json').read_bytes()).hexdigest()\n"
         "    assert frozen['task_prompts'][a.method]==hashlib.sha256((out/'task.txt').read_bytes()).hexdigest()",
         "    slot=json.loads((active/attempts/'SLOT_FROZEN.json').read_text()) if version!='v1' else frozen\n"
         "    assert slot['profiles'][a.method]==hashlib.sha256((out/'profile.json').read_bytes()).hexdigest()\n"
         "    assert slot['task_prompts'][a.method]==hashlib.sha256((out/'task.txt').read_bytes()).hexdigest()\n"
         "    if version!='v1':assert slot['variants'].get(a.method)==os.environ.get('CE_GRAPH_VARIANT')"),
    ],
    'freeze_method.py': [
        ("root=Path(os.environ['CE_RUN_ROOT']);active=root/'active-case';out=active/('smoke' if a.smoke else 'attempts')/a.method",
         "root=Path(os.environ['CE_RUN_ROOT']);active=root/'active-case'\n"
         "version=os.environ.get('CE_RUN_VERSION','v1');attempts='attempts' if version=='v1' else 'attempts-'+version\n"
         "out=active/('smoke' if a.smoke else attempts)/a.method"),
        ("    copy=active/'export-work'/a.method;copy.parent.mkdir(exist_ok=True)",
         "    copy=active/'export-work'/(a.method if version=='v1' else version+'-'+a.method);copy.parent.mkdir(exist_ok=True)"),
        ("archive_dir=active/('preflight-archives' if a.smoke else 'archives');archive_dir.mkdir(exist_ok=True)",
         "archive_dir=active/('preflight-archives' if a.smoke else 'archives' if version=='v1' else 'archives-'+version);archive_dir.mkdir(exist_ok=True)"),
    ],
    'prepare_ordinary_case.py': [
        ("    for method in ['live-swe-agent','swe-agent','openhands-codeact']:",
         "    for method in json.loads(os.environ['CE_ENABLED_METHODS']):"),
    ],
    'codeact_deployment.py': [
        ("    images = {method: base_image for method in ['swe-agent', 'openhands-codeact', 'live-swe-agent']}",
         "    images = {method: base_image for method in ['swe-agent', 'openhands-codeact', 'live-swe-agent', 'contractedge-graph']}"),
    ],
    'run_outcome.py': [
        ("    elif method=='live-swe-agent':completion=submitted and exit_status=='Submitted'",
         "    elif method in ('live-swe-agent','contractedge-graph'):completion=submitted and exit_status=='Submitted'"),
    ],
    'score_with_telemetry.py': [
        ("p.add_argument('--method',choices=['swe-agent','openhands-codeact','live-swe-agent'],required=True)",
         "p.add_argument('--method',choices=['swe-agent','openhands-codeact','live-swe-agent','contractedge-graph'],required=True)"),
    ],
    'framework_identity.py': [
        ("    for name in ['swerex','aiohttp']:",
         "    for name in ['swerex','aiohttp','minisweagent']:"),
        ("    versions={name:importlib.metadata.version(name) for name in ['swe-rex','aiohttp','litellm','pydantic','docker']}",
         "    versions={name:importlib.metadata.version(name) for name in ['swe-rex','aiohttp','litellm','pydantic','docker','mini-swe-agent']}"),
    ],
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(source: str, target: str) -> None:
    source, target = Path(source), Path(target)
    assert not target.exists(), 'Refuse to overwrite an existing controller copy'
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    applied = {}
    for name, edits in EDITS.items():
        path = target / name
        text = path.read_text()
        for old, new in edits:
            assert text.count(old) == 1, f'{name}: expected exactly one match for {old[:80]!r}'
            text = text.replace(old, new)
        path.write_text(text)
        applied[name] = {'before_sha256': sha(source / name), 'after_sha256': sha(path), 'edits': len(edits)}
    files = {p.relative_to(target).as_posix(): sha(p) for p in sorted(target.rglob('*'))
             if p.is_file() and p.name != 'SOURCE_IDENTITY.json'}
    identity = {'schema_version': 'contractedge.controller_source_identity.graph_repair.v1',
                'derived_from': str(source), 'derived_from_identity_sha256': sha(source / 'SOURCE_IDENTITY.json'),
                'registered_method': METHOD, 'edited_files': applied, 'files': files}
    (target / 'SOURCE_IDENTITY.json').write_text(json.dumps(identity, indent=2))
    print(json.dumps({'controller': str(target), 'edited': applied}, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:3])
