"""Create the graph-repair experiment root from the frozen baseline (recovery) root.

The baseline roots are only read. Release assets are hard-linked (same filesystem, no copy);
orchestration scripts are copied; the controller copy gets the contractedge-graph registration.
Usage: python setup_root.py BASELINE_ROOT ASSET_ROOT EXPERIMENT_ROOT WORKTREE_SYSTEM_SRC EVALUATION_LISTS
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = ['prepare_case150.py', 'deployment_preflight150.py', 'custody.py', 'prepare_extended_case.py',
           'qualify_pristine_export.py', 'qualification_identity.py', 'scoring_health.py', 'transfer_assets_local.py']
METHODS = ['live-swe-agent', 'contractedge-graph']
PREPARE_EDIT = (
    " config['controller_protocol_variant']='REUSE44_NEW31_EDITOR_EVENT_HEALTH_V1';config['codeact_deployment_manifest_sha256']=sha(code/'CODEACT_RUNTIME_DEPLOYMENT.json')\n",
    " config['controller_protocol_variant']='REUSE44_NEW31_EDITOR_EVENT_HEALTH_V1';config['codeact_deployment_manifest_sha256']=sha(code/'CODEACT_RUNTIME_DEPLOYMENT.json')\n"
    " tools=R/'ce-tools';config['graph_repair']={'tools_root':str(tools),'variants_file':str(tools/'VARIANTS.json'),'commands':read(tools/'MANIFEST.json')['commands'],'tools_manifest_sha256':sha(tools/'MANIFEST.json')}\n")
EXTENDED_EDIT = ("    for method in ['live-swe-agent','swe-agent','openhands-codeact']:",
                 "    for method in json.loads(os.environ['CE_ENABLED_METHODS']):")


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def link_tree(src: Path, dst: Path) -> int:
    count = 0
    for parent, dirs, files in os.walk(src):
        rel = Path(parent).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            source = Path(parent) / name
            if source.is_symlink():
                continue
            os.link(source, dst / rel / name)
            count += 1
    return count


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1, f'{path.name}: expected one match'
    path.write_text(text.replace(old, new))


def main(baseline, assets_root, root, src, lists):
    baseline, assets_root, root, src = Path(baseline), Path(assets_root), Path(root), Path(src)
    lists = json.loads(Path(lists).read_text())
    assert not (root / 'controller').exists(), 'Experiment root already set up'
    for name in SCRIPTS:
        shutil.copy2(baseline / name, root / name)
    edit(root / 'prepare_case150.py', *PREPARE_EDIT)
    edit(root / 'prepare_extended_case.py', *EXTENDED_EDIT)
    shutil.copytree(baseline / 'zero-model-guard', root / 'zero-model-guard', ignore=shutil.ignore_patterns('__pycache__'))
    (root / 'bin').mkdir()
    (root / 'bin/python3').write_text('#!/bin/sh\nexec /tmp/contractedge-deepseek-preflight-20261019/venv312/bin/python "$@"\n')
    (root / 'bin/python3').chmod(0o755)
    os.symlink(os.readlink(baseline / 'bin/docker'), root / 'bin/docker')
    subprocess.run([sys.executable, str(HERE / 'build_controller.py'), str(baseline / 'controller'), str(root / 'controller')], check=True)
    subprocess.run([sys.executable, str(HERE / 'package_tools.py'), str(src), str(root / 'ce-tools')], check=True)
    (root / 'evidence').mkdir()
    for name in ['RUNTIME_READY_TEMPLATE.json', 'SHARED_NATIVE_PUBLIC_IMAGE.json', 'bash.py']:
        shutil.copy2(baseline / 'evidence' / name, root / 'evidence' / name)
    (root / 'inputs').mkdir()
    for path in (baseline / 'inputs').glob('*.ARCHIVE_INDEX.json'):
        shutil.copy2(path, root / 'inputs' / path.name)
    linked = link_tree(assets_root / 'assets', root / 'assets')
    for name in ['coordination', 'cases', 'jobs', 'logs', 'slots', 'scoring-private', 'score-health', 'reports-data']:
        (root / name).mkdir(exist_ok=True)
    (root / 'coordination/network-allocation.lock').touch()
    plan = json.loads((assets_root / 'BATCH_PLAN.json').read_text())
    wanted = list(dict.fromkeys(lists['panel'] + lists['evaluation_without_holdout'] + lists['holdout']))
    rows = [dict(c, methods=METHODS) for c in plan['cases'] if c['case_id'] in wanted]
    missing = sorted(set(wanted) - {c['case_id'] for c in rows})
    value = {'experiment_id': 'ce-graph-repair-20261109', 'remote_root': str(root), 'cases': rows,
             'case_queue': [c for c in wanted if c not in missing], 'methods': METHODS,
             'source_plan_sha256': sha(assets_root / 'BATCH_PLAN.json'),
             'cases_not_in_source_plan': missing, 'agent_wall_seconds': 1800, 'max_parallel_runs': 3}
    (root / 'BATCH_PLAN.json').write_text(json.dumps(value, indent=1))
    print(json.dumps({'root': str(root), 'assets_linked': linked, 'cases': len(rows), 'missing': missing}))


if __name__ == '__main__':
    main(*sys.argv[1:6])
