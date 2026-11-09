"""Tool state outside /testbed: the candidate patch exports every untracked file there."""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

WORKSPACE = Path(os.environ.get('CE_WORKSPACE', '/testbed'))
STATE = Path(os.environ.get('CE_STATE', '/ce-state'))
INPUTS = Path(os.environ.get('CE_INPUTS', '/ce-inputs'))


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True))
    os.replace(tmp, path)


def append_jsonl(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')


@contextmanager
def locked(name: str = 'state'):
    """Serialize read-modify-write of state files between tool processes."""
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / f'.{name}.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def run_config() -> dict:
    """Host-written, read-only run inputs; empty when the run has no mechanisms."""
    return read_json(INPUTS / 'run.json', {}) or {}


def switches() -> dict:
    return run_config().get('variant', {})


def now() -> float:
    return time.time()
