#!/usr/bin/env python3

"""Run sequential repair experiments that grow cross-project pattern memory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from rich.console import Console

from topomen.core.paths import (
    EXPERIMENT_MEMORY_DIR,
    EXPERIMENT_RUNS_DIR,
    EXPERIMENT_SUMMARIES_DIR,
    EXPERIMENT_WORKSPACES_DIR,
)
from topomen.experiments.repair_experiment import (
    derive_trace_path,
    load_experiment_runs,
    load_project_manifest,
    make_stable_project_key,
    prepare_topology_prefill_workspace,
    run_single_project,
)
from topomen.experiments.trace_analysis import load_trace_events
from topomen.memory import (
    PatternMemory,
    RepairMemory,
    rebuild_pattern_memory_from_repair_records,
)


def configure_if_first_time():
    return None


console = Console(highlight=False)


class CumulativeRunResult(BaseModel):
    group: str
    project: str
    project_index: int = Field(ge=0)
    success: bool
    iterations: int = Field(default=0, ge=0)
    llm_loop_started: int = Field(default=0, ge=0)
    prefill_validated_first: bool = False
    docker_build_count: int = Field(default=0, ge=0)
    docker_run_count: int = Field(default=0, ge=0)
    total_seconds: float = Field(default=0.0, ge=0.0)
    patterns_used: int = Field(default=0, ge=0)
    patterns_learned: int = Field(default=0, ge=0)


def run_cumulative_batch(
    *,
    manifest: Path,
    base_config: str = "deployment_base.yaml",
    groups: str = "pattern-memory-cumulative",
    runs_path: Path = EXPERIMENT_RUNS_DIR / "cumulative_experiment_agent_runs.jsonl",
    cumulative_runs_path: Path = EXPERIMENT_RUNS_DIR / "cumulative_experiment_runs.jsonl",
    summary_path: Path = EXPERIMENT_SUMMARIES_DIR / "cumulative_experiment_summary.json",
    repair_memory_path: Path = EXPERIMENT_MEMORY_DIR / "cumulative_repair_memory.jsonl",
    pattern_memory_path: Path = EXPERIMENT_MEMORY_DIR / "pattern_memory.jsonl",
    prefill_workspace_root: Path = EXPERIMENT_WORKSPACES_DIR / "cumulative_topology",
) -> dict[str, Any]:
    configure_if_first_time()
    projects = load_project_manifest(manifest)
    selected_groups = [group.strip() for group in groups.split(",") if group.strip()]
    trace_path = derive_trace_path(runs_path)
    RepairMemory(repair_memory_path)
    PatternMemory(pattern_memory_path)
    _rebuild_patterns(repair_memory_path, pattern_memory_path)

    cumulative_results: list[CumulativeRunResult] = []
    for index, project in enumerate(projects, start=1):
        for group in selected_groups:
            if group not in _supported_groups():
                msg = f"Unsupported cumulative group: {group}"
                raise ValueError(msg)
            project_name = project.get("project_name") or Path(project["task"]).name or project["task"]
            task = project["task"]
            effective_task = (
                prepare_topology_prefill_workspace(task, project_name, prefill_workspace_root)
                if _uses_topology_workspace(group)
                else task
            )
            before_runs = len(load_experiment_runs(runs_path))
            before_trace = _load_trace_count(trace_path)
            before_pattern_count = len(PatternMemory(pattern_memory_path, create=False).load_records())
            config_specs = _group_config_specs(
                group=group,
                base_config=base_config,
                runs_path=runs_path,
                trace_path=trace_path,
                repair_memory_path=repair_memory_path,
                pattern_memory_path=pattern_memory_path,
                project_key=make_stable_project_key(project_name=project_name, task=project["task"]),
            )
            console.print(f"[bold green]Running cumulative[/bold green] {group}: {project_name} ({index}/{len(projects)})")
            run_single_project(config_specs, task=effective_task, project_name=project_name, group=group)

            run_result = _last_new_run(runs_path, before_runs, project_name=project_name, group=group)
            trace_events = _new_trace_events(trace_path, before_trace)
            after_patterns = _rebuild_patterns(repair_memory_path, pattern_memory_path)
            cumulative_result = CumulativeRunResult(
                group=group,
                project=project_name,
                project_index=index,
                success=run_result.success if run_result else False,
                iterations=run_result.iterations if run_result else 0,
                llm_loop_started=sum(1 for event in trace_events if event.get("event_type") == "llm_loop_started"),
                prefill_validated_first=run_result.prefill_validated_first if run_result else False,
                docker_build_count=run_result.docker_build_count if run_result else 0,
                docker_run_count=run_result.docker_run_count if run_result else 0,
                total_seconds=run_result.total_seconds if run_result else 0.0,
                patterns_used=sum(len(event.get("pattern_memory_matches") or []) for event in trace_events),
                patterns_learned=max(0, len(after_patterns) - before_pattern_count),
            )
            cumulative_results.append(cumulative_result)
            _append_cumulative_result(cumulative_runs_path, cumulative_result)

    summary = summarize_cumulative_runs(cumulative_results)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def summarize_cumulative_runs(results: list[CumulativeRunResult]) -> dict[str, Any]:
    grouped: dict[str, list[CumulativeRunResult]] = {}
    for result in results:
        grouped.setdefault(result.group, []).append(result)
    summary: dict[str, Any] = {}
    for group, items in grouped.items():
        summary[group] = {
            "runs": len(items),
            "successes": sum(1 for item in items if item.success),
            "success_rate": sum(1 for item in items if item.success) / len(items) if items else 0.0,
            "avg_iterations": sum(item.iterations for item in items) / len(items) if items else 0.0,
            "avg_llm_loop_started": sum(item.llm_loop_started for item in items) / len(items) if items else 0.0,
            "avg_total_seconds": sum(item.total_seconds for item in items) / len(items) if items else 0.0,
            "avg_docker_build_count": sum(item.docker_build_count for item in items) / len(items) if items else 0.0,
            "avg_docker_run_count": sum(item.docker_run_count for item in items) / len(items) if items else 0.0,
            "prefill_validated_first_count": sum(1 for item in items if item.prefill_validated_first),
            "patterns_used": sum(item.patterns_used for item in items),
            "patterns_learned": sum(item.patterns_learned for item in items),
        }
    return summary


def _group_config_specs(
    *,
    group: str,
    base_config: str,
    runs_path: Path,
    trace_path: Path,
    repair_memory_path: Path,
    pattern_memory_path: Path,
    project_key: str,
) -> list[str]:
    common = [
        f"agent.experiment_runs_path={json.dumps(str(runs_path))}",
        f"agent.topology_trace_path={json.dumps(str(trace_path))}",
        f"agent.repair_memory_path={json.dumps(str(repair_memory_path))}",
        f"agent.retrieval_memory_path={json.dumps(str(repair_memory_path))}",
        "agent.repair_memory_enabled=true",
        "agent.record_successful_repairs=true",
        "agent.track_command_timing=true",
        f"agent.project_key={json.dumps(project_key)}",
        "agent.eval_mode=cumulative_learning",
        "agent.allow_same_project_memory=false",
    ]
    topology_pattern = [
        f"agent.pattern_memory_path={json.dumps(str(pattern_memory_path))}",
        "agent.pattern_memory_enabled=true",
        "agent.topology_memory_evidence_enabled=true",
    ]
    if group == "repair-memory-only":
        return [base_config, "memory_rag.yaml", *common, "agent.experiment_group=repair-memory-only"]
    if group == "topology-prefill-guarded":
        return [
            base_config,
            "topology_guarded.yaml",
            *common,
            *topology_pattern,
            "agent.experiment_group=topology-prefill-guarded",
        ]
    if group == "topology-scheduler-guarded":
        return [
            base_config,
            "topology_scheduler_guarded.yaml",
            *common,
            *topology_pattern,
            "agent.experiment_group=topology-scheduler-guarded",
        ]
    if group == "pattern-memory-cumulative":
        return [
            base_config,
            "topology_scheduler_guarded.yaml",
            *common,
            *topology_pattern,
            "agent.experiment_group=pattern-memory-cumulative",
        ]
    msg = f"Unsupported cumulative group: {group}"
    raise ValueError(msg)


def _supported_groups() -> set[str]:
    return {
        "repair-memory-only",
        "topology-prefill-guarded",
        "topology-scheduler-guarded",
        "pattern-memory-cumulative",
    }


def _uses_topology_workspace(group: str) -> bool:
    return group in {"topology-prefill-guarded", "topology-scheduler-guarded", "pattern-memory-cumulative"}


def _rebuild_patterns(repair_memory_path: Path, pattern_memory_path: Path):
    records = RepairMemory(repair_memory_path, create=False).load_records()
    return rebuild_pattern_memory_from_repair_records(records, pattern_memory_path)


def _append_cumulative_result(path: Path, result: CumulativeRunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(result.model_dump_json())
        handle.write("\n")


def _last_new_run(path: Path, before_count: int, *, project_name: str, group: str):
    runs = load_experiment_runs(path)
    new_runs = runs[before_count:]
    for run in reversed(new_runs):
        if run.project == project_name and run.group == group:
            return run
    return new_runs[-1] if new_runs else None


def _load_trace_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(load_trace_events(path))


def _new_trace_events(path: Path, before_count: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return load_trace_events(path)[before_count:]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run sequential repair experiments with cross-project pattern memory.")
    parser.add_argument("manifest", type=Path, help="Project manifest (.txt or .json).")
    parser.add_argument("--base-config", default="deployment_base.yaml", help="Base TopoMen deployment config.")
    parser.add_argument("--groups", default="pattern-memory-cumulative", help="Comma-separated cumulative groups.")
    parser.add_argument(
        "--runs-path",
        type=Path,
        default=EXPERIMENT_RUNS_DIR / "cumulative_experiment_agent_runs.jsonl",
        help="Underlying agent run JSONL.",
    )
    parser.add_argument(
        "--cumulative-runs-path",
        type=Path,
        default=EXPERIMENT_RUNS_DIR / "cumulative_experiment_runs.jsonl",
        help="Sequential learning JSONL with per-project pattern metrics.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=EXPERIMENT_SUMMARIES_DIR / "cumulative_experiment_summary.json",
        help="Aggregated cumulative summary JSON.",
    )
    parser.add_argument("--repair-memory-path", type=Path, default=EXPERIMENT_MEMORY_DIR / "cumulative_repair_memory.jsonl")
    parser.add_argument("--pattern-memory-path", type=Path, default=EXPERIMENT_MEMORY_DIR / "pattern_memory.jsonl")
    parser.add_argument(
        "--prefill-workspace-root",
        type=Path,
        default=EXPERIMENT_WORKSPACES_DIR / "cumulative_topology",
        help="Workspace root for copied topology projects.",
    )
    return parser


def main_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_cumulative_batch(
        manifest=args.manifest,
        base_config=args.base_config,
        groups=args.groups,
        runs_path=args.runs_path,
        cumulative_runs_path=args.cumulative_runs_path,
        summary_path=args.summary_path,
        repair_memory_path=args.repair_memory_path,
        pattern_memory_path=args.pattern_memory_path,
        prefill_workspace_root=args.prefill_workspace_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
