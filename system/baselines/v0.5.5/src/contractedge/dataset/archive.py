from __future__ import annotations

import hashlib
import json
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from contractedge.contracts.models import Snapshot


class ArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractionLimits:
    max_members: int = 100_000
    max_expanded_bytes: int = 2 * 1024 * 1024 * 1024
    max_file_bytes: int = 512 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveInspection:
    expanded_bytes: int
    source_root: str
    snapshot_id: str
    file_digests: dict[str, str]


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if name in {".", "./"}:
        return PurePosixPath(".")
    if path.is_absolute() or not path.parts or any(part in {"", ".."} for part in path.parts):
        raise ArchiveError(f"unsafe archive path: {name!r}")
    if "\\" in name or "\x00" in name:
        raise ArchiveError(f"unsafe archive path: {name!r}")
    return path


def _resolved_link(member: PurePosixPath, link: str) -> PurePosixPath:
    target = PurePosixPath(link)
    if target.is_absolute() or "\\" in link or "\x00" in link:
        raise ArchiveError(f"unsafe symlink target: {link!r}")
    stack = list(member.parent.parts)
    for part in target.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                raise ArchiveError(f"symlink escapes extraction root: {member} -> {link}")
            stack.pop()
        else:
            stack.append(part)
    if not stack:
        raise ArchiveError(f"symlink resolves to extraction root: {member} -> {link}")
    return PurePosixPath(*stack)


