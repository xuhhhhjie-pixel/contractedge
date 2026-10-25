from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def supports_expected_missing_evidence(record: Mapping[str, Any]) -> bool:
    """Return whether a trusted evidence record directly establishes a bounded absence."""
    kind = record.get("kind")
    locator = record.get("locator")
    if not isinstance(locator, Mapping):
        return False
    if kind == "STATIC_SEARCH":
        digest_valid = record.get("content_digest") == static_search_content_digest(
            str(record.get("snapshot_id", "")), locator, str(record.get("excerpt", "")))
        return (
            digest_valid
            and
            locator.get("scope") == "snapshot_file_inventory"
            and locator.get("query_kind") in {"EXACT_PATH", "EXACT_SYMBOL"}
            and isinstance(locator.get("query"), str)
            and bool(locator.get("query", "").strip())
            and locator.get("complete") is True
            and locator.get("truncated") is False
            and locator.get("match_count") == 0
            and locator.get("matched_paths") == []
            and locator.get("inspected_file_count", 0) >= 0
        )
    if kind == "SOURCE_INVENTORY":
        digest_valid = record.get("content_digest") == static_search_content_digest(
            str(record.get("snapshot_id", "")), locator, str(record.get("excerpt", "")))
        return (
            digest_valid
            and
            locator.get("scope") == "snapshot_file_inventory"
            and locator.get("query_kind") == "EXACT_PATH"
            and bool(locator.get("query"))
            and locator.get("complete") is True
            and locator.get("truncated") is False
            and locator.get("match_count") == 0
            and locator.get("matched_paths") == []
        )
    if kind == "PUBLIC_OBSERVATION":
        return locator.get("observed_state") == "ABSENT" and bool(locator.get("target"))
    return False


def static_search_content_digest(snapshot_id: str, locator: Mapping[str, Any], excerpt: str) -> str:
    identity = {"snapshot_id": snapshot_id, "locator": dict(locator), "excerpt": excerpt}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
