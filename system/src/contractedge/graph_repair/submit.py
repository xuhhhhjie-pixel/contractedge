"""ce-submit --check: the only hard check, run when the model asks to submit.

Passes when the public check passes, calibrated violate probes pass, preserve probes still pass,
every closure candidate is decided (NEEDS_FIX ones edited), and the patch follows the published
submission rules. UNKNOWN edges never block.
"""
from __future__ import annotations

import fnmatch
import re
import subprocess
import time

from contractedge.graph_repair import checkpoint, state
from contractedge.graph_repair.graph import store
from contractedge.graph_repair.probes import run as probe_run
from contractedge.graph_repair.probes import spec

SCHEMA = 'contractedge.submit_check.v1'
MAX_PATCH_BYTES = 4 * 1024 * 1024


def matches(path: str, patterns) -> bool:
    """Same rule as the published validator (scorer/patches.py `_allowed`): fnmatchcase, where `*`
    also crosses '/'. Using shell-glob semantics here would reject patches the scorer accepts."""
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def changes(baseline: str, tree: str) -> dict:
    raw = checkpoint._git('diff', '--raw', '-z', '--no-renames', '--no-ext-diff', baseline, tree).stdout
    numstat = checkpoint._git('diff', '--numstat', '-z', '--no-renames', baseline, tree).stdout.decode(errors='replace')
    patch = checkpoint._git('diff', '--binary', '--no-renames', '--no-ext-diff', baseline, tree).stdout
    files, modes = [], []
    parts = raw.decode(errors='surrogateescape').split('\0')
    for meta, name in zip(parts[0::2], parts[1::2]):
        if not meta.startswith(':'):
            continue
        old_mode, new_mode = meta[1:].split()[:2]
        files.append(name)
        if old_mode not in ('000000', new_mode) and new_mode != '000000':
            modes.append(name)
        if new_mode not in ('100644', '100755', '000000'):
            modes.append(name)
    binary = [chunk.split('\t', 2)[-1] for chunk in numstat.split('\0') if chunk.startswith('-\t-\t')]
    return {'files': files, 'mode_changes': sorted(set(modes)), 'binary': binary, 'patch_bytes': len(patch)}


def policy_unmet(policy: dict, change: dict) -> list[dict]:
    unmet = []
    patchable, forbidden = policy.get('patchable_paths', []), policy.get('forbidden_paths', [])
    for path in change['files']:
        if forbidden and matches(path, forbidden):
            unmet.append({'id': path, 'kind': 'policy', 'detail': 'matches forbidden_paths; revert it (delete it if new)'})
        elif patchable and not matches(path, patchable):
            unmet.append({'id': path, 'kind': 'policy',
                          'detail': 'outside patchable_paths; revert it (scratch files under /testbed are exported too)'})
    for path in change['binary']:
        unmet.append({'id': path, 'kind': 'policy', 'detail': 'binary change is not allowed'})
    for path in change['mode_changes']:
        unmet.append({'id': path, 'kind': 'policy', 'detail': 'file mode change is not allowed'})
    if change['patch_bytes'] > MAX_PATCH_BYTES:
        unmet.append({'id': 'PATCH', 'kind': 'policy', 'detail': f"patch is {change['patch_bytes']} bytes (> 4 MiB)"})
    if not change['files']:
        unmet.append({'id': 'PATCH', 'kind': 'policy', 'detail': 'no changes against the original version'})
    return unmet


def clean() -> dict:
    """Undo changes the submission rules reject: restore files outside patchable_paths (or matching
    forbidden_paths) to the original version, and delete such files that are new.

    Used before a forced submission, which skips the check: real runs submitted the executor's own
    scratch files (`.probe/main.go`, `tmp_diag/main.go`, `.ce_candidate.go.mod`) and were rejected
    whole although the source fix passed (R4-PROVIDER-UPJET-AWS-2182, 2026-11-12)."""
    policy = state.run_config().get('policy', {})
    baseline = checkpoint.baseline_commit()
    change = changes(baseline, checkpoint.current_tree())
    original = checkpoint.tree_paths(baseline)
    restored, removed = [], []
    for row in policy_unmet(policy, {**change, 'binary': [], 'mode_changes': [], 'patch_bytes': 0}):
        path = row['id']
        if row['id'] == 'PATCH':
            continue
        if path in original:
            checkpoint._git('checkout', baseline, '--', path)
            restored.append(path)
        else:
            target = state.WORKSPACE / path
            if target.is_file() or target.is_symlink():
                target.unlink()
                removed.append(path)
    return {'restored': restored, 'removed': removed, 'tree': checkpoint.current_tree()}


