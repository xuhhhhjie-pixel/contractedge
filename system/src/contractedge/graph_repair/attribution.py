"""Map failed public checks, builds and probes to contract-graph nodes and edges.

Structure follows TopoMen's attribute_failure_to_node (rule -> target, confidence, reason); the
rules are for build/render/probe output rather than Dockerfiles. Provisional, replaceable.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

LOCATION = re.compile(r'(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:go|ya?ml|tpl|json|py|ts|js|tf|cue|sh|toml|txt|conf|cfg|ini|properties|java|rs))'
                      r'(?::|", line |\()(\d+)')
PROBE_ROW = re.compile(r'"(P\d+)":\s*\{[^{}]*?"exit_code":\s*([1-9]\d*)')
MAX_LINES = 8


@dataclass
class Attribution:
    location: str
    node: str | None
    edges: list
    confidence: float
    reason: str


def _same_file(a: str, b: str) -> bool:
    """Tools report paths relative to different roots (helm: <chart-name>/templates/x.yaml)."""
    pa, pb = a.split('/'), b.split('/')
    k = 0
    while k < min(len(pa), len(pb)) and pa[-1 - k] == pb[-1 - k]:
        k += 1
    return k == min(len(pa), len(pb)) or k >= 2


def _match_node(graph, path, line):
    best = None
    for node in graph.get('nodes', []):
        if not _same_file(node['path'], path):
            continue
        start, end = node['span']
        if start - 5 <= line <= end + 5:
            return node, 0.9
        best = best or (node, 0.5)
    return best if best else (None, 0.0)


def _edges_of(graph, node_id):
    return [e for e in graph.get('edges', []) if node_id in (e['from'], e['to'])]


def attribute(output: str, graph: dict | None, probes: list | None = None) -> list[Attribution]:
    graph = graph or {'nodes': [], 'edges': []}
    found, seen = [], set()
    for m in LOCATION.finditer(output or ''):
        path, line = m.group(1).lstrip('./'), int(m.group(2))
        if (path, line) in seen:
            continue
        seen.add((path, line))
        node, confidence = _match_node(graph, path, line)
        if node:
            edges = [f"{e['id']} {e['type']} {e['from']}->{e['to']} [{e['status']}]" for e in _edges_of(graph, node['id'])]
            reason = 'failure inside the node span' if confidence > 0.8 else 'failure in the node file, outside its span'
            found.append(Attribution(f'{path}:{line}', node['id'], edges, confidence, reason))
        else:
            found.append(Attribution(f'{path}:{line}', None, [], 0.2,
                                     'location not in the contract graph; if it is a consumer, add a node'))
        if len(found) >= MAX_LINES:
            break
    by_probe = {p['id']: p for p in probes or []}
    for pid, code in PROBE_ROW.findall(output or ''):
        probe = by_probe.get(pid)
        if probe:
            edge = next((e for e in graph.get('edges', []) if e['id'] == probe['edge']), None)
            label = f"{edge['id']} {edge['type']} {edge['from']}->{edge['to']} [{edge['status']}]" if edge else probe['edge']
            found.append(Attribution(pid, None, [label], 0.95,
                                     f"{probe['kind']} probe failed (exit {code}): "
                                     + ('the edge is still violated' if probe['kind'] == 'violate' else 'a preserved behaviour regressed')))
    return found[:MAX_LINES]


def render(rows: list[Attribution]) -> str:
    if not rows:
        return ''
    lines = ['[ce-attribution] failure mapped to the contract graph:']
    for row in rows:
        target = row.node or '-'
        edges = '; '.join(row.edges) if row.edges else 'no edge'
        lines.append(f'- {row.location} -> {target}: {edges} (confidence {row.confidence:.1f}; {row.reason})')
    return '\n'.join(lines)


def to_json(rows: list[Attribution]) -> str:
    return json.dumps([asdict(r) for r in rows])
