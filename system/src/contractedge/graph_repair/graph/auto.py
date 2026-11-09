"""Automatic graph seeding after an edit (no pre-edit gate).

When the model first changes a file, the executor calls `ce-graph auto --files ...`: the edited
span becomes a node, the linkers find the declaration it consumes, and the other consumers of
that declaration become closure candidates (UNDECIDED) that the stop rule asks the model to
decide. Linker hits are candidates, never facts. Provisional, replaceable implementation.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from contractedge.graph_repair import linkers
from contractedge.graph_repair.graph import store
from contractedge.graph_repair.graph.schema import GraphError

HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', re.M)
TOTAL_BUDGET = 30.0
MAX_FILES, MAX_SPANS, MAX_SIBLINGS = 3, 3, 5


def changed_spans(root: Path, baseline: str, path: str) -> list[tuple[int, int]]:
    done = subprocess.run(['git', '-c', 'safe.directory=*', 'diff', '-U0', '--no-ext-diff', baseline, '--', path],
                          cwd=root, capture_output=True, text=True, errors='replace', timeout=30)
    spans = []
    for m in HUNK.finditer(done.stdout):
        start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        spans.append((max(1, start), max(1, start + count - 1)))
    if not spans and (root / path).is_file():   # untracked new file
        spans = [(1, min(20, max(1, len((root / path).read_text(errors='replace').splitlines()))))]
    return spans[:MAX_SPANS]


def guess_role(path: str) -> str:
    name = path.rsplit('/', 1)[-1]
    if name.startswith('values') or name.startswith('kustomization') or '/crd' in '/' + path or name.endswith('.env'):
        return 'CONFIG_DECLARATION'
    return 'PRODUCTION_CONSUMER'


def _line_count(root: Path, path: str) -> int:
    try:
        return max(1, len((root / path).read_text(errors='replace').splitlines()))
    except OSError:
        return 1


def _node_for(graph, root, path, span, role, source):
    span = [max(1, span[0]), min(span[1], _line_count(root, path))]
    for node in graph['nodes']:
        if node['path'] == path and node['span'][0] <= span[0] <= node['span'][1]:
            return node
    return store.add_node(graph, role=role, path=path, span=span, source=source, root=root)


def auto(graph: dict, files: list[str], root: Path, baseline: str, closure_on: bool) -> list[str]:
    deadline = time.monotonic() + TOTAL_BUDGET
    lines = []
    edited = set(files)
    for path in files[:MAX_FILES]:
        if not (root / path).is_file():
            continue
        spans = changed_spans(root, baseline, path)
        if not spans:
            continue
        try:
            node = _node_for(graph, root, path, [spans[0][0], spans[-1][1]], guess_role(path), 'auto:edit')
        except GraphError:
            continue
        counterpart = {}
        for start, end in spans:
            for line in range(start, min(end, start + 3) + 1):
                left = deadline - time.monotonic()
                if left <= 1:
                    break
                for c in linkers.run(root, f'{path}:{line}', limit=200, budget=min(8.0, left))['candidates']:
                    other = c['to'] if c['from']['path'] == path else c['from']
                    if other['path'] and other['path'] != path and not other['path'].startswith('dependency:'):
                        counterpart.setdefault((other['path'], other['line']), (c['linker'], c['type'], other.get('anchor', '')))
        if not counterpart:
            lines.append(f'- {path}: no cross-artifact counterpart found by the static linkers')
            continue
        declarations = [(p, l, v) for (p, l), v in counterpart.items() if guess_role(p) == 'CONFIG_DECLARATION'] or \
            [(p, l, v) for (p, l), v in list(counterpart.items())[:1]]
        p, l, (linker, edge_type, anchor) = declarations[0]
        try:
            decl = _node_for(graph, root, p, [l, l], 'CONFIG_DECLARATION', 'auto:' + linker)
        except GraphError:
            continue
        edge = next((e for e in graph['edges'] if e['from'] == decl['id'] and e['to'] == node['id']), None) or \
            store.add_edge(graph, edge_type=edge_type, source=decl['id'], target=node['id'], status='UNKNOWN',
                           note=f'auto: {path} changed; consumes {anchor or p}')
        siblings = []
        left = deadline - time.monotonic()
        if left > 1:
            for c in linkers.run(root, f'{p}:{l}', limit=200, budget=min(8.0, left))['candidates']:
                other = c['to'] if c['from']['path'] == p else c['from']
                q = other['path']
                if q and q not in edited and q != p and not q.startswith('dependency:') and q not in [s[0] for s in siblings]:
                    siblings.append((q, max(1, other['line'])))
        others = [f'{q}:{n}' for (q, n) in counterpart if q != p][:4]
        lines.append(f'- {path}:{spans[0][0]} ({node["id"]}) <- {p}:{l} ({decl["id"]}, {linker}) as edge {edge["id"]}'
                     + (f'; also linked: {", ".join(others)}' if others else ''))
        for q, n in siblings[:MAX_SIBLINGS]:
            try:
                sib = _node_for(graph, root, q, [n, n], node['role'], 'auto:closure')
            except GraphError:
                continue
            known = any(i['of'] == edge['id'] and i['candidate'] == sib['id'] for i in graph['closure'])
            if closure_on and sib['id'] != node['id'] and not known:
                store.decide(graph, of=edge['id'], candidate=sib['id'], decision='UNDECIDED')
            lines.append(f'  same-declaration consumer not changed yet: {q}:{n} ({sib["id"]})')
    return lines
