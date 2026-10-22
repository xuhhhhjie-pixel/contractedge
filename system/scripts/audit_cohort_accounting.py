#!/usr/bin/env python3
"""Account for every frozen R1/R2/R5 member in the v1.1 method preparation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{number}: expected an object")
        rows.append(row)
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_manifest(root: Path) -> dict[str, str]:
    result = {}
    for number, line in enumerate((root / "SHA256SUMS").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        if relative in result:
            raise ValueError(f"duplicate SHA256SUMS member at {root}:{number}: {relative}")
        result[relative] = digest
    return result


def build_report(benchmark_root: Path, system_root: Path) -> dict[str, Any]:
    benchmark = benchmark_root / "benchmark"
    prepared_root = system_root / "outputs/pilot-v1_1-r1-r2-v2"
    prepared_audit = json.loads((system_root / "outputs/pilot-v1_1-r1-r2-v2-audit.json").read_text())
    if prepared_audit.get("status") != "PASS":
        raise ValueError("R1/R2 prepared release audit is not PASS")

    formal_by_class = {
        "R1": {row["candidate_id"] for row in _jsonl(benchmark / "R1/manifest.jsonl")},
        "R2": {row["candidate_id"] for row in _jsonl(benchmark / "R2/manifest.jsonl")},
    }
    prepared_rows = _jsonl(prepared_root / "public/manifest.jsonl")
    prepared_ids = {row["case_id"] for row in prepared_rows}
    expected_r12 = formal_by_class["R1"] | formal_by_class["R2"]
    if formal_by_class["R1"] & formal_by_class["R2"]:
        raise ValueError("R1/R2 source manifests have duplicate case IDs")
    if prepared_ids != expected_r12:
        raise ValueError("prepared R1/R2 membership does not match the source manifests")

    r5_root = benchmark / "R5/official_release_v28"
    r5_public = r5_root / "public"
    r5_rows = _jsonl(r5_public / "manifest.jsonl")
    r5_ids = {row["case_id"] for row in r5_rows if row.get("formal_membership") is True}
    if len(r5_rows) != 30 or len(r5_ids) != 30:
        raise ValueError("R5 v28 must have exactly 30 unique formal public manifest members")
    if expected_r12 & r5_ids:
        raise ValueError("R1/R2 and R5 formal case IDs overlap")

    preflight = json.loads((system_root / "outputs/r5-v28-adapter-review/preflight-report.json").read_text())
    decisions = {row["case_id"]: row for row in preflight.get("decisions", [])}
    if set(decisions) != r5_ids or any(row.get("status") != "HELD" for row in decisions.values()):
        raise ValueError("R5 adapter preflight does not account for all 30 members as held")

    input_hashes = _hash_manifest(r5_public)
    task_count = 0
    candidate_statuses: Counter[str] = Counter()
    input_hashes_checked = 0
    for row in r5_rows:
        relative = row["input"]
        path = (r5_public / relative).resolve(strict=True)
        path.relative_to(r5_public.resolve(strict=True))
        digest = _sha256(path)
        if input_hashes.get(relative) != digest:
            raise ValueError(f"R5 input hash mismatch or unlisted input: {row['case_id']}")
        input_hashes_checked += 1
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        first = documents[0] if len(documents) == 1 and isinstance(documents[0], dict) else {}
        task_count += int(isinstance(first.get("task"), str) and bool(first["task"].strip()))
        if isinstance(first.get("candidate_status"), str):
            candidate_statuses[first["candidate_status"]] += 1

    public_readme = (r5_public / "README.md").read_text(encoding="utf-8").strip()
    metadata_findings = []
    if "candidate method view v17" in public_readme.casefold() and len(r5_ids) == 30:
        metadata_findings.append("PUBLIC_README_IDENTIFIES_CANDIDATE_V17_WHILE_MANIFEST_MARKS_30_FORMAL")
    if task_count != 30:
        metadata_findings.append(f"PUBLIC_TASK_FIELD_INCOMPLETE_{task_count}_OF_30")
    if candidate_statuses:
        metadata_findings.append(f"PUBLIC_NONFORMAL_CANDIDATE_STATUS_{sum(candidate_statuses.values())}_OF_30")

    all_ids = expected_r12 | r5_ids
    held_rows = [decisions[case_id] for case_id in sorted(r5_ids)]
    classes = Counter()
    for row in prepared_rows:
        classes[row["release_id"].split("-", 1)[0]] += 1
    classes["R5"] = len(r5_ids)
    return {
        "schema_version": "ce.cohort_accounting.v1",
        "status": "PASS_WITH_HELD_MEMBERS" if len(prepared_ids) == 87 and len(r5_ids) == 30 else "FAIL",
        "frozen_member_count": len(all_ids),
        "unique_case_id_count": len(all_ids),
        "class_counts": dict(sorted(classes.items())),
        "v1_1_prepared_case_count": len(prepared_ids),
        "v1_1_held_case_count": len(r5_ids),
        "prepared_release_audit": prepared_audit,
        "held_reason_counts": dict(sorted(Counter(row["reason"].split(":", 1)[0] for row in held_rows).items())),
        "r5_public_integrity": {
            "formal_manifest_members": len(r5_ids),
            "input_hashes_verified": input_hashes_checked,
            "public_task_fields": task_count,
            "candidate_status_values": dict(candidate_statuses),
            "metadata_findings": metadata_findings,
            "readme_excerpt": public_readme,
        },
        "decisions": [
            {"case_id": row["case_id"], "release_id": row["release_id"], "status": "PREPARED"}
            for row in prepared_rows
        ] + [
            {"case_id": row["case_id"], "release_id": row["release_id"],
             "status": "HELD", "reason": row["reason"]}
            for row in held_rows
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path,
                        default=Path("/home/xhj/contractedge/benchmark-ready-export"))
    parser.add_argument("--system-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.benchmark_root.resolve(), args.system_root.resolve())
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "PASS_WITH_HELD_MEMBERS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
