"""Same-kind consumer closure: other consumers of an edge's source that the graph does not cover yet.

Provisional implementation against the frozen interface (to be replaced by the closure owner).
"""
from __future__ import annotations

import posixpath
from pathlib import Path

from contractedge.graph_repair import linkers
from contractedge.graph_repair.graph import store
from contractedge.graph_repair.graph.schema import GraphError

MAX_CANDIDATES = 8


def _covered(graph, path, line):
    return any(n['path'] == path and n['span'][0] - 3 <= line <= n['span'][1] + 3 for n in graph['nodes'])


def candidates(graph, edge_id, root: Path) -> list[dict]:
    edge = store.edge(graph, edge_id)
    source, target = store.node(graph, edge['from']), store.node(graph, edge['to'])
    found = []
    seeds = [f"{source['path']}:{line}" for line in range(source['span'][0], min(source['span'][1], source['span'][0] + 5) + 1)]
    if source.get('anchor'):
        seeds.append(source['anchor'])
    for seed in seeds:
        for item in linkers.run(root, seed, limit=400)['candidates']:
            consumer = item['to'] if item['from']['path'] == source['path'] or not item['from']['path'] else item['from']
            if consumer['path'] and not consumer['path'].startswith('dependency:') and consumer['path'] != source['path']:
                found.append((consumer['path'], consumer['line'], item['linker'], item['evidence']))
    # Structural siblings: the same file name as the known consumer in another directory.
    name = posixpath.basename(target['path'])
    repo = linkers.Repo(root)
    for path in repo.files():
        if posixpath.basename(path) == name and path != target['path']:
            found.append((path, 1, 'sibling', f'same file name as {target["path"]}'))
    result, seen = [], set()
    for path, line, how, evidence in found:
        if path == target['path'] or _covered(graph, path, line) or path in seen:
            continue
        seen.add(path)
        result.append({'path': path, 'line': max(1, line), 'linker': how, 'evidence': evidence})
        if len(result) >= MAX_CANDIDATES:
            break
    return result


def expand(graph, edge_id, root: Path) -> list[dict]:
    """Add candidate consumers as nodes and UNDECIDED closure items; the model decides each one."""
    edge = store.edge(graph, edge_id)
    target = store.node(graph, edge['to'])
    added = []
    for item in candidates(graph, edge_id, root):
        try:
            node = store.add_node(graph, role=target['role'], path=item['path'], span=[item['line'], item['line']],
                                  anchor=target.get('anchor', ''), source='closure:' + item['linker'], root=root)
        except GraphError:
            continue
        closure = store.decide(graph, of=edge_id, candidate=node['id'], decision='UNDECIDED')
        added.append({**closure, 'path': item['path'], 'line': item['line'], 'evidence': item['evidence']})
    return added
