"""Dependency APIs: project call sites of an imported package <-> pinned definitions when present."""
from __future__ import annotations

import re
from pathlib import Path

from contractedge.graph_repair.linkers.base import candidate, loc

IMPORT = re.compile(r'^\s*(?:import\s+)?(?:([A-Za-z_][\w]*|\.)\s+)?"([^"]+)"')
DEP_ROOTS = ('.contractedge-deps',)


def imports(lines):
    result = {}
    in_block = False
    for line in lines[:400]:
        stripped = line.strip()
        if stripped.startswith('import ('):
            in_block = True
            continue
        if in_block and stripped == ')':
            in_block = False
            continue
        if in_block or stripped.startswith('import "') or re.match(r'^import\s+\w+\s+"', stripped):
            m = IMPORT.match(stripped.removeprefix('import ').strip())
            if m:
                alias = m.group(1) or m.group(2).rstrip('/').rsplit('/', 1)[-1]
                result[alias] = m.group(2)
    return result


def module_dirs(root: Path, module: str):
    for dep_root in DEP_ROOTS:
        base = root / dep_root
        if not base.is_dir():
            continue
        for candidate_dir in base.rglob('pkg/mod'):
            for match in candidate_dir.glob(module.split('/')[0] + '/**'):
                if match.is_dir() and module.split('/')[-1] in match.name:
                    yield match
            break


def link(repo, seed: str, seed_path: str | None = None, seed_line: int | None = None) -> list[dict]:
    out = []
    go_files = [seed_path] if seed_path and seed_path.endswith('.go') else [p for p in repo.files() if p.endswith('.go')]
    for path in go_files[:400]:
        lines = repo.lines(path)
        table = imports(lines)
        for alias, imported in table.items():
            if seed_path is None and seed not in (alias, imported) and not imported.endswith('/' + seed):
                continue
            call = re.compile(r'\b' + re.escape(alias) + r'\.([A-Z]\w*)')
            for n, line in enumerate(lines, 1):
                if seed_line and n != seed_line:
                    continue
                for name in call.findall(line):
                    target = loc(f'dependency:{imported}', 0, name)
                    out.append(candidate('deps', 'BINDING', loc(path, n, f'{alias}.{name}'), target,
                                         'use ce-follow on the declaration handle to read the pinned definition'))
    return out[:300]
