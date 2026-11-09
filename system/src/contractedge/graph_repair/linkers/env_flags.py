"""ConfigMap keys, environment variables and command-line flags <-> the code that reads them."""
from __future__ import annotations

import re

from contractedge.graph_repair.linkers.base import candidate, loc

GO_ENV = re.compile(r'os\.(?:Getenv|LookupEnv)\(\s*"([A-Za-z0-9_]+)"')
GO_FLAG = re.compile(r'(?:flag|pflag|Flags\(\)|PersistentFlags\(\)|fs|flags)\.\w*?(?:String|Bool|Int|Duration|Float|StringSlice|StringArray|Uint)\w*(?:Var|VarP|P)?\(\s*(?:&[\w.]+\s*,\s*)?"([A-Za-z0-9_.-]+)"')
PY_ENV = re.compile(r'os\.(?:environ(?:\.get)?|getenv)\s*[\[(]\s*["\']([A-Za-z0-9_]+)["\']')
YAML_ENV = re.compile(r'^\s*-\s*name:\s*"?([A-Z][A-Z0-9_]+)"?\s*$')
ARG_FLAG = re.compile(r'--([a-z][a-z0-9.-]+)')
CM_KEY = re.compile(r'^\s{2}([A-Za-z0-9_.-]+):\s')


def readers(repo):
    for path in repo.files():
        if path.endswith('.go') or path.endswith('.py'):
            for n, line in enumerate(repo.lines(path), 1):
                for pattern, kind in ((GO_ENV, 'env'), (PY_ENV, 'env'), (GO_FLAG, 'flag')):
                    for name in pattern.findall(line):
                        yield kind, name, path, n, line.strip()[:160]


def writers(repo):
    for path in repo.files():
        if not path.endswith(('.yaml', '.yml', '.tpl', '.json', '.sh', '.env')) and 'Dockerfile' not in path:
            continue
        for n, line in enumerate(repo.lines(path), 1):
            m = YAML_ENV.match(line)
            if m:
                yield 'env', m.group(1), path, n, line.strip()[:160]
            for name in ARG_FLAG.findall(line):
                yield 'flag', name, path, n, line.strip()[:160]
            m = re.match(r'^\s*(?:export\s+|ENV\s+)?([A-Z][A-Z0-9_]+)=', line)
            if m:
                yield 'env', m.group(1), path, n, line.strip()[:160]


def link(repo, seed: str, seed_path: str | None = None, seed_line: int | None = None) -> list[dict]:
    all_readers = list(readers(repo))
    all_writers = list(writers(repo))
    if seed_path:
        names = {name for kind, name, p, n, _ in all_readers + all_writers
                 if p == seed_path and (not seed_line or n == seed_line)}
    else:
        names = {seed, seed.lstrip('-')}
    out = []
    for kind, name, path, n, text in all_writers:
        if name not in names:
            continue
        for rkind, rname, rpath, rn, rtext in all_readers:
            if rname == name and rkind == kind:
                out.append(candidate('env_flags', 'BINDING', loc(path, n, name), loc(rpath, rn, name), f'{text} -> {rtext}'))
    if not out:
        for kind, name, path, n, text in all_readers + all_writers:
            if name in names:
                out.append(candidate('env_flags', 'BINDING', loc(path, n, name), loc('', 0, name), text))
    return out[:300]
