"""Helm: values keys <-> .Values references in templates; define <-> include/template/tpl."""
from __future__ import annotations

import re

from contractedge.graph_repair.linkers.base import candidate, is_helm_template, is_values, loc, yaml_key_lines

VALUES_REF = re.compile(r'\.Values((?:\.[A-Za-z0-9_]+)+)')
INDEX_REF = re.compile(r'index\s+\.Values\s+((?:"[^"]+"\s*)+)')
DEFINE = re.compile(r'{{-?\s*define\s+"([^"]+)"')
INCLUDE = re.compile(r'(?:include|template)\s+"([^"]+)"')


def _refs(line):
    keys = [m.group(1).lstrip('.') for m in VALUES_REF.finditer(line)]
    keys += ['.'.join(re.findall(r'"([^"]+)"', m.group(1))) for m in INDEX_REF.finditer(line)]
    return keys


def _related(a: str, b: str) -> bool:
    return a == b or a.startswith(b + '.') or b.startswith(a + '.')


def keys_from_seed(repo, seed_path, seed_line):
    lines = repo.lines(seed_path)
    if is_values(seed_path):
        keyed = yaml_key_lines(lines)
        if seed_line:
            before = [key for number, key in keyed if number <= seed_line]
            return before[-1:] if before else []
        return [key for _, key in keyed]
    if is_helm_template(seed_path):
        window = lines[seed_line - 1:seed_line] if seed_line else lines
        return sorted({key for line in window for key in _refs(line)})
    return []


def link(repo, seed: str, seed_path: str | None = None, seed_line: int | None = None) -> list[dict]:
    keys = keys_from_seed(repo, seed_path, seed_line) if seed_path else [seed.removeprefix('.Values.').strip()]
    keys = [k for k in keys if k][:40]
    if not keys:
        return []
    out = []
    values_files = [p for p in repo.files() if is_values(p)]
    templates = [p for p in repo.files() if is_helm_template(p)]
    declared = {}
    for path in values_files:
        for number, key in yaml_key_lines(repo.lines(path)):
            for wanted in keys:
                if key == wanted or (key.startswith(wanted + '.') and key.count('.') == wanted.count('.') + 1):
                    declared.setdefault(wanted, []).append(loc(path, number, key))
    defines = {}
    for path in templates:
        for number, line in enumerate(repo.lines(path), 1):
            for name in DEFINE.findall(line):
                defines.setdefault(name, []).append(loc(path, number, name))
    for path in templates:
        for number, line in enumerate(repo.lines(path), 1):
            for ref in _refs(line):
                for wanted in keys:
                    if _related(ref, wanted):
                        for source in declared.get(wanted, [loc('', 0, wanted)])[:3]:
                            out.append(candidate('helm', 'PROPAGATION', source, loc(path, number, '.Values.' + ref),
                                                 line.strip()[:160]))
    # Templates that render a consumer via a named template are consumers too.
    consumer_files = {c['to']['path'] for c in out}
    for path in templates:
        for number, line in enumerate(repo.lines(path), 1):
            for name in INCLUDE.findall(line):
                for definition in defines.get(name, []):
                    if definition['path'] in consumer_files and path != definition['path']:
                        out.append(candidate('helm', 'TRANSFORMATION', definition, loc(path, number, f'include "{name}"'),
                                             line.strip()[:160]))
    return out
