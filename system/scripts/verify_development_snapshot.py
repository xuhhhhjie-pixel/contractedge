"""Verify a development freeze without changing the original delivery manifest.

The source map is exact for every .py/.json under src/. Other maps bind the
explicit public tools, configuration files and fixed test panel of that freeze.
No runtime output, benchmark data, model endpoint or Git metadata is read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


class SnapshotFormatError(ValueError):
    """The snapshot cannot safely describe a development freeze."""


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _relative(name: Any) -> str:
    if not isinstance(name, str) or not name or "\\" in name:
        raise SnapshotFormatError(f"Invalid relative path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or any(part in {".", ".."} or ":" in part for part in path.parts):
        raise SnapshotFormatError(f"Invalid relative path: {name!r}")
    return name


def _file_map(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise SnapshotFormatError(f"{label} must be a nonempty file/hash map")
    for name, digest in value.items():
        _relative(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SnapshotFormatError(f"Invalid SHA256 in {label}: {name}")
    return value


def _bound_file(root: Path, name: str) -> Path:
    path = root / _relative(name)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
        raise SnapshotFormatError(f"Symlink is not a frozen regular file: {name}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise SnapshotFormatError(f"File escapes verification root: {name}")
    return path


def verify_snapshot(root: Path, snapshot: dict[str, Any], *, allow_legacy_unbound: bool = False,
                    require_current_bindings: bool = False) -> dict[str, Any]:
    """Return an inspectable PASS/FAIL report; malformed or stale freezes fail."""
    root = Path(root).resolve()
    errors: list[str] = []
    coverage: dict[str, int] = {}
    unbound: list[str] = []
    try:
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "ce.development_snapshot.v1":
            raise SnapshotFormatError("Unsupported development snapshot schema")
        if snapshot.get("formal_ready") is not False:
            raise SnapshotFormatError("A development snapshot must preserve formal_ready=false")
        if not isinstance(snapshot.get("development_snapshot_id"), str) or not snapshot["development_snapshot_id"]:
            raise SnapshotFormatError("Missing development_snapshot_id")

        def digest_field(value: Any, field: str) -> None:
            if canonical_digest(value) != snapshot.get(field):
                errors.append(f"Digest mismatch: {field}")

        def files_match(base: Path, values: Any, label: str) -> dict[str, str]:
            mapping = _file_map(values, label)
            coverage[label] = len(mapping)
            for name, expected in sorted(mapping.items()):
                path = _bound_file(base, name)
                if not path.is_file():
                    errors.append(f"Missing {label} file: {name}")
                elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    errors.append(f"Modified {label} file: {name}")
            return mapping

        source = files_match(root / "src", snapshot.get("files"), "source")
        if any(Path(name).suffix not in {".py", ".json"} for name in source):
            raise SnapshotFormatError("Source map may contain only .py/.json files")
        if any(path.is_symlink() for path in (root / "src").rglob("*")):
            raise SnapshotFormatError("Symlink in source tree is not a frozen regular file")
        actual = {path.relative_to(root / "src").as_posix() for path in (root / "src").rglob("*")
                  if path.suffix in {".py", ".json"} and (path.is_file() or path.is_symlink())}
        for name in sorted(actual - source.keys()):
            errors.append(f"Extra source file: {name}")
        digest_field(source, "source_sha256")

        # The oldest v1 producer used fixture_configuration; later producers
        # consistently use configuration. Their canonical digest is identical.
        configurations = [snapshot[key] for key in ("configuration", "fixture_configuration") if key in snapshot]
        if len(configurations) != 1 or not isinstance(configurations[0], dict):
            raise SnapshotFormatError("Exactly one configuration or fixture_configuration object is required")
        configuration = configurations[0]
        digest_field(configuration, "configuration_sha256")
        if "stage_configuration_files" in configuration:
            files_match(root, configuration["stage_configuration_files"], "configuration")

        panel = snapshot.get("fixture_panel")
        if not isinstance(panel, list) or not panel or any(not isinstance(item, str) for item in panel):
            raise SnapshotFormatError("fixture_panel must be a nonempty path list")
        panel = [_relative(item) for item in panel]
        if len(panel) != len(set(panel)):
            raise SnapshotFormatError("Duplicate path in fixture_panel")
        digest_field(panel, "fixture_panel_sha256")
        if "fixture_files" in snapshot:
            fixtures = files_match(root, snapshot["fixture_files"], "fixture")
            if set(fixtures) != set(panel):
                errors.append("fixture_files must bind exactly fixture_panel (missing or extra panel hashes)")
        elif allow_legacy_unbound:
            unbound.extend(panel)
        else:
            errors.append("Missing fixture_files; legacy list-only panel needs explicit --allow-legacy-unbound")

        # tooling_files is the existing public-failure snapshot spelling.
        if "tooling_files" in snapshot and "tool_files" in snapshot:
            raise SnapshotFormatError("Conflicting tooling_files and tool_files aliases")
        for key in ("tooling_files", "tool_files"):
            if key in snapshot:
                mapping = files_match(root, snapshot[key], key)
                if key + "_sha256" in snapshot:
                    digest_field(mapping, key + "_sha256")
        if require_current_bindings:
            if "stage_configuration_files" not in configuration:
                raise SnapshotFormatError("Current snapshot requires configuration.stage_configuration_files")
            tool_keys = [key for key in ("tooling_files", "tool_files") if key in snapshot]
            if len(tool_keys) != 1 or tool_keys[0] + "_sha256" not in snapshot:
                raise SnapshotFormatError("Current snapshot requires one tooling file map and its SHA256 digest")
        if "repository_files" in snapshot:
            mapping = files_match(root.parent, snapshot["repository_files"], "repository_files")
            digest_field(mapping, "repository_files_sha256")
    except (SnapshotFormatError, OSError, ValueError, TypeError) as exc:
        errors.append(str(exc))
    return {
        "schema_version": "ce.development_snapshot_verification.v1",
        "development_snapshot_id": snapshot.get("development_snapshot_id") if isinstance(snapshot, dict) else None,
        "status": "FAIL" if errors else ("LEGACY_UNBOUND" if unbound else "PASS"),
        "formal_ready": False,
        "coverage": coverage,
        "unbound_legacy_fixture_files": unbound,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="system directory")
    parser.add_argument("--snapshot", type=Path, help="snapshot path relative to --root; default: CURRENT_STATUS current_development.snapshot")
    parser.add_argument("--require-current-bindings", action="store_true", help="require explicit config/tool file bindings when selecting a snapshot")
    parser.add_argument("--allow-legacy-unbound", action="store_true", help="inspect the oldest list-only v1 panel; reports LEGACY_UNBOUND, never PASS")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.allow_legacy_unbound and args.snapshot is None:
            raise SnapshotFormatError("Legacy inspection requires an explicit --snapshot")
        name = args.snapshot
        if name is None:
            status = json.loads((root / "CURRENT_STATUS.json").read_text(encoding="utf-8"))
            name = Path(status["current_development"]["snapshot"])
        path = name if name.is_absolute() else root / name
        if not path.resolve().is_relative_to(root):
            raise SnapshotFormatError("Snapshot path escapes system root")
        report = verify_snapshot(root, json.loads(path.read_text(encoding="utf-8")),
                                 allow_legacy_unbound=args.allow_legacy_unbound,
                                 require_current_bindings=args.require_current_bindings or args.snapshot is None)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report = {"status": "FAIL", "formal_ready": False, "errors": [str(exc)]}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