def public_result(tree: str, timeout: int) -> dict:
    cached = checkpoint.results().get(tree, {}).get('public')
    if cached is not None:
        return {**cached, 'cached': True}
    began = time.monotonic()
    try:
        done = subprocess.run(['ce-public'], cwd=state.WORKSPACE, capture_output=True, timeout=timeout)
        code, tail = done.returncode, (done.stdout + done.stderr).decode(errors='replace')[-1500:]
    except subprocess.TimeoutExpired:
        code, tail = 124, 'ce-public timed out'
    value = {'exit_code': code, 'seconds': round(time.monotonic() - began, 1), 'by': 'ce-submit'}
    checkpoint.record_result(tree, 'public', value)
    return {**value, 'cached': False, 'tail': tail}


def check() -> dict:
    config = state.run_config()
    variant = config.get('variant', {})
    baseline = checkpoint.baseline_commit()
    tree = checkpoint.current_tree()
    unmet, notes, checks = [], [], {}
    change = changes(baseline, tree)
    checks['policy'] = {'files': change['files'], 'patch_bytes': change['patch_bytes']}
    unmet += policy_unmet(config.get('policy', {}), change)
    graph = store.load() if variant.get('graph') else None
    if graph is not None and variant.get('closure'):
        decided = []
        for item in graph['closure']:
            node = store.node(graph, item['candidate'])
            key = f"{item['of']}/{item['candidate']}"
            if item['decision'] == 'UNDECIDED':
                unmet.append({'id': key, 'kind': 'closure',
                              'detail': f"{node['path']}:{node['span'][0]} undecided; ce-graph decide --of {item['of']} "
                                        f"--candidate {item['candidate']} --decision NEEDS_FIX|NO_FIX --reason ..."})
            elif item['decision'] == 'NEEDS_FIX' and node['path'] not in change['files']:
                unmet.append({'id': key, 'kind': 'closure', 'detail': f"marked NEEDS_FIX but {node['path']} is unchanged"})
            else:
                decided.append(key)
        checks['closure'] = {'decided': decided, 'total': len(graph['closure'])}
    if graph is not None:
        unknown = [e['id'] for e in graph['edges'] if e['status'] == 'UNKNOWN']
        if unknown:
            notes.append(f'UNKNOWN edges do not block: {", ".join(unknown)}')
    public = public_result(tree, int(config.get('tool_timeout_seconds', 600)))
    checks['public'] = public
    if public['exit_code'] != 0:
        unmet.append({'id': 'PUBLIC', 'kind': 'public_check', 'detail': f"ce-public exit {public['exit_code']}"})
    probes_disabled = None
    if variant.get('probes'):
        probes_disabled = spec.disabled()
        if probes_disabled is None and any(p['status'] in ('CALIBRATED', 'SUSPECT') for p in spec.load()):
            outcome = probe_run.run(variant, tree=tree)
            checks['probes'] = outcome['summary']
            status = {p['id']: p['status'] for p in spec.load()}
            for pid, row in outcome['probes'].items():
                if row['kind'] == 'violate' and row['exit_code'] != 0:
                    if status.get(pid) == 'SUSPECT':
                        notes.append(f'{pid} is SUSPECT (keeps failing while the public check passes); not blocking')
                    else:
                        unmet.append({'id': pid, 'kind': 'violate_probe', 'detail': f"edge {row['edge']} probe still fails"})
                elif row['kind'] == 'preserve' and row['exit_code'] != 0:
                    unmet.append({'id': pid, 'kind': 'preserve_probe',
                                  'detail': f"edge {row['edge']} preserved behaviour now fails"})
        elif probes_disabled:
            notes.append('probes disabled by budget: ' + probes_disabled['reason'])
    value = {'schema': SCHEMA, 'tree': tree, 'passed': not unmet, 'unmet': unmet, 'checks': checks,
             'notes': notes, 'probes_disabled': probes_disabled}
    state.append_jsonl(state.STATE / 'submit-checks.jsonl', {**value, 'at': time.time()})
    return value
