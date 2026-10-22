#!/usr/bin/env python3
"""Read-only structural audit of the R5 v28 release and evaluator-only GT.

This checks identity, schema shape, relation references, class-boundary fields,
family ledger consistency, replay ledger coverage, and release hash audit status.
It deliberately does not decide whether GT relations are semantically complete.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def ids(rows: list[dict[str, Any]]) -> tuple[set[str], bool]:
    values = [str(row.get("id", "")) for row in rows]
    return set(values), len(values) == len(set(values)) and all(values)


def audit(benchmark_root: Path) -> dict[str, Any]:
    release = benchmark_root / "benchmark/R5/official_release_v28"
    root_manifest = jsonl(release / "manifest.jsonl")
    private_manifest = jsonl(release / "private/manifest.jsonl")
    replay = jsonl(release / "replay_ledger.jsonl")
    release_report = json.loads((release / "release_report.json").read_text(encoding="utf-8"))
    release_audit = json.loads((release / "release_audit.json").read_text(encoding="utf-8"))
    pairwise_path = benchmark_root / "reports/R5_PAIRWISE_FAMILY_ADJUDICATION_V27_2026-10-03.jsonl"
    pairwise = jsonl(pairwise_path)

    root_by_id = {row["case_id"]: row for row in root_manifest}
    private_by_id = {row["case_id"]: row for row in private_manifest}
    replay_by_id = {row["case_id"]: row for row in replay}
    pair_counter: Counter[tuple[str, str]] = Counter()
    pair_family_mismatches: list[dict[str, str]] = []
    for row in pairwise:
        left, right = sorted((row["candidate_a"], row["candidate_b"]))
        pair_counter[(left, right)] += 1
        if (row.get("repair_family_a") != root_by_id.get(row["candidate_a"], {}).get("repair_family")
                or row.get("repair_family_b") != root_by_id.get(row["candidate_b"], {}).get("repair_family")):
            pair_family_mismatches.append({"a": row["candidate_a"], "b": row["candidate_b"]})

    case_rows: list[dict[str, Any]] = []
    def resolves_endpoint(value: Any, unit_ids: set[str]) -> bool:
        if not isinstance(value, str):
            return False
        return any(value == unit_id or (
            value.startswith(unit_id) and len(value) > len(unit_id)
            and value[len(unit_id)] in " \t\r\n:([]"
        ) for unit_id in unit_ids)

    for case_id in sorted(set(root_by_id) | set(private_by_id)):
        row = root_by_id.get(case_id, {})
        private_row = private_by_id.get(case_id, {})
        issues: list[str] = []
        gt_path = release / private_row.get("private_gt", "")
        if not gt_path.is_file():
            issues.append("PRIVATE_GT_MISSING")
            gt: dict[str, Any] = {}
        else:
            gt = yaml.safe_load(gt_path.read_text(encoding="utf-8")) or {}
        if case_id not in root_by_id or case_id not in private_by_id:
            issues.append("ROOT_PRIVATE_MANIFEST_MISMATCH")
        if gt.get("case_id") != case_id:
            issues.append("GT_CASE_ID_MISMATCH")
        if row.get("primary_class") != "R5" or gt.get("primary_class") != "R5":
            issues.append("PRIMARY_CLASS_NOT_R5")
        boundary = gt.get("classification", {})
        for field in ("fault_boundary", "oracle_boundary", "observed_depth"):
            if not str(row.get(field, "")).strip() or not str(boundary.get(field, "")).strip():
                issues.append(f"{field.upper()}_MISSING")
        if row.get("fault_boundary") != boundary.get("fault_boundary"):
            issues.append("FAULT_BOUNDARY_MANIFEST_GT_MISMATCH")
        if row.get("oracle_boundary") != boundary.get("oracle_boundary"):
            issues.append("ORACLE_BOUNDARY_MANIFEST_GT_MISMATCH")
        if row.get("observed_depth") != boundary.get("observed_depth"):
            issues.append("OBSERVED_DEPTH_MANIFEST_GT_MISMATCH")
        if not gt.get("requirement", {}).get("statement"):
            issues.append("REQUIREMENT_STATEMENT_MISSING")

        coordination = gt.get("coordination", {})
        units = gt.get("relevant_units") or coordination.get("relevant_units") or coordination.get("units", [])
        expected = gt.get("expected_relations") or coordination.get("expected_relations", [])
        violations = gt.get("violated_relations") or coordination.get("violated_relations", [])
        unit_ids, unit_unique = ids(units)
        relation_ids, relation_unique = ids(expected)
        if not units or not unit_unique:
            issues.append("UNITS_EMPTY_OR_IDS_INVALID")
        if not expected or not relation_unique:
            issues.append("EXPECTED_RELATIONS_EMPTY_OR_IDS_INVALID")
        if not violations:
            issues.append("VIOLATIONS_EMPTY")
        for relation in expected:
            for side in ("source", "target"):
                endpoint = relation.get(side)
                if not resolves_endpoint(endpoint, unit_ids):
                    issues.append(f"EXPECTED_RELATION_ENDPOINT_UNRESOLVED:{relation.get('id')}:{side}")
        for violation in violations:
            relation_id = violation.get("relation")
            if relation_id not in relation_ids:
                issues.append(f"VIOLATION_RELATION_UNRESOLVED:{violation.get('id')}")
        replay_row = replay_by_id.get(case_id, {})
        if not replay_row:
            issues.append("REPLAY_LEDGER_ROW_MISSING")
        else:
            results = replay_row.get("results", {})
            if (len(results.get("broken_trigger", [])) != 3
                    or len(results.get("fixed_trigger", [])) != 3
                    or not str(results.get("broken_case_specific_controls", "")).startswith("PASS")
                    or not str(results.get("fixed_case_specific_controls", "")).startswith("PASS")):
                issues.append("REPLAY_MATRIX_MISSING")
        case_rows.append({
            "case_id": case_id,
            "issue_count": len(set(issues)),
            "issues": sorted(set(issues)),
            "unit_count": len(units),
            "expected_relation_count": len(expected),
            "violation_count": len(violations),
            "repair_family": row.get("repair_family"),
            "repair_pr": row.get("repair_pr"),
            "repository": row.get("repository"),
        })

    ids_root = set(root_by_id)
    ids_private = set(private_by_id)
    ids_replay = set(replay_by_id)
    expected_pair_count = len(ids_root) * (len(ids_root) - 1) // 2
    expected_pairs = {
        tuple(sorted((a, b)))
        for index, a in enumerate(sorted(ids_root))
        for b in sorted(ids_root)[index + 1:]
    }
    duplicate_pairs = [pair for pair, count in pair_counter.items() if count != 1]
    missing_pairs = sorted(expected_pairs - set(pair_counter))
    unexpected_pairs = sorted(set(pair_counter) - expected_pairs)

    family_counts = Counter(row.get("repair_family") for row in root_manifest)
    pr_counts = Counter(row.get("repair_pr") for row in root_manifest)
    repo_counts = Counter(row.get("repository") for row in root_manifest)
    structural_issues = [row for row in case_rows if row["issues"]]
    package_audit_pass = release_audit.get("decision") == "PASS" and not release_audit.get("errors")
    status = "STRUCTURAL_PASS_SEMANTIC_REVIEW_OPEN"
    if (len(root_manifest) != 30 or len(private_manifest) != 30 or len(replay) != 30
            or ids_root != ids_private or ids_root != ids_replay or structural_issues
            or len(family_counts) != 30 or len(pr_counts) != 30
            or len(pairwise) != expected_pair_count or missing_pairs or unexpected_pairs
            or duplicate_pairs or pair_family_mismatches or not package_audit_pass):
        status = "STRUCTURAL_REVIEW_REQUIRED"

    return {
        "schema_version": "contractedge.r5_v28_dataset_structure_audit.v1",
        "status": status,
        "scope": "read-only structure and release metadata; private GT accessed only by evaluator-side audit",
        "semantic_claim": "not adjudicated by this report; relation necessity/sufficiency and R5 admission remain human semantic review questions",
        "counts": {
            "root_manifest_cases": len(root_manifest),
            "private_manifest_cases": len(private_manifest),
            "replay_ledger_cases": len(replay),
            "unique_root_ids": len(ids_root),
            "root_private_id_sets_match": ids_root == ids_private,
            "root_replay_id_sets_match": ids_root == ids_replay,
            "case_gt_structural_issues": len(structural_issues),
            "unique_repair_families": len(family_counts),
            "unique_upstream_prs": len(pr_counts),
            "unique_repositories": len(repo_counts),
            "max_cases_per_repository": max(repo_counts.values(), default=0),
            "pairwise_rows": len(pairwise),
            "expected_unordered_pairs": expected_pair_count,
            "pairwise_missing": len(missing_pairs),
            "pairwise_duplicate_or_malformed": len(duplicate_pairs),
            "pairwise_unexpected": len(unexpected_pairs),
            "pairwise_family_mismatches": len(pair_family_mismatches),
            "release_hash_audit_pass": package_audit_pass,
            "reported_fresh_replay_cases": release_report.get("fresh_replayed_case_count"),
            "reported_formal_cases": release_report.get("case_count"),
        },
        "distribution": {
            "repository_counts": dict(sorted(repo_counts.items())),
            "pairwise_family_label_mismatch_rows": len(pair_family_mismatches),
        },
        "semantic_review_open": {
            "r5_precision_recall_eligibility": "30/30 unadjudicated in gt-qualification-v1_1-2026-10-04.json",
            "classification_and_relation_semantics": "not independently re-adjudicated here",
            "source_level_case_by_case_gt_review": "still required before claiming precision/recall quality",
        },
        "version_skew": {
            "pairwise_family_labels_match_current_v28_manifest": len(pair_family_mismatches) == 0,
            "details": "Pairwise rationales are from the v27 review ledger; its family label strings do not fully match the v28 manifest for some rows. This does not invalidate pair coverage, but the authoritative family labels and pairwise review should be reconciled before an immutable benchmark v1.1 publication.",
        },
        "issues": {
            "cases": structural_issues,
            "missing_pair_keys_sample": [list(pair) for pair in missing_pairs[:10]],
            "duplicate_pair_keys": [list(pair) for pair in duplicate_pairs],
            "unexpected_pair_keys_sample": [list(pair) for pair in unexpected_pairs[:10]],
            "pairwise_family_mismatch_sample": pair_family_mismatches[:10],
        },
        "case_rows": case_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path, default=Path("/home/xhj/contractedge/benchmark-ready-export"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.benchmark_root.resolve())
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["status"] == "STRUCTURAL_PASS_SEMANTIC_REVIEW_OPEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
