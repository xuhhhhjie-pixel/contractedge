from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from contractedge.contracts.models import ContractError
from contractedge.contracts.validation import parse_method_input


_FORBIDDEN_METHOD_KEYS = {
    "private_gt", "ground_truth", "fixed_source", "fixed_sha", "repair_pr", "repair_commit",
    "repair_family", "answer_key", "violations", "violated_relations", "expected_relations",
    "verifier", "full_recall_eligible", "provenance", "patch", "patches",
}
_PRIVATE_PATH_PARTS = {"private", "evaluator", "gt", "ground_truth", "fixed", "verifier", "patch", "patches"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if (not relative or rel.is_absolute() or rel.as_posix() != relative
            or any(part in {"", ".", ".."} for part in rel.parts) or "\\" in relative):
        raise ValueError(f"unsafe relative path: {relative!r}")
    path = root.joinpath(*rel.parts)
    if path.is_symlink():
        raise ValueError(f"symlink is forbidden in release view: {relative}")
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"path is missing or escapes release view: {relative}") from exc
    if not path.is_file():
        raise ValueError(f"expected regular file: {relative}")
    return path


def _manifest(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid manifest JSON at {path}:{line_number}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            raise ValueError(f"invalid manifest row at {path}:{line_number}")
        rows.append(row)
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate case_id in {path}")
    return rows


def _verify_hash_manifest(root: Path, errors: list[str]) -> int:
    manifest = root / "SHA256SUMS"
    if not manifest.is_file() or manifest.is_symlink():
        errors.append(f"missing hash manifest: {manifest}")
        return 0
    listed: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError:
            errors.append(f"malformed SHA256SUMS line {line_number} in {root.name}")
            continue
        if relative in listed:
            errors.append(f"duplicate hash path: {root.name}/{relative}")
            continue
        listed[relative] = digest
        try:
            path = _safe_file(root, relative)
            if _sha256(path) != digest:
                errors.append(f"hash mismatch: {root.name}/{relative}")
        except (OSError, ValueError) as exc:
            errors.append(f"invalid hash member {root.name}/{relative}: {exc}")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*")
              if path.is_file() and not path.is_symlink() and path.name != "SHA256SUMS"}
    if actual != set(listed):
        errors.append(f"hash manifest membership mismatch in {root.name}: unlisted={sorted(actual-set(listed))[:8]}, missing={sorted(set(listed)-actual)[:8]}")
    if any(path.is_symlink() for path in root.rglob("*")):
        errors.append(f"symlink found in release view: {root.name}")
    return len(listed)


def _forbidden_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).casefold()
            location = f"{prefix}.{key}" if prefix else str(key)
            if normalized in _FORBIDDEN_METHOD_KEYS:
                found.append(location)
            else:
                found.extend(_forbidden_keys(child, location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_keys(child, f"{prefix}[{index}]"))
    return found


def audit_prepared_release(output_root: str | Path, *, expected_members: int | None = None) -> dict[str, Any]:
    """Audit a materialized public/evaluator pair without merging their data."""
    root = Path(output_root).resolve(strict=True)
    public_root, evaluator_root = root / "public", root / "evaluator"
    errors: list[str] = []
    hashes = {"public": _verify_hash_manifest(public_root, errors),
              "evaluator": _verify_hash_manifest(evaluator_root, errors)}
    public_rows: list[dict[str, Any]] = []
    evaluator_rows: list[dict[str, Any]] = []
    try:
        public_rows = _manifest(public_root / "manifest.jsonl")
    except (OSError, ValueError) as exc:
        errors.append(f"public manifest invalid: {exc}")
    try:
        evaluator_rows = _manifest(evaluator_root / "manifest.jsonl")
    except (OSError, ValueError) as exc:
        errors.append(f"evaluator manifest invalid: {exc}")
    public_ids = {row.get("case_id") for row in public_rows}
    evaluator_ids = {row.get("case_id") for row in evaluator_rows}
    if public_ids != evaluator_ids:
        errors.append(f"public/evaluator membership differs: public_only={sorted(public_ids-evaluator_ids)}, evaluator_only={sorted(evaluator_ids-public_ids)}")
    if expected_members is not None and len(public_rows) != expected_members:
        errors.append(f"expected {expected_members} members, found {len(public_rows)}")

    for row in public_rows:
        case_id = row["case_id"]
        try:
            input_path = _safe_file(public_root, row["method_input"])
            input_value = json.loads(input_path.read_text(encoding="utf-8"))
            method_input = parse_method_input(input_value)
            if method_input.case_id != case_id or method_input.release_id != row.get("release_id"):
                errors.append(f"public input identity mismatch: {case_id}")
            if _sha256(input_path) != row.get("input_sha256"):
                errors.append(f"public input manifest digest mismatch: {case_id}")
            key_leaks = _forbidden_keys(input_value)
            if key_leaks:
                errors.append(f"private/answer-like public input keys in {case_id}: {key_leaks}")
            archive_path = _safe_file(public_root, row["broken_source"])
            if _sha256(archive_path) != method_input.archive_sha256 or _sha256(archive_path) != row.get("broken_source_sha256"):
                errors.append(f"Broken archive digest mismatch: {case_id}")
            for artifact in method_input.declared_artifacts:
                artifact_path = _safe_file(public_root / "cases" / case_id, artifact["path"])
                if _sha256(artifact_path) != artifact["content_digest"]:
                    errors.append(f"declared artifact digest mismatch: {case_id}/{artifact['id']}")
            for evidence in method_input.public_evidence:
                evidence_path = _safe_file(public_root, evidence["locator"]["path"])
                if _sha256(evidence_path) != evidence["content_digest"]:
                    errors.append(f"public evidence digest mismatch: {case_id}/{evidence['id']}")
            for path in (public_root / "cases" / case_id).rglob("*"):
                relative_parts = path.relative_to(public_root / "cases" / case_id).parts
                if any(part.casefold() in _PRIVATE_PATH_PARTS for part in relative_parts):
                    errors.append(f"private path component in public case {case_id}: {path}")
        except (ContractError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"public case invalid {case_id}: {exc}")

    for row in evaluator_rows:
        case_id = row["case_id"]
        try:
            gt_path = _safe_file(evaluator_root, row["ground_truth"])
            if _sha256(gt_path) != row.get("ground_truth_sha256"):
                errors.append(f"evaluator GT digest mismatch: {case_id}")
        except (KeyError, OSError, ValueError) as exc:
            errors.append(f"evaluator case invalid {case_id}: {exc}")

    return {
        "schema_version": "ce.release_audit.v1",
        "output_root": str(root), "status": "PASS" if not errors else "FAIL",
        "public_case_count": len(public_rows), "evaluator_case_count": len(evaluator_rows),
        "expected_member_count": expected_members, "hash_entries": hashes,
        "errors": errors,
    }
