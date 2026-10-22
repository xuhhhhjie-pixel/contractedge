#!/usr/bin/env python3

"""Run zero-shot vs RAG Docker repair experiments over a project list."""

import argparse
import hashlib
import json
import re
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rich.console import Console

from topomen.collaboration import get_agent
from topomen.core.config import get_config_from_spec
from topomen.core.paths import (
    EXPERIMENT_RUNS_DIR,
    EXPERIMENT_SUMMARIES_DIR,
    EXPERIMENT_TRACES_DIR,
    EXPERIMENT_WORKSPACES_DIR,
)
from topomen.core.serialize import recursive_merge
from topomen.environment import get_environment
from topomen.llm import get_model
from topomen.memory import ExperimentRunResult, summarize_experiment_runs


def configure_if_first_time():
    return None

console = Console(highlight=False)


def load_project_manifest(path: Path) -> list[dict[str, str]]:
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item if isinstance(item, dict) else {"task": str(item)} for item in data]
        msg = f"Expected a JSON list in {path}"
        raise ValueError(msg)

    projects: list[dict[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        projects.append({"task": line})
    return projects


def load_experiment_runs(path: Path) -> list[ExperimentRunResult]:
    if not path.exists():
        return []
    runs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        runs.append(ExperimentRunResult.model_validate_json(line))
    return runs


def build_runtime_config(config_specs: list[str], extra_overrides: dict[str, Any]) -> dict[str, Any]:
    configs = [get_config_from_spec(spec) for spec in config_specs]
    configs.append(extra_overrides)
    return recursive_merge(*configs)


def make_docker_image_name(*, group: str, project_name: str, task: str) -> str:
    safe_group = _docker_image_component(group)
    safe_project = _docker_image_component(project_name, max_length=48)
    digest = hashlib.sha1(f"{group}:{project_name}:{task}".encode()).hexdigest()[:10]
    return f"topomen-{safe_group}-{safe_project}-{digest}"


def make_stable_project_key(*, project_name: str, task: str) -> str:
    safe_project = _docker_image_component(project_name, max_length=48)
    digest = hashlib.sha1(f"{project_name}:{task}".encode()).hexdigest()[:10]
    return f"{safe_project}-{digest}"


def _docker_image_component(value: str, *, max_length: int | None = None) -> str:
    safe = re.sub(r"[^a-z0-9.-]+", "-", value.lower()).strip(".-")
    if max_length is not None:
        safe = safe[:max_length].strip(".-")
    return safe or "project"


def derive_trace_path(runs_path: Path) -> Path:
    stem = runs_path.stem
    if stem.endswith("_runs"):
        stem = stem[: -len("_runs")]
    if runs_path.parent == EXPERIMENT_RUNS_DIR:
        return EXPERIMENT_TRACES_DIR / f"{stem}_trace.jsonl"
    return runs_path.with_name(f"{stem}_trace.jsonl")


def run_single_project(
    config_specs: list[str],
    *,
    task: str,
    project_name: str,
    group: str = "run",
) -> dict[str, Any]:
    config = build_runtime_config(
        config_specs,
        {
            "run": {"task": task},
            "agent": {"project_name": project_name},
        },
    )
    model = get_model(config=config.get("model", {}))
    env = get_environment(config.get("environment", {}), default_type="local")
    agent = get_agent(model, env, config.get("agent", {}), default_type="repair_memory")
    image_name = make_docker_image_name(group=group, project_name=project_name, task=task)
    return agent.run(task, image_name=image_name)


def prepare_topology_prefill_workspace(task: str, project_name: str, workspace_root: Path) -> str:
    source = Path(task)
    if not source.exists() or not source.is_dir():
        return task
    safe_project = re.sub(r"[^a-z0-9_.-]+", "-", project_name.lower()).strip("-")[:48] or "project"
    digest = hashlib.sha1(str(source).encode()).hexdigest()[:8]
    target = workspace_root / f"{safe_project}-{digest}-{uuid.uuid4().hex[:8]}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".ruff_cache"),
    )
    return str(target)


def _requires_topology_workspace(group: str) -> bool:
    return group.startswith(
        (
            "topology-prefill",
            "topology-scheduler",
            "topology-multistage",
            "topology-threaded-merge",
            "scheduler-no-topology",
        )
    )


def _run_project_group(
    *,
    group: str,
    project: dict[str, str],
    group_to_configs: dict[str, list[str]],
    prefill_workspace_root: Path,
) -> dict[str, Any]:
    task = project["task"]
    project_name = project.get("project_name") or Path(task).name or task
    if _requires_topology_workspace(group):
        task = prepare_topology_prefill_workspace(task, project_name, prefill_workspace_root)
    project_key = make_stable_project_key(project_name=project_name, task=project["task"])
    config_specs = [
        *group_to_configs[group],
        f"agent.project_key={json.dumps(project_key)}",
        "agent.eval_mode=benchmark_ab",
    ]
    console.print(f"[bold green]Running[/bold green] {group}: {project_name}")
    return run_single_project(config_specs, task=task, project_name=project_name, group=group)


def run_batch(
    *,
    manifest: Path,
    base_config: str = "deployment_base.yaml",
    zero_shot_config: str = "memory_zero_shot.yaml",
    rag_config: str = "memory_rag.yaml",
    groups: str = "zero-shot,rag",
    runs_path: Path = EXPERIMENT_RUNS_DIR / "experiment_runs.jsonl",
    summary_path: Path = EXPERIMENT_SUMMARIES_DIR / "experiment_summary.json",
    repair_memory_path: Path | None = None,
    retrieval_memory_path: Path | None = None,
    pattern_memory_path: Path | None = None,
    runtime_validation_memory_path: Path | None = None,
    max_workers: int = 1,
    prefill_workspace_root: Path = EXPERIMENT_WORKSPACES_DIR / "topology_prefill_runs",
) -> dict[str, dict[str, float | int | dict[str, int]]]:
    configure_if_first_time()
    projects = load_project_manifest(manifest)
    selected_groups = [group.strip() for group in groups.split(",") if group.strip()]
    common_agent_overrides = [
        f"agent.experiment_runs_path={json.dumps(str(runs_path))}",
        f"agent.topology_trace_path={json.dumps(str(derive_trace_path(runs_path)))}",
        "agent.track_command_timing=true",
        "agent.allow_same_project_memory=false",
    ]
    if repair_memory_path is not None:
        common_agent_overrides.append(f"agent.repair_memory_path={json.dumps(str(repair_memory_path))}")
    rag_agent_overrides = [*common_agent_overrides]
    if retrieval_memory_path is not None:
        rag_agent_overrides.append(f"agent.retrieval_memory_path={json.dumps(str(retrieval_memory_path))}")
    topology_agent_overrides = [*rag_agent_overrides]
    topology_prefill_overrides = [
        f"agent.experiment_runs_path={json.dumps(str(runs_path))}",
        f"agent.topology_trace_path={json.dumps(str(derive_trace_path(runs_path)))}",
        "agent.record_successful_repairs=false",
        "agent.track_command_timing=true",
        "agent.allow_same_project_memory=false",
    ]
    if pattern_memory_path is not None:
        topology_prefill_overrides.extend(
            [
                f"agent.pattern_memory_path={json.dumps(str(pattern_memory_path))}",
                "agent.pattern_memory_enabled=true",
            ]
        )
    if runtime_validation_memory_path is not None:
        topology_prefill_overrides.extend(
            [
                f"agent.runtime_validation_memory_path={json.dumps(str(runtime_validation_memory_path))}",
                "agent.runtime_validation_memory_enabled=true",
            ]
        )
    topology_prefill_memory_overrides = [*topology_prefill_overrides, "agent.repair_memory_enabled=true"]
    if retrieval_memory_path is not None:
        topology_prefill_memory_overrides.append(
            f"agent.retrieval_memory_path={json.dumps(str(retrieval_memory_path))}"
        )
    group_to_configs = {
        "zero-shot": [base_config, zero_shot_config, *common_agent_overrides],
        "rag": [base_config, rag_config, *rag_agent_overrides],
        "topology-rag": [base_config, "topology_rag.yaml", *topology_agent_overrides],
        "topology-prefill": [base_config, "topology_prefill.yaml", *topology_prefill_overrides],
        "topology-prefill-guarded": [
            base_config,
            "topology_guarded.yaml",
            *topology_prefill_overrides,
        ],
        "topology-scheduler-guarded": [
            base_config,
            "topology_scheduler_guarded.yaml",
            *topology_prefill_overrides,
        ],
        "scheduler-no-topology-nomem": [
            base_config,
            "topology_scheduler_guarded.yaml",
            *topology_prefill_overrides,
            "agent.experiment_group=scheduler-no-topology-nomem",
            "agent.topology_enabled=false",
            "agent.topology_prefill_dockerfile=false",
            "agent.topology_guard_enabled=false",
            "agent.topology_memory_evidence_enabled=false",
            "agent.repair_memory_enabled=false",
            "agent.topology_repair_scope=full_dockerfile",
        ],
        "scheduler-no-topology-memory": [
            base_config,
            "topology_scheduler_guarded.yaml",
            *topology_prefill_memory_overrides,
            "agent.experiment_group=scheduler-no-topology-memory",
            "agent.topology_enabled=false",
            "agent.topology_prefill_dockerfile=false",
            "agent.topology_guard_enabled=false",
            "agent.topology_memory_evidence_enabled=false",
            "agent.topology_repair_scope=full_dockerfile",
        ],
        "topology-multistage-guarded": [
            base_config,
            "topology_multistage_guarded.yaml",
            *topology_prefill_overrides,
        ],
        "topology-multistage-parallel-repair-guarded": [
            base_config,
            "topology_multistage_parallel_repair_guarded.yaml",
            *topology_prefill_overrides,
        ],
        "topology-threaded-merge-guarded": [
            base_config,
            "topology_threaded_merge_guarded.yaml",
            *topology_prefill_overrides,
        ],
        "topology-threaded-merge-parallel-repair-guarded": [
            base_config,
            "topology_threaded_merge_parallel_repair_guarded.yaml",
            *topology_prefill_overrides,
        ],
        "topology-scheduler-guarded-nomem": [
            base_config,
            "topology_scheduler_guarded.yaml",
            *topology_prefill_overrides,
            "agent.experiment_group=topology-scheduler-guarded-nomem",
            "agent.repair_memory_enabled=false",
            "agent.topology_memory_evidence_enabled=false",
        ],
        "topology-scheduler-guarded-memory": [
            base_config,
            "topology_scheduler_guarded.yaml",
            *topology_prefill_memory_overrides,
            "agent.experiment_group=topology-scheduler-guarded-memory",
            "agent.topology_memory_evidence_enabled=true",
        ],
        "topology-prefill-guarded-nomem": [
            base_config,
            "topology_guarded.yaml",
            *topology_prefill_overrides,
            "agent.experiment_group=topology-prefill-guarded-nomem",
            "agent.repair_memory_enabled=false",
            "agent.topology_memory_evidence_enabled=false",
        ],
        "topology-prefill-guarded-memory": [
            base_config,
            "topology_guarded.yaml",
            *topology_prefill_memory_overrides,
            "agent.experiment_group=topology-prefill-guarded-memory",
            "agent.topology_memory_evidence_enabled=true",
        ],
    }

    if max_workers <= 1:
        for project in projects:
            task = project["task"]
            project_name = project.get("project_name") or Path(task).name or task
            for group in selected_groups:
                if group not in group_to_configs:
                    msg = f"Unsupported group: {group}"
                    raise ValueError(msg)
                effective_task = task
                if _requires_topology_workspace(group):
                    effective_task = prepare_topology_prefill_workspace(task, project_name, prefill_workspace_root)
                project_key = make_stable_project_key(project_name=project_name, task=task)
                config_specs = [
                    *group_to_configs[group],
                    f"agent.project_key={json.dumps(project_key)}",
                    "agent.eval_mode=benchmark_ab",
                ]
                console.print(f"[bold green]Running[/bold green] {group}: {project_name}")
                run_single_project(config_specs, task=effective_task, project_name=project_name, group=group)
    else:
        for group in selected_groups:
            if group not in group_to_configs:
                msg = f"Unsupported group: {group}"
                raise ValueError(msg)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [
                    executor.submit(
                        _run_project_group,
                        group=group,
                        project=project,
                        group_to_configs=group_to_configs,
                        prefill_workspace_root=prefill_workspace_root,
                    )
                    for project in projects
                ]
                for future in as_completed(futures):
                    future.result()

    runs = load_experiment_runs(runs_path)
    summary = summarize_experiment_runs(runs)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run zero-shot vs RAG Docker repair experiments over a project list.")
    parser.add_argument("manifest", type=Path, help="Project manifest (.txt or .json).")
    parser.add_argument("--base-config", default="deployment_base.yaml", help="Base TopoMen deployment config.")
    parser.add_argument("--zero-shot-config", default="memory_zero_shot.yaml", help="Overlay config for zero-shot runs.")
    parser.add_argument("--rag-config", default="memory_rag.yaml", help="Overlay config for memory-RAG runs.")
    parser.add_argument("--groups", default="zero-shot,rag", help="Comma-separated experiment groups to run.")
    parser.add_argument("--runs-path", type=Path, default=EXPERIMENT_RUNS_DIR / "experiment_runs.jsonl", help="JSONL output for per-run results.")
    parser.add_argument("--summary-path", type=Path, default=EXPERIMENT_SUMMARIES_DIR / "experiment_summary.json", help="JSON output for aggregated metrics.")
    parser.add_argument("--repair-memory-path", type=Path, help="JSONL path for repair records written during this run.")
    parser.add_argument("--retrieval-memory-path", type=Path, help="JSONL path used as read-only retrieval memory for RAG runs.")
    parser.add_argument("--pattern-memory-path", type=Path, help="JSONL path used as read-only cross-project pattern memory.")
    parser.add_argument(
        "--runtime-validation-memory-path",
        type=Path,
        help="JSONL path used as read-only cross-project runtime validation memory.",
    )
    parser.add_argument("--max-workers", type=int, default=1, help="Parallel projects per experiment group.")
    parser.add_argument(
        "--prefill-workspace-root",
        type=Path,
        default=EXPERIMENT_WORKSPACES_DIR / "topology_prefill_runs",
        help="Workspace root for copied projects used by the topology-prefill group.",
    )
    return parser


def main_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_batch(
        manifest=args.manifest,
        base_config=args.base_config,
        zero_shot_config=args.zero_shot_config,
        rag_config=args.rag_config,
        groups=args.groups,
        runs_path=args.runs_path,
        summary_path=args.summary_path,
        repair_memory_path=args.repair_memory_path,
        retrieval_memory_path=args.retrieval_memory_path,
        pattern_memory_path=args.pattern_memory_path,
        runtime_validation_memory_path=args.runtime_validation_memory_path,
        max_workers=args.max_workers,
        prefill_workspace_root=args.prefill_workspace_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
