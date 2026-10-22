from __future__ import annotations

import hashlib
import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from contractedge.contracts.evidence import static_search_content_digest
from contractedge.contracts.models import Snapshot
from contractedge.source.snapshot import compute_snapshot


def _record(snapshot_id: str, query_kind: str, query: str, inspected: int,
            matched_paths: list[str], *, complete: bool, inspected_bytes: int = 0,
            max_bytes: int | None = None, failure: str | None = None) -> dict[str, Any]:
    locator: dict[str, Any] = {
        "query_kind": query_kind,
        "query": query,
        "scope": "snapshot_file_inventory",
        "inspected_file_count": inspected,
        "inspected_byte_count": inspected_bytes,
        "match_count": len(matched_paths),
        "matched_paths": matched_paths[:50],
        "complete": complete,
        "truncated": not complete,
    }
    if failure:
        locator["incomplete_reason"] = failure
    if max_bytes is not None:
        locator["max_bytes"] = max_bytes
    excerpt = (f"Complete {query_kind.lower()} search found {len(matched_paths)} matching path(s)."
               if complete else f"Bounded {query_kind.lower()} search was incomplete: {failure or 'file limit reached'}.")
    digest = static_search_content_digest(snapshot_id, locator, excerpt)
    evidence_id = "SEARCH-" + digest[:16]
    return {"id": evidence_id, "snapshot_id": snapshot_id, "kind": "STATIC_SEARCH",
            "locator": locator, "content_digest": digest, "excerpt": excerpt,
            "origin": "runner-generated exact search over the current Broken source snapshot"}


def collect_absence_search_evidence(snapshot: Snapshot, evidence_gaps: Sequence[Mapping[str, Any]], *,
                                    max_files: int, max_bytes: int = 64 * 1024 * 1024
                                    ) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """Run exact path/symbol searches over a bounded, snapshot-bound source inventory.

    Search terms are useful for retrieval but do not establish absence. Only exact `path`
    and `symbol` hints produce records; a record proves absence only when its complete
    search has zero matches.
    """
    root_paths = sorted(snapshot.file_digests)
    queries: list[tuple[str, str]] = []
    for gap in evidence_gaps:
        hint = gap.get("locator_hint")
        if not isinstance(hint, Mapping):
            continue
        path = hint.get("path")
        if isinstance(path, str) and path.strip():
            candidate = PurePosixPath(path.strip())
            if not candidate.is_absolute() and ".." not in candidate.parts and "*" not in path:
                normalized = candidate.as_posix()
                if normalized not in {"", "."}:
                    queries.append(("EXACT_PATH", normalized))
        symbol = hint.get("symbol")
        if isinstance(symbol, str) and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", symbol.strip()):
            queries.append(("EXACT_SYMBOL", symbol.strip()))
    queries = list(dict.fromkeys(queries))
    records: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for query_kind, query in queries:
        if query_kind == "EXACT_PATH":
            # The snapshot already contains the complete normalized file inventory.
            # This lookup reads no file contents and must not inherit the scan budget.
            matched = [query] if query in snapshot.file_digests else []
            record = _record(snapshot.snapshot_id, query_kind, query, len(root_paths), matched, complete=True)
            records.append(record)
            audits.append(dict(record["locator"]))
            continue
        if len(root_paths) > max_files:
            record = _record(snapshot.snapshot_id, query_kind, query, max_files, [], complete=False,
                             failure=f"snapshot contains {len(root_paths)} files; limit is {max_files}")
            records.append(record)
            audits.append(dict(record["locator"]))
            continue

        needle = query.encode("utf-8").lower()
        boundary = re.compile(rb"(?<![A-Za-z0-9_$])" + re.escape(needle) + rb"(?![A-Za-z0-9_$])")
        matched: list[str] = []
        failure = None
        inspected = 0
        inspected_bytes = 0
        for relative in root_paths:
            inspected += 1
            try:
                path = root_path(snapshot, relative)
                size = path.stat().st_size
                if inspected_bytes + size > max_bytes:
                    failure = f"byte limit {max_bytes} would be exceeded at {relative}"
                    inspected -= 1
                    break
                content = path.read_bytes()
            except OSError as exc:
                failure = f"cannot read {relative}: {type(exc).__name__}"
                break
            inspected_bytes += size
            if hashlib.sha256(content).hexdigest() != snapshot.file_digests[relative]:
                failure = f"snapshot content changed while searching {relative}"
                break
            if boundary.search(content.lower()):
                matched.append(relative)
        complete = failure is None and inspected == len(root_paths)
        record = _record(snapshot.snapshot_id, query_kind, query, inspected, matched,
                         complete=complete, inspected_bytes=inspected_bytes,
                         max_bytes=max_bytes, failure=failure)
        records.append(record)
        audits.append(dict(record["locator"]))
    if records and compute_snapshot(snapshot.source_root).snapshot_id != snapshot.snapshot_id:
        refreshed = []
        audits = []
        for record in records:
            locator = dict(record["locator"])
            locator["complete"] = False
            locator["truncated"] = True
            locator["incomplete_reason"] = "source snapshot changed during absence search"
            replacement = _record(snapshot.snapshot_id, locator["query_kind"], locator["query"],
                                  locator["inspected_file_count"], locator["matched_paths"],
                                  complete=False, failure="source snapshot changed during absence search")
            refreshed.append(replacement)
            audits.append(dict(replacement["locator"]))
        records, audits = refreshed, audits
    return tuple(records), {"query_count": len(queries), "records": audits}


def root_path(snapshot: Snapshot, relative: str):
    """Resolve a snapshot path without following a symlink outside its source root."""
    root = Path(snapshot.source_root).resolve()
    raw = root / relative
    if raw.is_symlink():
        raise OSError("snapshot path became a symlink")
    path = raw.resolve()
    path.relative_to(root)
    if not path.is_file():
        raise OSError("snapshot path is not a regular file")
    return path