def safe_extract(archive_path: str | Path, destination: str | Path,
                 limits: ExtractionLimits = ExtractionLimits()) -> Snapshot:
    source = Path(archive_path)
    requested_dest = Path(destination)
    if requested_dest.is_symlink():
        raise ArchiveError(f"destination cannot be a symlink: {requested_dest}")
    dest = requested_dest.resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ArchiveError(f"destination must be empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        archive = tarfile.open(source, "r:*")
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveError(f"cannot open archive: {exc}") from exc
    with archive:
        members = archive.getmembers()
        if len(members) > limits.max_members:
            raise ArchiveError(f"archive has {len(members)} members; limit is {limits.max_members}")
        paths: dict[str, tarfile.TarInfo] = {}
        symlinks: list[tuple[PurePosixPath, str]] = []
        expanded = 0
        for member in members:
            rel = _member_path(member.name)
            key = rel.as_posix().rstrip("/")
            if key == ".":
                if member.isdir():
                    continue
                raise ArchiveError("only a directory entry may name the extraction root")
            if key in paths:
                raise ArchiveError(f"duplicate archive member path: {key}")
            if not (member.isdir() or member.isfile() or member.issym()):
                raise ArchiveError(f"unsupported archive member type: {key}")
            if member.isfile():
                if member.size < 0 or member.size > limits.max_file_bytes:
                    raise ArchiveError(f"file size exceeds limit: {key}")
                expanded += member.size
                if expanded > limits.max_expanded_bytes:
                    raise ArchiveError(f"expanded size exceeds limit {limits.max_expanded_bytes}")
            if member.issym():
                target = _resolved_link(rel, member.linkname)
                symlinks.append((rel, target.as_posix()))
            paths[key] = member
        symlink_names = {path.as_posix() for path, _ in symlinks}
        for key in paths:
            parts = PurePosixPath(key).parts
            if any(PurePosixPath(*parts[:i]).as_posix() in symlink_names for i in range(1, len(parts))):
                raise ArchiveError(f"archive member is nested below a symlink: {key}")

        for key, member in paths.items():
            target = dest.joinpath(*PurePosixPath(key).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            elif member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ArchiveError(f"cannot read archive member: {key}")
                with stream, target.open("xb") as output:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
        for rel, link in symlinks:
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            relative_link = os.path.relpath(link, start=rel.parent.as_posix())
            target.symlink_to(relative_link)

    all_files = sorted(path for path in dest.rglob("*") if path.is_file() and not path.is_symlink())
    roots = list(dest.iterdir())
    root_name = roots[0].name if len(roots) == 1 and roots[0].is_dir() and not roots[0].is_symlink() else "."
    selected_root = dest if root_name == "." else dest / root_name
    file_digests: dict[str, str] = {}
    for path in all_files:
        try:
            relative = path.relative_to(selected_root).as_posix()
        except ValueError:
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        file_digests[relative] = digest.hexdigest()
    snapshot_id = hashlib.sha256(json.dumps(file_digests, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return Snapshot(snapshot_id=snapshot_id, source_root=str(selected_root), file_digests=file_digests)


def inspect_archive_size(archive_path: str | Path, limits: ExtractionLimits = ExtractionLimits()) -> int:
    """Validate archive member paths/types and return its expanded regular-file size without extracting."""
    try:
        archive = tarfile.open(archive_path, "r:*")
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveError(f"cannot open archive: {exc}") from exc
    with archive:
        members = archive.getmembers()
        if len(members) > limits.max_members:
            raise ArchiveError(f"archive member count exceeds limit {limits.max_members}")
        seen: set[str] = set()
        expanded = 0
        symlinks: set[str] = set()
        paths: list[str] = []
        for member in members:
            rel = _member_path(member.name)
            key = rel.as_posix().rstrip("/")
            if key == ".":
                if member.isdir():
                    continue
                raise ArchiveError("only a directory entry may name the extraction root")
            if key in seen:
                raise ArchiveError(f"duplicate archive member path: {key}")
            seen.add(key)
            paths.append(key)
            if member.isdir():
                continue
            if member.isfile():
                if member.size < 0 or member.size > limits.max_file_bytes:
                    raise ArchiveError(f"file size exceeds limit: {key}")
                expanded += member.size
                if expanded > limits.max_expanded_bytes:
                    raise ArchiveError(f"expanded size exceeds limit {limits.max_expanded_bytes}")
                continue
            if member.issym():
                _resolved_link(rel, member.linkname)
                symlinks.add(key)
                continue
            raise ArchiveError(f"unsupported archive member type: {key}")
        for key in paths:
            parts = PurePosixPath(key).parts
            if any(PurePosixPath(*parts[:i]).as_posix() in symlinks for i in range(1, len(parts))):
                raise ArchiveError(f"archive member is nested below a symlink: {key}")
        return expanded


def inspect_archive(archive_path: str | Path, limits: ExtractionLimits = ExtractionLimits(),
                    source_root: str | None = None) -> ArchiveInspection:
    """Inspect safely and hash the frozen source tree without writing extracted files."""
    try:
        archive = tarfile.open(archive_path, "r:*")
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveError(f"cannot open archive: {exc}") from exc
    with archive:
        members = archive.getmembers()
        if len(members) > limits.max_members:
            raise ArchiveError(f"archive member count exceeds limit {limits.max_members}")
        seen: set[str] = set()
        symlinks: set[str] = set()
        file_hashes: dict[str, str] = {}
        expanded = 0
        all_paths: list[str] = []
        for member in members:
            rel = _member_path(member.name)
            key = rel.as_posix().rstrip("/")
            if key == ".":
                if member.isdir():
                    continue
                raise ArchiveError("only a directory entry may name the extraction root")
            if key in seen:
                raise ArchiveError(f"duplicate archive member path: {key}")
            seen.add(key)
            all_paths.append(key)
            if member.isdir():
                continue
            if member.issym():
                _resolved_link(rel, member.linkname)
                symlinks.add(key)
                continue
            if not member.isfile():
                raise ArchiveError(f"unsupported archive member type: {key}")
            if member.size < 0 or member.size > limits.max_file_bytes:
                raise ArchiveError(f"file size exceeds limit: {key}")
            expanded += member.size
            if expanded > limits.max_expanded_bytes:
                raise ArchiveError(f"expanded size exceeds limit {limits.max_expanded_bytes}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ArchiveError(f"cannot read archive member: {key}")
            digest = hashlib.sha256()
            actual_size = 0
            with stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    actual_size += len(chunk)
                    digest.update(chunk)
            if actual_size != member.size:
                raise ArchiveError(f"archive member size mismatch: {key}")
            file_hashes[key] = digest.hexdigest()
        for key in all_paths:
            parts = PurePosixPath(key).parts
            if any(PurePosixPath(*parts[:i]).as_posix() in symlinks for i in range(1, len(parts))):
                raise ArchiveError(f"archive member is nested below a symlink: {key}")

    if source_root is None:
        top_levels = {PurePosixPath(path).parts[0] for path in all_paths if PurePosixPath(path).parts}
        top = next(iter(top_levels)) if len(top_levels) == 1 else None
        root_name = top if top and any(path.startswith(top + "/") for path in file_hashes) else "."
    else:
        normalized = _member_path(source_root)
        root_name = normalized.as_posix()
        if root_name == ".":
            pass
        elif any(path != root_name and not path.startswith(root_name.rstrip("/") + "/") for path in file_hashes):
            raise ArchiveError(f"configured source root {root_name!r} does not contain all source files")
        if root_name != "." and not any(path.startswith(root_name.rstrip("/") + "/") for path in file_hashes):
            raise ArchiveError(f"configured source root {root_name!r} contains no source files")
    if root_name == ".":
        relative_hashes = dict(file_hashes)
    else:
        prefix = root_name.rstrip("/") + "/"
        relative_hashes = {path[len(prefix):]: digest for path, digest in file_hashes.items() if path.startswith(prefix)}
    snapshot_id = hashlib.sha256(json.dumps(relative_hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ArchiveInspection(expanded, root_name, snapshot_id, relative_hashes)
