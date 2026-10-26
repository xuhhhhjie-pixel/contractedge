from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from contractedge.contracts.models import Snapshot


DEFAULT_SCOPE_SEARCH_FILES = 20_000
DEFAULT_SCOPE_SEARCH_BYTES = 64 * 1024 * 1024
DEFAULT_SCOPE_SEARCH_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_SCOPE_MATCHES_PER_HINT = 64

_CONCEPT_STOPWORDS = {
    "after", "and", "before", "by", "for", "from", "into", "is", "of", "or", "should",
    "the", "through", "to", "when", "with", "without", "set",
}
_SOURCE_SUFFIXES = {".go", ".ts", ".tsx", ".js", ".jsx", ".py", ".yaml", ".yml", ".json",
                    ".tpl", ".gotpl", ".toml", ".conf", ".ini", ".sh", '.c', '.h', '.java'}
_LOW_PRIORITY_PARTS = {"vendor", "node_modules", "third_party", "third-party", "dist", "build", ".git"}


@dataclass(frozen=True)
class ScopeResolution:
    seed_paths: tuple[str, ...]
    unresolved: tuple[Mapping[str, Any], ...]
    matched_by_hint: Mapping[str, tuple[str, ...]]
    extension_paths: tuple[str, ...] = ()
    search_audit: tuple[Mapping[str, Any], ...] = ()


def _path_hint(value: str, files: set[str]) -> tuple[list[str], str]:
    normalized = value.replace("\\", "/").strip()
    if normalized.startswith("path:"):
        normalized = normalized[5:]
    # The first colon separates a file path from a selector, as in path:selector hints.
    if ":" in normalized:
        left, _selector = normalized.split(":", 1)
        if left in files:
            normalized = left

    def direct(candidate: str) -> tuple[list[str], str]:
        candidate = candidate.strip()
        if candidate.startswith("/") or ".." in candidate.split("/"):
            return [], "PATH_UNSAFE_OR_UNRESOLVED"
        if candidate.startswith("./"):
            candidate = candidate[2:]
        if candidate in files:
            return [candidate], "PATH_EXACT"
        prefix = candidate.rstrip("/") + "/"
        matches = sorted(path for path in files if path.startswith(prefix))
        if matches:
            return matches, "PATH_PREFIX"
        return [], "PATH_UNRESOLVED"

    found, mode = direct(normalized)
    if found:
        return found, mode

    # Some historical scopes append a role/selector description after a path,
    # and some omit a single top-level archive directory. Recover only a unique
    # source-file suffix so an ambiguous basename never selects arbitrary code.
    path_tokens = re.findall(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)+", normalized)
    ambiguous = False
    for token in path_tokens:
        if Path(token).suffix.casefold() not in _SOURCE_SUFFIXES:
            continue
        token_matches, _token_mode = direct(token)
        if token_matches:
            return token_matches, "PATH_DESCRIPTION_TOKEN"
        suffix = "/" + token.lstrip("./")
        matches = sorted(path for path in files if path.endswith(suffix))
        if len(matches) == 1:
            return matches, "PATH_UNIQUE_SUFFIX"
        if len(matches) > 1:
            ambiguous = True
    return [], "PATH_AMBIGUOUS" if ambiguous else mode


def _concept_terms(value: str) -> tuple[str, ...]:
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9]+", value):
        split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token)
        for part in split.split():
            term = part.casefold()
            if len(term) >= 3 and term not in _CONCEPT_STOPWORDS:
                terms.add(term)
    return tuple(sorted(terms))


def _search_priority(path: str) -> tuple[int, int, str]:
    parts = set(Path(path).parts)
    low_priority = int(bool(parts & _LOW_PRIORITY_PARTS))
    source_priority = int(Path(path).suffix.casefold() not in _SOURCE_SUFFIXES)
    return low_priority, source_priority, path


