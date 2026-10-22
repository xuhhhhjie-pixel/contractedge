#!/usr/bin/env python3
"""Reproduce bounded MethodInput scope resolution for an isolated public release."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from contractedge.contracts.validation import parse_method_input
from contractedge.dataset.archive import safe_extract
from contractedge.source.scopes import resolve_scope
from contractedge.source.snapshot import compute_snapshot


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    return path


def _source_root(extracted_root: Path, configured: str) -> Path:
    if configured == ".":
        return extracted_root
    relative = Path(configured)
    appended = extracted_root / relative
    if appended.exists():
        return appended
    if relative.parts and relative.parts[0] == extracted_root.name:
        stripped = extracted_root.joinpath(*relative.parts[1:])
        if stripped.exists():
            return stripped
    raise ValueError(f"cannot resolve source_root={configured!r} below extracted root {extracted_root}")


def audit(public_root: Path, *, max_search_files: int, max_search_bytes: int,
          max_search_file_bytes: int, max_matches_per_hint: int) -> dict[str, Any]:
    public_root = public_root.resolve(strict=True)
    manifest = public_root / "manifest.jsonl"
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    limits = {
        "max_search_files": max_search_files,
        "max_search_bytes": max_search_bytes,
        "max_search_file_bytes": max_search_file_bytes,
        "max_matches_per_hint": max_matches_per_hint,
    }
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        method_path = _inside(public_root, row["method_input"])
        archive_path = _inside(public_root, row["broken_source"])
        method = parse_method_input(json.loads(method_path.read_text(encoding="utf-8")))
        if method.case_id != row["case_id"] or method.release_id != row["release_id"]:
            raise ValueError(f"manifest/MethodInput identity mismatch: {row['case_id']}")
        archive_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        if archive_digest != row["broken_source_sha256"] or archive_digest != method.archive_sha256:
            raise ValueError(f"Broken source archive digest mismatch: {method.case_id}")
        with tempfile.TemporaryDirectory(prefix="contractedge-scope-audit-") as temporary:
            extracted = safe_extract(archive_path, Path(temporary) / "source")
            source_root = _source_root(Path(extracted.source_root), method.source_root)
            snapshot = compute_snapshot(source_root)
            if snapshot.snapshot_id != method.initial_snapshot_id:
                raise ValueError(f"initial snapshot mismatch: {method.case_id}")
            scope = resolve_scope(source_root, snapshot, method.scope_hints, **limits)
        results.append({
            "case_id": method.case_id,
            "release_id": method.release_id,
            "hint_count": len(method.scope_hints),
            "file_count": len(snapshot.file_digests),
            "seed_count": len(scope.seed_paths),
            "resolved_seed_paths": list(scope.seed_paths),
            "unresolved": list(scope.unresolved),
            "search_audit": list(scope.search_audit),
        })
        if index % 10 == 0 or index == len(rows):
            print(f"audited {index}/{len(rows)} cases", flush=True)

    counts = Counter(
        "INCOMPLETE" if any(item.get("truncated") or item.get("results_capped") for item in row["unresolved"])
        else "UNRESOLVED" if row["unresolved"] else "RESOLVED"
        for row in results
    )
    seeds = [row["seed_count"] for row in results]
    by_release: dict[str, dict[str, int]] = {}
    for release in sorted({row["release_id"] for row in results}):
        members = [row for row in results if row["release_id"] == release]
        by_release[release] = dict(Counter(
            "INCOMPLETE" if any(item.get("truncated") or item.get("results_capped") for item in row["unresolved"])
            else "UNRESOLVED" if row["unresolved"] else "RESOLVED"
            for row in members
        ))
    return {
        "schema_version": "ce.source_scope_audit.v1",
        "release_members": len(rows),
        "search_limits": limits,
        "status_counts": dict(counts),
        "status_counts_by_release": by_release,
        "seed_count": {
            "min": min(seeds, default=0),
            "median": statistics.median(seeds) if seeds else 0,
            "max": max(seeds, default=0),
        },
        "hint_counts": {
            "total": sum(row["hint_count"] for row in results),
            "incomplete_searches": sum(bool(item.get("truncated")) for row in results for item in row["search_audit"]),
            "capped_match_lists": sum(bool(item.get("match_cap_applied")) for row in results for item in row["search_audit"]),
            "zero_match": sum(not item.get("matched_paths") for row in results for item in row["search_audit"]),
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", type=Path, default=Path("outputs/pilot-v1_1-r1-r2-v2/public"))
    parser.add_argument("--output", type=Path, default=Path("outputs/source-scope-conformance-r1-r2-v2.json"))
    parser.add_argument("--max-search-files", type=int, default=20_000)
    parser.add_argument("--max-search-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-search-file-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--max-matches-per-hint", type=int, default=64)
    args = parser.parse_args()
    report = audit(args.public_root, max_search_files=args.max_search_files,
                   max_search_bytes=args.max_search_bytes,
                   max_search_file_bytes=args.max_search_file_bytes,
                   max_matches_per_hint=args.max_matches_per_hint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("release_members", "status_counts", "status_counts_by_release", "seed_count", "hint_counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
