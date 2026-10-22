from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from contractedge.evaluation.gt_adapter import load_private_gt, normalize_gt, score_run


_METRICS = (
    "expected_relation_precision", "expected_relation_recall",
    "violation_precision", "violation_recall",
)


def _safe_member(root: Path, relative: str, label: str) -> Path:
    item = PurePosixPath(relative)
    if (not relative or item.is_absolute() or item.as_posix() != relative
            or any(part in {"", ".", ".."} for part in item.parts) or "\\" in relative):
        raise ValueError(f"unsafe {label} path: {relative!r}")
    path = root.joinpath(*item.parts)
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symlink: {relative}")
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} path is missing or escapes its root: {relative}") from exc
    if not path.is_file():
        raise ValueError(f"{label} path is not a regular file: {relative}")
    return path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(row["status"]) for row in rows)
    failure_counts = Counter(str(row["failure_kind"]) for row in rows if row.get("failure_kind"))
    metrics: dict[str, Any] = {}
    for metric in _METRICS:
        values = [row[metric] for row in rows if isinstance(row.get(metric), (int, float))]
        metrics[metric] = {"n": len(values), "mean": statistics.fmean(values) if values else None}
    return {
        "cases": len(rows),
        "attempted": len(rows) - status_counts.get("NOT_RUN", 0),
        "not_run": status_counts.get("NOT_RUN", 0),
        "status_counts": dict(sorted(status_counts.items())),
        "failure_kind_counts": dict(sorted(failure_counts.items())),
        "usable_diagnosis_count": status_counts.get("READY", 0) + status_counts.get("READY_PARTIAL", 0),
        "metrics": metrics,
    }


def build_cohort_report(members: Sequence[Mapping[str, Any]],
                        trace_rows: Sequence[Mapping[str, Any]],
                        trace_root: str | Path,
                        gt_root: str | Path) -> dict[str, Any]:
    """Score a frozen evaluator manifest; missing attempts remain explicit in its denominator."""
    traces_root = Path(trace_root).resolve(strict=True)
    ground_truth_root = Path(gt_root).resolve(strict=True)
    member_by_id: dict[str, Mapping[str, Any]] = {}
    for member in members:
        case_id = member.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in member_by_id:
            raise ValueError("frozen cohort contains a missing or duplicate case_id")
        member_by_id[case_id] = member
    trace_by_id: dict[str, Mapping[str, Any]] = {}
    for row in trace_rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in trace_by_id:
            raise ValueError("trace manifest contains a missing or duplicate case_id")
        if case_id not in member_by_id:
            raise ValueError(f"trace case {case_id!r} is outside the frozen cohort")
        trace_by_id[case_id] = row

    records: list[dict[str, Any]] = []
    for case_id, member in sorted(member_by_id.items()):
        release_id = str(member["release_id"])
        trace_row = trace_by_id.get(case_id)
        if trace_row is None:
            records.append({
                "case_id": case_id, "release_id": release_id,
                "primary_class": member.get("primary_class") or release_id.split("-", 1)[0],
                "fault_boundary": member.get("fault_boundary"),
                "observed_depth": member.get("observed_depth"),
                "oracle_boundary": member.get("oracle_boundary"),
                "oracle_limit": member.get("oracle_boundary"),
                "status": "NOT_RUN", "denominator_included": True,
                "failure_kind": "NOT_RUN", "scoring_status": "NOT_SCORED_NOT_RUN",
                "precision": None, "recall": None,
                "expected_relation_precision": None, "expected_relation_recall": None,
                "violation_precision": None, "violation_recall": None,
                "precision_eligible": None, "recall_eligible": None,
                "eligibility_status": "NOT_EVALUATED_NOT_RUN",
            })
            continue

        gt_relative = member.get("ground_truth")
        if not isinstance(gt_relative, str):
            raise ValueError(f"frozen member {case_id} has no ground_truth path")
        member_gt_root = Path(member.get("ground_truth_root", ground_truth_root)).resolve(strict=True)
        gt_path = _safe_member(member_gt_root, gt_relative, "ground-truth")
        expected_gt_hash = member.get("ground_truth_sha256")
        if expected_gt_hash is not None and _hash(gt_path) != expected_gt_hash:
            raise ValueError(f"ground-truth digest mismatch for {case_id}")
        trace_relative = trace_row.get("trace")
        if not isinstance(trace_relative, str):
            raise ValueError(f"trace manifest row for {case_id} has no trace path")
        trace_path = _safe_member(traces_root, trace_relative, "trace")
        try:
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read trace for {case_id}: {exc}") from exc
        if not isinstance(trace, Mapping) or trace.get("case_id") != case_id:
            raise ValueError(f"trace case_id does not match frozen member {case_id}")
        private_gt = load_private_gt(gt_path)
        metadata = {**dict(member), "case_id": case_id}
        normalized = normalize_gt(private_gt, release_id, release_metadata=metadata)
        record = score_run(trace, normalized)
        record["trace_path"] = trace_relative
        records.append(record)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["release_id"])].append(record)
    return {
        "schema_version": "ce.cohort_score_report.v1",
        "cohort_size": len(member_by_id),
        "attempted_count": len(trace_by_id),
        "case_count": len(records),
        "failure_denominator_includes_not_run": True,
        "metric_denominators_include_only_eligible_scored_cases": True,
        "by_release": {release: _summary(rows) for release, rows in sorted(grouped.items())},
        "cases": records,
    }
