#!/usr/bin/env python3
"""Audit evaluator-only GT references and metric eligibility for frozen R1/R2/R5."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from contractedge.evaluation.gt_adapter import load_private_gt, normalize_gt


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ids(rows: list[Mapping[str, Any]]) -> tuple[set[str], bool]:
    values = [str(row["id"]) for row in rows if row.get("id") is not None]
    return set(values), len(values) == len(set(values))


def _endpoints(relation: Mapping[str, Any], side: str) -> list[str]:
    value = relation.get(f"{side}_unit_ids", relation.get(side))
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []


def _audit_case(gt: Mapping[str, Any], release_id: str, expected_case_id: str,
                release_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    normalized = normalize_gt(gt, release_id, release_metadata=release_metadata)
    actual_case_id = normalized.get("case_id")
    case_id = expected_case_id
    units = normalized["units"]
    relations = normalized["expected_relations"]
    violations = normalized["violations"]
    unit_ids, unique_units = _ids(units)
    relation_ids, unique_relations = _ids(relations)
    issues: list[str] = []
    if actual_case_id is None:
        issues.append("GT_CASE_ID_ABSENT_MANIFEST_BINDED")
    elif actual_case_id != expected_case_id:
        issues.append("GT_CASE_ID_MISMATCH")
    if not unique_units:
        issues.append("DUPLICATE_OR_MISSING_UNIT_ID")
    if not unique_relations:
        issues.append("DUPLICATE_OR_MISSING_RELATION_ID")
    if not relations:
        issues.append("EXPECTED_RELATIONS_EMPTY")
    if not violations:
        issues.append("VIOLATIONS_EMPTY")
    for index, relation in enumerate(relations):
        for side in ("source", "target"):
            values = _endpoints(relation, side)
            if not values:
                issues.append(f"RELATION_{index}_{side.upper()}_MISSING")
            elif set(values) - unit_ids:
                issues.append(f"RELATION_{index}_{side.upper()}_UNIT_UNRESOLVED")
    for index, violation in enumerate(violations):
        relation_id = violation.get("relation_id", violation.get("relation"))
        if relation_id is None:
            issues.append(f"VIOLATION_{index}_RELATION_ID_MISSING")
        elif str(relation_id) not in relation_ids:
            issues.append(f"VIOLATION_{index}_RELATION_UNRESOLVED")
    if normalized["precision_eligible"] is None:
        issues.append("PRECISION_ELIGIBILITY_UNADJUDICATED")
    if normalized["recall_eligible"] is None:
        issues.append("RECALL_ELIGIBILITY_UNADJUDICATED")
    return {
        "case_id": case_id, "release_id": release_id,
        "gt_case_id_matches_manifest": actual_case_id == expected_case_id,
        "primary_class": normalized["primary_class"],
        "unit_count": len(units), "expected_relation_count": len(relations),
        "violation_count": len(violations),
        "precision_eligible": normalized["precision_eligible"],
        "recall_eligible": normalized["recall_eligible"],
        "full_recall_eligible": normalized["full_recall_eligible"],
        "oracle_limit_present": normalized["oracle_limit"] is not None,
        "issues": sorted(set(issues)),
    }


def build_report(benchmark_root: Path, system_root: Path) -> dict[str, Any]:
    prepared_root = system_root / "outputs/pilot-v1_1-r1-r2-v2/evaluator"
    prepared_rows = _jsonl(prepared_root / "manifest.jsonl")
    release_metadata = {}
    for release in ("R1", "R2"):
        for row in _jsonl(benchmark_root / f"benchmark/{release}/manifest.jsonl"):
            release_metadata[row["candidate_id"]] = row
    cases = []
    for row in prepared_rows:
        release_id = str(row["release_id"])
        gt = load_private_gt(prepared_root / row["ground_truth"])
        cases.append(_audit_case(gt, release_id, str(row["case_id"]),
                                 release_metadata.get(str(row["case_id"]))))

    r5_root = benchmark_root / "benchmark/R5/official_release_v28"
    for row in _jsonl(r5_root / "private/manifest.jsonl"):
        gt = load_private_gt(r5_root / row["private_gt"])
        cases.append(_audit_case(gt, "R5", str(row["case_id"]), row))

    if len(cases) != 117 or len({row["case_id"] for row in cases}) != 117:
        raise ValueError("evaluator-only GT must resolve to 117 unique formal case IDs")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in cases:
        grouped.setdefault(row["release_id"], []).append(row)

    def eligibility_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "cases": len(rows),
            "precision_true": sum(row["precision_eligible"] is True for row in rows),
            "precision_false": sum(row["precision_eligible"] is False for row in rows),
            "precision_unadjudicated": sum(row["precision_eligible"] is None for row in rows),
            "recall_true": sum(row["recall_eligible"] is True for row in rows),
            "recall_false": sum(row["recall_eligible"] is False for row in rows),
            "recall_unadjudicated": sum(row["recall_eligible"] is None for row in rows),
            "oracle_limit_present": sum(row["oracle_limit_present"] for row in rows),
            "cases_with_gt_issues": sum(bool(row["issues"]) for row in rows),
        }

    issue_cases = [row for row in cases if row["issues"]]
    return {
        "schema_version": "ce.gt_qualification_audit.v1",
        "status": "REVIEW_REQUIRED" if issue_cases else "PASS",
        "gt_scope": "evaluator-only; no private GT text is copied to public MethodInput",
        "case_count": len(cases),
        "unique_case_ids": len({row["case_id"] for row in cases}),
        "by_release": {release: eligibility_rows(rows) for release, rows in sorted(grouped.items())},
        "issue_counts": dict(sorted(Counter(issue for row in cases for issue in row["issues"]).items())),
        "cases": sorted(cases, key=lambda row: (row["release_id"], row["case_id"])),
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
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
