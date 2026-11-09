"""Shared file inventory for the static linkers (high recall, model confirms)."""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = ('vendor/', 'node_modules/', '.contractedge-deps/', '.ce75-public-logs/', 'third_party/', '.git/')
MAX_FILE_BYTES = 1_000_000
TEXT_SUFFIXES = ('.go', '.yaml', '.yml', '.json', '.tpl', '.txt', '.py', '.ts', '.js', '.sh', '.toml', '.cue',
                 '.md', '.env', '.conf', '.cfg', '.ini', '.jsonnet', '.libsonnet', '.java', '.rs', '.tf', '.hcl')


def candidate(linker, edge_type, source, target, evidence) -> dict:
    return {'linker': linker, 'type': edge_type, 'from': source, 'to': target, 'evidence': evidence}


def loc(path, line, anchor='') -> dict:
    return {'path': path, 'line': line, 'anchor': anchor}


class LinkerTimeout(RuntimeError):
    """The linker time budget ran out; results so far are kept and reported as partial."""


@dataclass
class Repo:
    root: Path
    _files: list[str] | None = None
    _texts: dict[str, list[str]] = field(default_factory=dict)
    focus: set[str] | None = None        # files that can match the seed (git grep prefilter)
    deadline: float | None = None        # time.monotonic() budget for one ce-links call

    def inventory(self) -> list[str]:
        if self._files is None:
            try:
                out = subprocess.run(['git', '-c', 'safe.directory=*', 'ls-files', '-co', '--exclude-standard', '-z'],
                                     cwd=self.root, capture_output=True, timeout=60, check=True).stdout
                names = [n for n in out.decode(errors='replace').split('\0') if n]
            except (OSError, subprocess.SubprocessError):
                names = [p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file()]
            self._files = sorted(n for n in names if not n.startswith(SKIP_DIRS) and '/vendor/' not in n
                                 and (n.endswith(TEXT_SUFFIXES) or '.' not in n.rsplit('/', 1)[-1]))
        return self._files

    def files(self) -> list[str]:
        names = self.inventory()
        return names if self.focus is None else [n for n in names if n in self.focus]

    def prefilter(self, tokens, always=()) -> None:
        """Restrict scans to files containing any token (case-insensitive) plus `always`."""
        tokens = [t for t in dict.fromkeys(tokens) if t and len(t) >= 2][:80]
        if not tokens:
            return
        argv = ['git', '-c', 'safe.directory=*', 'grep', '--untracked', '-l', '-I', '-i', '-F', '-z']
        for token in tokens:
            argv += ['-e', token]
        argv += ['--', '.', ':!vendor', ':!**/vendor/**', ':!node_modules', ':!.contractedge-deps']
        try:
            out = subprocess.run(argv, cwd=self.root, capture_output=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            return
        self.focus = {n for n in out.decode(errors='replace').split('\0') if n} | set(always)

    def lines(self, path: str) -> list[str]:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise LinkerTimeout('linker time budget exhausted')
        if path not in self._texts:
            target = self.root / path
            try:
                if target.is_symlink() or target.stat().st_size > MAX_FILE_BYTES:
                    self._texts[path] = []
                else:
                    self._texts[path] = target.read_text(errors='replace').splitlines()
            except OSError:
                self._texts[path] = []
        return self._texts[path]

    def grep(self, pattern: re.Pattern, *, suffixes=None, limit=200):
        hits = []
        for path in self.files():
            if suffixes and not path.endswith(suffixes):
                continue
            for number, line in enumerate(self.lines(path), 1):
                if pattern.search(line):
                    hits.append((path, number, line.strip()[:200]))
                    if len(hits) >= limit:
                        return hits
        return hits


def is_helm_template(path: str) -> bool:
    return '/templates/' in '/' + path and path.endswith(('.yaml', '.yml', '.tpl', '.txt'))


def is_values(path: str) -> bool:
    name = path.rsplit('/', 1)[-1]
    return name.startswith('values') and name.endswith(('.yaml', '.yml'))


def yaml_key_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Dotted key paths for each mapping key line, from indentation (good enough for values files)."""
    stack: list[tuple[int, str]] = []
    result = []
    for number, raw in enumerate(lines, 1):
        stripped = raw.split('#', 1)[0].rstrip()
        match = re.match(r'^(\s*)(?:-\s+)?([A-Za-z0-9_.\-"\']+)\s*:(\s|$)', stripped)
        if not match:
            continue
        indent = len(match.group(1))
        key = match.group(2).strip('"\'')
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        result.append((number, '.'.join(k for _, k in stack)))
    return result
