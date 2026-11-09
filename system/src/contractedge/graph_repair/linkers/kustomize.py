"""Kustomize: kustomization resources/patches <-> target files and resources by kind/name."""
from __future__ import annotations

import posixpath
import re

from contractedge.graph_repair.linkers.base import candidate, loc

LIST_KEYS = ('resources', 'bases', 'components', 'patches', 'patchesStrategicMerge', 'patchesJson6902',
             'configMapGenerator', 'crds')


def _is_kustomization(path):
    return path.rsplit('/', 1)[-1] in ('kustomization.yaml', 'kustomization.yml', 'Kustomization')


def _kind_name(lines):
    kind = name = None
    for line in lines[:60]:
        m = re.match(r'^kind:\s*(\S+)', line)
        if m and kind is None:
            kind = m.group(1)
        m = re.match(r'^\s{2}name:\s*(\S+)', line)
        if m and name is None:
            name = m.group(1).strip('"\'')
    return kind, name


def link(repo, seed: str, seed_path: str | None = None, seed_line: int | None = None) -> list[dict]:
    files = set(repo.inventory())          # path references resolve against every file
    kustomizations = [p for p in files if _is_kustomization(p)]
    if seed_path and not _is_kustomization(seed_path):
        relevant = [k for k in kustomizations
                    if any(seed_path.endswith(t) or posixpath.basename(seed_path) in line
                           for line in repo.lines(k) for t in [posixpath.basename(seed_path)])]
    elif seed_path:
        relevant = [seed_path]
    else:
        relevant = [k for k in kustomizations if any(seed in line for line in repo.lines(k))]
    out = []
    by_kind_name = {}
    for path in repo.files():              # kind/name lookup only where the seed's words occur
        if path.endswith(('.yaml', '.yml')) and not _is_kustomization(path):
            kind, name = _kind_name(repo.lines(path))
            if kind and name:
                by_kind_name.setdefault((kind, name), []).append(path)
    for k in relevant[:30]:
        base = posixpath.dirname(k)
        lines = repo.lines(k)
        section = None
        for number, line in enumerate(lines, 1):
            m = re.match(r'^([A-Za-z0-9]+):\s*$', line)
            if m:
                section = m.group(1)
                continue
            m = re.match(r'^\s*-\s*(?:path:\s*)?(\S+)\s*$', line)
            if section in LIST_KEYS and m:
                target = posixpath.normpath(posixpath.join(base, m.group(1).strip('"\'')))
                hits = [target] if target in files else [f for f in files if f.startswith(target + '/') and _is_kustomization(f)]
                for hit in hits[:5]:
                    out.append(candidate('kustomize', 'TRANSFORMATION', loc(k, number, section), loc(hit, 1, m.group(1)),
                                         line.strip()))
            m = re.match(r'^\s*(?:kind|name):\s*(\S+)', line)
            if section in ('patches', 'patchesJson6902') and m and line.strip().startswith('kind'):
                kind = m.group(1)
                name_line = next((l for l in lines[number:number + 4] if 'name:' in l), '')
                name = name_line.split('name:', 1)[-1].strip().strip('"\'') if name_line else None
                for target in by_kind_name.get((kind, name), [])[:5]:
                    out.append(candidate('kustomize', 'TRANSFORMATION', loc(k, number, f'{kind}/{name}'), loc(target, 1, f'{kind}/{name}'),
                                         'patch target'))
    return out
