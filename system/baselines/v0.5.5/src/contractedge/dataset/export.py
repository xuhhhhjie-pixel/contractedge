from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

import yaml

from contractedge.contracts.validation import canonical_digest, parse_method_input
from contractedge.dataset.archive import inspect_archive_size
from contractedge.dataset.releases import ReleaseError, ReleaseMember, _safe_member_path, project_method_input


class PreparationBlocked(RuntimeError):
    def __init__(self, report: Mapping[str, Any]):
        self.report = dict(report)
        super().__init__(f"release materialization blocked: need {report['required_bytes']} bytes, have {report['available_bytes']}")


def _private_gt(member: ReleaseMember, private_gt_paths: Mapping[str, tuple[str | Path, str]] | None):
    row = member.record
    if isinstance(row.get("gt_path"), str):
        root = member.package_root
        rel = row["gt_path"]
    elif private_gt_paths and member.case_id in private_gt_paths:
        root, rel = private_gt_paths[member.case_id]
        root = Path(root).resolve()
    else:
        raise ReleaseError(f"PRIVATE_GT_NOT_MAPPED: {member.case_id}")
    source = _safe_member_path(Path(root), str(rel), "private GT")
    if not source.is_file():
        raise ReleaseError(f"PRIVATE_GT_MISSING: {member.case_id}: {rel}")
    return Path(root).resolve(), str(rel), source


