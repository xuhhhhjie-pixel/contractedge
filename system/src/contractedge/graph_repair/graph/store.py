"""Load, mutate and validate /ce-state/graph.json."""
from __future__ import annotations

from pathlib import Path

from contractedge.graph_repair import state
from contractedge.graph_repair.graph.schema import (DECISIONS, EDGE_STATUS, EDGE_TYPES, ROLES, GraphError,
                                                    empty, format_problems, relative_path, span_text,
                                                    text_digest)


def path() -> Path:
    return state.STATE / 'graph.json'


def load() -> dict:
    return state.read_json(path(), None) or empty()


def save(graph: dict) -> None:
    problems = format_problems(graph)
    if problems:
        raise GraphError('BAD_GRAPH', '; '.join(problems[:8]))
    state.write_json(path(), graph)


def _next_id(items, prefix):
    used = {item.get('id') for item in items}
    n = 1
    while f'{prefix}{n}' in used:
        n += 1
    return f'{prefix}{n}'


def node(graph, node_id):
    for item in graph['nodes']:
        if item['id'] == node_id:
            return item
    raise GraphError('NO_SUCH_NODE', node_id)


def edge(graph, edge_id):
    for item in graph['edges']:
        if item['id'] == edge_id:
            return item
    raise GraphError('NO_SUCH_EDGE', edge_id)


def add_node(graph, *, role, path, span, anchor='', source='model', node_id=None, root=None) -> dict:
    root = Path(root or state.WORKSPACE)
    if role not in ROLES:
        raise GraphError('BAD_ROLE', f'role must be one of {ROLES}')
    rel = relative_path(path)
    text = span_text(root, rel, span)
    node_id = node_id or _next_id(graph['nodes'], 'N')
    value = {'id': node_id, 'role': role, 'path': rel, 'anchor': anchor or '', 'span': [int(span[0]), int(span[1])],
             'source': source, 'text_sha256': text_digest(text)}
    existing = [i for i, item in enumerate(graph['nodes']) if item['id'] == node_id]
    if existing:
        graph['nodes'][existing[0]] = value
    else:
        graph['nodes'].append(value)
    return value


def add_edge(graph, *, edge_type, source, target, status='UNKNOWN', note='', edge_id=None) -> dict:
    if edge_type not in EDGE_TYPES:
        raise GraphError('BAD_TYPE', f'type must be one of {EDGE_TYPES}')
    if status not in EDGE_STATUS:
        raise GraphError('BAD_STATUS', f'status must be one of {EDGE_STATUS}')
    node(graph, source), node(graph, target)
    edge_id = edge_id or _next_id(graph['edges'], 'E')
    value = {'id': edge_id, 'type': edge_type, 'from': source, 'to': target, 'status': status, 'note': note}
    existing = [i for i, item in enumerate(graph['edges']) if item['id'] == edge_id]
    if existing:
        value['probe'] = graph['edges'][existing[0]].get('probe')
        graph['edges'][existing[0]] = value
    else:
        graph['edges'].append(value)
    return value


def set_edge(graph, edge_id, *, status=None, note=None) -> dict:
    item = edge(graph, edge_id)
    if status is not None:
        if status not in EDGE_STATUS:
            raise GraphError('BAD_STATUS', f'status must be one of {EDGE_STATUS}')
        item['status'] = status
    if note is not None:
        item['note'] = note
    return item


def add_preserve(graph, *, edge_id, scenario) -> dict:
    edge(graph, edge_id)
    value = {'edge': edge_id, 'scenario': scenario}
    graph['preserve'].append(value)
    return value


def decide(graph, *, of, candidate, decision, reason='') -> dict:
    if decision not in DECISIONS:
        raise GraphError('BAD_DECISION', f'decision must be one of {DECISIONS}')
    if decision == 'NO_FIX' and not reason.strip():
        raise GraphError('REASON_REQUIRED', 'NO_FIX needs --reason')
    edge(graph, of), node(graph, candidate)
    for item in graph['closure']:
        if item['of'] == of and item['candidate'] == candidate:
            item.update(decision=decision, reason=reason)
            return item
    value = {'of': of, 'candidate': candidate, 'decision': decision, 'reason': reason}
    graph['closure'].append(value)
    return value


def add_unknown(graph, *, question, check='') -> dict:
    value = {'question': question, 'check': check}
    graph['unknown'].append(value)
    return value


def validate(graph, root=None) -> dict:
    """Format problems plus anchors whose current text no longer matches the recorded span."""
    root = Path(root or state.WORKSPACE)
    report = {'format_problems': format_problems(graph), 'stale_nodes': [], 'missing_nodes': []}
    for item in graph.get('nodes', []):
        try:
            text = span_text(root, item['path'], item['span'])
        except GraphError as exc:
            report['missing_nodes'].append({'id': item['id'], 'problem': str(exc)})
            continue
        if item.get('text_sha256') and text_digest(text) != item['text_sha256']:
            report['stale_nodes'].append(item['id'])
    report['valid'] = not report['format_problems'] and not report['missing_nodes']
    return report
