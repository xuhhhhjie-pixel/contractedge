"""Automatic checkpoints that never move HEAD, touch the real index or enter the exported patch.

A checkpoint is `write-tree` of a private index (/ce-state/*.index) that includes untracked,
non-ignored files, wrapped by `commit-tree` and kept under refs/ce/ckpt/N. Restoring writes the
checkpoint tree into the work tree through another private index and deletes files it lacks.
The candidate export (`git add -N . && git diff BASELINE`) ignores refs and objects.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from contractedge.graph_repair import state

GIT = 'git -c safe.directory=* -c core.hooksPath=/dev/null'
IDENT = ('GIT_AUTHOR_NAME=ce GIT_AUTHOR_EMAIL=ce@localhost GIT_COMMITTER_NAME=ce '
         "GIT_COMMITTER_EMAIL=ce@localhost GIT_AUTHOR_DATE='@0 +0000' GIT_COMMITTER_DATE='@0 +0000'")


def tree_script(index: str) -> str:
    """Shell that prints TREE=<id> for the current work tree, untracked files included."""
    return (f'cd /testbed && export GIT_INDEX_FILE={index} GIT_OPTIONAL_LOCKS=0 && '
            f'{{ test -f {index} || {GIT} read-tree HEAD; }} && {GIT} add -A . >/dev/null 2>&1; '
            f'echo TREE=$({GIT} write-tree)')


def checkpoint_script(n: int, last_tree: str | None = None, index: str = '/ce-state/ckpt.index') -> str:
    """Shell for the agent: prints TREE=...; when the tree differs from `last_tree` it also commits
    the tree, points refs/ce/ckpt/N at it and prints COMMIT=..."""
    last = last_tree or 'none'
    return (f'cd /testbed && export GIT_INDEX_FILE={index} GIT_OPTIONAL_LOCKS=0 && '
            f'{{ test -f {index} || {GIT} read-tree HEAD; }} && {{ {GIT} add -A . >/dev/null 2>&1; true; }} && '
            f'T=$({GIT} write-tree) && echo TREE=$T && if [ "$T" != "{last}" ]; then '
            f'C=$({IDENT} {GIT} commit-tree $T -p HEAD -m ce-ckpt-{n}) && '
            f'{GIT} update-ref refs/ce/ckpt/{n} $C && echo COMMIT=$C; fi')


def parse(output: str, key: str) -> str | None:
    m = re.search(rf'^{key}=([0-9a-f]{{40,64}})\s*$', output, re.M)
    return m.group(1) if m else None


# ---------------------------------------------------------------- container side
def _git(*args, index=None, check=True, timeout=120, cwd=None):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0', GIT_AUTHOR_NAME='ce', GIT_AUTHOR_EMAIL='ce@localhost',
               GIT_COMMITTER_NAME='ce', GIT_COMMITTER_EMAIL='ce@localhost', GIT_AUTHOR_DATE='@0 +0000',
               GIT_COMMITTER_DATE='@0 +0000')
    if index:
        env['GIT_INDEX_FILE'] = index
    return subprocess.run(['git', '-c', 'safe.directory=*', '-c', 'core.hooksPath=/dev/null', *args],
                          cwd=cwd or state.WORKSPACE, env=env, capture_output=True, check=check, timeout=timeout)


def current_tree(index: str | None = None) -> str:
    index = index or str(state.STATE / 'tool.index')
    if not Path(index).exists():
        _git('read-tree', 'HEAD', index=index)
    _git('add', '-A', '.', index=index, check=False)
    return _git('write-tree', index=index).stdout.decode().strip()


def tree_paths(rev: str) -> set[str]:
    out = _git('ls-tree', '-r', '-z', '--name-only', rev).stdout.decode(errors='surrogateescape')
    return {p for p in out.split('\0') if p}


def worktree_paths() -> set[str]:
    out = _git('ls-files', '-co', '--exclude-standard', '-z').stdout.decode(errors='surrogateescape')
    return {p for p in out.split('\0') if p}


def restore(rev: str) -> dict:
    """Make the work tree equal to `rev`'s tree; HEAD and the real index are untouched."""
    index = str(state.STATE / 'restore.index')
    Path(index).unlink(missing_ok=True)
    _git('read-tree', rev, index=index)
    _git('checkout-index', '-a', '-f', index=index)
    wanted = tree_paths(rev)
    removed = []
    for path in sorted(worktree_paths() - wanted):
        target = state.WORKSPACE / path
        if target.is_file() or target.is_symlink():
            target.unlink()
            removed.append(path)
    Path(index).unlink(missing_ok=True)
    return {'restored': rev, 'removed_files': removed}


def baseline_commit() -> str:
    return state.run_config().get('baseline_commit') or _git('rev-parse', 'HEAD').stdout.decode().strip()


def checkpoints() -> list[dict]:
    return state.read_json(state.STATE / 'checkpoints.json', []) or []


def results() -> dict:
    return state.read_json(state.STATE / 'results.json', {}) or {}


def record_result(tree: str, key: str, value) -> None:
    with state.locked('results'):
        data = results()
        data.setdefault(tree, {})[key] = value
        state.write_json(state.STATE / 'results.json', data)


def score(tree: str, data: dict | None = None) -> tuple:
    """Order: public check passes, then more violated-edge probes pass, then fewer probe regressions."""
    row = (data if data is not None else results()).get(tree, {})
    public = row.get('public', {}).get('exit_code')
    probes = row.get('probes', {})
    passed = sum(1 for v in probes.values() if v.get('kind') == 'violate' and v.get('exit_code') == 0)
    broken = sum(1 for v in probes.values() if v.get('kind') == 'preserve' and v.get('exit_code') != 0)
    return (1 if public == 0 else 0, passed, -broken)


def best(changed_only: bool = True) -> dict | None:
    """Best checkpoint; ties go to the newest. Checkpoints identical to the baseline are skipped."""
    rows = [c for c in checkpoints() if not changed_only or c.get('changed', True)]
    if not rows:
        return None
    data = results()
    return max(rows, key=lambda c: (score(c['tree'], data), c['n']))
