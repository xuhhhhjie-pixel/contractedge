"""Static linkers. Provisional versions written against the frozen interface (to be replaced by the
graph/linker owner); each returns candidate edges, the model confirms them into the graph."""
from __future__ import annotations

import re
import time
from pathlib import Path

from contractedge.graph_repair.linkers import crd, deps, env_flags, go_tags, helm, kustomize
from contractedge.graph_repair.linkers.base import Repo

LINKERS = {'helm': helm.link, 'kustomize': kustomize.link, 'go_tags': go_tags.link, 'crd': crd.link,
           'env_flags': env_flags.link, 'deps': deps.link}


def parse_seed(root: Path, seed: str):
    """`path`, `path:line`, or a bare token (key, field, env name)."""
    m = re.match(r'^(.+?):(\d+)$', seed)
    if m and (root / m.group(1)).is_file():
        return m.group(1), int(m.group(2))
    if (root / seed).is_file():
        return seed, None
    return None, None


IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_-]{2,}')
COMMON = {'the', 'and', 'for', 'func', 'return', 'string', 'true', 'false', 'nil', 'err', 'if', 'else', 'var', 'const',
          'type', 'struct', 'package', 'import', 'end', 'with', 'range', 'define', 'include', 'name', 'value', 'Values',
          'metadata', 'spec', 'kind', 'apiVersion', 'json', 'yaml', 'omitempty', 'int', 'bool', 'error', 'self', 'def'}
BUDGET_SECONDS = 25


def seed_tokens(repo: Repo, seed: str, path: str | None, line: int | None) -> list[str]:
    """Words that any matching file must contain; used only to narrow which files are scanned."""
    if path is None:
        parts = [p for p in re.split(r'[.\s]', seed.removeprefix('.Values.')) if p]
        return [seed.removeprefix('.Values.')] + parts
    text = repo.lines(path)
    window = text[line - 1:line] if line else text
    words = [w for l in window for w in IDENT.findall(l) if w not in COMMON]
    if not line:
        words = sorted(set(words), key=len, reverse=True)[:60]
    tokens = list(dict.fromkeys(words))[:60] + [path.rsplit('/', 1)[-1]]
    if line is None and path.rsplit('/', 1)[-1].startswith('values'):
        tokens.append('.Values')
    return tokens


def run(root: Path, seed: str, names=None, limit: int = 120, budget: float = BUDGET_SECONDS) -> dict:
    repo = Repo(Path(root))
    path, line = parse_seed(Path(root), seed.removeprefix('/testbed/'))
    always = {n for n in repo.inventory() if n.rsplit('/', 1)[-1] in ('kustomization.yaml', 'kustomization.yml', 'Kustomization')}
    always |= {path} if path else set()
    repo.prefilter(seed_tokens(repo, seed, path, line), always)
    repo.deadline = time.monotonic() + budget
    found, errors = [], {}
    for name in names or LINKERS:
        try:
            found += LINKERS[name](repo, seed, path, line)
        except Exception as exc:  # one broken or slow linker must not hide the others
            errors[name] = f'{type(exc).__name__}: {str(exc)[:200]}'
    unique, seen = [], set()
    for item in found:
        key = (item['linker'], item['from']['path'], item['from']['line'], item['to']['path'], item['to']['line'])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return {'seed': seed, 'seed_path': path, 'seed_line': line, 'count': len(unique),
            'truncated': len(unique) > limit, 'candidates': unique[:limit], 'linker_errors': errors,
            'partial': any('LinkerTimeout' in v for v in errors.values()),
            'hint': 'partial: the seed is too common; use a path:line seed or a more specific key' if any(
                'LinkerTimeout' in v for v in errors.values()) else ''}
