"""contractedge.contract_graph.v1: format checks only, plus the one anchor check."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath

SCHEMA = 'contractedge.contract_graph.v1'
ROLES = ('CONFIG_DECLARATION', 'PRODUCTION_CONSUMER', 'GENERATED_STATE', 'RUNTIME_CONSUMER', 'OBSERVED_STATE')
EDGE_TYPES = ('PROPAGATION', 'TRANSFORMATION', 'BINDING', 'SYNCHRONIZATION', 'RECONCILIATION')
EDGE_STATUS = ('EXPECTED', 'VIOLATED', 'UNKNOWN')
DECISIONS = ('UNDECIDED', 'NEEDS_FIX', 'NO_FIX')
ID = re.compile(r'^[A-Za-z][A-Za-z0-9_.-]{0,39}$')


class GraphError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f'{code}: {message}')
        self.code = code


def empty() -> dict:
    return {'schema': SCHEMA, 'nodes': [], 'edges': [], 'preserve': [], 'closure': [], 'unknown': []}


def relative_path(value: str) -> str:
    value = (value or '').strip()
    while value.startswith('./'):
        value = value[2:]
    if value.startswith('/testbed/'):
        value = value[len('/testbed/'):]
    parts = PurePosixPath(value).parts
    if not value or PurePosixPath(value).is_absolute() or '..' in parts or '\\' in value:
        raise GraphError('BAD_PATH', f'{value!r} must be a path relative to /testbed')
    return PurePosixPath(value).as_posix()


def span_text(root: Path, path: str, span) -> str:
    """The hard check: a real regular file inside the workspace and a span within it."""
    target = (root / path)
    if target.is_symlink() or not target.is_file():
        raise GraphError('NO_SUCH_FILE', f'{path} is not a regular file in the workspace')
    if not target.resolve().is_relative_to(root.resolve()):
        raise GraphError('OUTSIDE_WORKSPACE', path)
    try:
        start, end = int(span[0]), int(span[1])
    except (TypeError, ValueError, IndexError):
        raise GraphError('BAD_SPAN', f'span {span!r} must be [start, end]') from None
    lines = target.read_text(errors='replace').splitlines()
    if not 1 <= start <= end <= max(len(lines), 1):
        raise GraphError('BAD_SPAN', f'{path} has {len(lines)} lines; span {start}-{end} is outside it')
    return '\n'.join(lines[start - 1:end])


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def format_problems(graph: dict) -> list[str]:
    """Structural problems; content is the model's responsibility."""
    problems = []
    if graph.get('schema') != SCHEMA:
        problems.append(f'schema must be {SCHEMA}')
    for key in ('nodes', 'edges', 'preserve', 'closure', 'unknown'):
        if not isinstance(graph.get(key), list):
            problems.append(f'{key} must be a list')
    if problems:
        return problems
    node_ids = set()
    for node in graph['nodes']:
        if not ID.match(str(node.get('id', ''))):
            problems.append(f'node id {node.get("id")!r} is invalid')
        if node.get('id') in node_ids:
            problems.append(f'duplicate node {node.get("id")}')
        node_ids.add(node.get('id'))
        if node.get('role') not in ROLES:
            problems.append(f'node {node.get("id")}: role must be one of {ROLES}')
        if not isinstance(node.get('path'), str) or not isinstance(node.get('span'), list) or len(node['span']) != 2:
            problems.append(f'node {node.get("id")}: path and span [start, end] are required')
    edge_ids = set()
    for edge in graph['edges']:
        if not ID.match(str(edge.get('id', ''))) or edge.get('id') in edge_ids:
            problems.append(f'edge id {edge.get("id")!r} is invalid or duplicated')
        edge_ids.add(edge.get('id'))
        if edge.get('type') not in EDGE_TYPES:
            problems.append(f'edge {edge.get("id")}: type must be one of {EDGE_TYPES}')
        if edge.get('status') not in EDGE_STATUS:
            problems.append(f'edge {edge.get("id")}: status must be one of {EDGE_STATUS}')
        for end in ('from', 'to'):
            if edge.get(end) not in node_ids:
                problems.append(f'edge {edge.get("id")}: {end} {edge.get(end)!r} is not a node')
    for item in graph['preserve']:
        if item.get('edge') not in edge_ids or not item.get('scenario'):
            problems.append(f'preserve item {item} needs an existing edge and a scenario')
    for item in graph['closure']:
        if item.get('of') not in edge_ids or item.get('candidate') not in node_ids:
            problems.append(f'closure item {item} needs an existing edge and candidate node')
        if item.get('decision') not in DECISIONS:
            problems.append(f'closure item {item}: decision must be one of {DECISIONS}')
        if item.get('decision') == 'NO_FIX' and not str(item.get('reason', '')).strip():
            problems.append(f'closure item {item.get("of")}/{item.get("candidate")}: NO_FIX needs a reason')
    for item in graph['unknown']:
        if not item.get('question'):
            problems.append('unknown items need a question')
    return problems
