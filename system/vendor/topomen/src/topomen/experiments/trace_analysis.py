from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console(highlight=False)


def expand_trace_paths(patterns: list[str | Path]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for pattern in patterns:
        text = str(pattern)
        matches = [Path(match) for match in glob.glob(text)] if glob.has_magic(text) else [Path(text)]
        for path in matches:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    return paths


def load_trace_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            msg = f"Invalid JSON in {path} at line {line_number}: {exc}"
            raise ValueError(msg) from exc
        if not isinstance(payload, dict):
            msg = f"Expected JSON object in {path} at line {line_number}"
            raise ValueError(msg)
        events.append(payload)
    return events


def load_trace_events_from_paths(paths: list[Path]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in paths:
        events.extend(load_trace_events(path))
    return events


def summarize_trace_events(events: list[dict[str, Any]], *, trace_files: list[Path] | None = None) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        group = str(event.get("group") or "unknown")
        metrics = groups.setdefault(group, _new_group_metrics())
        _update_group_metrics(metrics, event)

    return {
        "trace_files": [str(path) for path in trace_files or []],
        "total_events": len(events),
        "groups": {group: _finalize_group_metrics(metrics) for group, metrics in sorted(groups.items())},
    }


def write_trace_summary(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def render_trace_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Topology Trace Analysis",
        "",
        f"- trace files: {len(summary.get('trace_files', []))}",
        f"- total events: {summary.get('total_events', 0)}",
        "",
        "## Group Summary",
        "",
        "| group | final results | successes | RAG hits | topology memory matches | prefill candidates | memory prefill | pattern matches | xproj patterns | blocked patterns | pattern prefill | LLM loops | guardrail rejects | prevalidation |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|---:|---:|---|",
    ]
    groups = summary.get("groups", {})
    for group, metrics in groups.items():
        prevalidation = _format_counter(metrics.get("prevalidation_results", {}))
        lines.append(
            "| {group} | {final_results} | {successes} | {memory_hits} | {topology_memory_matches} | {prefill_candidates} | {memory_prefill} | {pattern_matches} | {xproj_patterns} | {blocked_patterns} | {pattern_prefill} | {llm_loops} | {guardrail_rejects} | {prevalidation} |".format(
                group=group,
                final_results=metrics.get("final_results", 0),
                successes=metrics.get("successes", 0),
                memory_hits=metrics.get("memory_hits_observed", 0),
                topology_memory_matches=metrics.get("topology_memory_matches", 0),
                prefill_candidates=metrics.get("memory_prefill_candidates", 0),
                memory_prefill=_format_memory_prefill(metrics),
                pattern_matches=metrics.get("pattern_memory_matches", 0),
                xproj_patterns=metrics.get("pattern_cross_project_matches", 0),
                blocked_patterns=metrics.get("pattern_low_support_matches", 0)
                + metrics.get("pattern_negative_evidence_matches", 0),
                pattern_prefill=_format_pattern_prefill(metrics),
                llm_loops=metrics.get("llm_loop_started", 0),
                guardrail_rejects=metrics.get("guardrail_rejection_events", 0),
                prevalidation=prevalidation or "-",
            )
        )

    lines.extend(["", "## Node Attribution", ""])
    for group, metrics in groups.items():
        lines.append(f"### {group}")
        lines.append("")
        lines.append(f"- attributed failures: {_format_counter(metrics.get('failure_nodes', {})) or '-'}")
        lines.append(f"- final repair nodes: {_format_counter(metrics.get('final_repair_nodes', {})) or '-'}")
        lines.append(f"- error types: {_format_counter(metrics.get('error_types', {})) or '-'}")
        lines.append(f"- guardrail violations: {_format_counter(metrics.get('guardrail_violation_types', {})) or '-'}")
        lines.append(f"- runtime validation modes: {_format_counter(metrics.get('runtime_validation_modes', {})) or '-'}")
        lines.append(f"- runtime command sources: {_format_counter(metrics.get('runtime_command_sources', {})) or '-'}")
        lines.append(
            "- runtime validation memory: matches={matches}, prefill_candidates={prefill}".format(
                matches=metrics.get("runtime_validation_memory_matches", 0),
                prefill=metrics.get("runtime_validation_memory_prefill_candidates", 0),
            )
        )
        lines.append(
            f"- runtime validation effect: planned={metrics.get('runtime_validation_planned', 0)}, "
            f"executed={metrics.get('runtime_validation_executed', 0)}, "
            f"skipped={metrics.get('runtime_validation_skipped', 0)}"
        )
        lines.append(
            f"- runtime fallback: attempted={metrics.get('runtime_fallback_attempted', 0)}, "
            f"succeeded={metrics.get('runtime_fallback_succeeded', 0)}"
        )
        lines.append(
            f"- preflight: checks={metrics.get('preflight_checks', 0)}, "
            f"blocked={metrics.get('preflight_blocked', 0)}"
        )
        lines.append(
            f"- planning: avg_seconds={metrics.get('avg_topology_planning_seconds', 0.0)}, "
            f"avg_python_files={metrics.get('avg_python_files_scanned', 0.0)}"
        )
        planner_eval_summary = metrics.get("planner_eval_summary", {})
        lines.append(
            f"- planner selected targets: {_format_counter(planner_eval_summary.get('selected_targets', {})) or '-'}"
        )
        lines.append(
            f"- planner rejected reasons: {_format_counter(planner_eval_summary.get('rejected_reasons', {})) or '-'}"
        )
        lines.append(
            f"- verifier/critic: checked={metrics.get('verifier_checked', 0)}/{metrics.get('critic_checked', 0)}, "
            f"rejected={metrics.get('verifier_rejected', 0)}/{metrics.get('critic_rejected', 0)}"
        )
        lines.append(
            f"- node repair package: injected={metrics.get('node_repair_package_injected', 0)}, "
            f"retry_escalated={metrics.get('node_retry_escalated', 0)}"
        )
        lines.append(
            f"- checkpoint/rollback: created={metrics.get('checkpoint_created', 0)}, "
            f"applied={metrics.get('rollback_applied', 0)}, skipped={metrics.get('rollback_skipped', 0)}"
        )
        lines.append(
            f"- build cache: planned={metrics.get('build_cache_planned', 0)}, "
            f"enabled={metrics.get('build_cache_enabled_count', 0)}, "
            f"reuse_existing={metrics.get('build_cache_reuse_existing_count', 0)}, "
            f"scopes={_format_counter(metrics.get('build_cache_id_scopes', {})) or '-'}, "
            f"namespaces={_format_counter(metrics.get('build_cache_namespaces', {})) or '-'}"
        )
        lines.append(
            f"- topology DAG scheduling: plans={metrics.get('topology_build_plan_created', 0)}, "
            f"ready={metrics.get('scheduler_node_ready', 0)}, "
            f"resource_wait_events={metrics.get('scheduler_node_waiting_resource', 0)}, "
            f"ready_queue_max={metrics.get('ready_queue_max_size', 0)}, "
            f"resource_wait_seconds={metrics.get('resource_wait_seconds', 0.0)}, "
            f"critical_path_seconds={metrics.get('critical_path_seconds', 0.0)}, "
            f"rebuild_saved_nodes={metrics.get('rebuild_saved_nodes', 0)}, "
            f"internal_image_reused={metrics.get('internal_image_reused_count', 0)}, "
            f"node_cache_rates={_format_float_counter(metrics.get('cache_hit_rate_by_node', {})) or '-'}, "
            f"strategies={_format_counter(metrics.get('build_scheduler_strategies', {})) or '-'}"
        )
        lines.append(f"- scheduler node states: {_format_counter(metrics.get('scheduler_node_states', {})) or '-'}")
        lines.append(f"- scheduler skipped nodes: {_format_counter(metrics.get('scheduler_skipped_nodes', {})) or '-'}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_trace_markdown(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_trace_markdown(summary), encoding="utf-8")


def derive_trace_summary_path(trace_path: Path) -> Path:
    stem = trace_path.stem
    if stem.endswith("_trace"):
        stem = stem[: -len("_trace")]
    return trace_path.with_name(f"{stem}_trace_summary.json")


def derive_trace_report_path(trace_path: Path) -> Path:
    stem = trace_path.stem
    if stem.endswith("_trace"):
        stem = stem[: -len("_trace")]
    return trace_path.with_name(f"{stem}_trace_report.md")


def analyze_trace_files(
    trace_patterns: list[str],
    *,
    summary_path: Path | None = None,
    report_path: Path | None = None,
) -> dict:
    trace_paths = expand_trace_paths(trace_patterns)
    if not trace_paths:
        msg = f"No trace files matched: {', '.join(trace_patterns)}"
        raise FileNotFoundError(msg)
    missing = [path for path in trace_paths if not path.exists()]
    if missing:
        msg = f"Trace file does not exist: {missing[0]}"
        raise FileNotFoundError(msg)

    events = load_trace_events_from_paths(trace_paths)
    summary = summarize_trace_events(events, trace_files=trace_paths)
    if summary_path is not None:
        write_trace_summary(summary, summary_path)
    if report_path is not None:
        write_trace_markdown(summary, report_path)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize topology trace JSONL files.")
    parser.add_argument(
        "trace",
        nargs="+",
        help="Trace JSONL path or glob pattern, e.g. experiments/results/traces/*_trace.jsonl.",
    )
    parser.add_argument("--summary-path", type=Path, help="JSON output path for trace metrics.")
    parser.add_argument("--report-path", type=Path, help="Markdown output path for trace metrics.")
    parser.add_argument("--print-markdown", action="store_true", help="Print the Markdown report instead of JSON.")
    return parser


def main_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trace_paths = expand_trace_paths(args.trace)
    if not trace_paths:
        msg = f"No trace files matched: {', '.join(args.trace)}"
        raise FileNotFoundError(msg)

    summary_path = args.summary_path
    report_path = args.report_path
    if len(trace_paths) == 1:
        summary_path = summary_path or derive_trace_summary_path(trace_paths[0])
        report_path = report_path or derive_trace_report_path(trace_paths[0])

    summary = analyze_trace_files(
        [str(path) for path in trace_paths],
        summary_path=summary_path,
        report_path=report_path,
    )
    if args.print_markdown:
        console.print(render_trace_markdown(summary))
    else:
        console.print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _new_group_metrics() -> dict[str, Any]:
    return {
        "projects": set(),
        "total_events": 0,
        "event_types": Counter(),
        "memory_hits_observed": 0,
        "memory_retrieval_events": 0,
        "retrieved_matches": 0,
        "memory_precise_matches": 0,
        "topology_memory_matches": 0,
        "memory_prefill_candidates": 0,
        "memory_prefill_applied_packages": Counter(),
        "memory_evidence_items": 0,
        "memory_prefill_apt_packages": Counter(),
        "memory_prefill_python_packages": Counter(),
        "pattern_evidence_items": 0,
        "pattern_memory_matches": 0,
        "pattern_cross_project_matches": 0,
        "pattern_low_support_matches": 0,
        "pattern_negative_evidence_matches": 0,
        "runtime_validation_memory_matches": 0,
        "runtime_validation_memory_prefill_candidates": 0,
        "pattern_prefill_apt_packages": Counter(),
        "pattern_prefill_python_packages": Counter(),
        "pattern_source_projects": Counter(),
        "llm_loop_started": 0,
        "failure_nodes": Counter(),
        "error_types": Counter(),
        "failure_stages": Counter(),
        "guardrail_checks": 0,
        "guardrail_rejection_events": 0,
        "guardrail_violation_types": Counter(),
        "max_cumulative_guardrail_violations": 0,
        "prevalidation_results": Counter(),
        "prevalidation_command_failures": 0,
        "action_events": 0,
        "failed_action_events": 0,
        "final_results": 0,
        "successes": 0,
        "failures": 0,
        "prefill_validated_first": 0,
        "final_repair_nodes": Counter(),
        "scheduler_started": 0,
        "scheduler_passed": 0,
        "scheduler_failed": 0,
        "scheduler_executed_nodes": Counter(),
        "scheduler_failed_nodes": Counter(),
        "scheduler_skipped_nodes": Counter(),
        "scheduler_node_states": Counter(),
        "runtime_validation_modes": Counter(),
        "runtime_command_sources": Counter(),
        "scheduler_stage_parallel": 0,
        "parallel_stage_names": Counter(),
        "runtime_fallback_attempted": 0,
        "runtime_fallback_succeeded": 0,
        "preflight_checks": 0,
        "preflight_blocked": 0,
        "preflight_blockers": Counter(),
        "topology_planning_seconds_total": 0.0,
        "topology_planning_samples": 0,
        "python_files_scanned_total": 0,
        "python_scan_budgets": Counter(),
        "planner_selected_targets": Counter(),
        "planner_rejected_reasons": Counter(),
        "planner_versions": Counter(),
        "verifier_checked": 0,
        "verifier_rejected": 0,
        "critic_checked": 0,
        "critic_rejected": 0,
        "node_repair_package_injected": 0,
        "node_retry_escalated": 0,
        "checkpoint_created": 0,
        "rollback_applied": 0,
        "rollback_skipped": 0,
        "threaded_build_used_count": 0,
        "threaded_base_materialized": 0,
        "threaded_base_fast_path": 0,
        "parallel_build_planned": 0,
        "parallel_build_branch_started": 0,
        "parallel_build_branch_skipped": 0,
        "parallel_build_branch_finished": 0,
        "parallel_merge_started": 0,
        "parallel_merge_succeeded": 0,
        "parallel_repair_branch_started": 0,
        "parallel_repair_branch_won": 0,
        "parallel_branch_rollback_applied": 0,
        "parallel_build_branches_total": 0,
        "parallel_repair_used_count": 0,
        "repair_branches_tried_total": 0,
        "scheduler_parallel_repair": 0,
        "build_cache_planned": 0,
        "build_cache_enabled_count": 0,
        "build_cache_reuse_existing_count": 0,
        "build_cache_namespaces": Counter(),
        "build_cache_id_scopes": Counter(),
        "build_cache_ids": Counter(),
        "final_images": Counter(),
        "internal_branch_image_events": 0,
        "internal_branch_images_cleaned": 0,
        "public_image_contract_violations": 0,
        "topology_build_plan_created": 0,
        "scheduler_node_ready": 0,
        "scheduler_node_waiting_resource": 0,
        "scheduler_node_finished": 0,
        "ready_queue_max_size": 0,
        "resource_wait_seconds": 0.0,
        "critical_path_seconds": 0.0,
        "node_wait_seconds": Counter(),
        "node_build_seconds": Counter(),
        "reused_internal_images": 0,
        "internal_image_reused_count": 0,
        "rebuild_saved_nodes": 0,
        "cache_hit_by_node": Counter(),
        "cache_total_by_node": Counter(),
        "cache_hit_rate_by_node": {},
        "topology_node_repaired": 0,
        "repair_scopes": Counter(),
        "build_scheduler_strategies": Counter(),
    }


def _update_group_metrics(metrics: dict[str, Any], event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or "unknown")
    metrics["total_events"] += 1
    metrics["event_types"][event_type] += 1
    if event.get("project"):
        metrics["projects"].add(str(event["project"]))
    if event_type in {"parallel_merge_started", "parallel_merge_succeeded", "scheduler_passed"}:
        _update_image_contract_metrics(metrics, event)
    if event_type == "internal_branch_images_cleaned":
        metrics["internal_branch_images_cleaned"] += 1
        _update_image_contract_metrics(metrics, event)
    _update_topology_dag_metrics(metrics, event)

    memory_hits = _as_int(event.get("memory_hits"))
    metrics["memory_hits_observed"] = max(metrics["memory_hits_observed"], memory_hits)
    metrics["memory_precise_matches"] = max(metrics["memory_precise_matches"], _as_int(event.get("memory_precise_matches")))
    metrics["max_cumulative_guardrail_violations"] = max(
        metrics["max_cumulative_guardrail_violations"],
        _as_int(event.get("guardrail_violations")),
    )

    if event_type == "topology_created":
        metrics["memory_evidence_items"] += len(event.get("memory_evidence_items") or [])
        prefill_packages = event.get("memory_prefill_packages") or {}
        metrics["memory_prefill_apt_packages"].update(str(package) for package in prefill_packages.get("apt") or [])
        metrics["memory_prefill_python_packages"].update(str(package) for package in prefill_packages.get("python") or [])
        metrics["memory_prefill_applied_packages"].update(
            str(package) for package in [*(prefill_packages.get("apt") or []), *(prefill_packages.get("python") or [])]
        )
        repair_memory_matches = event.get("repair_memory_matches") or []
        metrics["topology_memory_matches"] += len(repair_memory_matches)
        metrics["memory_prefill_candidates"] += sum(
            1 for match in repair_memory_matches if match.get("evidence_level") == "prefill"
        )
        pattern_matches = event.get("pattern_memory_matches") or []
        metrics["pattern_memory_matches"] += len(pattern_matches)
        metrics["pattern_cross_project_matches"] += sum(
            1 for match in pattern_matches if _as_int(match.get("unique_project_count")) >= 2
        )
        metrics["pattern_low_support_matches"] += sum(
            1 for match in pattern_matches if match.get("blocked_reason") == "insufficient_cross_project_support"
        )
        metrics["pattern_negative_evidence_matches"] += sum(
            1
            for match in pattern_matches
            if match.get("blocked_reason") == "negative_evidence" or _as_int(match.get("failed_project_count")) > 0
        )
        runtime_memory_matches = event.get("runtime_validation_memory_matches") or []
        metrics["runtime_validation_memory_matches"] += len(runtime_memory_matches)
        metrics["runtime_validation_memory_prefill_candidates"] += sum(
            1 for match in runtime_memory_matches if match.get("prefill_ready")
        )
        metrics["pattern_evidence_items"] += len(event.get("pattern_evidence_items") or [])
        pattern_prefill_packages = event.get("pattern_prefill_packages") or {}
        metrics["pattern_prefill_apt_packages"].update(
            str(package) for package in pattern_prefill_packages.get("apt") or []
        )
        metrics["pattern_prefill_python_packages"].update(
            str(package) for package in pattern_prefill_packages.get("python") or []
        )
        metrics["pattern_source_projects"].update(str(project) for project in event.get("pattern_source_projects") or [])
        planning_seconds = event.get("topology_planning_seconds")
        if isinstance(planning_seconds, (int, float)):
            metrics["topology_planning_seconds_total"] += float(planning_seconds)
            metrics["topology_planning_samples"] += 1
        files_scanned = _as_int(event.get("python_files_scanned"))
        if files_scanned > 0:
            metrics["python_files_scanned_total"] += files_scanned
        budget = _as_int(event.get("python_scan_budget"))
        if budget > 0:
            metrics["python_scan_budgets"][str(budget)] += 1
        build_cache_plan = event.get("build_cache_plan") or {}
        if isinstance(build_cache_plan, dict) and build_cache_plan.get("namespace"):
            metrics["build_cache_namespaces"][str(build_cache_plan["namespace"])] += 1
    elif event_type == "repair_memory_retrieved":
        matches = event.get("matches") or []
        metrics["memory_retrieval_events"] += 1
        metrics["retrieved_matches"] += len(matches)
        metrics["memory_precise_matches"] = max(
            metrics["memory_precise_matches"],
            sum(1 for match in matches if match.get("evidence_level") in {"candidate", "prefill"}),
        )
    elif event_type == "llm_loop_started":
        metrics["llm_loop_started"] += 1
    elif event_type == "failure_attributed":
        _update_failure_metrics(metrics, event)
    elif event_type == "guardrail_check":
        _update_guardrail_metrics(metrics, event)
    elif event_type in {"prevalidation_failed", "prevalidation_passed"}:
        stage = str(event.get("stage") or "unknown")
        result = str(event.get("prevalidation_result") or event_type.removeprefix("prevalidation_"))
        metrics["prevalidation_results"][f"{stage}:{result}"] += 1
    elif event_type == "prevalidation_command_finished" and _as_int(event.get("returncode")) != 0:
        metrics["prevalidation_command_failures"] += 1
    elif event_type == "action_executed":
        metrics["action_events"] += 1
        if _as_int(event.get("returncode")) != 0:
            metrics["failed_action_events"] += 1
    elif event_type == "final_result":
        _update_final_metrics(metrics, event)
    elif event_type == "scheduler_started":
        metrics["scheduler_started"] += 1
        _update_scheduler_state_metrics(metrics, event)
    elif event_type == "runtime_validation_planned":
        if event.get("mode"):
            metrics["runtime_validation_modes"][str(event["mode"])] += 1
        if event.get("command_source"):
            metrics["runtime_command_sources"][str(event["command_source"])] += 1
        if event.get("selected_target"):
            metrics["planner_selected_targets"][str(event["selected_target"])] += 1
        if event.get("planner_version"):
            metrics["planner_versions"][str(event["planner_version"])] += 1
        for item in event.get("rejected_candidates") or []:
            if isinstance(item, dict) and item.get("reason"):
                metrics["planner_rejected_reasons"][str(item["reason"])] += 1
    elif event_type == "scheduler_state_transition":
        node_id = str(event.get("node_id") or "")
        to_state = str(event.get("to_state") or "")
        if node_id and to_state:
            metrics["scheduler_node_states"][f"{node_id}:{to_state}"] += 1
    elif event_type == "scheduler_stage_parallel":
        metrics["scheduler_stage_parallel"] += 1
        metrics["parallel_stage_names"].update(str(stage) for stage in event.get("stages") or [])
    elif event_type == "scheduler_passed":
        metrics["scheduler_passed"] += 1
        _update_scheduler_state_metrics(metrics, event)
    elif event_type == "scheduler_failed":
        metrics["scheduler_failed"] += 1
        if event.get("failed_node_id"):
            metrics["scheduler_failed_nodes"][str(event["failed_node_id"])] += 1
        _update_scheduler_state_metrics(metrics, event)
    elif event_type == "scheduler_node_skipped":
        if event.get("node_id"):
            metrics["scheduler_skipped_nodes"][str(event["node_id"])] += 1
    elif event_type == "scheduler_node_finished" and event.get("node_id") and event.get("status") != "prepared":
        metrics["scheduler_executed_nodes"][str(event["node_id"])] += 1
        if event.get("step_id"):
            metrics["scheduler_node_finished"] += 1
        if event.get("runtime_validation_mode"):
            metrics["runtime_validation_modes"][str(event["runtime_validation_mode"])] += 1
        if event.get("command_source"):
            metrics["runtime_command_sources"][str(event["command_source"])] += 1
    elif event_type == "runtime_fallback_attempted":
        metrics["runtime_fallback_attempted"] += 1
    elif event_type == "runtime_fallback_succeeded":
        metrics["runtime_fallback_succeeded"] += 1
    elif event_type == "preflight_check":
        metrics["preflight_checks"] += 1
    elif event_type == "preflight_blocked":
        metrics["preflight_blocked"] += 1
        blocker = str(event.get("blocking_reason") or "unknown")
        metrics["preflight_blockers"][blocker] += 1
    elif event_type == "verifier_checked":
        metrics["verifier_checked"] += 1
    elif event_type == "verifier_rejected":
        metrics["verifier_rejected"] += 1
    elif event_type == "critic_checked":
        metrics["critic_checked"] += 1
    elif event_type == "critic_rejected":
        metrics["critic_rejected"] += 1
    elif event_type == "node_repair_package_injected":
        metrics["node_repair_package_injected"] += 1
    elif event_type == "node_retry_escalated":
        metrics["node_retry_escalated"] += 1
    elif event_type == "checkpoint_created":
        metrics["checkpoint_created"] += 1
    elif event_type == "rollback_applied":
        metrics["rollback_applied"] += 1
    elif event_type == "rollback_skipped":
        metrics["rollback_skipped"] += 1
    elif event_type == "parallel_build_planned":
        metrics["parallel_build_planned"] += 1
    elif event_type == "parallel_build_branch_started":
        metrics["parallel_build_branch_started"] += 1
    elif event_type == "parallel_build_branch_skipped":
        metrics["parallel_build_branch_skipped"] += 1
    elif event_type == "parallel_build_branch_finished":
        metrics["parallel_build_branch_finished"] += 1
    elif event_type == "threaded_base_materialized":
        if event.get("materialized"):
            metrics["threaded_base_materialized"] += 1
        else:
            metrics["threaded_base_fast_path"] += 1
    elif event_type == "parallel_merge_started":
        metrics["parallel_merge_started"] += 1
    elif event_type == "parallel_merge_succeeded":
        metrics["parallel_merge_succeeded"] += 1
    elif event_type == "parallel_repair_branch_started":
        metrics["parallel_repair_branch_started"] += 1
    elif event_type == "parallel_repair_branch_won":
        metrics["parallel_repair_branch_won"] += 1
    elif event_type == "parallel_branch_rollback_applied":
        metrics["parallel_branch_rollback_applied"] += 1
    elif event_type == "scheduler_parallel_repair":
        metrics["scheduler_parallel_repair"] += 1
    elif event_type == "topology_build_plan_created":
        metrics["topology_build_plan_created"] += 1
    elif event_type == "scheduler_node_ready" and event.get("step_id"):
        metrics["scheduler_node_ready"] += 1
    elif event_type == "scheduler_node_waiting_resource":
        metrics["scheduler_node_waiting_resource"] += 1
    elif event_type == "scheduler_node_finished" and event.get("step_id"):
        metrics["scheduler_node_finished"] += 1
    elif event_type == "build_cache_planned":
        metrics["build_cache_planned"] += 1
        if event.get("enabled"):
            metrics["build_cache_enabled_count"] += 1
            if event.get("reuse_existing"):
                metrics["build_cache_reuse_existing_count"] += 1
        if event.get("namespace"):
            metrics["build_cache_namespaces"][str(event["namespace"])] += 1
        if event.get("cache_id_scope"):
            metrics["build_cache_id_scopes"][str(event["cache_id_scope"])] += 1
        for key in ("apt_cache_id", "apt_lists_cache_id", "pip_cache_id"):
            value = str(event.get(key) or "")
            if value:
                metrics["build_cache_ids"][value] += 1


def _update_failure_metrics(metrics: dict[str, Any], event: dict[str, Any]) -> None:
    node_id = str(event.get("node_id") or "")
    attribution = event.get("attribution") or {}
    if not node_id and isinstance(attribution, dict):
        node_id = str(attribution.get("topology_node_id") or "")
    if node_id:
        metrics["failure_nodes"][node_id] += 1
    if event.get("error_type"):
        metrics["error_types"][str(event["error_type"])] += 1
    if event.get("stage"):
        metrics["failure_stages"][str(event["stage"])] += 1


def _update_guardrail_metrics(metrics: dict[str, Any], event: dict[str, Any]) -> None:
    metrics["guardrail_checks"] += 1
    if event.get("valid", True):
        return
    metrics["guardrail_rejection_events"] += 1
    violations = event.get("violations") or ["unknown violation"]
    for violation in violations:
        metrics["guardrail_violation_types"][str(violation)] += 1


def _update_final_metrics(metrics: dict[str, Any], event: dict[str, Any]) -> None:
    metrics["final_results"] += 1
    if event.get("success"):
        metrics["successes"] += 1
    else:
        metrics["failures"] += 1
    if event.get("prefill_validated_first"):
        metrics["prefill_validated_first"] += 1
    if event.get("threaded_build_used"):
        metrics["threaded_build_used_count"] += 1
    metrics["parallel_build_branches_total"] += _as_int(event.get("parallel_build_branches"))
    if event.get("parallel_repair_used"):
        metrics["parallel_repair_used_count"] += 1
    metrics["repair_branches_tried_total"] += _as_int(event.get("repair_branches_tried"))
    node_failures = event.get("node_failures") or {}
    if isinstance(node_failures, dict):
        for node_id in node_failures:
            metrics["final_repair_nodes"][str(node_id)] += 1
    if event.get("final_image"):
        metrics["final_images"][str(event["final_image"])] += 1


def _update_image_contract_metrics(metrics: dict[str, Any], event: dict[str, Any]) -> None:
    final_image = str(event.get("final_image") or "")
    if final_image:
        metrics["final_images"][final_image] += 1
    internal_images = event.get("internal_branch_images") or event.get("cleaned_internal_branch_images") or {}
    if isinstance(internal_images, dict) and internal_images:
        metrics["internal_branch_image_events"] += 1
    public_images = event.get("public_images") or ([final_image] if final_image else [])
    if final_image and public_images != [final_image]:
        metrics["public_image_contract_violations"] += 1


def _update_topology_dag_metrics(metrics: dict[str, Any], event: dict[str, Any]) -> None:
    strategy = str(event.get("build_scheduler_strategy") or "")
    if strategy:
        metrics["build_scheduler_strategies"][strategy] += 1
    metrics["ready_queue_max_size"] = max(metrics["ready_queue_max_size"], _as_int(event.get("ready_queue_max_size")))
    metrics["resource_wait_seconds"] = max(metrics["resource_wait_seconds"], _as_float(event.get("resource_wait_seconds")))
    metrics["critical_path_seconds"] = max(metrics["critical_path_seconds"], _as_float(event.get("critical_path_seconds")))
    metrics["reused_internal_images"] = max(metrics["reused_internal_images"], _as_int(event.get("reused_internal_images")))
    metrics["internal_image_reused_count"] = max(
        metrics["internal_image_reused_count"],
        _as_int(event.get("internal_image_reused_count") or event.get("reused_internal_images")),
    )
    metrics["rebuild_saved_nodes"] = max(metrics["rebuild_saved_nodes"], _as_int(event.get("rebuild_saved_nodes")))
    if event.get("event_type") == "topology_node_repaired":
        metrics["topology_node_repaired"] += 1
        repair_scope = str(event.get("repair_scope") or "")
        if repair_scope:
            metrics["repair_scopes"][repair_scope] += 1
    node_wait = event.get("node_wait_seconds") or {}
    if isinstance(node_wait, dict):
        for node_id, seconds in node_wait.items():
            metrics["node_wait_seconds"][str(node_id)] += _as_float(seconds)
    node_build = event.get("node_build_seconds") or {}
    if isinstance(node_build, dict):
        for node_id, seconds in node_build.items():
            metrics["node_build_seconds"][str(node_id)] += _as_float(seconds)
    cache_hits = event.get("cache_hit_by_node") or {}
    if isinstance(cache_hits, dict):
        for node_id, count in cache_hits.items():
            key = str(node_id)
            metrics["cache_hit_by_node"][key] = max(metrics["cache_hit_by_node"][key], _as_int(count))
    cache_totals = event.get("cache_total_by_node") or {}
    if isinstance(cache_totals, dict):
        for node_id, count in cache_totals.items():
            key = str(node_id)
            metrics["cache_total_by_node"][key] = max(metrics["cache_total_by_node"][key], _as_int(count))
    metrics["cache_hit_rate_by_node"] = _cache_hit_rate_by_node(
        metrics["cache_hit_by_node"],
        metrics["cache_total_by_node"],
    )


def _finalize_group_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    runtime_validation_planned = sum(metrics["runtime_validation_modes"].values())
    runtime_validation_executed = metrics["scheduler_executed_nodes"].get("runtime_validation", 0)
    runtime_validation_skipped = metrics["scheduler_skipped_nodes"].get("runtime_validation", 0)
    return {
        "projects": sorted(metrics["projects"]),
        "total_events": metrics["total_events"],
        "event_types": _counter_to_dict(metrics["event_types"]),
        "memory_hits_observed": metrics["memory_hits_observed"],
        "memory_retrieval_events": metrics["memory_retrieval_events"],
        "retrieved_matches": metrics["retrieved_matches"],
        "memory_precise_matches": metrics["memory_precise_matches"],
        "topology_memory_matches": metrics["topology_memory_matches"],
        "memory_prefill_candidates": metrics["memory_prefill_candidates"],
        "memory_prefill_applied_packages": _counter_to_dict(metrics["memory_prefill_applied_packages"]),
        "memory_evidence_items": metrics["memory_evidence_items"],
        "memory_prefill_apt_packages": _counter_to_dict(metrics["memory_prefill_apt_packages"]),
        "memory_prefill_python_packages": _counter_to_dict(metrics["memory_prefill_python_packages"]),
        "pattern_evidence_items": metrics["pattern_evidence_items"],
        "pattern_memory_matches": metrics["pattern_memory_matches"],
        "pattern_cross_project_matches": metrics["pattern_cross_project_matches"],
        "pattern_low_support_matches": metrics["pattern_low_support_matches"],
        "pattern_negative_evidence_matches": metrics["pattern_negative_evidence_matches"],
        "runtime_validation_memory_matches": metrics["runtime_validation_memory_matches"],
        "runtime_validation_memory_prefill_candidates": metrics["runtime_validation_memory_prefill_candidates"],
        "pattern_prefill_apt_packages": _counter_to_dict(metrics["pattern_prefill_apt_packages"]),
        "pattern_prefill_python_packages": _counter_to_dict(metrics["pattern_prefill_python_packages"]),
        "pattern_source_projects": _counter_to_dict(metrics["pattern_source_projects"]),
        "llm_loop_started": metrics["llm_loop_started"],
        "failure_nodes": _counter_to_dict(metrics["failure_nodes"]),
        "error_types": _counter_to_dict(metrics["error_types"]),
        "failure_stages": _counter_to_dict(metrics["failure_stages"]),
        "guardrail_checks": metrics["guardrail_checks"],
        "guardrail_rejection_events": metrics["guardrail_rejection_events"],
        "guardrail_violation_types": _counter_to_dict(metrics["guardrail_violation_types"]),
        "max_cumulative_guardrail_violations": metrics["max_cumulative_guardrail_violations"],
        "prevalidation_results": _counter_to_dict(metrics["prevalidation_results"]),
        "prevalidation_command_failures": metrics["prevalidation_command_failures"],
        "action_events": metrics["action_events"],
        "failed_action_events": metrics["failed_action_events"],
        "final_results": metrics["final_results"],
        "successes": metrics["successes"],
        "failures": metrics["failures"],
        "prefill_validated_first": metrics["prefill_validated_first"],
        "final_repair_nodes": _counter_to_dict(metrics["final_repair_nodes"]),
        "scheduler_started": metrics["scheduler_started"],
        "scheduler_passed": metrics["scheduler_passed"],
        "scheduler_failed": metrics["scheduler_failed"],
        "scheduler_executed_nodes": _counter_to_dict(metrics["scheduler_executed_nodes"]),
        "scheduler_failed_nodes": _counter_to_dict(metrics["scheduler_failed_nodes"]),
        "scheduler_skipped_nodes": _counter_to_dict(metrics["scheduler_skipped_nodes"]),
        "scheduler_node_states": _counter_to_dict(metrics["scheduler_node_states"]),
        "runtime_validation_modes": _counter_to_dict(metrics["runtime_validation_modes"]),
        "runtime_command_sources": _counter_to_dict(metrics["runtime_command_sources"]),
        "runtime_validation_planned": runtime_validation_planned,
        "runtime_validation_executed": runtime_validation_executed,
        "runtime_validation_skipped": runtime_validation_skipped,
        "scheduler_stage_parallel": metrics["scheduler_stage_parallel"],
        "parallel_stage_names": _counter_to_dict(metrics["parallel_stage_names"]),
        "runtime_fallback_attempted": metrics["runtime_fallback_attempted"],
        "runtime_fallback_succeeded": metrics["runtime_fallback_succeeded"],
        "preflight_checks": metrics["preflight_checks"],
        "preflight_blocked": metrics["preflight_blocked"],
        "preflight_blockers": _counter_to_dict(metrics["preflight_blockers"]),
        "avg_topology_planning_seconds": (
            round(metrics["topology_planning_seconds_total"] / metrics["topology_planning_samples"], 4)
            if metrics["topology_planning_samples"]
            else 0.0
        ),
        "avg_python_files_scanned": (
            round(metrics["python_files_scanned_total"] / metrics["topology_planning_samples"], 2)
            if metrics["topology_planning_samples"]
            else 0.0
        ),
        "python_scan_budgets": _counter_to_dict(metrics["python_scan_budgets"]),
        "planner_eval_summary": {
            "selected_targets": _counter_to_dict(metrics["planner_selected_targets"]),
            "rejected_reasons": _counter_to_dict(metrics["planner_rejected_reasons"]),
            "planner_versions": _counter_to_dict(metrics["planner_versions"]),
        },
        "verifier_checked": metrics["verifier_checked"],
        "verifier_rejected": metrics["verifier_rejected"],
        "critic_checked": metrics["critic_checked"],
        "critic_rejected": metrics["critic_rejected"],
        "node_repair_package_injected": metrics["node_repair_package_injected"],
        "node_retry_escalated": metrics["node_retry_escalated"],
        "checkpoint_created": metrics["checkpoint_created"],
        "rollback_applied": metrics["rollback_applied"],
        "rollback_skipped": metrics["rollback_skipped"],
        "threaded_build_used_count": metrics["threaded_build_used_count"],
        "threaded_base_materialized": metrics["threaded_base_materialized"],
        "threaded_base_fast_path": metrics["threaded_base_fast_path"],
        "parallel_build_planned": metrics["parallel_build_planned"],
        "parallel_build_branch_started": metrics["parallel_build_branch_started"],
        "parallel_build_branch_skipped": metrics["parallel_build_branch_skipped"],
        "parallel_build_branch_finished": metrics["parallel_build_branch_finished"],
        "parallel_merge_started": metrics["parallel_merge_started"],
        "parallel_merge_succeeded": metrics["parallel_merge_succeeded"],
        "parallel_repair_branch_started": metrics["parallel_repair_branch_started"],
        "parallel_repair_branch_won": metrics["parallel_repair_branch_won"],
        "parallel_branch_rollback_applied": metrics["parallel_branch_rollback_applied"],
        "avg_parallel_build_branches": (
            round(metrics["parallel_build_branches_total"] / metrics["final_results"], 2)
            if metrics["final_results"]
            else 0.0
        ),
        "parallel_repair_used_count": metrics["parallel_repair_used_count"],
        "avg_repair_branches_tried": (
            round(metrics["repair_branches_tried_total"] / metrics["final_results"], 2) if metrics["final_results"] else 0.0
        ),
        "scheduler_parallel_repair": metrics["scheduler_parallel_repair"],
        "build_cache_planned": metrics["build_cache_planned"],
        "build_cache_enabled_count": metrics["build_cache_enabled_count"],
        "build_cache_reuse_existing_count": metrics["build_cache_reuse_existing_count"],
        "build_cache_namespaces": _counter_to_dict(metrics["build_cache_namespaces"]),
        "build_cache_id_scopes": _counter_to_dict(metrics["build_cache_id_scopes"]),
        "build_cache_ids": _counter_to_dict(metrics["build_cache_ids"]),
        "final_images": _counter_to_dict(metrics["final_images"]),
        "internal_branch_image_events": metrics["internal_branch_image_events"],
        "internal_branch_images_cleaned": metrics["internal_branch_images_cleaned"],
        "public_image_contract_violations": metrics["public_image_contract_violations"],
        "topology_build_plan_created": metrics["topology_build_plan_created"],
        "scheduler_node_ready": metrics["scheduler_node_ready"],
        "scheduler_node_waiting_resource": metrics["scheduler_node_waiting_resource"],
        "scheduler_node_finished": metrics["scheduler_node_finished"],
        "ready_queue_max_size": metrics["ready_queue_max_size"],
        "resource_wait_seconds": round(metrics["resource_wait_seconds"], 4),
        "critical_path_seconds": round(metrics["critical_path_seconds"], 4),
        "node_wait_seconds": _counter_to_rounded_dict(metrics["node_wait_seconds"]),
        "node_build_seconds": _counter_to_rounded_dict(metrics["node_build_seconds"]),
        "reused_internal_images": metrics["reused_internal_images"],
        "internal_image_reused_count": metrics["internal_image_reused_count"],
        "rebuild_saved_nodes": metrics["rebuild_saved_nodes"],
        "cache_hit_by_node": _counter_to_dict(metrics["cache_hit_by_node"]),
        "cache_total_by_node": _counter_to_dict(metrics["cache_total_by_node"]),
        "cache_hit_rate_by_node": dict(sorted(metrics["cache_hit_rate_by_node"].items())),
        "topology_node_repaired": metrics["topology_node_repaired"],
        "repair_scopes": _counter_to_dict(metrics["repair_scopes"]),
        "build_scheduler_strategies": _counter_to_dict(metrics["build_scheduler_strategies"]),
    }


def _update_scheduler_state_metrics(metrics: dict[str, Any], event: dict[str, Any]) -> None:
    node_states = event.get("node_states") or {}
    if not isinstance(node_states, dict):
        return
    for node_id, state in node_states.items():
        metrics["scheduler_node_states"][f"{node_id}:{state}"] += 1


def _counter_to_dict(counter: Counter) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _counter_to_rounded_dict(counter: Counter) -> dict[str, float]:
    return {str(key): round(float(value), 4) for key, value in sorted(counter.items())}


def _cache_hit_rate_by_node(cache_hits: Counter, cache_totals: Counter) -> dict[str, float]:
    return {
        str(node_id): round(float(cache_hits[node_id]) / float(total), 4)
        for node_id, total in sorted(cache_totals.items())
        if total > 0
    }


def _format_counter(counter: dict[str, int]) -> str:
    return ", ".join(f"{_single_line(key)}={value}" for key, value in counter.items())


def _format_float_counter(counter: dict[str, float]) -> str:
    return ", ".join(f"{_single_line(key)}={float(value):.2f}" for key, value in counter.items())


def _format_memory_prefill(metrics: dict[str, Any]) -> str:
    apt = _format_counter(metrics.get("memory_prefill_apt_packages", {}))
    python = _format_counter(metrics.get("memory_prefill_python_packages", {}))
    parts = []
    if apt:
        parts.append(f"apt: {apt}")
    if python:
        parts.append(f"python: {python}")
    return "; ".join(parts) or "-"


def _format_pattern_prefill(metrics: dict[str, Any]) -> str:
    apt = _format_counter(metrics.get("pattern_prefill_apt_packages", {}))
    python = _format_counter(metrics.get("pattern_prefill_python_packages", {}))
    parts = []
    if apt:
        parts.append(f"apt: {apt}")
    if python:
        parts.append(f"python: {python}")
    return "; ".join(parts) or "-"


def _single_line(text: str) -> str:
    return " ".join(str(text).split())


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, int | float):
        return float(value)
    return 0.0
