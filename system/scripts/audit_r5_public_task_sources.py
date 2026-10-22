#!/usr/bin/env python3
"""Index public-origin evidence for R5 task review without reading private release data.

The output is a reviewer aid only. It never approves a task or creates MethodInput.
Only the R5 public release and the separately collected public issue catalog are read.
PR bodies, source archives, private GT, verifier files, and existing candidate task text
are deliberately excluded from the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(path: Path, root: Path, *, source_kind: str) -> dict[str, Any]:
    return {
        "source_kind": source_kind,
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected object at {path}:{line_no}")
        rows.append(value)
    return rows


def audit(public_root: Path, issue_catalog: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    public_root = public_root.resolve(strict=True)
    inputs_root = public_root / "inputs"
    manifest = load_jsonl(public_root / "manifest.jsonl")
    catalog_rows = load_jsonl(issue_catalog)
    catalog = {row["case_id"]: row for row in catalog_rows}
    manifest_ids = {row["case_id"] for row in manifest}
    if len(manifest_ids) != len(manifest):
        raise ValueError("duplicate case_id in R5 public manifest")
    if set(catalog) != manifest_ids:
        raise ValueError(
            f"issue catalog membership mismatch: missing={sorted(manifest_ids-set(catalog))}, "
            f"extra={sorted(set(catalog)-manifest_ids)}"
        )

    rows: list[dict[str, Any]] = []
    issue_case_count = 0
    issue_source_count = 0
    declaration_case_count = 0
    no_public_issue_or_fixture: list[str] = []
    for member in manifest:
        case_id = member["case_id"]
        case_root = inputs_root / case_id
        if case_root.is_symlink() or not case_root.resolve(strict=True).is_relative_to(inputs_root.resolve(strict=True)):
            raise ValueError(f"unsafe public case path: {case_id}")

        # Existing input.yaml may carry superseded, unreviewed task prose. Record only
        # its digest and structural flags; never copy its task text into this artifact.
        input_yaml = case_root / "input.yaml"
        legacy_input: dict[str, Any] | None = None
        if input_yaml.is_file() and not input_yaml.is_symlink():
            content = input_yaml.read_text(encoding="utf-8", errors="replace")
            legacy_input = {
                **file_entry(input_yaml, public_root, source_kind="QUARANTINED_EXISTING_INPUT"),
                "contains_task_key": any(line.lstrip().startswith("task:") for line in content.splitlines()),
                "contains_candidate_or_pending_marker": "candidate" in content.casefold() or "pending" in content.casefold(),
                "content_included": False,
                "usable_as_requirement_source": False,
            }

        issue_sources = []
        for issue in catalog[case_id].get("candidate_issue_sources", []):
            body = issue.get("body") or ""
            url = issue.get("url")
            if not isinstance(url, str) or "/issues/" not in url:
                continue
            issue_sources.append({
                "source_kind": "PUBLIC_ISSUE_BODY",
                "url": url,
                "title": issue.get("title"),
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "body_char_count": len(body),
                "content_included": False,
                "usable_as_requirement_source": bool(body.strip()),
                "review_required": True,
            })

        fixtures = []
        for path in sorted(case_root.iterdir()):
            if not path.is_file() or path.is_symlink() or path.name in {"input.yaml", "broken-source.tar.gz"}:
                continue
            if path.suffix.casefold() not in {".yaml", ".yml", ".json", ".toml", ".conf", ".ini"}:
                continue
            fixtures.append(file_entry(path, public_root, source_kind="PUBLIC_DECLARATION_OR_OBSERVATION_FIXTURE"))

        if issue_sources:
            issue_case_count += 1
            issue_source_count += len(issue_sources)
        if fixtures:
            declaration_case_count += 1
        if not issue_sources and not fixtures:
            no_public_issue_or_fixture.append(case_id)
        rows.append({
            "case_id": case_id,
            "release_id": member.get("status"),
            "issue_sources": issue_sources,
            "public_fixture_files": fixtures,
            "existing_input_quarantine": legacy_input,
            "review_status": "PENDING_HUMAN_PUBLIC_SOURCE_AND_TASK_FIDELITY_REVIEW",
            "method_input_eligible": False,
            "automated_approval": False,
        })

    summary = {
        "schema_version": "ce.r5_public_task_source_audit.v1",
        "public_release_case_count": len(manifest),
        "issue_catalog_case_count": len(catalog_rows),
        "cases_with_public_issue_evidence": issue_case_count,
        "public_issue_sources": issue_source_count,
        "cases_with_public_fixture_files": declaration_case_count,
        "cases_without_issue_or_fixture_evidence": no_public_issue_or_fixture,
        "existing_input_task_text_included": False,
        "pr_bodies_included": False,
        "private_gt_or_verifier_read": False,
        "decisions": "This index is not a task statement, human approval, GT decision, or MethodInput.",
    }
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--issue-catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows, summary = audit(args.public_root, args.issue_catalog)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    (output / "public-requirement-origin-index.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    (output / "public-requirement-origin-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