def resolve_scope(source_root: str | Path, snapshot: Snapshot,
                  hints: Iterable[Mapping[str, Any]], *,
                  max_search_files: int = DEFAULT_SCOPE_SEARCH_FILES,
                  max_search_bytes: int = DEFAULT_SCOPE_SEARCH_BYTES,
                  max_search_file_bytes: int = DEFAULT_SCOPE_SEARCH_FILE_BYTES,
                  max_matches_per_hint: int = DEFAULT_SCOPE_MATCHES_PER_HINT) -> ScopeResolution:
    if min(max_search_files, max_search_bytes, max_search_file_bytes, max_matches_per_hint) < 0:
        raise ValueError("scope search budgets must be nonnegative")
    root = Path(source_root).resolve()
    files = set(snapshot.file_digests)
    seed: set[str] = set()
    seed_scores: dict[str, int] = {}
    unresolved: list[Mapping[str, Any]] = []
    matches: dict[str, tuple[str, ...]] = {}
    searchable: dict[str, str] = {}
    indexed_files = 0
    indexed_bytes = 0
    search_audit: list[Mapping[str, Any]] = []

    for index, hint in enumerate(hints):
        kind = str(hint.get("kind", "concept"))
        value = str(hint.get("value", "")).strip()
        raw_text = str(hint.get("raw_text", value))
        origin = str(hint.get("origin_ref", f"scope_hints[{index}]"))
        found: list[str] = []
        files_inspected = 0
        bytes_inspected = 0
        truncated = False
        query_terms: tuple[str, ...] = ()
        match_mode = "PATH_UNRESOLVED" if kind == "path" else "EXACT"
        scored: dict[str, int] = {}
        if kind == "path":
            found, match_mode = _path_hint(value, files)
        elif kind in {"symbol", "field", "concept"} and value:
            if kind == "concept":
                query_terms = _concept_terms(value)
                if query_terms:
                    match_mode = "CONCEPT_TERMS"
            if query_terms:
                minimum_hits = min(2, len(query_terms))
                for path in sorted(files):
                    path_hits = {term for term in query_terms if term in path.casefold()}
                    if len(path_hits) >= minimum_hits:
                        scored[path] = len(path_hits)
                # A path match is a useful locator, not a ceiling on content
                # relevance. Read those candidates too, under the same shared
                # limits, before lower-priority paths consume the search cap.
                scan_paths = sorted(files, key=lambda path: (
                    _search_priority(path)[0], path not in scored,
                    *_search_priority(path)[1:]))
                for path in scan_paths:
                    if path not in searchable:
                        try:
                            size = (root / path).stat().st_size
                        except OSError:
                            truncated = True
                            continue
                        if size > max_search_file_bytes:
                            truncated = True
                            continue
                        if indexed_files >= max_search_files or indexed_bytes + size > max_search_bytes:
                            truncated = True
                            break
                        try:
                            searchable[path] = (root / path).read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            truncated = True
                            continue
                        indexed_files += 1
                        indexed_bytes += size
                    files_inspected += 1
                    bytes_inspected += len(searchable[path].encode("utf-8"))
                    hits = {term for term in query_terms if term in path.casefold() or
                            term in searchable[path].casefold()}
                    if len(hits) >= minimum_hits:
                        scored[path] = len(hits)
                found = sorted(scored, key=lambda path: (-scored[path], _search_priority(path), path))
            else:
                pattern = re.compile(re.escape(value), re.IGNORECASE)
                scored = {path: 1 for path in files if pattern.search(path)}
                for path in sorted(files - set(scored), key=_search_priority):
                    if path not in searchable:
                        try:
                            size = (root / path).stat().st_size
                        except OSError:
                            truncated = True
                            continue
                        if size > max_search_file_bytes:
                            truncated = True
                            continue
                        if indexed_files >= max_search_files or indexed_bytes + size > max_search_bytes:
                            truncated = True
                            break
                        try:
                            searchable[path] = (root / path).read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            truncated = True
                            continue
                        indexed_files += 1
                        indexed_bytes += size
                    files_inspected += 1
                    bytes_inspected += len(searchable[path].encode("utf-8"))
                    if pattern.search(searchable[path]):
                        scored[path] = 1
                found = sorted(scored)
        matches_total = len(scored) if scored else len(found)
        match_cap_applied = len(found) > max_matches_per_hint
        if match_cap_applied:
            found = found[:max_matches_per_hint]
        if found:
            unique = tuple(sorted(set(found)))
            matches[origin] = tuple(found)
            seed.update(unique)
            hint_weight = 10_000 if kind == "path" else 0
            for path in found:
                seed_scores[path] = seed_scores.get(path, 0) + hint_weight + max(1, scored.get(path, 1))
        if not found or truncated or match_cap_applied:
            unresolved.append({"kind": kind, "value": value, "raw_text": raw_text,
                               "origin_ref": origin, "search_complete": not truncated,
                               "truncated": truncated, "results_capped": match_cap_applied,
                               "matched_path_count": len(found), "matches_total": matches_total})
        search_audit.append({"kind": kind, "query": value, "origin_ref": origin,
                             "matched_paths": list(found), "files_inspected": files_inspected,
                             "bytes_inspected": bytes_inspected, "search_complete": not truncated,
                             "truncated": truncated, "indexed_files_total": indexed_files,
                             "indexed_bytes_total": indexed_bytes, "match_mode": match_mode,
                             "query_terms": list(query_terms),
                             "matched_scores": {path: scored.get(path, 1) for path in found},
                             "matches_total": matches_total,
                             "match_cap_applied": match_cap_applied,
                             "coverage_incomplete": truncated or match_cap_applied})
    ordered_seed = tuple(sorted(seed, key=lambda path: (-seed_scores.get(path, 0), _search_priority(path), path)))
    return ScopeResolution(ordered_seed, tuple(unresolved), matches,
                           search_audit=tuple(search_audit))
