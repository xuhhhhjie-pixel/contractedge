from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from contractedge.contracts.models import Snapshot


class SnapshotError(ValueError):
    pass


def compute_snapshot(source_root: str | Path) -> Snapshot:
    root = Path(source_root).resolve(strict=True)
    if not root.is_dir():
        raise SnapshotError(f"source root is not a directory: {root}")
    digests: dict[str, str] = {}
    paths = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in {".git", ".contractedge-deps"}
                         and not (Path(directory) / name).is_symlink())
        paths.extend(Path(directory) / name for name in names if name not in {".git", ".contractedge-deps"})
    for path in sorted(paths):
        if path.is_symlink():
            # Do not follow symlinks into an unregistered tree. Archive preparation validates them.
            continue
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            try:
                digests[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                raise SnapshotError(f"cannot read source file {relative}: {exc}") from exc
    canonical = json.dumps(digests, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    snapshot_id = hashlib.sha256(canonical).hexdigest()
    return Snapshot(snapshot_id=snapshot_id, source_root=str(root), file_digests=digests)