def _reviewed_public_sources(member: ReleaseMember, review: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Check that an R5 approval is bound to digest-verifiable public evidence."""
    sources = review.get("public_sources")
    if not isinstance(sources, list) or not sources:
        raise ReleaseError(f"PUBLIC_REQUIREMENT_PROVENANCE_REQUIRED: {member.case_id}")
    validated: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, Mapping):
            raise ReleaseError(f"PUBLIC_REQUIREMENT_PROVENANCE_INVALID: {member.case_id}")
        kind = source.get("kind")
        uri = source.get("uri")
        expected_digest = source.get("sha256")
        refs = source.get("evidence_refs")
        if (kind not in {"PUBLIC_ISSUE", "PUBLIC_DECLARATION"}
                or not isinstance(uri, str)
                or not isinstance(expected_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
                or not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or not ref.strip() for ref in refs)):
            raise ReleaseError(f"PUBLIC_REQUIREMENT_PROVENANCE_INVALID: {member.case_id}")

        if kind == "PUBLIC_ISSUE":
            parsed = urlparse(uri)
            captured_content = source.get("captured_content")
            if (parsed.scheme != "https" or not parsed.netloc or "/issues/" not in parsed.path
                    or "/pull/" in parsed.path or not isinstance(captured_content, str)
                    or not captured_content.strip()):
                raise ReleaseError(f"PUBLIC_REQUIREMENT_PROVENANCE_INVALID: {member.case_id}")
            actual_digest = hashlib.sha256(captured_content.encode("utf-8")).hexdigest()
        else:
            prefix = "public://"
            relative = uri[len(prefix):] if uri.startswith(prefix) else ""
            source_path = _safe_member_path(member.package_root, relative, "public requirement source") if relative else None
            if (source_path is None or "\\" in relative or source_path.is_symlink()
                    or not source_path.is_file()):
                raise ReleaseError(f"PUBLIC_REQUIREMENT_PROVENANCE_INVALID: {member.case_id}")
            try:
                source_path.resolve(strict=True).relative_to(member.package_root.resolve(strict=True))
            except (OSError, ValueError) as exc:
                raise ReleaseError(f"PUBLIC_REQUIREMENT_PROVENANCE_INVALID: {member.case_id}") from exc
            actual_digest = _sha256(source_path)
        if actual_digest != expected_digest:
            raise ReleaseError(f"PUBLIC_REQUIREMENT_SOURCE_DIGEST_MISMATCH: {member.case_id}")

        validated.append({"kind": kind, "uri": uri, "sha256": expected_digest,
                          "evidence_refs": list(dict.fromkeys(ref.strip() for ref in refs))})
    return validated


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_tree(root: Path) -> list[str]:
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ReleaseError(f"export contains an unexpected symlink: {path}")
        rows.append(f"{_sha256(path)}  {path.relative_to(root).as_posix()}")
    return rows


def prepare_release(members: Sequence[ReleaseMember], output_root: str | Path, *,
                    private_gt_paths: Mapping[str, tuple[str | Path, str]] | None = None,
                    source_roots: Mapping[str, str] | None = None,
                    r5_requirement_reviews: Mapping[str, Mapping[str, Any]] | None = None,
                    materialize: bool = False, available_bytes: int | None = None,
                    reserve_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Preflight all manifest members; optionally publish isolated public/evaluator trees atomically."""
    output = Path(output_root).absolute()
    if output.exists():
        raise FileExistsError(f"output root already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    prepared: list[tuple[ReleaseMember, dict[str, Any], Path, Path]] = []
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    requirement_sources_by_case: dict[str, list[dict[str, Any]]] = {}
    required = 0
    peak_source_workspace = 0
    for member in members:
        try:
            if member.case_id in seen:
                raise ReleaseError(f"DUPLICATE_CASE_ID: {member.case_id}")
            seen.add(member.case_id)
            is_r5 = member.release_id.split("-", 1)[0] == "R5"
            source_root = (source_roots or {}).get(member.case_id)
            if is_r5 and source_root is None:
                raise ReleaseError(f"SOURCE_ROOT_MAPPING_MISSING: {member.case_id}")
            review = (r5_requirement_reviews or {}).get(member.case_id)
            requirement_override = None
            if is_r5:
                if review and review.get("decision") == "HELD_PRIVATE_ORIGIN":
                    raise ReleaseError(f"PUBLIC_REQUIREMENT_ORIGIN_UNVERIFIED: {member.case_id}")
                if not review or review.get("decision") != "APPROVED" or not review.get("reviewer_id") or not review.get("rationale"):
                    raise ReleaseError(f"R5_REQUIREMENT_REVIEW_REQUIRED: {member.case_id}")
                requirement_sources_by_case[member.case_id] = _reviewed_public_sources(member, review)
                requirement_override = review.get("requirement_text")
                if not requirement_override:
                    raise ReleaseError(f"R5_REQUIREMENT_REVIEW_REQUIRED: no approved requirement text for {member.case_id}")
            method_input = project_method_input(member, requirement_override=requirement_override,
                                                source_root=source_root)
            parse_method_input(method_input)
            archive = _safe_member_path(member.package_root, method_input["archive_path"], "broken archive")
            expanded = inspect_archive_size(archive)
            gt_root, gt_rel, gt = _private_gt(member, private_gt_paths)
            # Syntax-check private GT in the trusted preparation process only.
            loaded_gt = yaml.safe_load(gt.read_text(encoding="utf-8"))
            if not isinstance(loaded_gt, Mapping) or loaded_gt.get("case_id") not in {None, member.case_id}:
                raise ReleaseError(f"PRIVATE_GT_INVALID: {member.case_id}")
            required += archive.stat().st_size + gt.stat().st_size + 4096
            peak_source_workspace = max(peak_source_workspace, expanded)
            prepared.append((member, method_input, archive, gt))
            decisions.append({"case_id": member.case_id, "release_id": member.release_id, "status": "PREPARED"})
        except (ReleaseError, OSError, ValueError, yaml.YAMLError) as exc:
            decisions.append({"case_id": member.case_id, "release_id": member.release_id,
                              "status": "HELD", "reason": str(exc)})

    if available_bytes is None:
        available_bytes = shutil.disk_usage(output.parent).free
    required_with_reserve = required + reserve_bytes if prepared else 0
    report: dict[str, Any] = {
        "schema_version": "ce.preparation_report.v1",
        "output_root": str(output), "materialized": False,
        "manifest_member_count": len(members), "prepared_count": len(prepared),
        "held_count": len(decisions) - len(prepared),
        "copy_bytes": required, "peak_source_workspace_bytes": peak_source_workspace,
        "reserve_bytes": reserve_bytes if prepared else 0,
        "required_bytes": required_with_reserve + peak_source_workspace, "available_bytes": available_bytes,
        "decisions": decisions,
    }
    if not materialize:
        return report
    report["required_bytes"] = required_with_reserve + peak_source_workspace
    if report["required_bytes"] > available_bytes:
        report["blocked_reason"] = "INSUFFICIENT_SPACE"
        raise PreparationBlocked(report)

    staging = Path(tempfile.mkdtemp(prefix=".contractedge-prepare-", dir=output.parent))
    try:
        public_manifest: list[dict[str, Any]] = []
        private_manifest: list[dict[str, Any]] = []
        public_root = staging / "public"
        evaluator_root = staging / "evaluator"
        for member, source_input, archive, gt in prepared:
            public_case = public_root / "cases" / member.case_id
            evaluator_case = evaluator_root / "cases" / member.case_id
            public_case.mkdir(parents=True, exist_ok=True)
            evaluator_case.mkdir(parents=True, exist_ok=True)
            archive_target = public_case / "broken-source.tar.gz"
            shutil.copyfile(archive, archive_target)
            projected = dict(source_input)
            projected["archive_path"] = f"cases/{member.case_id}/broken-source.tar.gz"
            projected["archive_sha256"] = _sha256(archive_target)
            if projected["archive_sha256"] != source_input["archive_sha256"]:
                raise ReleaseError(f"BROKEN_ARCHIVE_DIGEST_MISMATCH: {member.case_id}")
            projected.pop("input_digest", None)
            projected["input_digest"] = canonical_digest(projected)
            input_path = public_case / "method_input.json"
            for artifact in projected["declared_artifacts"]:
                artifact_file = _safe_member_path(public_case, artifact["path"], "declared artifact")
                artifact_file.parent.mkdir(parents=True, exist_ok=True)
                artifact_file.write_text(artifact["content"], encoding="utf-8")
                if _sha256(artifact_file) != artifact["content_digest"]:
                    raise ReleaseError(f"DECLARED_ARTIFACT_DIGEST_MISMATCH: {member.case_id}/{artifact['id']}")
            for evidence in projected["public_evidence"]:
                evidence_file = _safe_member_path(public_root, evidence["locator"]["path"], "public evidence")
                if evidence["kind"] == "DECLARED_ARTIFACT":
                    artifact_file = public_case / next(item["path"] for item in projected["declared_artifacts"]
                                                       if item["id"] == evidence["locator"].get("artifact_id"))
                    if _sha256(artifact_file) != evidence["content_digest"]:
                        raise ReleaseError(f"PUBLIC_ARTIFACT_EVIDENCE_MISMATCH: {member.case_id}/{evidence['id']}")
                    continue
                payload = {"id": evidence["id"], "kind": evidence["kind"],
                           "excerpt": evidence["excerpt"], "origin": evidence["origin"]}
                _write_json(evidence_file, payload)
                if _sha256(evidence_file) != evidence["content_digest"]:
                    raise ReleaseError(f"PUBLIC_EVIDENCE_DIGEST_MISMATCH: {member.case_id}/{evidence['id']}")
            _write_json(input_path, projected)
            parse_method_input(projected)
            gt_target = evaluator_case / "ground_truth.yaml"
            shutil.copyfile(gt, gt_target)
            public_manifest.append({"case_id": member.case_id, "release_id": member.release_id,
                                    "method_input": f"cases/{member.case_id}/method_input.json",
                                    "broken_source": f"cases/{member.case_id}/broken-source.tar.gz",
                                    "input_sha256": _sha256(input_path), "broken_source_sha256": _sha256(archive_target)})
            private_manifest.append({"case_id": member.case_id, "release_id": member.release_id,
                                     "ground_truth": f"cases/{member.case_id}/ground_truth.yaml",
                                     "ground_truth_sha256": _sha256(gt_target),
                                     "requirement_sources": requirement_sources_by_case.get(member.case_id, [])})
        _write_jsonl(public_root / "manifest.jsonl", public_manifest)
        _write_jsonl(evaluator_root / "manifest.jsonl", private_manifest)
        (public_root / "SHA256SUMS").write_text("\n".join(_hash_tree(public_root)) + "\n", encoding="utf-8")
        (evaluator_root / "SHA256SUMS").write_text("\n".join(_hash_tree(evaluator_root)) + "\n", encoding="utf-8")
        os.replace(staging, output)
        report["materialized"] = True
        report["public_cases"] = len(public_manifest)
        report["evaluator_cases"] = len(private_manifest)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
