"""CRD / API types: schema properties <-> Go API type fields <-> controller references."""
from __future__ import annotations

import re

from contractedge.graph_repair.linkers import go_tags
from contractedge.graph_repair.linkers.base import candidate, loc, yaml_key_lines


def _is_crd(repo, path):
    return path.endswith(('.yaml', '.yml')) and any('kind: CustomResourceDefinition' in l for l in repo.lines(path)[:40])


def link(repo, seed: str, seed_path: str | None = None, seed_line: int | None = None) -> list[dict]:
    props = []
    if seed_path and _is_crd(repo, seed_path):
        keyed = yaml_key_lines(repo.lines(seed_path))
        chosen = [k for n, k in keyed if not seed_line or n == seed_line]
        props = sorted({k.rsplit('.', 1)[-1] for k in chosen if '.properties.' in k})[:20]
    elif not seed_path:
        props = [seed]
    out = []
    crds = [p for p in repo.files() if _is_crd(repo, p)]
    for prop in props:
        decls = go_tags.fields(repo, seed=prop)
        for crd in crds:
            for n, key in yaml_key_lines(repo.lines(crd)):
                if key.endswith('.properties.' + prop):
                    for field_name, tag, path, number in decls[:5]:
                        out.append(candidate('crd', 'BINDING', loc(crd, n, key), loc(path, number, f'{field_name} `{tag}`'),
                                             'CRD schema property matches Go json tag'))
        for field_name, tag, path, number in decls[:5]:
            use = re.compile(r'\.Spec\.(?:\w+\.)*' + re.escape(field_name) + r'\b|\.Status\.(?:\w+\.)*' + re.escape(field_name) + r'\b')
            for go in repo.files():
                if not go.endswith('.go') or go.endswith('_test.go') or go == path:
                    continue
                for n, line in enumerate(repo.lines(go), 1):
                    if use.search(line):
                        out.append(candidate('crd', 'RECONCILIATION', loc(path, number, field_name), loc(go, n, field_name),
                                             line.strip()[:160]))
    if seed_path and seed_path.endswith('.go'):
        out += [dict(c, linker='crd') for c in go_tags.link(repo, seed, seed_path, seed_line)
                if c['from']['path'] and _is_crd(repo, c['from']['path'])]
    return out[:300]
