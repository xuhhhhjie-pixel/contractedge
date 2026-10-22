from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

from contractedge.contracts.validation import canonical_digest
from contractedge.dataset.archive import inspect_archive


class ReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseMember:
    case_id: str
    release_id: str
    manifest_path: Path
    package_root: Path
    record: Mapping[str, Any]


def _safe_member_path(root: Path, value: str, label: str) -> Path:
    rel = PurePosixPath(value)
    if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
        raise ReleaseError(f"{label} must be a normalized relative path: {value!r}")
    candidate = root.joinpath(*rel.parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise ReleaseError(f"{label} escapes release root: {value!r}") from exc
    return candidate


def load_release_members(manifest_path: str | Path, release_id: str,
                         package_root: str | Path | None = None) -> list[ReleaseMember]:
    manifest = Path(manifest_path).resolve()
    if not manifest.is_file():
        raise ReleaseError(f"manifest does not exist: {manifest}")
    package_root = Path(package_root).resolve() if package_root else manifest.parent
    members: list[ReleaseMember] = []
    seen: set[str] = set()
    for line_no, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReleaseError(f"invalid JSONL at {manifest}:{line_no}: {exc}") from exc
        if not isinstance(row, dict):
            raise ReleaseError(f"manifest row {line_no} must be an object")
        case_id = row.get("case_id") or row.get("candidate_id")
        if not isinstance(case_id, str) or not case_id:
            raise ReleaseError(f"manifest row {line_no} has no case_id/candidate_id")
        if case_id in seen:
            raise ReleaseError(f"duplicate case in manifest: {case_id}")
        seen.add(case_id)
        input_value = row.get("input") or row.get("input_path")
        if not isinstance(input_value, str):
            raise ReleaseError(f"manifest row {line_no} has no input path")
        input_path = _safe_member_path(package_root, input_value, "input")
        if not input_path.is_file():
            raise ReleaseError(f"manifest input is missing for {case_id}: {input_value}")
        for key in ("gt_path", "private_gt", "broken_source", "input", "input_path"):
            value = row.get(key)
            if isinstance(value, str):
                _safe_member_path(package_root, value, key)
        members.append(ReleaseMember(case_id, release_id, manifest, package_root, row))
    return members


def load_private_gt_paths(manifest_path: str | Path,
                          release_root: str | Path | None = None) -> dict[str, tuple[Path, str]]:
    """Load case-to-GT paths from a trusted private manifest, never the method-side tree."""
    manifest = Path(manifest_path).resolve()
    root = Path(release_root).resolve() if release_root else manifest.parent.parent
    if not manifest.is_file():
        raise ReleaseError(f"private manifest does not exist: {manifest}")
    result: dict[str, tuple[Path, str]] = {}
    for line_no, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReleaseError(f"invalid private manifest JSON at line {line_no}: {exc}") from exc
        if not isinstance(row, Mapping) or not isinstance(row.get("case_id"), str) or not isinstance(row.get("private_gt"), str):
            raise ReleaseError(f"private manifest row {line_no} lacks case_id/private_gt")
        case_id = row["case_id"]
        if case_id in result:
            raise ReleaseError(f"duplicate private GT member: {case_id}")
        path = _safe_member_path(root, row["private_gt"], "private_gt")
        if not path.is_file():
            raise ReleaseError(f"private GT file missing for {case_id}: {row['private_gt']}")
        if row.get("formal_membership") is not True:
            raise ReleaseError(f"private GT is not marked as a formal member: {case_id}")
        result[case_id] = (root, row["private_gt"])
    return result


def _read_yaml(path: Path) -> Mapping[str, Any]:
    try:
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise ReleaseError(f"cannot read public input {path}: {exc}") from exc
    if len(documents) == 1 and isinstance(documents[0], Mapping):
        return documents[0]
    if not documents or any(value is not None and not isinstance(value, Mapping) for value in documents):
        raise ReleaseError(f"public input must be a YAML mapping: {path}")
    # Multi-document Kubernetes/Helm fixtures are supported. Only explicitly named
    # metadata fields are projected; resource bodies remain their own public payload.
    merged: dict[str, Any] = {"_public_documents": documents}
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        for key in ("requirement", "artifact_scope", "observable", "behavioral_observable",
                    "public_observable_text", "evidence", "broken"):
            if key in document:
                if key in merged and merged[key] != document[key]:
                    raise ReleaseError(f"conflicting public metadata field {key!r} in {path}")
                merged[key] = document[key]
    return merged


def _requirement_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, Mapping):
        candidates = [value.get(key) for key in ("text", "statement")
                      if isinstance(value.get(key), str) and value.get(key).strip()]
        if len(set(candidates)) > 1:
            raise ReleaseError("REQUIREMENT_CONFLICT: requirement.text and requirement.statement disagree")
        if candidates:
            return candidates[0]
    return None


