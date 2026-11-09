"""ce-search / ce-read / ce-follow over the live /testbed through the existing SourceNavigator.

The snapshot is the current work tree (edited files included). Its id is constant so a handle
depends only on the file's bytes and offsets: a handle stays valid until that file changes, and
a new process re-creates it by indexing the file again.
"""
from __future__ import annotations

import atexit
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from contractedge.graph_repair import state
from contractedge.graph_repair.linkers.base import Repo
from contractedge.source.navigation import NavigationError, SourceNavigator

MAX_DIGEST_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class LiveSnapshot:
    snapshot_id: str
    source_root: str
    file_digests: Mapping


class LazyDigests(Mapping):
    """Inventory from git; a file is hashed only when the navigator looks at it (large repos)."""

    def __init__(self, root: Path):
        self.root = root
        self.names = Repo(root).inventory()
        self.known = set(self.names)
        self.cache_path = state.STATE / 'nav-digests.json'
        self.cache = state.read_json(self.cache_path, {}) if state.STATE.exists() else {}
        self.dirty = False
        atexit.register(self.flush)

    def __contains__(self, name):
        return name in self.known

    def __iter__(self):
        return iter(self.names)

    def __len__(self):
        return len(self.names)

    def __getitem__(self, name):
        if name not in self.known:
            raise KeyError(name)
        target = self.root / name
        try:
            info = target.lstat()
        except OSError:
            return ''
        if not target.is_file() or target.is_symlink() or info.st_size > MAX_DIGEST_BYTES:
            return ''
        key = f'{info.st_size}:{info.st_mtime_ns}'
        row = self.cache.get(name)
        if not row or row[0] != key:
            row = [key, hashlib.sha256(target.read_bytes()).hexdigest()]
            self.cache[name] = row
            self.dirty = True
        return row[1]

    def flush(self):
        if self.dirty and state.STATE.exists():
            state.write_json(self.cache_path, self.cache)
            self.dirty = False


def _digests(root: Path):
    return LazyDigests(root)


def navigator(root: Path | None = None) -> SourceNavigator:
    root = Path(root or state.WORKSPACE)
    return SourceNavigator(LiveSnapshot('testbed-live', str(root), _digests(root)))


def _handles() -> dict:
    return state.read_json(state.STATE / 'handles.json', {}) or {}


def _remember(result) -> None:
    """Keep handle -> path so a later process can index the right file before using it."""
    if not state.STATE.exists():
        return
    found = {}

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('handle'), str) and isinstance(value.get('path'), str):
                found[value['handle']] = value['path']
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(result)
    if found:
        with state.locked('handles'):
            data = _handles()
            data.update(found)
            state.write_json(state.STATE / 'handles.json', data)


def _prepare(nav: SourceNavigator, handle: str) -> None:
    path = _handles().get(handle)
    if path and handle not in nav._handles:
        try:
            nav._index(path)
        except NavigationError:
            pass
    if handle not in nav._handles:
        raise NavigationError('UNKNOWN_HANDLE', 'Handle unknown or its file changed since; run ce-search again')


def search(**query) -> dict:
    nav = navigator()
    result = nav.search(**query)
    _remember(result)
    return result


def read(handle=None, **query) -> dict:
    nav = navigator()
    if handle:
        _prepare(nav, handle)
    result = nav.read(handle, **query)
    _remember(result)
    return result


def follow(handle, selector=None, cursor=None) -> dict:
    nav = navigator()
    _prepare(nav, handle)
    result = nav.follow_dependency(handle, selector=selector, cursor=cursor)
    _remember(result)
    return result


def dumps(value, limit=12000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=1, default=str)
    if len(text) > limit:
        text = text[:limit] + f'\n... [{len(text) - limit} more characters; narrow the scope or page with --cursor]'
    return text
