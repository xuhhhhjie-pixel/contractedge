"""Go struct tags json/yaml:"x" <-> field reads/writes, constructor params and config files using key x."""
from __future__ import annotations

import re

from contractedge.graph_repair.linkers.base import candidate, loc

FIELD = re.compile(r'^\s*([A-Z][A-Za-z0-9_]*)\s+[^`]*`[^`]*(?:json|yaml|mapstructure|protobuf)[:]"([^",]+)')


def fields(repo, seed_path=None, seed_line=None, seed=None):
    """(field, tag, path, line) declarations matching the seed."""
    found = []
    paths = [seed_path] if seed_path and seed_path.endswith('.go') else [p for p in repo.files() if p.endswith('.go')]
    for path in paths:
        for number, line in enumerate(repo.lines(path), 1):
            m = FIELD.match(line)
            if not m:
                continue
            if seed_path and seed_line and abs(number - seed_line) > 0:
                continue
            if seed and seed not in (m.group(1), m.group(2)):
                continue
            found.append((m.group(1), m.group(2), path, number))
    return found


def link(repo, seed: str, seed_path: str | None = None, seed_line: int | None = None) -> list[dict]:
    decls = fields(repo, seed_path, seed_line, None if seed_path else seed)[:20]
    out = []
    go_files = [p for p in repo.files() if p.endswith('.go') and not p.endswith('_test.go')]
    data_files = [p for p in repo.files() if p.endswith(('.yaml', '.yml', '.json'))]
    for field_name, tag, path, number in decls:
        source = loc(path, number, f'{field_name} `{tag}`')
        use = re.compile(r'\.' + re.escape(field_name) + r'\b')
        literal = re.compile(r'\b' + re.escape(field_name) + r'\s*:')
        for go in go_files:
            for n, line in enumerate(repo.lines(go), 1):
                if (go, n) == (path, number):
                    continue
                if use.search(line) or literal.search(line):
                    out.append(candidate('go_tags', 'BINDING', source, loc(go, n, field_name), line.strip()[:160]))
        key = re.compile(r'^\s*-?\s*"?' + re.escape(tag) + r'"?\s*:')
        for data in data_files:
            for n, line in enumerate(repo.lines(data), 1):
                if key.search(line):
                    out.append(candidate('go_tags', 'BINDING', loc(data, n, tag), source, line.strip()[:160]))
    return out[:300]