def _archive_from_public(source: Mapping[str, Any], row: Mapping[str, Any], case_id: str,
                         package_root: Path, release_id: str) -> tuple[str, str | None]:
    broken = source.get("broken")
    if isinstance(broken, Mapping):
        path = broken.get("source_archive")
        revision = broken.get("revision")
        if isinstance(path, str) and (package_root / path).is_file():
            return path, revision if isinstance(revision, str) else None
    path = row.get("broken_source")
    if isinstance(path, str):
        return path, None
    # R1/R2 public package trees pair each released case id with its Broken tarball,
    # while the release manifest records the public task YAML separately.
    release_class = release_id.split("-", 1)[0]
    package_candidates = []
    if release_class in {"R1", "R2"}:
        package_candidates.append(Path("exports/自然部署的真实失败项目") / release_class / "cases" / case_id)
    package_candidates.extend((
        Path("cases") / case_id,
        Path("extension_candidates") / case_id,
    ))
    for package in package_candidates:
        relative = package / "public/broken-source.tar.gz"
        candidate = package_root / relative
        try:
            candidate.resolve(strict=False).relative_to(package_root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            revision = _public_broken_revision(package_root / package)
            supplied = None
            broken = source.get("broken")
            if isinstance(broken, Mapping) and isinstance(broken.get("revision"), str):
                supplied = broken["revision"]
            if supplied and revision and supplied != revision:
                raise ReleaseError(f"BROKEN_REVISION_CONFLICT: input and case package disagree for {case_id}")
            return relative.as_posix(), supplied or revision
    raise ReleaseError("public manifest/input does not identify the Broken source archive")


def _public_broken_revision(case_package: Path) -> str | None:
    metadata = case_package / "case.yaml"
    if not metadata.is_file():
        return None
    try:
        record = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    history = record.get("history") if isinstance(record, Mapping) else None
    revision = history.get("broken_sha") if isinstance(history, Mapping) else None
    return revision if isinstance(revision, str) and revision else None


def _kind(value: Any) -> str:
    text = str(value or "PUBLIC_OBSERVATION").upper()
    if "SOURCE" in text or text in {"STATIC_SEARCH", "SOURCE_SPAN", "SOURCE_INVENTORY"}:
        raise ReleaseError("PUBLIC_SOURCE_EVIDENCE_UNVERIFIED: source excerpts need a verified source locator and content digest")
    if "REQUIREMENT" in text:
        raise ReleaseError("PUBLIC_EVIDENCE_KIND_UNSUPPORTED: requirement text is projected through requirement_text")
    if text in {"PUBLIC", "PUBLIC_OBSERVATION", "BROKEN_RUNTIME_OBSERVATION", "RUNTIME_OBSERVATION", "OBSERVATION"}:
        return "PUBLIC_OBSERVATION"
    raise ReleaseError(f"PUBLIC_EVIDENCE_KIND_UNSUPPORTED: {text}")


def project_method_input(member: ReleaseMember, package_root: str | Path | None = None, *,
                         requirement_override: str | None = None,
                         source_root: str | None = None) -> dict[str, Any]:
    root = Path(package_root).resolve() if package_root else member.package_root
    row = member.record
    input_value = row.get("input") or row.get("input_path")
    if not isinstance(input_value, str):
        raise ReleaseError(f"manifest member {member.case_id} has no input")
    public_path = _safe_member_path(root, input_value, "input")
    source = _read_yaml(public_path)
    text = _requirement_text(source.get("requirement"))
    if requirement_override is not None:
        if member.release_id.split("-", 1)[0] == "R5" and text is None:
            raise ReleaseError(f"PUBLIC_REQUIREMENT_MISSING: reviewed text is not present in public input for {member.case_id}")
        if text is not None and text != requirement_override:
            raise ReleaseError(f"REQUIREMENT_CONFLICT: approved override disagrees with public text for {member.case_id}")
        text = requirement_override if requirement_override.strip() else None
    if text is None:
        # R5 declaration fixtures may describe a trigger without stating an intent.
        # Do not turn the expected behavior or private GT into a method-side requirement.
        raise ReleaseError(f"PUBLIC_REQUIREMENT_MISSING: {member.case_id}")
    archive_rel, revision = _archive_from_public(source, row, member.case_id, root, member.release_id)
    archive_path = _safe_member_path(root, archive_rel, "broken archive")
    if not archive_path.is_file():
        raise ReleaseError(f"BROKEN_ARCHIVE_MISSING: {member.case_id}: {archive_rel}")
    inspection = inspect_archive(archive_path, source_root=source_root)
    archive_digest = row.get("broken_source_sha256")
    if not isinstance(archive_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", archive_digest):
        digest = hashlib.sha256()
        with archive_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        archive_digest = digest.hexdigest()

    scope = source.get("artifact_scope", [])
    scope_hints: list[dict[str, str]] = []
    if isinstance(scope, list):
        for raw in scope:
            if not isinstance(raw, str) or not raw.strip():
                continue
            normalized = raw.strip()
            is_path = "/" in normalized or normalized.endswith((".go", ".ts", ".js", ".yaml", ".yml", ".json"))
            scope_hints.append({"kind": "path" if is_path else "concept", "value": normalized,
                                "raw_text": raw, "origin_ref": "public.artifact_scope"})
    if not scope_hints and member.release_id.split("-", 1)[0] == "R5":
        repository = member.record.get("repository")
        if isinstance(repository, str) and repository.strip():
            scope_hints.append({"kind": "concept", "value": repository.rsplit("/", 1)[-1],
                                "raw_text": repository, "origin_ref": "public.release_manifest.repository"})

    observable = source.get("observable")
    if isinstance(observable, Mapping):
        observable = observable.get("statement")
    if not isinstance(observable, str):
        observable = source.get("behavioral_observable")
    if not isinstance(observable, str):
        observable = source.get("public_observable_text")
    observable = observable.strip() if isinstance(observable, str) and observable.strip() else None

    evidence: list[dict[str, Any]] = []
    declared_artifacts: list[dict[str, Any]] = []
    if (member.release_id.split("-", 1)[0] == "R5" and
            member.record.get("input_view") == "broken_source_and_declared_input_only"):
        artifact_path = _safe_member_path(root, input_value, "declared R5 input")
        if artifact_path.stat().st_size > 1_000_000:
            raise ReleaseError(f"DECLARED_ARTIFACT_TOO_LARGE: {member.case_id}")
        artifact_content = artifact_path.read_bytes().decode("utf-8")
        artifact_digest = hashlib.sha256(artifact_content.encode("utf-8")).hexdigest()
        artifact = {"id": "INPUT1", "path": "declared-artifacts/input.yaml",
                    "media_type": "application/yaml", "content_digest": artifact_digest,
                    "content": artifact_content}
        declared_artifacts.append(artifact)
    raw_evidence = source.get("evidence", [])
    if isinstance(raw_evidence, list):
        for index, item in enumerate(raw_evidence):
            if not isinstance(item, Mapping):
                continue
            excerpt = item.get("excerpt")
            evidence_id = f"PUB{index + 1}"
            evidence_kind = _kind(item.get("kind"))
            evidence_payload = {"id": evidence_id, "kind": evidence_kind,
                                "excerpt": excerpt if isinstance(excerpt, str) else "",
                                "origin": "public release excerpt"}
            encoded = (json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
            evidence.append({"id": evidence_id, "snapshot_id": inspection.snapshot_id,
                             "kind": evidence_kind,
                             "locator": {"path": f"cases/{member.case_id}/evidence/{evidence_id}.json"},
                             "content_digest": hashlib.sha256(encoded).hexdigest(),
                             "excerpt": evidence_payload["excerpt"],
                             "origin": evidence_payload["origin"]})

    result: dict[str, Any] = {
        "schema_version": "ce.method_input.v1_1", "case_id": member.case_id,
        "release_id": member.release_id, "original_broken_revision": revision or "archive-sha256:" + archive_digest,
        "archive_path": archive_rel, "archive_sha256": archive_digest,
        "source_root": inspection.source_root, "initial_snapshot_id": inspection.snapshot_id,
        "requirement_text": text, "public_observable_text": observable,
        "scope_hints": scope_hints, "public_evidence": evidence,
        "declared_artifacts": declared_artifacts,
    }
    result["input_digest"] = canonical_digest(result)
    return result
