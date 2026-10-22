from __future__ import annotations

import os
import re
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from topomen.memory.repair_memory import classify_error_type, extract_error_signature
from topomen.topology.build_topology import (
    EXECUTABLE_DOCKERFILE_NODE_ORDER,
    IMPORT_TO_DEPENDENCY,
    BuildCachePlan,
    BuildTopology,
    FailureAttribution,
    RuntimeValidationPlan,
    TopologyBuildPlan,
    TopologyBuildStep,
    attribute_failure_to_node,
    create_topology_build_plan,
    format_runtime_host_command,
    generate_multistage_dockerfile,
    generate_topology_dockerfile,
    normalize_system_package_name,
    validate_topology_dockerfile,
)

SCHEDULER_STATES = {"planned", "ready", "running", "prepared", "passed", "failed", "skipped"}
VERIFICATION_NODE_ORDER = ["dockerfile_generation", "build_validation", "runtime_validation"]


class TopologyNodeRunResult(BaseModel):
    node_id: str
    status: str
    stage: str
    command: str = ""
    returncode: int = 0
    elapsed: float = Field(default=0.0, ge=0.0)
    output: dict[str, Any] = Field(default_factory=dict)
    error_signature: str = ""
    attribution: FailureAttribution | None = None
    attempt: int = Field(default=1, ge=1)
    state_before: str = ""
    state_after: str = ""
    runtime_validation_mode: str = ""
    command_source: str = ""
    runtime_attempt_index: int = Field(default=0, ge=0)
    runtime_candidate_id: str = ""
    fallback_trigger_reason: str = ""
    cache_hit_steps: int = Field(default=0, ge=0)
    cache_total_steps: int = Field(default=0, ge=0)


class TopologyScheduleResult(BaseModel):
    success: bool
    failed_node_id: str = ""
    executed_nodes: list[str] = Field(default_factory=list)
    node_retries: dict[str, int] = Field(default_factory=dict)
    docker_build_count: int = 0
    docker_run_count: int = 0
    node_results: list[TopologyNodeRunResult] = Field(default_factory=list)
    node_states: dict[str, str] = Field(default_factory=dict)
    state_transitions: list[dict[str, str]] = Field(default_factory=list)
    runtime_validation_plan: RuntimeValidationPlan = Field(default_factory=RuntimeValidationPlan)
    skipped_nodes: list[str] = Field(default_factory=list)
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    rollback_events: list[dict[str, str]] = Field(default_factory=list)
    restored_checkpoint_id: str = ""
    primary_failure_stage: str = ""
    primary_failure_error_type: str = ""
    runtime_fallback_attempts: int = 0
    runtime_fallback_recovered: bool = False
    preflight_passed: bool = True
    preflight_blocker: str = ""
    runtime_semantic_success: bool = False
    multistage_used: bool = False
    parallel_stages: list[str] = Field(default_factory=list)
    threaded_build_used: bool = False
    parallel_build_branches: int = Field(default=0, ge=0)
    parallel_repair_used: bool = False
    repair_branches_tried: int = Field(default=0, ge=0)
    build_cache_enabled: bool = False
    build_cache_namespace: str = ""
    build_cache_reuse_existing: bool = False
    build_cache_id_scope: str = ""
    build_cache_ids: dict[str, str] = Field(default_factory=dict)
    final_image: str = ""
    internal_branch_images: dict[str, str] = Field(default_factory=dict)
    build_scheduler_strategy: str = "legacy_threaded"
    ready_queue_max_size: int = Field(default=0, ge=0)
    resource_wait_seconds: float = Field(default=0.0, ge=0.0)
    critical_path_seconds: float = Field(default=0.0, ge=0.0)
    node_wait_seconds: dict[str, float] = Field(default_factory=dict)
    node_build_seconds: dict[str, float] = Field(default_factory=dict)
    reused_internal_images: int = Field(default=0, ge=0)
    internal_image_reused_count: int = Field(default=0, ge=0)
    rebuild_saved_nodes: int = Field(default=0, ge=0)
    cache_hit_steps: int = Field(default=0, ge=0)
    cache_total_steps: int = Field(default=0, ge=0)
    cache_hit_by_node: dict[str, int] = Field(default_factory=dict)
    cache_total_by_node: dict[str, int] = Field(default_factory=dict)
    cache_hit_rate_by_node: dict[str, float] = Field(default_factory=dict)
    dag_failed_step_id: str = ""
    repaired_step_ids: list[str] = Field(default_factory=list)
    reused_step_ids: list[str] = Field(default_factory=list)


class ThreadedBuildBranch(BaseModel):
    branch_id: str
    node_id: str
    image_tag: str
    base_image_tag: str
    dockerfile_text: str
    depends_on: list[str] = Field(default_factory=list)
    cache_ids: dict[str, str] = Field(default_factory=dict)
    internal_image: bool = True


class ThreadedBranchResult(BaseModel):
    branch_id: str
    node_id: str
    status: str
    image_tag: str
    dockerfile_text: str
    command: str
    output: dict[str, Any] = Field(default_factory=dict)
    elapsed: float = Field(default=0.0, ge=0.0)
    error_signature: str = ""
    attribution: FailureAttribution | None = None
    candidate_id: str = ""
    repaired: bool = False
    cache_hit_steps: int = Field(default=0, ge=0)
    cache_total_steps: int = Field(default=0, ge=0)


class TopologyScheduler:
    def __init__(
        self,
        *,
        topology: BuildTopology,
        project_path: str | Path,
        image_name: str,
        env,
        trace_callback: Callable[[str, dict[str, Any]], None] | None = None,
        command_timeout_seconds: int = 180,
        docker_build_timeout_seconds: int = 1800,
        docker_run_timeout_seconds: int = 900,
        parallel_repair: bool = False,
        threaded_build: bool = False,
        build_scheduler_strategy: str = "legacy_threaded",
        parallel_build_max_workers: int = 3,
        parallel_repair_max_branches: int = 3,
    ):
        self.topology = topology
        self.project_path = Path(project_path)
        self.image_name = image_name
        self.env = env
        self.trace_callback = trace_callback
        self.runtime_validation_plan = topology.analysis.runtime_validation
        self.build_cache_plan = topology.analysis.build_cache
        self.node_retries: Counter[str] = Counter()
        self._seen_failures: Counter[str] = Counter()
        self.node_states: dict[str, str] = {node.node_id: "planned" for node in topology.nodes}
        self.state_transitions: list[dict[str, str]] = []
        self.skipped_nodes: list[str] = []
        self.checkpoints: list[dict[str, Any]] = []
        self.rollback_events: list[dict[str, str]] = []
        self.restored_checkpoint_id = ""
        self.runtime_fallback_attempts = 0
        self.runtime_fallback_recovered = False
        self.preflight_passed = True
        self.preflight_blocker = ""
        self.runtime_command_count = 0
        self.command_timeout_seconds = command_timeout_seconds
        self.docker_build_timeout_seconds = docker_build_timeout_seconds
        self.docker_run_timeout_seconds = docker_run_timeout_seconds
        self.parallel_repair = parallel_repair
        self.threaded_build = threaded_build
        self.build_scheduler_strategy = build_scheduler_strategy
        self.parallel_build_max_workers = max(1, parallel_build_max_workers)
        self.parallel_repair_max_branches = max(1, parallel_repair_max_branches)
        self.multistage_used = bool(topology.multistage)
        self.threaded_build_used = bool(threaded_build)
        self.parallel_stages: list[str] = []
        self.parallel_stage_windows: dict[str, dict[str, int]] = {}
        self.parallel_build_branches = 0
        self.threaded_branch_checkpoints: list[dict[str, str]] = []
        self.threaded_branch_results: dict[str, ThreadedBranchResult] = {}
        self._threaded_node_results: list[TopologyNodeRunResult] = []
        self.parallel_repair_used = False
        self.repair_branches_tried = 0
        self.parallel_repair_build_count = 0
        self.parallel_repair_run_count = 0
        self._internal_branch_images_cleaned = False
        self.ready_queue_max_size = 0
        self.resource_wait_seconds = 0.0
        self.critical_path_seconds = 0.0
        self.node_wait_seconds: dict[str, float] = {}
        self.node_build_seconds: dict[str, float] = {}
        self.reused_internal_images = 0
        self.rebuild_saved_nodes = 0
        self._dag_critical_paths: dict[str, float] = {}
        self._dag_build_plan: TopologyBuildPlan | None = None
        self._dag_failed_step_id = ""
        self._dag_completed_step_ids: set[str] = set()
        self._dag_repaired_step_ids: set[str] = set()
        self._dag_reused_step_ids: set[str] = set()
        self._dag_reused_internal_step_ids: set[str] = set()
        self.cache_hit_steps = 0
        self.cache_total_steps = 0
        self.cache_hit_by_node: Counter[str] = Counter()
        self.cache_total_by_node: Counter[str] = Counter()

    def run(self) -> TopologyScheduleResult:
        node_results: list[TopologyNodeRunResult] = []
        self._record_trace(
            "scheduler_started",
            node_order=self._schedule_node_order(),
            node_states=dict(self.node_states),
            runtime_validation_plan=self.runtime_validation_plan.model_dump(mode="json"),
            build_scheduler_strategy=self.build_scheduler_strategy,
        )
        self._record_trace("runtime_validation_planned", **self.runtime_validation_plan.model_dump(mode="json"))
        self._record_trace("build_cache_planned", **self.build_cache_plan.model_dump(mode="json"))
        preflight_ok, preflight_blocker = self._run_preflight()
        self.preflight_passed = preflight_ok
        self.preflight_blocker = preflight_blocker
        if not preflight_ok:
            output = {"output": preflight_blocker, "returncode": 1, "exception_info": ""}
            preflight_result = TopologyNodeRunResult(
                node_id="preflight_validation",
                status="failed",
                stage="preflight",
                returncode=1,
                output=output,
                error_signature=preflight_blocker,
            )
            node_results.append(preflight_result)
            self._record_trace(
                "scheduler_failed",
                failed_node_id="preflight_validation",
                stage="preflight",
                error_signature=preflight_blocker,
                attribution={},
                executed_nodes=[],
                node_states=dict(self.node_states),
                skipped_nodes=list(self.skipped_nodes),
                restored_checkpoint_id=self.restored_checkpoint_id,
                preflight_blocker=preflight_blocker,
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                build_cache_id_scope=self.build_cache_plan.cache_id_scope,
            )
            return TopologyScheduleResult(
                success=False,
                failed_node_id="preflight_validation",
                node_results=node_results,
                node_states=dict(self.node_states),
                state_transitions=list(self.state_transitions),
                runtime_validation_plan=self.runtime_validation_plan,
                skipped_nodes=list(self.skipped_nodes),
                checkpoints=list(self.checkpoints),
                rollback_events=list(self.rollback_events),
                restored_checkpoint_id=self.restored_checkpoint_id,
                primary_failure_stage="preflight",
                runtime_fallback_attempts=0,
                runtime_fallback_recovered=False,
                preflight_passed=False,
                preflight_blocker=preflight_blocker,
                runtime_semantic_success=False,
                threaded_build_used=self.threaded_build_used,
                parallel_build_branches=self.parallel_build_branches,
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_namespace=self.build_cache_plan.namespace,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                build_cache_id_scope=self.build_cache_plan.cache_id_scope,
                build_cache_ids=self._build_cache_ids(),
                final_image=self.image_name,
                internal_branch_images=self._internal_branch_images(),
                **self._schedule_result_metrics(),
            )

        node_results.extend(self._prefill_dockerfile())
        self._create_checkpoint("checkpoint-prefill", trigger_node_id="runtime_validation")
        guard_result = self._validate_dockerfile()
        node_results.append(guard_result)
        if guard_result.status == "failed":
            self._rollback_to_latest_checkpoint(reason="guard_failed")
            self._skip_downstream("dockerfile_generation", reason="Dockerfile guard failed.")
            return self._failure_result(node_results, guard_result)
        self._create_checkpoint("checkpoint-guard", trigger_node_id="dockerfile_generation")

        if self.threaded_build:
            build_result = self._execute_topology_dag_build() if self._topology_dag_enabled() else self._execute_threaded_build()
            node_results.extend(self._consume_threaded_node_results())
        else:
            build_result = self._execute_command(
                node_id="build_validation",
                stage="build",
                command=self._build_command(),
            )
        node_results.append(build_result)
        if build_result.status == "failed":
            repaired_build_result = None if self.threaded_build else self._parallel_repair(build_result)
            if repaired_build_result is not None:
                node_results.append(repaired_build_result)
                build_result = repaired_build_result
            else:
                self._rollback_to_latest_checkpoint(reason="build_failed")
                self._skip_downstream("build_validation", reason="Docker build failed.")
                return self._failure_result(node_results, build_result)
        self._create_checkpoint("checkpoint-build", trigger_node_id="build_validation")

        run_result = (
            self._execute_topology_dag_runtime_validation()
            if self.threaded_build and self._topology_dag_enabled()
            else self._execute_runtime_validation()
        )
        node_results.append(run_result)
        if run_result.status == "failed":
            repaired_run_result = (
                self._threaded_repair_runtime(run_result) if self.threaded_build else self._parallel_repair(run_result)
            )
            if repaired_run_result is not None:
                node_results.append(repaired_run_result)
                run_result = repaired_run_result
            else:
                self._rollback_to_latest_checkpoint(reason="runtime_failed")
                self._skip_downstream("runtime_validation", reason="Runtime validation failed.")
                return self._failure_result(node_results, run_result)

        self._mark_finalization_passed()
        self._cleanup_internal_branch_images(reason="scheduler_success")
        self._record_trace(
            "scheduler_passed",
            executed_nodes=self._executed_nodes(node_results),
            node_states=dict(self.node_states),
            final_image=self.image_name,
            public_images=[self.image_name],
            internal_branch_images=self._internal_branch_images(),
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            build_cache_id_scope=self.build_cache_plan.cache_id_scope,
            **self._schedule_result_metrics(),
        )
        return self._success_result(node_results)

    def _prefill_dockerfile(self) -> list[TopologyNodeRunResult]:
        dockerfile_path = self.project_path / "Dockerfile"
        dockerfile_text = (
            generate_multistage_dockerfile(self.topology) if self.topology.multistage else generate_topology_dockerfile(self.topology)
        )
        dockerfile_path.write_text(dockerfile_text, encoding="utf-8")
        results = []
        for node_id in EXECUTABLE_DOCKERFILE_NODE_ORDER:
            if node_id not in self.topology.node_map():
                continue
            state_before = self.node_states.get(node_id, "planned")
            self._transition(node_id, "ready", stage="prefill", reason="Dockerfile block is ready to be materialized.")
            self._record_trace("scheduler_node_started", node_id=node_id, stage="prefill")
            self._transition(node_id, "running", stage="prefill", reason="Writing Dockerfile node block.")
            self._transition(node_id, "prepared", stage="prefill", reason="Dockerfile node block was written.")
            result = TopologyNodeRunResult(
                node_id=node_id,
                status="prepared",
                stage="prefill",
                state_before=state_before,
                state_after=self.node_states[node_id],
                runtime_validation_mode=self.runtime_validation_plan.mode if node_id == "runtime_validation" else "",
                command_source=self.runtime_validation_plan.command_source if node_id == "runtime_validation" else "",
            )
            self._record_trace("scheduler_node_finished", **result.model_dump(mode="json", exclude={"output"}))
            results.append(result)
        return results

    def _validate_dockerfile(self) -> TopologyNodeRunResult:
        node_id = "dockerfile_generation"
        state_before = self.node_states.get(node_id, "planned")
        self._transition(node_id, "ready", stage="guard", reason="Dockerfile artifact is ready for guard validation.")
        self._record_trace("scheduler_node_started", node_id=node_id, stage="guard")
        self._transition(node_id, "running", stage="guard", reason="Validating topology Dockerfile invariants.")
        dockerfile_path = self.project_path / "Dockerfile"
        dockerfile_text = dockerfile_path.read_text(encoding="utf-8") if dockerfile_path.exists() else ""
        guard = validate_topology_dockerfile(dockerfile_text, topology=self.topology, protect_base_image=True)
        if guard.valid:
            self._transition(node_id, "passed", stage="guard", reason="Dockerfile guard validation passed.")
            result = TopologyNodeRunResult(
                node_id=node_id,
                status="passed",
                stage="guard",
                state_before=state_before,
                state_after=self.node_states[node_id],
            )
        else:
            self._transition(node_id, "failed", stage="guard", reason="Dockerfile guard validation failed.")
            output = {
                "output": "Dockerfile guardrail rejected scheduler prefill.\n" + "\n".join(guard.violations),
                "returncode": 1,
                "exception_info": "",
            }
            result = TopologyNodeRunResult(
                node_id=node_id,
                status="failed",
                stage="guard",
                returncode=1,
                output=output,
                error_signature="Dockerfile guardrail rejected scheduler prefill",
                attribution=FailureAttribution(
                    stage="build",
                    error_signature="Dockerfile guardrail rejected scheduler prefill",
                    error_type="dockerfile_syntax_error",
                    topology_node_id=node_id,
                    confidence=0.95,
                    reason="Scheduler generated a Dockerfile that failed topology guard validation.",
                ),
                state_before=state_before,
                state_after=self.node_states[node_id],
            )
        self._record_trace("scheduler_node_finished", **result.model_dump(mode="json", exclude={"output"}))
        return result

    def _execute_command(
        self,
        *,
        node_id: str,
        stage: str,
        command: str,
        runtime_plan: RuntimeValidationPlan | None = None,
        cache_node_id: str | None = None,
    ) -> TopologyNodeRunResult:
        state_before = self.node_states.get(node_id, "planned")
        self._transition(node_id, "ready", stage=stage, reason=f"{node_id} command is ready.")
        self._record_trace("scheduler_node_started", node_id=node_id, stage=stage, command=command)
        self._transition(node_id, "running", stage=stage, reason=f"Executing {node_id} command.")
        started_at = time.perf_counter()
        output = self.env.execute({"command": command}, timeout=self._resolve_command_timeout(command))
        elapsed = time.perf_counter() - started_at
        if stage == "build" and self.topology.multistage:
            self._capture_parallel_stage_windows(output.get("output", ""))
        cache_hit_steps, cache_total_steps = (
            _count_docker_cache_steps(output.get("output", ""))
            if _docker_command_stage(command) == "build"
            else (0, 0)
        )
        if cache_total_steps > 0 or cache_hit_steps > 0:
            self._record_cache_metrics(cache_node_id or node_id, cache_hit_steps, cache_total_steps)
        returncode = int(output.get("returncode", 0))
        if returncode == 0:
            self._transition(node_id, "passed", stage=stage, reason=f"{node_id} command passed.")
            result = TopologyNodeRunResult(
                node_id=node_id,
                status="passed",
                stage=stage,
                command=command,
                returncode=returncode,
                elapsed=round(elapsed, 4),
                output=output,
                state_before=state_before,
                state_after=self.node_states[node_id],
                runtime_validation_mode=runtime_plan.mode if runtime_plan else "",
                command_source=runtime_plan.command_source if runtime_plan else "",
                cache_hit_steps=cache_hit_steps,
                cache_total_steps=cache_total_steps,
            )
        else:
            error_signature = extract_error_signature(output.get("output", ""))
            error_type = classify_error_type(error_signature, output.get("output", ""))
            attribution = attribute_failure_to_node(
                output.get("output", ""),
                stage=stage,
                error_type=error_type,
                error_signature=error_signature,
            )
            self._record_retry(attribution, error_signature)
            self._transition(node_id, "failed", stage=stage, reason=error_signature)
            result = TopologyNodeRunResult(
                node_id=node_id,
                status="failed",
                stage=stage,
                command=command,
                returncode=returncode,
                elapsed=round(elapsed, 4),
                output=output,
                error_signature=error_signature,
                attribution=attribution,
                state_before=state_before,
                state_after=self.node_states[node_id],
                runtime_validation_mode=runtime_plan.mode if runtime_plan else "",
                command_source=runtime_plan.command_source if runtime_plan else "",
                cache_hit_steps=cache_hit_steps,
                cache_total_steps=cache_total_steps,
            )
        self._record_trace("scheduler_node_finished", **result.model_dump(mode="json", exclude={"output"}))
        return result

    def _execute_runtime_validation(self) -> TopologyNodeRunResult:
        primary_command = self._run_command()
        self.runtime_command_count += 1
        primary_result = self._execute_command(
            node_id="runtime_validation",
            stage="run",
            command=primary_command,
            runtime_plan=self.runtime_validation_plan,
        )
        primary_result.runtime_attempt_index = 0
        primary_result.runtime_candidate_id = "primary"
        if primary_result.status == "passed":
            return primary_result

        fallback_candidates = self.runtime_validation_plan.fallback_candidates or []
        for index, candidate in enumerate(fallback_candidates, start=1):
            command = _coerce_fallback_command(candidate.get("command"))
            if not command:
                continue
            self.runtime_fallback_attempts += 1
            candidate_mode = str(candidate.get("mode") or self.runtime_validation_plan.mode)
            candidate_source = str(candidate.get("command_source") or "runtime_fallback")
            candidate_id = str(candidate.get("candidate_id") or f"fallback-{index}")
            reason = str(candidate.get("reason") or "primary runtime command failed")
            self._record_trace(
                "runtime_fallback_attempted",
                attempt=index,
                candidate_id=candidate_id,
                mode=candidate_mode,
                command_source=candidate_source,
                trigger_error=primary_result.error_signature,
            )
            plan = self.runtime_validation_plan.model_copy(
                update={
                    "mode": candidate_mode,
                    "command_source": candidate_source,
                }
            )
            self.runtime_command_count += 1
            result = self._execute_command(
                node_id="runtime_validation",
                stage="run",
                command=format_runtime_host_command(self.runtime_validation_plan, self.image_name, command),
                runtime_plan=plan,
            )
            result.runtime_attempt_index = index
            result.runtime_candidate_id = candidate_id
            result.fallback_trigger_reason = reason
            if result.status == "passed":
                self.runtime_fallback_recovered = True
                self._record_trace(
                    "runtime_fallback_succeeded",
                    attempt=index,
                    candidate_id=candidate_id,
                    mode=candidate_mode,
                    command_source=candidate_source,
                )
                return result
            primary_result = result
        return primary_result

    def _execute_topology_dag_runtime_validation(self) -> TopologyNodeRunResult:
        build_plan = self._dag_build_plan or create_topology_build_plan(
            self.topology,
            docker_build_limit=self.parallel_build_max_workers,
        )
        step = self._build_step(build_plan, "runtime-validation")
        self.ready_queue_max_size = max(self.ready_queue_max_size, 1)
        self.node_wait_seconds.setdefault("runtime-validation", 0.0)
        self._record_dag_step_ready(step)
        self._record_dag_step_started(step, resource_keys=list(step.resource_keys))
        result = self._execute_runtime_validation()
        self.node_build_seconds["runtime-validation"] = round(result.elapsed, 4)
        parent_critical = self._dag_critical_paths.get("merge-final", 0.0)
        self._dag_critical_paths["runtime-validation"] = parent_critical + result.elapsed
        self.critical_path_seconds = max(self.critical_path_seconds, self._dag_critical_paths["runtime-validation"])
        if result.status == "passed":
            self._dag_completed_step_ids.add("runtime-validation")
        else:
            self._dag_failed_step_id = "runtime-validation"
        self._record_dag_step_finished(step, result)
        return result

    def _skip_downstream(self, failed_node_id: str, *, reason: str) -> None:
        for node_id in self._downstream_nodes(failed_node_id):
            if node_id not in self.node_states or self.node_states[node_id] in {"passed", "failed", "skipped"}:
                continue
            state_before = self.node_states[node_id]
            self._transition(node_id, "skipped", stage="skip", reason=reason)
            if node_id not in self.skipped_nodes:
                self.skipped_nodes.append(node_id)
            self._record_trace("scheduler_node_skipped", node_id=node_id, reason=reason, state_before=state_before)

    def _mark_finalization_passed(self) -> None:
        node_id = "dockerfile_finalization"
        if node_id not in self.node_states:
            return
        self._transition(node_id, "ready", stage="finalize", reason="Runtime validation passed.")
        self._transition(node_id, "running", stage="finalize", reason="Finalizing Dockerfile artifact.")
        self._transition(node_id, "passed", stage="finalize", reason="Dockerfile artifact finalized.")

    def _success_result(self, node_results: list[TopologyNodeRunResult]) -> TopologyScheduleResult:
        runtime_result = next((item for item in reversed(node_results) if item.node_id == "runtime_validation"), None)
        runtime_mode = runtime_result.runtime_validation_mode if runtime_result is not None else self.runtime_validation_plan.mode
        runtime_semantic_success = runtime_mode in {"script", "package_import", "test", "web_service"}
        return TopologyScheduleResult(
            success=True,
            executed_nodes=self._executed_nodes(node_results),
            node_retries=dict(sorted(self.node_retries.items())),
            docker_build_count=(
                sum(1 for result in node_results if _docker_command_stage(result.command) == "build")
                + self.parallel_repair_build_count
            ),
            docker_run_count=max(
                sum(1 for result in node_results if _docker_command_stage(result.command) == "run")
                + self.parallel_repair_run_count,
                self.runtime_command_count,
            ),
            node_results=node_results,
            node_states=dict(self.node_states),
            state_transitions=list(self.state_transitions),
            runtime_validation_plan=self.runtime_validation_plan,
            skipped_nodes=list(self.skipped_nodes),
            checkpoints=list(self.checkpoints),
            rollback_events=list(self.rollback_events),
            restored_checkpoint_id=self.restored_checkpoint_id,
            runtime_fallback_attempts=self.runtime_fallback_attempts,
            runtime_fallback_recovered=self.runtime_fallback_recovered,
            preflight_passed=self.preflight_passed,
            preflight_blocker=self.preflight_blocker,
            runtime_semantic_success=runtime_semantic_success,
            multistage_used=self.multistage_used,
            parallel_stages=list(self.parallel_stages),
            threaded_build_used=self.threaded_build_used,
            parallel_build_branches=self.parallel_build_branches,
            parallel_repair_used=self.parallel_repair_used,
            repair_branches_tried=self.repair_branches_tried,
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_namespace=self.build_cache_plan.namespace,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            build_cache_id_scope=self.build_cache_plan.cache_id_scope,
            build_cache_ids=self._build_cache_ids(),
            final_image=self.image_name,
            internal_branch_images=self._internal_branch_images(),
            **self._schedule_result_metrics(),
        )

    def _failure_result(
        self,
        node_results: list[TopologyNodeRunResult],
        failed_result: TopologyNodeRunResult,
    ) -> TopologyScheduleResult:
        failed_node_id = failed_result.attribution.topology_node_id if failed_result.attribution else failed_result.node_id
        self._record_trace(
            "scheduler_failed",
            failed_node_id=failed_node_id,
            stage=failed_result.stage,
            error_signature=failed_result.error_signature,
            attribution=failed_result.attribution.model_dump(mode="json") if failed_result.attribution else {},
            executed_nodes=self._executed_nodes(node_results),
            node_states=dict(self.node_states),
            skipped_nodes=list(self.skipped_nodes),
            restored_checkpoint_id=self.restored_checkpoint_id,
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            build_cache_id_scope=self.build_cache_plan.cache_id_scope,
            **self._schedule_result_metrics(),
        )
        error_type = failed_result.attribution.error_type if failed_result.attribution else ""
        return TopologyScheduleResult(
            success=False,
            failed_node_id=failed_node_id,
            executed_nodes=self._executed_nodes(node_results),
            node_retries=dict(sorted(self.node_retries.items())),
            docker_build_count=(
                sum(1 for result in node_results if _docker_command_stage(result.command) == "build")
                + self.parallel_repair_build_count
            ),
            docker_run_count=max(
                sum(1 for result in node_results if _docker_command_stage(result.command) == "run")
                + self.parallel_repair_run_count,
                self.runtime_command_count,
            ),
            node_results=node_results,
            node_states=dict(self.node_states),
            state_transitions=list(self.state_transitions),
            runtime_validation_plan=self.runtime_validation_plan,
            skipped_nodes=list(self.skipped_nodes),
            checkpoints=list(self.checkpoints),
            rollback_events=list(self.rollback_events),
            restored_checkpoint_id=self.restored_checkpoint_id,
            primary_failure_stage=failed_result.stage,
            primary_failure_error_type=error_type,
            runtime_fallback_attempts=self.runtime_fallback_attempts,
            runtime_fallback_recovered=self.runtime_fallback_recovered,
            preflight_passed=self.preflight_passed,
            preflight_blocker=self.preflight_blocker,
            runtime_semantic_success=False,
            multistage_used=self.multistage_used,
            parallel_stages=list(self.parallel_stages),
            threaded_build_used=self.threaded_build_used,
            parallel_build_branches=self.parallel_build_branches,
            parallel_repair_used=self.parallel_repair_used,
            repair_branches_tried=self.repair_branches_tried,
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_namespace=self.build_cache_plan.namespace,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            build_cache_id_scope=self.build_cache_plan.cache_id_scope,
            build_cache_ids=self._build_cache_ids(),
            final_image=self.image_name,
            internal_branch_images=self._internal_branch_images(),
            **self._schedule_result_metrics(),
        )

    def _record_retry(self, attribution: FailureAttribution | None, error_signature: str) -> None:
        if attribution is None:
            return
        key = f"{attribution.topology_node_id}:{error_signature}"
        self._seen_failures[key] += 1
        if self._seen_failures[key] > 1:
            self.node_retries[attribution.topology_node_id] += 1

    def _transition(self, node_id: str, to_state: str, *, stage: str, reason: str) -> None:
        if to_state not in SCHEDULER_STATES:
            msg = f"Unsupported scheduler state: {to_state}"
            raise ValueError(msg)
        from_state = self.node_states.get(node_id, "planned")
        if from_state == to_state:
            return
        self.node_states[node_id] = to_state
        transition = {
            "node_id": node_id,
            "from_state": from_state,
            "to_state": to_state,
            "stage": stage,
            "reason": reason,
        }
        self.state_transitions.append(transition)
        self._record_trace("scheduler_state_transition", **transition)

    def _build_command(self) -> str:
        return self._build_command_for_dockerfile(self._dockerfile_path(), self.image_name)

    def _build_command_for_dockerfile(self, dockerfile_path: Path, image_name: str) -> str:
        quoted_dockerfile = str(dockerfile_path)
        quoted_context = str(self.project_path)
        if self.topology.multistage or self._build_cache_enabled():
            return (
                f'docker buildx build --load --progress=plain -f "{quoted_dockerfile}" '
                f'-t {image_name} "{quoted_context}" 2>&1'
            )
        return f'docker build -f "{quoted_dockerfile}" -t {image_name} "{quoted_context}" 2>&1'

    def _run_command(self) -> str:
        return format_runtime_host_command(self.runtime_validation_plan, self.image_name)

    def _run_command_for_image(self, image_name: str) -> str:
        return format_runtime_host_command(self.runtime_validation_plan, image_name)

    def _capture_parallel_stage_windows(self, build_output: str) -> None:
        stage_windows = _extract_buildkit_stage_windows(build_output)
        if not stage_windows:
            return
        overlapping = _overlapping_stage_names(stage_windows)
        if len(overlapping) < 2:
            return
        self.parallel_stages = overlapping
        self.parallel_stage_windows = stage_windows
        self._record_trace(
            "scheduler_stage_parallel",
            stages=list(overlapping),
            stage_windows=stage_windows,
        )

    def _topology_dag_enabled(self) -> bool:
        return self.build_scheduler_strategy == "topology_dag"

    def _schedule_result_metrics(self) -> dict[str, Any]:
        return {
            "build_scheduler_strategy": self.build_scheduler_strategy,
            "ready_queue_max_size": self.ready_queue_max_size,
            "resource_wait_seconds": round(self.resource_wait_seconds, 4),
            "critical_path_seconds": round(self.critical_path_seconds, 4),
            "node_wait_seconds": dict(sorted(self.node_wait_seconds.items())),
            "node_build_seconds": dict(sorted(self.node_build_seconds.items())),
            "reused_internal_images": self.reused_internal_images,
            "internal_image_reused_count": self.reused_internal_images,
            "rebuild_saved_nodes": self.rebuild_saved_nodes,
            "cache_hit_steps": self.cache_hit_steps,
            "cache_total_steps": self.cache_total_steps,
            "cache_hit_by_node": dict(sorted(self.cache_hit_by_node.items())),
            "cache_total_by_node": dict(sorted(self.cache_total_by_node.items())),
            "cache_hit_rate_by_node": self._cache_hit_rate_by_node(),
            "dag_failed_step_id": self._dag_failed_step_id,
            "repaired_step_ids": sorted(self._dag_repaired_step_ids),
            "reused_step_ids": sorted(self._dag_reused_step_ids),
        }

    def _cache_hit_rate_by_node(self) -> dict[str, float]:
        return {
            node_id: round(self.cache_hit_by_node[node_id] / total, 4)
            for node_id, total in sorted(self.cache_total_by_node.items())
            if total > 0
        }

    def _record_cache_metrics(self, node_id: str, cache_hit_steps: int, cache_total_steps: int) -> None:
        if cache_total_steps <= 0 and cache_hit_steps <= 0:
            return
        self.cache_hit_steps += max(0, cache_hit_steps)
        self.cache_total_steps += max(0, cache_total_steps)
        self.cache_hit_by_node[node_id] += max(0, cache_hit_steps)
        self.cache_total_by_node[node_id] += max(0, cache_total_steps)

    def _execute_topology_dag_build(self) -> TopologyNodeRunResult:
        self.threaded_build_used = True
        self._threaded_node_results = []
        self._dag_critical_paths = {}
        self._dag_failed_step_id = ""
        self._dag_completed_step_ids = set()
        self._dag_repaired_step_ids = set()
        self._dag_reused_step_ids = set()
        self._dag_reused_internal_step_ids = set()
        build_plan = create_topology_build_plan(
            self.topology,
            docker_build_limit=self.parallel_build_max_workers,
        )
        self._dag_build_plan = build_plan
        self._record_trace(
            "topology_build_plan_created",
            strategy=build_plan.strategy,
            steps=[step.model_dump(mode="json") for step in build_plan.steps],
            resource_limits=build_plan.resource_limits,
        )
        branch_root = Path(tempfile.mkdtemp(prefix="topomen-topology-dag-build-"))
        base_branch = self._threaded_base_branch()
        try:
            base_step = self._build_step(build_plan, "base")
            self._record_dag_step_ready(base_step)
            self._record_dag_step_started(base_step)
            base_skipped = self._can_skip_threaded_base_branch(base_branch)
            base_result = (
                self._skip_threaded_base_branch(base_branch)
                if base_skipped
                else self._run_threaded_branch(base_branch, branch_root)
            )
            if base_skipped:
                self.reused_internal_images += 1
            self.node_build_seconds["base"] = round(base_result.elapsed, 4)
            self._dag_critical_paths["base"] = base_result.elapsed
            self._record_cache_metrics("base", base_result.cache_hit_steps, base_result.cache_total_steps)
            self._record_dag_step_finished(base_step, base_result)
            self._threaded_node_results.append(self._threaded_node_result(base_result))
            if base_result.status != "passed":
                self._dag_failed_step_id = "base"
                return self._threaded_failure_result(base_result)
            self._dag_completed_step_ids.add("base")
            self.threaded_branch_results[base_result.branch_id] = base_result
            self._record_trace(
                "threaded_base_materialized",
                branch_id=base_result.branch_id,
                node_id=base_result.node_id,
                materialized=not base_skipped,
                image_tag=base_result.image_tag,
                base_image=self.topology.analysis.base_image,
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=self._build_cache_ids(),
                build_scheduler_strategy=self.build_scheduler_strategy,
            )

            dependency_branches = self._threaded_dependency_branches(base_result.image_tag)
            self.parallel_build_branches = len(dependency_branches)
            self._record_trace(
                "parallel_build_planned",
                branches=[branch.model_dump(mode="json") for branch in dependency_branches],
                max_workers=self.parallel_build_max_workers,
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=self._build_cache_ids(),
                build_scheduler_strategy=self.build_scheduler_strategy,
            )
            dependency_results = self._run_topology_dag_dependency_branches(
                dependency_branches,
                branch_root,
                build_plan,
            )
            for branch_result in dependency_results:
                self._threaded_node_results.append(self._threaded_node_result(branch_result))
                if branch_result.status != "passed":
                    return self._threaded_failure_result(branch_result)
                self.threaded_branch_results[branch_result.branch_id] = branch_result

            return self._execute_topology_dag_merge(build_plan)
        finally:
            for file_path in branch_root.iterdir():
                file_path.unlink(missing_ok=True)
            branch_root.rmdir()

    def _run_topology_dag_dependency_branches(
        self,
        branches: list[ThreadedBuildBranch],
        branch_root: Path,
        build_plan: TopologyBuildPlan,
    ) -> list[ThreadedBranchResult]:
        if not branches:
            return []
        branch_by_step_id = {branch.branch_id: branch for branch in branches}
        pending = {
            step.step_id: step
            for step in build_plan.steps
            if step.step_id in branch_by_step_id
        }
        completed: dict[str, ThreadedBranchResult] = {}
        running: dict[Any, tuple[ThreadedBuildBranch, TopologyBuildStep, list[str]]] = {}
        resource_limits = dict(build_plan.resource_limits)
        resource_usage: Counter[str] = Counter()
        ready_since: dict[str, float] = {}
        resource_wait_started: dict[str, float] = {}

        with ThreadPoolExecutor(max_workers=self.parallel_build_max_workers) as executor:
            while pending or running:
                ready = [
                    step
                    for step in pending.values()
                    if all(
                        dependency in self._dag_completed_step_ids or dependency in completed
                        for dependency in step.depends_on
                    )
                ]
                self.ready_queue_max_size = max(self.ready_queue_max_size, len(ready))
                if len(ready) > 1:
                    self.parallel_stages = sorted(step.step_id for step in ready)
                scheduled_any = False
                for step in sorted(ready, key=lambda item: item.estimated_cost_weight, reverse=True):
                    branch = branch_by_step_id[step.step_id]
                    ready_since.setdefault(step.step_id, time.perf_counter())
                    self._record_dag_step_ready(step)
                    resources = self._dag_resource_keys(step, branch)
                    blocked = self._blocked_resources(resources, resource_usage, resource_limits)
                    if blocked:
                        if step.step_id not in resource_wait_started:
                            resource_wait_started[step.step_id] = time.perf_counter()
                            self._record_trace(
                                "scheduler_node_waiting_resource",
                                step_id=step.step_id,
                                node_id=step.node_id,
                                resource_keys=resources,
                                blocked_resources=blocked,
                                resource_usage=dict(resource_usage),
                                resource_limits=resource_limits,
                            )
                        continue
                    if step.step_id in resource_wait_started:
                        self.resource_wait_seconds += time.perf_counter() - resource_wait_started.pop(step.step_id)
                    self.node_wait_seconds[step.step_id] = round(time.perf_counter() - ready_since[step.step_id], 4)
                    self._allocate_resources(resources, resource_usage)
                    self._record_dag_step_started(step, resource_keys=resources)
                    future = executor.submit(self._run_threaded_branch, branch, branch_root)
                    running[future] = (branch, step, resources)
                    pending.pop(step.step_id, None)
                    scheduled_any = True
                if running:
                    done, _ = wait(running.keys(), return_when=FIRST_COMPLETED)
                    for future in done:
                        branch, step, resources = running.pop(future)
                        self._release_resources(resources, resource_usage)
                        result = future.result()
                        completed[result.branch_id] = result
                        self.node_build_seconds[result.branch_id] = round(result.elapsed, 4)
                        self._record_cache_metrics(result.branch_id, result.cache_hit_steps, result.cache_total_steps)
                        if result.status == "passed":
                            self.threaded_branch_results[result.branch_id] = result
                        if result.repaired:
                            self._dag_failed_step_id = step.step_id
                            self._record_topology_node_repaired(step, result, build_plan)
                        parent_critical = max((self._dag_critical_paths.get(dep, 0.0) for dep in step.depends_on), default=0.0)
                        self._dag_critical_paths[result.branch_id] = parent_critical + result.elapsed
                        self.critical_path_seconds = max(self.critical_path_seconds, self._dag_critical_paths[result.branch_id])
                        self._record_dag_step_finished(step, result)
                        if result.status != "passed":
                            self._dag_failed_step_id = step.step_id
                            return [*completed.values()]
                        self._dag_completed_step_ids.add(step.step_id)
                    continue
                if not scheduled_any and pending:
                    self._dag_failed_step_id = next(iter(pending))
                    return [
                        ThreadedBranchResult(
                            branch_id=branch_by_step_id[step.step_id].branch_id,
                            node_id=branch_by_step_id[step.step_id].node_id,
                            status="failed",
                            image_tag=branch_by_step_id[step.step_id].image_tag,
                            dockerfile_text=branch_by_step_id[step.step_id].dockerfile_text,
                            command="",
                            output={"output": "Topology DAG scheduler could not allocate resources.", "returncode": 1, "exception_info": ""},
                            error_signature="Topology DAG scheduler could not allocate resources",
                            attribution=FailureAttribution(
                                stage="build",
                                error_signature="Topology DAG scheduler could not allocate resources",
                                error_type="generic",
                                topology_node_id=branch_by_step_id[step.step_id].node_id,
                                confidence=0.8,
                                reason="Ready topology build step could not acquire required resources.",
                            ),
                        )
                        for step in pending.values()
                    ]
        return [completed[branch.branch_id] for branch in branches]

    def _execute_topology_dag_merge(self, build_plan: TopologyBuildPlan) -> TopologyNodeRunResult:
        step = self._build_step(build_plan, "merge-final")
        self._record_dag_step_ready(step)
        self._record_dag_step_started(step)
        final_dockerfile_text = self._generate_threaded_merge_dockerfile()
        dockerfile_path = self._dockerfile_path()
        dockerfile_path.write_text(final_dockerfile_text, encoding="utf-8")
        self._record_trace(
            "parallel_merge_started",
            dockerfile_path=str(dockerfile_path),
            branch_images=self._internal_branch_images(),
            internal_branch_images=self._internal_branch_images(),
            final_image=self.image_name,
            public_images=[self.image_name],
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=self._build_cache_ids(),
            build_scheduler_strategy=self.build_scheduler_strategy,
        )
        guard = validate_topology_dockerfile(final_dockerfile_text, topology=self.topology, protect_base_image=True)
        if not guard.valid:
            output = {
                "output": "Threaded merge Dockerfile failed topology guard.\n" + "\n".join(guard.violations),
                "returncode": 1,
                "exception_info": "",
            }
            result = TopologyNodeRunResult(
                node_id="build_validation",
                status="failed",
                stage="build",
                returncode=1,
                output=output,
                error_signature="Threaded merge Dockerfile failed topology guard",
                attribution=FailureAttribution(
                    stage="build",
                    error_signature="Threaded merge Dockerfile failed topology guard",
                    error_type="dockerfile_syntax_error",
                    topology_node_id="dockerfile_generation",
                    confidence=0.95,
                    reason="Threaded merge generated a Dockerfile that failed topology guard validation.",
                ),
            )
            self._dag_failed_step_id = "merge-final"
            self._record_dag_step_finished(step, result)
            return result
        build_result = self._execute_command(
            node_id="build_validation",
            stage="build",
            command=self._plain_build_command_for_dockerfile(dockerfile_path, self.image_name),
            cache_node_id="merge-final",
        )
        self.node_build_seconds["merge-final"] = round(build_result.elapsed, 4)
        parent_critical = max(
            (
                self._dag_critical_paths.get(step_id, 0.0)
                for step_id in ("system-deps", "python-deps", "base")
            ),
            default=0.0,
        )
        self._dag_critical_paths["merge-final"] = parent_critical + build_result.elapsed
        self.critical_path_seconds = max(self.critical_path_seconds, self._dag_critical_paths["merge-final"])
        self._record_dag_step_finished(step, build_result)
        if build_result.status == "passed":
            self._dag_completed_step_ids.add("merge-final")
            self._record_trace(
                "parallel_merge_succeeded",
                image_name=self.image_name,
                dockerfile_path=str(dockerfile_path),
                branch_images=self._internal_branch_images(),
                internal_branch_images=self._internal_branch_images(),
                final_image=self.image_name,
                public_images=[self.image_name],
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=self._build_cache_ids(),
                build_scheduler_strategy=self.build_scheduler_strategy,
                cache_hit_steps=build_result.cache_hit_steps,
                cache_total_steps=build_result.cache_total_steps,
                cache_hit_by_node=dict(sorted(self.cache_hit_by_node.items())),
                cache_total_by_node=dict(sorted(self.cache_total_by_node.items())),
                cache_hit_rate_by_node=self._cache_hit_rate_by_node(),
            )
        else:
            self._dag_failed_step_id = "merge-final"
        return build_result

    def _build_step(self, build_plan: TopologyBuildPlan, step_id: str) -> TopologyBuildStep:
        for step in build_plan.steps:
            if step.step_id == step_id:
                return step
        return TopologyBuildStep(step_id=step_id, node_id=step_id, scheduler_role=step_id)

    def _step_id_for_node(self, build_plan: TopologyBuildPlan, node_id: str) -> str:
        for step in build_plan.steps:
            if step.node_id == node_id:
                return step.step_id
        return ""

    def _downstream_step_ids(
        self,
        build_plan: TopologyBuildPlan,
        step_id: str,
        *,
        include_self: bool = True,
    ) -> list[str]:
        downstream: set[str] = {step_id} if include_self else set()
        changed = True
        while changed:
            changed = False
            for step in build_plan.steps:
                if step.step_id in downstream:
                    continue
                if any(dependency in downstream for dependency in step.depends_on):
                    downstream.add(step.step_id)
                    changed = True
        return sorted(downstream, key=lambda item: self._step_order_index(build_plan, item))

    def _step_order_index(self, build_plan: TopologyBuildPlan, step_id: str) -> int:
        for index, step in enumerate(build_plan.steps):
            if step.step_id == step_id:
                return index
        return len(build_plan.steps)

    def _record_topology_node_repaired(
        self,
        step: TopologyBuildStep,
        result: ThreadedBranchResult,
        build_plan: TopologyBuildPlan,
    ) -> None:
        downstream_rebuild_steps = self._downstream_step_ids(build_plan, step.step_id, include_self=True)
        reusable_steps = sorted(
            self._dag_completed_step_ids.difference(downstream_rebuild_steps),
            key=lambda item: self._step_order_index(build_plan, item),
        )
        internal_reused_steps = [
            step_id
            for step_id in reusable_steps
            if step_id in self.threaded_branch_results and step_id not in {"base", "merge-final", "runtime-validation"}
        ]
        new_internal_reuse = set(internal_reused_steps).difference(self._dag_reused_internal_step_ids)
        self._dag_reused_internal_step_ids.update(internal_reused_steps)
        self._dag_repaired_step_ids.add(step.step_id)
        self._dag_reused_step_ids.update(reusable_steps)
        self.rebuild_saved_nodes = len(self._dag_reused_step_ids)
        self.reused_internal_images += len(new_internal_reuse)
        self._record_trace(
            "topology_node_repaired",
            step_id=step.step_id,
            node_id=step.node_id,
            scheduler_role=step.scheduler_role,
            repair_scope=step.repair_scope,
            failed_step_id=step.step_id,
            winner_image_tag=result.image_tag,
            candidate_id=result.candidate_id,
            reused_sibling_steps=reusable_steps,
            reused_internal_steps=internal_reused_steps,
            downstream_rebuild_steps=downstream_rebuild_steps,
            rebuild_saved_nodes=self.rebuild_saved_nodes,
            reused_internal_images=self.reused_internal_images,
            cache_hit_steps=result.cache_hit_steps,
            cache_total_steps=result.cache_total_steps,
        )

    def _record_dag_step_ready(self, step: TopologyBuildStep) -> None:
        self._record_trace(
            "scheduler_node_ready",
            step_id=step.step_id,
            node_id=step.node_id,
            scheduler_role=step.scheduler_role,
            resource_keys=list(step.resource_keys),
            depends_on=list(step.depends_on),
        )

    def _record_dag_step_started(self, step: TopologyBuildStep, *, resource_keys: list[str] | None = None) -> None:
        self._record_trace(
            "scheduler_node_started",
            step_id=step.step_id,
            node_id=step.node_id,
            scheduler_role=step.scheduler_role,
            stage="topology_dag_build",
            resource_keys=resource_keys or list(step.resource_keys),
        )

    def _record_dag_step_finished(self, step: TopologyBuildStep, result: ThreadedBranchResult | TopologyNodeRunResult) -> None:
        elapsed = getattr(result, "elapsed", 0.0)
        status = getattr(result, "status", "")
        cache_hit_steps = int(getattr(result, "cache_hit_steps", 0))
        cache_total_steps = int(getattr(result, "cache_total_steps", 0))
        self._record_trace(
            "scheduler_node_finished",
            step_id=step.step_id,
            node_id=step.node_id,
            scheduler_role=step.scheduler_role,
            status=status,
            elapsed=round(elapsed, 4),
            repair_scope=step.repair_scope,
            checkpoint_scope=step.checkpoint_scope,
            cache_hit_steps=cache_hit_steps,
            cache_total_steps=cache_total_steps,
            cache_hit_by_node=dict(sorted(self.cache_hit_by_node.items())),
            cache_total_by_node=dict(sorted(self.cache_total_by_node.items())),
            cache_hit_rate_by_node=self._cache_hit_rate_by_node(),
            resource_wait_seconds=round(self.resource_wait_seconds, 4),
            critical_path_seconds=round(self.critical_path_seconds, 4),
            reused_internal_images=self.reused_internal_images,
            rebuild_saved_nodes=self.rebuild_saved_nodes,
        )

    def _dag_resource_keys(self, step: TopologyBuildStep, branch: ThreadedBuildBranch) -> list[str]:
        keys = list(step.resource_keys)
        if "docker_build" not in keys:
            keys.append("docker_build")
        if "docker_daemon" not in keys:
            keys.append("docker_daemon")
        if "network" not in keys and ("apt-get" in branch.dockerfile_text or "pip install" in branch.dockerfile_text):
            keys.append("network")
        return _dedupe_strings(keys)

    def _blocked_resources(
        self,
        resources: list[str],
        resource_usage: Counter[str],
        resource_limits: dict[str, int],
    ) -> list[str]:
        blocked = []
        for resource in resources:
            limit = max(1, int(resource_limits.get(resource, self.parallel_build_max_workers)))
            if resource_usage[resource] >= limit:
                blocked.append(resource)
        return blocked

    def _allocate_resources(self, resources: list[str], resource_usage: Counter[str]) -> None:
        for resource in resources:
            resource_usage[resource] += 1

    def _release_resources(self, resources: list[str], resource_usage: Counter[str]) -> None:
        for resource in resources:
            resource_usage[resource] = max(0, resource_usage[resource] - 1)

    def _execute_threaded_build(self) -> TopologyNodeRunResult:
        self.threaded_build_used = True
        self._threaded_node_results = []
        branch_root = Path(tempfile.mkdtemp(prefix="topomen-threaded-build-"))
        base_branch = self._threaded_base_branch()
        self._record_trace(
            "parallel_build_planned",
            branches=[base_branch.model_dump(mode="json")],
            max_workers=self.parallel_build_max_workers,
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=self._build_cache_ids(),
        )
        try:
            base_skipped = self._can_skip_threaded_base_branch(base_branch)
            base_result = (
                self._skip_threaded_base_branch(base_branch)
                if base_skipped
                else self._run_threaded_branch(base_branch, branch_root)
            )
            self._threaded_node_results.append(self._threaded_node_result(base_result))
            if base_result.status != "passed":
                return self._threaded_failure_result(base_result)
            self.threaded_branch_results[base_result.branch_id] = base_result
            self._record_trace(
                "threaded_base_materialized",
                branch_id=base_result.branch_id,
                node_id=base_result.node_id,
                materialized=not base_skipped,
                image_tag=base_result.image_tag,
                base_image=self.topology.analysis.base_image,
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=self._build_cache_ids(),
            )

            dependency_branches = self._threaded_dependency_branches(base_result.image_tag)
            self.parallel_build_branches = len(dependency_branches)
            self._record_trace(
                "parallel_build_planned",
                branches=[branch.model_dump(mode="json") for branch in dependency_branches],
                max_workers=self.parallel_build_max_workers,
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=self._build_cache_ids(),
            )
            dependency_results = self._run_threaded_dependency_branches(dependency_branches, branch_root)
            for branch_result in dependency_results:
                self._threaded_node_results.append(self._threaded_node_result(branch_result))
                if branch_result.status != "passed":
                    return self._threaded_failure_result(branch_result)
                self.threaded_branch_results[branch_result.branch_id] = branch_result

            final_dockerfile_text = self._generate_threaded_merge_dockerfile()
            dockerfile_path = self._dockerfile_path()
            dockerfile_path.write_text(final_dockerfile_text, encoding="utf-8")
            self._record_trace(
                "parallel_merge_started",
                dockerfile_path=str(dockerfile_path),
                branch_images=self._internal_branch_images(),
                internal_branch_images=self._internal_branch_images(),
                final_image=self.image_name,
                public_images=[self.image_name],
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=self._build_cache_ids(),
            )
            guard = validate_topology_dockerfile(final_dockerfile_text, topology=self.topology, protect_base_image=True)
            if not guard.valid:
                output = {
                    "output": "Threaded merge Dockerfile failed topology guard.\n" + "\n".join(guard.violations),
                    "returncode": 1,
                    "exception_info": "",
                }
                return TopologyNodeRunResult(
                    node_id="build_validation",
                    status="failed",
                    stage="build",
                    returncode=1,
                    output=output,
                    error_signature="Threaded merge Dockerfile failed topology guard",
                    attribution=FailureAttribution(
                        stage="build",
                        error_signature="Threaded merge Dockerfile failed topology guard",
                        error_type="dockerfile_syntax_error",
                        topology_node_id="dockerfile_generation",
                        confidence=0.95,
                        reason="Threaded merge generated a Dockerfile that failed topology guard validation.",
                    ),
                )

            build_result = self._execute_command(
                node_id="build_validation",
                stage="build",
                command=self._plain_build_command_for_dockerfile(dockerfile_path, self.image_name),
            )
            if build_result.status == "passed":
                self._record_trace(
                    "parallel_merge_succeeded",
                    image_name=self.image_name,
                    dockerfile_path=str(dockerfile_path),
                    branch_images=self._internal_branch_images(),
                    internal_branch_images=self._internal_branch_images(),
                    final_image=self.image_name,
                    public_images=[self.image_name],
                    build_cache_enabled=self.build_cache_plan.enabled,
                    build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                    cache_ids=self._build_cache_ids(),
                )
            return build_result
        finally:
            for file_path in branch_root.iterdir():
                file_path.unlink(missing_ok=True)
            branch_root.rmdir()

    def _can_skip_threaded_base_branch(self, branch: ThreadedBuildBranch) -> bool:
        expected_lines = [f"FROM {self.topology.analysis.base_image}", "WORKDIR /app"]
        block_lines = [line.strip() for line in self._node_block(branch.node_id).splitlines() if line.strip()]
        return block_lines == expected_lines

    def _skip_threaded_base_branch(self, branch: ThreadedBuildBranch) -> ThreadedBranchResult:
        self._transition(
            branch.node_id,
            "passed",
            stage="threaded_build",
            reason="Base branch uses the resolved base image directly.",
        )
        self._record_trace(
            "parallel_build_branch_skipped",
            branch_id=branch.branch_id,
            node_id=branch.node_id,
            image_tag=self.topology.analysis.base_image,
            original_image_tag=branch.image_tag,
            reason="base_branch_fast_path",
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=dict(branch.cache_ids or self._build_cache_ids()),
            internal_image=False,
        )
        return ThreadedBranchResult(
            branch_id=branch.branch_id,
            node_id=branch.node_id,
            status="passed",
            image_tag=self.topology.analysis.base_image,
            dockerfile_text=branch.dockerfile_text,
            command="",
            output={"output": "Base branch fast path used resolved base image.", "returncode": 0, "exception_info": ""},
            elapsed=0.0,
        )

    def _run_threaded_dependency_branches(
        self,
        branches: list[ThreadedBuildBranch],
        branch_root: Path,
    ) -> list[ThreadedBranchResult]:
        if not branches:
            return []
        branch_map = {branch.branch_id: branch for branch in branches}
        completed: dict[str, ThreadedBranchResult] = {}
        pending = dict(branch_map)
        while pending:
            ready = [
                branch
                for branch in pending.values()
                if all(dependency in completed for dependency in branch.depends_on)
            ]
            if not ready:
                return [
                    ThreadedBranchResult(
                        branch_id=branch.branch_id,
                        node_id=branch.node_id,
                        status="failed",
                        image_tag=branch.image_tag,
                        dockerfile_text=branch.dockerfile_text,
                        command="",
                        output={"output": "Threaded build dependency cycle detected.", "returncode": 1, "exception_info": ""},
                        error_signature="Threaded build dependency cycle detected",
                        attribution=FailureAttribution(
                            stage="build",
                            error_signature="Threaded build dependency cycle detected",
                            error_type="generic",
                            topology_node_id=branch.node_id,
                            confidence=0.8,
                            reason="No dependency branch could be scheduled.",
                        ),
                    )
                    for branch in pending.values()
                ]
            if len(ready) > 1:
                self.parallel_stages = sorted(branch.branch_id for branch in ready)
            with ThreadPoolExecutor(max_workers=min(self.parallel_build_max_workers, len(ready))) as executor:
                futures = {executor.submit(self._run_threaded_branch, branch, branch_root): branch for branch in ready}
                for future in as_completed(futures):
                    result = future.result()
                    completed[result.branch_id] = result
                    pending.pop(result.branch_id, None)
                    if result.status != "passed":
                        return list(completed.values())
        return [completed[branch.branch_id] for branch in branches]

    def _run_threaded_branch(self, branch: ThreadedBuildBranch, branch_root: Path) -> ThreadedBranchResult:
        dockerfile_path = branch_root / f"{branch.branch_id}.Dockerfile"
        dockerfile_text = self._dockerfile_text_for_branch_build(branch.dockerfile_text)
        branch = branch.model_copy(update={"dockerfile_text": dockerfile_text})
        dockerfile_path.write_text(branch.dockerfile_text, encoding="utf-8")
        command = self._plain_build_command_for_dockerfile(dockerfile_path, branch.image_tag)
        state_before = self.node_states.get(branch.node_id, "planned")
        self._transition(branch.node_id, "ready", stage="threaded_build", reason=f"{branch.branch_id} branch is ready.")
        self._transition(branch.node_id, "running", stage="threaded_build", reason=f"Building {branch.branch_id} branch.")
        self._record_trace(
            "parallel_build_branch_started",
            branch_id=branch.branch_id,
            node_id=branch.node_id,
            image_tag=branch.image_tag,
            base_image_tag=branch.base_image_tag,
            command=command,
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=dict(branch.cache_ids or self._build_cache_ids()),
            internal_image=branch.internal_image,
        )
        started_at = time.perf_counter()
        output = self.env.execute({"command": command}, timeout=self._resolve_command_timeout(command))
        elapsed = time.perf_counter() - started_at
        cache_hit_steps, cache_total_steps = _count_docker_cache_steps(output.get("output", ""))
        returncode = int(output.get("returncode", 0))
        if returncode == 0:
            self._transition(branch.node_id, "passed", stage="threaded_build", reason=f"{branch.branch_id} branch built.")
            result = ThreadedBranchResult(
                branch_id=branch.branch_id,
                node_id=branch.node_id,
                status="passed",
                image_tag=branch.image_tag,
                dockerfile_text=branch.dockerfile_text,
                command=command,
                output=output,
                elapsed=round(elapsed, 4),
                cache_hit_steps=cache_hit_steps,
                cache_total_steps=cache_total_steps,
            )
            self._create_branch_checkpoint(branch, parent_checkpoint_id="", image_tag=branch.image_tag)
        else:
            error_signature = extract_error_signature(output.get("output", ""))
            error_type = classify_error_type(error_signature, output.get("output", ""))
            attribution = attribute_failure_to_node(
                output.get("output", ""),
                stage="build",
                error_type=error_type,
                error_signature=error_signature,
            )
            self._record_retry(attribution, error_signature)
            self._record_trace(
                "parallel_branch_rollback_applied",
                branch_id=branch.branch_id,
                node_id=branch.node_id,
                reason=error_signature,
                parent_checkpoint_id=self._parent_checkpoint_id(branch),
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=dict(branch.cache_ids or self._build_cache_ids()),
            )
            repaired = self._repair_threaded_branch(branch, output, error_signature, attribution, branch_root)
            if repaired is not None:
                result = repaired
                self._transition(
                    branch.node_id,
                    "passed",
                    stage="parallel_repair",
                    reason=f"{branch.branch_id} branch recovered via isolated repair.",
                )
            else:
                self._transition(branch.node_id, "failed", stage="threaded_build", reason=error_signature)
                result = ThreadedBranchResult(
                    branch_id=branch.branch_id,
                    node_id=branch.node_id,
                    status="failed",
                    image_tag=branch.image_tag,
                    dockerfile_text=branch.dockerfile_text,
                    command=command,
                    output=output,
                    elapsed=round(elapsed, 4),
                    error_signature=error_signature,
                    attribution=attribution,
                    cache_hit_steps=cache_hit_steps,
                    cache_total_steps=cache_total_steps,
                )
        self._record_trace(
            "parallel_build_branch_finished",
            branch_id=result.branch_id,
            node_id=result.node_id,
            status=result.status,
            image_tag=result.image_tag,
            returncode=int(result.output.get("returncode", 0)),
            elapsed=result.elapsed,
            error_signature=result.error_signature,
            repaired=result.repaired,
            candidate_id=result.candidate_id,
            state_before=state_before,
            state_after=self.node_states.get(branch.node_id, ""),
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=dict(branch.cache_ids or self._build_cache_ids()),
            internal_image=branch.internal_image,
            cache_hit_steps=result.cache_hit_steps,
            cache_total_steps=result.cache_total_steps,
        )
        return result

    def _repair_threaded_branch(
        self,
        branch: ThreadedBuildBranch,
        output: dict[str, Any],
        error_signature: str,
        attribution: FailureAttribution,
        branch_root: Path,
    ) -> ThreadedBranchResult | None:
        if not self.parallel_repair:
            return None
        if attribution.confidence < 0.7:
            return None
        if attribution.topology_node_id not in {"system_dependency_plan", "python_dependency_plan"}:
            return None
        failed_result = TopologyNodeRunResult(
            node_id=branch.node_id,
            status="failed",
            stage="build",
            command="",
            output=output,
            error_signature=error_signature,
            attribution=attribution,
        )
        candidates = self._repair_candidates_for_failure(failed_result, branch.dockerfile_text)[
            : self.parallel_repair_max_branches
        ]
        if len(candidates) < 1:
            return None
        self.parallel_repair_used = True
        self.repair_branches_tried = max(self.repair_branches_tried, len(candidates))
        self._record_trace(
            "scheduler_parallel_repair",
            node_id=attribution.topology_node_id,
            stage="build",
            branches_tried=len(candidates),
            candidate_ids=[candidate["candidate_id"] for candidate in candidates],
            branch_id=branch.branch_id,
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=self._build_cache_ids(),
        )
        successful: ThreadedBranchResult | None = None
        candidate_results: list[ThreadedBranchResult] = []
        with ThreadPoolExecutor(max_workers=min(self.parallel_repair_max_branches, len(candidates))) as executor:
            futures = {
                executor.submit(self._run_threaded_repair_candidate, branch, candidate, branch_root): candidate
                for candidate in candidates
            }
            for future in as_completed(futures):
                result = future.result()
                candidate_results.append(result)
                if successful is None and result.status == "passed":
                    successful = result
        self.parallel_repair_build_count += max(0, len(candidate_results) - 1)
        cleanup_images = {
            candidate_result.candidate_id or candidate_result.image_tag: candidate_result.image_tag
            for candidate_result in candidate_results
            if successful is None or candidate_result.image_tag != successful.image_tag
        }
        if cleanup_images:
            cleaned, failed = self._cleanup_images_parallel(cleanup_images)
            self._record_trace(
                "parallel_repair_loser_images_cleaned",
                branch_id=branch.branch_id,
                cleaned_internal_branch_images=cleaned,
                failed_internal_branch_images=failed,
            )
        if successful is None:
            return None
        self._record_trace(
            "parallel_repair_branch_won",
            branch_id=branch.branch_id,
            node_id=branch.node_id,
            candidate_id=successful.candidate_id,
            image_tag=successful.image_tag,
        )
        checkpoint_branch = branch.model_copy(
            update={"dockerfile_text": successful.dockerfile_text, "image_tag": successful.image_tag}
        )
        self._create_branch_checkpoint(
            checkpoint_branch,
            parent_checkpoint_id=self._parent_checkpoint_id(branch),
            image_tag=successful.image_tag,
        )
        return successful

    def _run_threaded_repair_candidate(
        self,
        branch: ThreadedBuildBranch,
        candidate: dict[str, Any],
        branch_root: Path,
    ) -> ThreadedBranchResult:
        candidate_id = str(candidate["candidate_id"])
        candidate_image = f"{self.image_name}-{branch.branch_id}-{candidate_id}"
        dockerfile_text = self._dockerfile_text_for_branch_build(str(candidate["dockerfile_text"]))
        dockerfile_path = branch_root / f"{branch.branch_id}-{candidate_id}.Dockerfile"
        dockerfile_path.write_text(dockerfile_text, encoding="utf-8")
        command = self._plain_build_command_for_dockerfile(dockerfile_path, candidate_image)
        self._record_trace(
            "parallel_repair_branch_started",
            branch_id=branch.branch_id,
            node_id=branch.node_id,
            candidate_id=candidate_id,
            image_tag=candidate_image,
            command=command,
            parent_checkpoint_id=self._parent_checkpoint_id(branch),
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=dict(branch.cache_ids or self._build_cache_ids()),
            internal_image=True,
        )
        started_at = time.perf_counter()
        output = self.env.execute({"command": command}, timeout=self._resolve_command_timeout(command))
        elapsed = time.perf_counter() - started_at
        returncode = int(output.get("returncode", 0))
        cache_hit_steps, cache_total_steps = _count_docker_cache_steps(output.get("output", ""))
        error_signature = "" if returncode == 0 else extract_error_signature(output.get("output", ""))
        attribution = None
        if returncode != 0:
            error_type = classify_error_type(error_signature, output.get("output", ""))
            attribution = attribute_failure_to_node(
                output.get("output", ""),
                stage="build",
                error_type=error_type,
                error_signature=error_signature,
            )
        result = ThreadedBranchResult(
            branch_id=branch.branch_id,
            node_id=branch.node_id,
            status="passed" if returncode == 0 else "failed",
            image_tag=candidate_image,
            dockerfile_text=dockerfile_text,
            command=command,
            output=output,
            elapsed=round(elapsed, 4),
            error_signature=error_signature,
            attribution=attribution,
            candidate_id=candidate_id,
            repaired=returncode == 0,
            cache_hit_steps=cache_hit_steps,
            cache_total_steps=cache_total_steps,
        )
        self._record_trace(
            "parallel_build_branch_finished",
            branch_id=branch.branch_id,
            node_id=branch.node_id,
            status=result.status,
            image_tag=candidate_image,
            returncode=returncode,
            elapsed=result.elapsed,
            error_signature=error_signature,
            repaired=result.repaired,
            candidate_id=candidate_id,
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=dict(branch.cache_ids or self._build_cache_ids()),
            internal_image=True,
            cache_hit_steps=result.cache_hit_steps,
            cache_total_steps=result.cache_total_steps,
        )
        if returncode != 0:
            self._record_trace(
                "parallel_branch_rollback_applied",
                branch_id=branch.branch_id,
                node_id=branch.node_id,
                candidate_id=candidate_id,
                reason=error_signature,
                parent_checkpoint_id=self._parent_checkpoint_id(branch),
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=dict(branch.cache_ids or self._build_cache_ids()),
            )
        return result

    def _threaded_repair_runtime(self, failed_result: TopologyNodeRunResult) -> TopologyNodeRunResult | None:
        if not self.parallel_repair or failed_result.attribution is None:
            return None
        branch_id = _branch_id_for_node(failed_result.attribution.topology_node_id)
        if not branch_id:
            return None
        current = self.threaded_branch_results.get(branch_id)
        if current is None:
            return None
        branch = ThreadedBuildBranch(
            branch_id=branch_id,
            node_id=current.node_id,
            image_tag=current.image_tag,
            base_image_tag=self._parent_image_for_branch(branch_id),
            dockerfile_text=current.dockerfile_text,
            depends_on=[],
            cache_ids=self._build_cache_ids(),
        )
        branch_root = Path(tempfile.mkdtemp(prefix="topomen-threaded-runtime-repair-"))
        try:
            repaired = self._repair_threaded_branch(
                branch,
                failed_result.output,
                failed_result.error_signature,
                failed_result.attribution,
                branch_root,
            )
            if repaired is None:
                return None
            self.threaded_branch_results[branch_id] = repaired
            final_dockerfile_text = self._generate_threaded_merge_dockerfile()
            dockerfile_path = self._dockerfile_path()
            dockerfile_path.write_text(final_dockerfile_text, encoding="utf-8")
            merge_build = self._execute_command(
                node_id="build_validation",
                stage="build",
                command=self._plain_build_command_for_dockerfile(dockerfile_path, self.image_name),
            )
            if merge_build.status != "passed":
                return None
            self.parallel_repair_build_count += 1
            run_command = self._run_command()
            self.runtime_command_count += 1
            run_started = time.perf_counter()
            output = self.env.execute({"command": run_command}, timeout=self._resolve_command_timeout(run_command))
            elapsed = time.perf_counter() - run_started
            if int(output.get("returncode", 0)) != 0:
                return None
            self.parallel_repair_run_count += 1
            self._transition(
                "runtime_validation",
                "passed",
                stage="parallel_repair",
                reason="Runtime validation recovered after isolated threaded branch repair.",
            )
            self._record_trace(
                "parallel_merge_succeeded",
                image_name=self.image_name,
                dockerfile_path=str(dockerfile_path),
                branch_images=self._internal_branch_images(),
                internal_branch_images=self._internal_branch_images(),
                final_image=self.image_name,
                public_images=[self.image_name],
                build_cache_enabled=self.build_cache_plan.enabled,
                build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
                cache_ids=self._build_cache_ids(),
            )
            return TopologyNodeRunResult(
                node_id="runtime_validation",
                status="passed",
                stage="run",
                command=run_command,
                returncode=0,
                elapsed=round(elapsed, 4),
                output=output,
                state_before=failed_result.state_after or "failed",
                state_after=self.node_states.get("runtime_validation", "passed"),
                runtime_validation_mode=failed_result.runtime_validation_mode,
                command_source="threaded_parallel_repair",
                runtime_attempt_index=failed_result.runtime_attempt_index,
                runtime_candidate_id=repaired.candidate_id,
                fallback_trigger_reason="parallel_repair",
            )
        finally:
            for file_path in branch_root.iterdir():
                file_path.unlink(missing_ok=True)
            branch_root.rmdir()

    def _consume_threaded_node_results(self) -> list[TopologyNodeRunResult]:
        results = list(self._threaded_node_results)
        self._threaded_node_results = []
        return results

    def _threaded_base_branch(self) -> ThreadedBuildBranch:
        node_id = "base_image_selection"
        image_tag = f"{self.image_name}-threaded-base"
        block = self._node_block(node_id)
        return ThreadedBuildBranch(
            branch_id="base",
            node_id=node_id,
            image_tag=image_tag,
            base_image_tag=self.topology.analysis.base_image,
            dockerfile_text=self._threaded_dockerfile_text(_wrap_threaded_block(node_id, block)),
            cache_ids=self._build_cache_ids(),
        )

    def _threaded_dependency_branches(self, base_image_tag: str) -> list[ThreadedBuildBranch]:
        branches: list[ThreadedBuildBranch] = []
        system_needed = self._node_has_work("system_dependency_plan")
        python_needed = self._node_has_work("python_dependency_plan")
        system_image = f"{self.image_name}-threaded-system-deps"
        if system_needed:
            system_block = "\n".join(
                [
                    f"FROM {base_image_tag} AS system-deps",
                    "WORKDIR /app",
                    self._node_block("system_dependency_plan"),
                ]
            )
            branches.append(
                ThreadedBuildBranch(
                    branch_id="system-deps",
                    node_id="system_dependency_plan",
                    image_tag=system_image,
                    base_image_tag=base_image_tag,
                    dockerfile_text=self._threaded_dockerfile_text(
                        _wrap_threaded_block("system_dependency_plan", system_block)
                    ),
                    depends_on=[],
                    cache_ids=self._build_cache_ids(),
                )
            )
        if python_needed:
            python_depends_system = self._python_branch_depends_on_system()
            python_base = system_image if python_depends_system and system_needed else base_image_tag
            python_block = "\n".join(
                [
                    f"FROM {python_base} AS python-deps",
                    "WORKDIR /app",
                    self._node_block("python_dependency_plan"),
                ]
            )
            branches.append(
                ThreadedBuildBranch(
                    branch_id="python-deps",
                    node_id="python_dependency_plan",
                    image_tag=f"{self.image_name}-threaded-python-deps",
                    base_image_tag=python_base,
                    dockerfile_text=self._threaded_dockerfile_text(
                        _wrap_threaded_block("python_dependency_plan", python_block)
                    ),
                    depends_on=["system-deps"] if python_depends_system and system_needed else [],
                    cache_ids=self._build_cache_ids(),
                )
            )
        return branches

    def _node_block(self, node_id: str) -> str:
        node = self.topology.node_map().get(node_id)
        if node is None or not node.dockerfile_block.strip():
            return f"# No Dockerfile work for {node_id}."
        return node.dockerfile_block.strip()

    def _node_has_work(self, node_id: str) -> bool:
        block = self._node_block(node_id).lower()
        no_work_markers = (
            "no system packages inferred",
            "no python dependency file detected",
            "pyproject.toml detected; install the project after copy . . if needed.",
            "no dockerfile work",
        )
        if any(marker in block for marker in no_work_markers) and "run " not in block and "copy " not in block:
            return False
        return "run " in block or "copy " in block

    def _threaded_dockerfile_text(self, dockerfile_text: str) -> str:
        if not self._build_cache_enabled():
            return dockerfile_text
        syntax_line = self._buildkit_syntax_line()
        body = re.sub(r"^# syntax=[^\r\n]+(?:\r?\n){1,2}", "", dockerfile_text, count=1)
        if body.startswith("# TOPOMEN_BUILD_CACHE"):
            return f"{syntax_line}\n\n" + body
        cache = self._build_cache_ids()
        cache_lines = [
            f"# TOPOMEN_BUILD_CACHE namespace={cache.get('namespace', '')}",
            f"# TOPOMEN_BUILD_CACHE dependency_hash={cache.get('dependency_hash', '')}",
            f"# TOPOMEN_BUILD_CACHE apt_cache_id={cache.get('apt_cache_id', '')}",
            f"# TOPOMEN_BUILD_CACHE apt_lists_cache_id={cache.get('apt_lists_cache_id', '')}",
            f"# TOPOMEN_BUILD_CACHE pip_cache_id={cache.get('pip_cache_id', '')}",
        ]
        return f"{syntax_line}\n\n" + "\n".join(cache_lines) + "\n\n" + body

    def _buildkit_syntax_line(self) -> str:
        frontend_image = self.build_cache_plan.buildkit_frontend_image.strip() or "docker/dockerfile:1.7"
        return f"# syntax={frontend_image}"

    def _python_branch_depends_on_system(self) -> bool:
        native_build_packages = {"build-essential", "gcc", "g++", "libpq-dev", "libffi-dev", "libcurl4-openssl-dev"}
        return any(package in native_build_packages for package in self.topology.analysis.system_packages)

    def _generate_threaded_merge_dockerfile(self) -> str:
        base_block = self._node_block("base_image_selection")
        base_image = self.threaded_branch_results.get("base")
        system_image = self.threaded_branch_results.get("system-deps")
        python_image = self.threaded_branch_results.get("python-deps")
        blocks = [_wrap_threaded_block("base_image_selection", base_block)]

        if system_image is not None:
            system_block = "\n".join(
                [
                    f"FROM {system_image.image_tag} AS system-deps",
                    "# Imported from TopoMen threaded system dependency branch.",
                ]
            )
        else:
            parent = base_image.image_tag if base_image is not None else self.topology.analysis.base_image
            system_block = "\n".join([f"FROM {parent} AS system-deps", "# No threaded system dependency branch required."])
        blocks.append(_wrap_threaded_block("system_dependency_plan", system_block))

        if python_image is not None:
            python_block = "\n".join(
                [
                    f"FROM {python_image.image_tag} AS python-deps",
                    "# Imported from TopoMen threaded Python dependency branch.",
                ]
            )
        else:
            parent = system_image.image_tag if system_image is not None else (base_image.image_tag if base_image is not None else self.topology.analysis.base_image)
            python_block = "\n".join([f"FROM {parent} AS python-deps", "# No threaded Python dependency branch required."])
        blocks.append(_wrap_threaded_block("python_dependency_plan", python_block))

        if system_image is not None and python_image is not None and not self._python_branch_depends_on_system():
            final_base = "system-deps"
        elif python_image is not None:
            final_base = "python-deps"
        elif system_image is not None:
            final_base = "system-deps"
        else:
            final_base = "python-deps"
        file_copy_lines = [f"FROM {final_base} AS final"]
        if system_image is not None and python_image is not None and not self._python_branch_depends_on_system():
            file_copy_lines.append("COPY --from=python-deps /usr/local /usr/local")
        file_copy_lines.append("COPY . .")
        blocks.append(_wrap_threaded_block("file_copy_plan", "\n".join(file_copy_lines)))
        blocks.append(_wrap_threaded_block("runtime_validation", self._node_block("runtime_validation")))
        return self._threaded_dockerfile_text("\n\n".join(blocks).rstrip() + "\n")

    def _threaded_node_result(self, result: ThreadedBranchResult) -> TopologyNodeRunResult:
        return TopologyNodeRunResult(
            node_id=result.node_id,
            status=result.status,
            stage="build",
            command=result.command,
            returncode=int(result.output.get("returncode", 0)),
            elapsed=result.elapsed,
            output=result.output,
            error_signature=result.error_signature,
            attribution=result.attribution,
            state_after=self.node_states.get(result.node_id, ""),
            runtime_candidate_id=result.candidate_id,
            fallback_trigger_reason="threaded_repair" if result.repaired else "",
            cache_hit_steps=result.cache_hit_steps,
            cache_total_steps=result.cache_total_steps,
        )

    def _threaded_failure_result(self, result: ThreadedBranchResult) -> TopologyNodeRunResult:
        return TopologyNodeRunResult(
            node_id="build_validation",
            status="failed",
            stage="build",
            command=result.command,
            returncode=int(result.output.get("returncode", 1)),
            elapsed=result.elapsed,
            output=result.output,
            error_signature=result.error_signature,
            attribution=result.attribution,
            state_after=self.node_states.get("build_validation", "planned"),
            runtime_candidate_id=result.candidate_id,
            fallback_trigger_reason="threaded_branch_failure",
            cache_hit_steps=result.cache_hit_steps,
            cache_total_steps=result.cache_total_steps,
        )

    def _plain_build_command_for_dockerfile(self, dockerfile_path: Path, image_name: str) -> str:
        use_classic_builder = self._uses_internal_parent_image(dockerfile_path.read_text(encoding="utf-8"))
        if self._build_cache_enabled() and not use_classic_builder:
            return (
                f'docker buildx build --load --progress=plain -f "{dockerfile_path}" '
                f'-t {image_name} "{self.project_path}" 2>&1'
            )
        if use_classic_builder:
            build_command = f'docker build -f "{dockerfile_path}" -t {image_name} "{self.project_path}" 2>&1'
            return self._classic_docker_build_command(build_command)
        return f'docker build -f "{dockerfile_path}" -t {image_name} "{self.project_path}" 2>&1'

    def _dockerfile_text_for_branch_build(self, dockerfile_text: str) -> str:
        if self._uses_internal_parent_image(dockerfile_text):
            return _classic_compatible_dockerfile_text(dockerfile_text)
        return dockerfile_text

    def _uses_internal_parent_image(self, dockerfile_text: str) -> bool:
        image_prefix = re.escape(f"{self.image_name}-")
        return re.search(rf"(?m)^FROM\s+{image_prefix}[^\s]*", dockerfile_text) is not None

    def _classic_docker_build_command(self, build_command: str) -> str:
        if os.name == "nt":
            return f"set DOCKER_BUILDKIT=0&& {build_command}"
        return f"DOCKER_BUILDKIT=0 {build_command}"

    def _create_branch_checkpoint(
        self,
        branch: ThreadedBuildBranch,
        *,
        parent_checkpoint_id: str,
        image_tag: str,
    ) -> None:
        checkpoint_id = f"branch-{branch.branch_id}-{len(self.threaded_branch_checkpoints) + 1}"
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "branch_id": branch.branch_id,
            "node_id": branch.node_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "dockerfile_text": branch.dockerfile_text,
            "image_tag": image_tag,
            "winner_image_tag": image_tag,
            "base_image_tag": branch.base_image_tag,
            "cache_ids": dict(branch.cache_ids or self._build_cache_ids()),
            "build_cache_enabled": self.build_cache_plan.enabled,
            "build_cache_reuse_existing": self.build_cache_plan.reuse_existing,
            "build_cache_id_scope": self.build_cache_plan.cache_id_scope,
            "internal_image": branch.internal_image,
        }
        self.threaded_branch_checkpoints.append(checkpoint)
        self.checkpoints.append(checkpoint)
        self._record_trace(
            "checkpoint_created",
            checkpoint_id=checkpoint_id,
            branch_id=branch.branch_id,
            node_id=branch.node_id,
            trigger_node_id=branch.node_id,
            cache_ids=dict(branch.cache_ids or self._build_cache_ids()),
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            build_cache_id_scope=self.build_cache_plan.cache_id_scope,
            internal_image=branch.internal_image,
        )

    def _parent_checkpoint_id(self, branch: ThreadedBuildBranch) -> str:
        for checkpoint in reversed(self.threaded_branch_checkpoints):
            if checkpoint.get("branch_id") == branch.branch_id:
                return str(checkpoint.get("checkpoint_id", ""))
        return "checkpoint-guard"

    def _parent_image_for_branch(self, branch_id: str) -> str:
        if branch_id == "python-deps" and self._python_branch_depends_on_system():
            system = self.threaded_branch_results.get("system-deps")
            if system is not None:
                return system.image_tag
        base = self.threaded_branch_results.get("base")
        return base.image_tag if base is not None else self.topology.analysis.base_image

    def _parallel_repair(self, failed_result: TopologyNodeRunResult) -> TopologyNodeRunResult | None:
        if not self.parallel_repair or failed_result.attribution is None:
            return None
        attribution = failed_result.attribution
        if attribution.confidence < 0.7:
            return None
        if attribution.topology_node_id not in {"system_dependency_plan", "python_dependency_plan"}:
            return None
        dockerfile_path = self._dockerfile_path()
        if not dockerfile_path.exists():
            return None
        original_dockerfile = dockerfile_path.read_text(encoding="utf-8")
        candidates = self._repair_candidates_for_failure(failed_result, original_dockerfile)
        candidates = candidates[: self.parallel_repair_max_branches]
        if len(candidates) < 2:
            return None

        self.parallel_repair_used = True
        self.repair_branches_tried = max(self.repair_branches_tried, len(candidates))
        self._record_trace(
            "scheduler_parallel_repair",
            node_id=attribution.topology_node_id,
            stage=failed_result.stage,
            branches_tried=len(candidates),
            candidate_ids=[candidate["candidate_id"] for candidate in candidates],
            build_cache_enabled=self.build_cache_plan.enabled,
            build_cache_reuse_existing=self.build_cache_plan.reuse_existing,
            cache_ids=self._build_cache_ids(),
        )

        branch_root = Path(tempfile.mkdtemp(prefix="topomen-parallel-repair-"))
        try:
            with ThreadPoolExecutor(max_workers=min(3, len(candidates))) as executor:
                futures = [
                    executor.submit(self._run_repair_branch, failed_result, candidate, branch_root)
                    for candidate in candidates
                ]
                successful: dict[str, Any] | None = None
                for future in as_completed(futures):
                    branch_result = future.result()
                    if successful is None and branch_result.get("success"):
                        successful = branch_result
                if successful is None:
                    return None

                winning_dockerfile = str(successful["dockerfile_text"])
                dockerfile_path.write_text(winning_dockerfile, encoding="utf-8")
                branch_image = str(successful["branch_image"])
                tag_command = f"docker tag {branch_image} {self.image_name}"
                self.env.execute({"command": tag_command}, timeout=self._resolve_command_timeout(tag_command))
                self.parallel_repair_build_count += int(successful.get("build_commands", 0))
                self.parallel_repair_run_count += int(successful.get("run_commands", 0))
                output = dict(successful["output"])
                command = str(successful["command"])
                if failed_result.stage == "build":
                    self._transition("build_validation", "ready", stage="parallel_repair", reason="Retrying winning repair branch.")
                    self._transition("build_validation", "running", stage="parallel_repair", reason="Executing winning repair branch.")
                    self._transition("build_validation", "passed", stage="parallel_repair", reason="Winning repair branch recovered build validation.")
                else:
                    self._transition("runtime_validation", "ready", stage="parallel_repair", reason="Retrying winning repair branch.")
                    self._transition("runtime_validation", "running", stage="parallel_repair", reason="Executing winning repair branch.")
                    self._transition("runtime_validation", "passed", stage="parallel_repair", reason="Winning repair branch recovered runtime validation.")
                result = TopologyNodeRunResult(
                    node_id=failed_result.node_id,
                    status="passed",
                    stage=failed_result.stage,
                    command=command,
                    returncode=int(output.get("returncode", 0)),
                    elapsed=float(successful.get("elapsed", 0.0)),
                    output=output,
                    state_before=failed_result.state_after or "failed",
                    state_after=self.node_states.get(failed_result.node_id, "passed"),
                    runtime_validation_mode=failed_result.runtime_validation_mode,
                    command_source="parallel_repair_branch",
                    runtime_attempt_index=failed_result.runtime_attempt_index,
                    runtime_candidate_id=str(successful["candidate_id"]),
                    fallback_trigger_reason="parallel_repair",
                )
                self._record_trace(
                    "scheduler_parallel_repair_succeeded",
                    node_id=attribution.topology_node_id,
                    stage=failed_result.stage,
                    candidate_id=str(successful["candidate_id"]),
                    branches_tried=len(candidates),
                )
                return result
        finally:
            cleanup_images = {
                str(candidate["candidate_id"]): f"{self.image_name}-{candidate['candidate_id']}"
                for candidate in candidates
            }
            cleaned, failed = self._cleanup_images_parallel(cleanup_images)
            self._record_trace(
                "parallel_repair_loser_images_cleaned",
                cleaned_internal_branch_images=cleaned,
                failed_internal_branch_images=failed,
            )
            for file_path in branch_root.iterdir():
                file_path.unlink(missing_ok=True)
            branch_root.rmdir()

    def _run_preflight(self) -> tuple[bool, str]:
        checks = [
            ("docker_cli", "docker --version"),
            ("docker_daemon", 'docker info --format "{{json .ServerVersion}}"'),
        ]
        if self.topology.multistage or self._build_cache_enabled():
            checks.append(("docker_buildx", "docker buildx version"))
        for capability, command in checks:
            output = self.env.execute({"command": command}, timeout=self.command_timeout_seconds)
            returncode = int(output.get("returncode", 0))
            self._record_trace("preflight_check", capability=capability, returncode=returncode)
            if returncode != 0:
                reason = f"Preflight blocked: {capability} unavailable."
                self._record_trace(
                    "preflight_blocked",
                    capability=capability,
                    blocking_reason=reason,
                    command=command,
                )
                return False, reason
        try:
            dockerfile_path = self._dockerfile_path()
            dockerfile_path.parent.mkdir(parents=True, exist_ok=True)
            probe_path = dockerfile_path.parent / ".topomen_preflight_write_probe"
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink(missing_ok=True)
        except OSError as exc:
            reason = f"Preflight blocked: workspace is not writable ({type(exc).__name__})."
            self._record_trace(
                "preflight_blocked",
                capability="filesystem_write",
                blocking_reason=reason,
            )
            return False, reason
        return True, ""

    def _schedule_node_order(self) -> list[str]:
        order = [*EXECUTABLE_DOCKERFILE_NODE_ORDER, *VERIFICATION_NODE_ORDER]
        return list(dict.fromkeys(order))

    def _downstream_nodes(self, failed_node_id: str) -> list[str]:
        if failed_node_id == "dockerfile_generation":
            return ["build_validation", "runtime_validation", "dockerfile_finalization"]
        if failed_node_id == "build_validation":
            return ["runtime_validation", "dockerfile_finalization"]
        if failed_node_id == "runtime_validation":
            return ["dockerfile_finalization"]
        return []

    def _executed_nodes(self, node_results: list[TopologyNodeRunResult]) -> list[str]:
        return [result.node_id for result in node_results if result.status != "skipped"]

    def _record_trace(self, event_type: str, **payload) -> None:
        if self.trace_callback is not None:
            self.trace_callback(event_type, payload)

    def _internal_branch_images(self) -> dict[str, str]:
        return {
            branch_id: result.image_tag
            for branch_id, result in sorted(self.threaded_branch_results.items())
            if result.image_tag != self.topology.analysis.base_image
        }

    def _cleanup_internal_branch_images(self, *, reason: str) -> None:
        images = self._internal_branch_images()
        if not images or self._internal_branch_images_cleaned:
            return
        cleaned, failed = self._cleanup_images_parallel(
            {branch_id: image_tag for branch_id, image_tag in images.items() if image_tag != self.image_name}
        )
        self._internal_branch_images_cleaned = True
        self._record_trace(
            "internal_branch_images_cleaned",
            reason=reason,
            final_image=self.image_name,
            public_images=[self.image_name],
            cleaned_internal_branch_images=cleaned,
            failed_internal_branch_images=failed,
        )

    def _cleanup_images_parallel(self, images: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
        if not images:
            return {}, {}
        cleaned: dict[str, str] = {}
        failed: dict[str, str] = {}

        def remove_image(item: tuple[str, str]) -> tuple[str, str, int]:
            image_id, image_tag = item
            output = self.env.execute({"command": f"docker image rm -f {image_tag}"}, timeout=self.command_timeout_seconds)
            return image_id, image_tag, int(output.get("returncode", 0))

        with ThreadPoolExecutor(max_workers=min(3, len(images))) as executor:
            futures = [executor.submit(remove_image, item) for item in images.items()]
            for future in as_completed(futures):
                image_id, image_tag, returncode = future.result()
                if returncode == 0:
                    cleaned[image_id] = image_tag
                else:
                    failed[image_id] = image_tag
        return dict(sorted(cleaned.items())), dict(sorted(failed.items()))

    def _build_cache_enabled(self) -> bool:
        return bool(self.build_cache_plan.enabled)

    def _build_cache_ids(self) -> dict[str, str]:
        if not self.build_cache_plan.enabled:
            return {}
        return {
            "apt_cache_id": self.build_cache_plan.apt_cache_id,
            "apt_lists_cache_id": self.build_cache_plan.apt_lists_cache_id,
            "pip_cache_id": self.build_cache_plan.pip_cache_id,
            "dependency_hash": self.build_cache_plan.dependency_hash,
            "namespace": self.build_cache_plan.namespace,
            "cache_id_scope": self.build_cache_plan.cache_id_scope,
        }

    def _resolve_command_timeout(self, command: str) -> int:
        stage = _docker_command_stage(command)
        if stage == "build":
            return self.docker_build_timeout_seconds
        if stage == "run":
            return self.docker_run_timeout_seconds
        return self.command_timeout_seconds

    def _dockerfile_path(self) -> Path:
        return self.project_path / "Dockerfile"

    def _create_checkpoint(self, checkpoint_id: str, *, trigger_node_id: str) -> None:
        dockerfile_path = self._dockerfile_path()
        dockerfile_text = dockerfile_path.read_text(encoding="utf-8") if dockerfile_path.exists() else ""
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "trigger_node_id": trigger_node_id,
            "node_states": dict(self.node_states),
            "state_transitions_count": len(self.state_transitions),
            "runtime_validation_mode": self.runtime_validation_plan.mode,
            "dockerfile_text": dockerfile_text,
            "build_cache_enabled": self.build_cache_plan.enabled,
            "cache_ids": self._build_cache_ids(),
        }
        self.checkpoints.append(checkpoint)
        self._record_trace("checkpoint_created", checkpoint_id=checkpoint_id, trigger_node_id=trigger_node_id)

    def _rollback_to_latest_checkpoint(self, *, reason: str) -> None:
        if not self.checkpoints:
            self._record_trace("rollback_skipped", reason=reason)
            self.rollback_events.append({"reason": reason, "status": "skipped"})
            return
        checkpoint = self.checkpoints[-1]
        dockerfile_path = self._dockerfile_path()
        dockerfile_path.write_text(str(checkpoint.get("dockerfile_text", "")), encoding="utf-8")
        self.restored_checkpoint_id = str(checkpoint.get("checkpoint_id", ""))
        self.rollback_events.append(
            {"reason": reason, "status": "applied", "checkpoint_id": self.restored_checkpoint_id}
        )
        self._record_trace(
            "rollback_applied",
            reason=reason,
            checkpoint_id=self.restored_checkpoint_id,
            trigger_node_id=str(checkpoint.get("trigger_node_id", "")),
        )

    def _repair_candidates_for_failure(
        self,
        failed_result: TopologyNodeRunResult,
        original_dockerfile: str,
    ) -> list[dict[str, Any]]:
        attribution = failed_result.attribution
        if attribution is None:
            return []
        if attribution.topology_node_id == "system_dependency_plan":
            packages = _system_repair_packages(self.topology, failed_result.error_signature, failed_result.output.get("output", ""))
            return [
                {
                    "candidate_id": f"system-{index}",
                    "dockerfile_text": _patch_system_dependency_block(original_dockerfile, package),
                    "package": package,
                }
                for index, package in enumerate(packages[:3], start=1)
            ]
        if attribution.topology_node_id == "python_dependency_plan":
            packages = _python_repair_packages(self.topology, failed_result.error_signature, failed_result.output.get("output", ""))
            return [
                {
                    "candidate_id": f"python-{index}",
                    "dockerfile_text": _patch_python_dependency_block(original_dockerfile, package),
                    "package": package,
                }
                for index, package in enumerate(packages[:3], start=1)
            ]
        return []

    def _run_repair_branch(
        self,
        failed_result: TopologyNodeRunResult,
        candidate: dict[str, Any],
        branch_root: Path,
    ) -> dict[str, Any]:
        candidate_id = str(candidate["candidate_id"])
        dockerfile_path = branch_root / f"{candidate_id}.Dockerfile"
        dockerfile_text = str(candidate["dockerfile_text"])
        dockerfile_path.write_text(dockerfile_text, encoding="utf-8")
        branch_image = f"{self.image_name}-{candidate_id}"
        build_command = self._build_command_for_dockerfile(dockerfile_path, branch_image)
        build_started = time.perf_counter()
        build_output = self.env.execute({"command": build_command}, timeout=self._resolve_command_timeout(build_command))
        build_elapsed = time.perf_counter() - build_started
        if int(build_output.get("returncode", 0)) != 0:
            return {
                "success": False,
                "candidate_id": candidate_id,
                "dockerfile_text": dockerfile_text,
                "branch_image": branch_image,
                "output": build_output,
                "command": build_command,
                "elapsed": round(build_elapsed, 4),
                "build_commands": 1,
                "run_commands": 0,
            }

        if failed_result.stage == "build":
            return {
                "success": True,
                "candidate_id": candidate_id,
                "dockerfile_text": dockerfile_text,
                "branch_image": branch_image,
                "output": build_output,
                "command": build_command,
                "elapsed": round(build_elapsed, 4),
                "build_commands": 1,
                "run_commands": 0,
            }

        run_command = self._run_command_for_image(branch_image)
        run_started = time.perf_counter()
        run_output = self.env.execute({"command": run_command}, timeout=self._resolve_command_timeout(run_command))
        run_elapsed = time.perf_counter() - run_started
        success = int(run_output.get("returncode", 0)) == 0
        return {
            "success": success,
            "candidate_id": candidate_id,
            "dockerfile_text": dockerfile_text,
            "branch_image": branch_image,
            "output": run_output,
            "command": run_command,
            "elapsed": round(build_elapsed + run_elapsed, 4),
            "build_commands": 1,
            "run_commands": 1,
        }

def _extract_buildkit_stage_windows(build_output: str) -> dict[str, dict[str, int]]:
    stage_windows: dict[str, dict[str, int]] = {}
    for line_index, line in enumerate(build_output.splitlines(), start=1):
        match = re.search(r"\[(?P<stage>[a-zA-Z0-9_.-]+)(?:\s+\d+/\d+)?\]", line)
        if not match:
            continue
        stage = match.group("stage")
        if stage in {"internal"}:
            continue
        window = stage_windows.setdefault(stage, {"first_line": line_index, "last_line": line_index})
        window["last_line"] = line_index
    return stage_windows


def _overlapping_stage_names(stage_windows: dict[str, dict[str, int]]) -> list[str]:
    overlapping: set[str] = set()
    items = sorted(stage_windows.items())
    for index, (left_stage, left_window) in enumerate(items):
        for right_stage, right_window in items[index + 1 :]:
            if left_window["first_line"] <= right_window["last_line"] and right_window["first_line"] <= left_window["last_line"]:
                overlapping.add(left_stage)
                overlapping.add(right_stage)
    return sorted(overlapping)


def _system_repair_packages(topology: BuildTopology, error_signature: str, output: str) -> list[str]:
    analysis = topology.analysis
    normalized = f"{error_signature}\n{output}".lower()
    candidates: list[str] = []
    heuristic_map = {
        "ffmpeg": "ffmpeg",
        "tesseract": "tesseract-ocr",
        "zbar": "libzbar0",
        "magic": "libmagic1",
        "libgl": "libgl1",
        "gcc": "gcc",
        "g++": "g++",
        "curl-config": "libcurl4-openssl-dev",
        "pg-config": "libpq-dev",
        "portaudio": "portaudio19-dev",
        "pyaudio": "portaudio19-dev",
    }
    for marker, package in heuristic_map.items():
        if marker in normalized:
            candidates.append(package)
    if "missing_compiler" in normalized or "command 'gcc' failed" in normalized:
        candidates.extend(["build-essential", "gcc", "g++"])
    candidates.extend(analysis.memory_apt_packages)
    candidates.extend(analysis.pattern_apt_packages)
    candidates.extend(analysis.system_packages)
    return _dedupe_strings([normalize_system_package_name(package) for package in candidates])


def _python_repair_packages(topology: BuildTopology, error_signature: str, output: str) -> list[str]:
    analysis = topology.analysis
    normalized = f"{error_signature}\n{output}"
    candidates: list[str] = []
    module_match = re.search(r"No module named ['\"](?P<module>[^'\"]+)['\"]", normalized)
    if module_match:
        module_name = module_match.group("module").strip().lower().replace("_", "-")
        candidates.append(IMPORT_TO_DEPENDENCY.get(module_name, module_name))
    candidates.extend(analysis.memory_python_packages)
    candidates.extend(analysis.pattern_python_packages)
    candidates.extend(analysis.missing_python_packages)
    return _dedupe_strings(candidates)


def _patch_system_dependency_block(dockerfile_text: str, package: str) -> str:
    block = _extract_topology_block(dockerfile_text, "system_dependency_plan")
    if package in block:
        return dockerfile_text
    cache_plan = _cache_plan_from_dockerfile(dockerfile_text)
    if "apt-get install -y --no-install-recommends" in block:
        updated = re.sub(
            r"(apt-get install -y --no-install-recommends\s+)([^\n&]+)",
            lambda match: f"{match.group(1)}{match.group(2).strip()} {package}",
            block,
            count=1,
        )
    elif "No system packages inferred" in block:
        updated = block.replace("# No system packages inferred for this topology.", _system_repair_block(package, cache_plan))
    else:
        updated = block + "\n" + _system_repair_block(package, cache_plan)
    return _replace_topology_block(dockerfile_text, "system_dependency_plan", updated)


def _patch_python_dependency_block(dockerfile_text: str, package: str) -> str:
    block = _extract_topology_block(dockerfile_text, "python_dependency_plan")
    if re.search(rf"\bpip install[^\n]*\b{re.escape(package)}\b", block):
        return dockerfile_text
    cache_plan = _cache_plan_from_dockerfile(dockerfile_text)
    pip_install = "pip install" if cache_plan.enabled else "pip install --no-cache-dir"
    if "pip install" in block:
        install_lines = block.splitlines()
        for index in range(len(install_lines) - 1, -1, -1):
            line = install_lines[index]
            if "pip install" in line:
                install_lines[index] = f"{line} && \\"
                install_lines.insert(index + 1, f"    {pip_install} {package}")
                updated = "\n".join(install_lines)
                break
        else:
            updated = block + "\n" + _python_repair_block(package, cache_plan)
    elif "No Python dependency file detected" in block or "pyproject.toml detected" in block:
        updated = block + "\n" + _python_repair_block(package, cache_plan)
    else:
        updated = block + "\n" + _python_repair_block(package, cache_plan)
    return _replace_topology_block(dockerfile_text, "python_dependency_plan", updated)


def _cache_plan_from_dockerfile(dockerfile_text: str) -> BuildCachePlan:
    apt_cache_id = _extract_cache_id(dockerfile_text, "/var/cache/apt")
    apt_lists_cache_id = _extract_cache_id(dockerfile_text, "/var/lib/apt/lists")
    pip_cache_id = _extract_cache_id(dockerfile_text, "/root/.cache/pip")
    enabled = bool(apt_cache_id or apt_lists_cache_id or pip_cache_id or dockerfile_text.startswith("# syntax="))
    return BuildCachePlan(
        enabled=enabled,
        strategy="buildkit_cache_mount" if enabled else "layer_order",
        requires_buildkit=enabled,
        apt_cache_id=apt_cache_id,
        apt_lists_cache_id=apt_lists_cache_id,
        pip_cache_id=pip_cache_id,
    )


def _extract_cache_id(dockerfile_text: str, target: str) -> str:
    pattern = rf"target={re.escape(target)},id=(?P<cache_id>[^,\s\\]+)"
    match = re.search(pattern, dockerfile_text)
    return match.group("cache_id") if match else ""


def _system_repair_block(package: str, cache_plan: BuildCachePlan) -> str:
    if cache_plan.enabled and cache_plan.apt_cache_id and cache_plan.apt_lists_cache_id:
        return (
            f"RUN --mount=type=cache,target=/var/cache/apt,id={cache_plan.apt_cache_id},sharing=locked \\\n"
            f"    --mount=type=cache,target=/var/lib/apt/lists,id={cache_plan.apt_lists_cache_id},sharing=locked \\\n"
            "    apt-get update && \\\n"
            f"    apt-get install -y --no-install-recommends {package}"
        )
    return (
        "RUN apt-get update && "
        f"apt-get install -y --no-install-recommends {package} && "
        "rm -rf /var/lib/apt/lists/*"
    )


def _python_repair_block(package: str, cache_plan: BuildCachePlan) -> str:
    if cache_plan.enabled and cache_plan.pip_cache_id:
        return (
            f"RUN --mount=type=cache,target=/root/.cache/pip,id={cache_plan.pip_cache_id},sharing=locked "
            f"pip install {package}"
        )
    return f"RUN pip install --no-cache-dir {package}"


def _extract_topology_block(dockerfile_text: str, node_id: str) -> str:
    pattern = re.compile(
        rf"(?ms)^# TOPOLOGY_NODE: {re.escape(node_id)} START\s*\n(?P<body>.*?)^# TOPOLOGY_NODE: {re.escape(node_id)} END\s*$"
    )
    match = pattern.search(dockerfile_text)
    return match.group("body").strip() if match else ""


def _replace_topology_block(dockerfile_text: str, node_id: str, block_body: str) -> str:
    pattern = re.compile(
        rf"(?ms)^# TOPOLOGY_NODE: {re.escape(node_id)} START\s*\n(?P<body>.*?)^# TOPOLOGY_NODE: {re.escape(node_id)} END\s*$"
    )
    replacement = f"# TOPOLOGY_NODE: {node_id} START\n{block_body.rstrip()}\n# TOPOLOGY_NODE: {node_id} END"
    return pattern.sub(replacement, dockerfile_text, count=1)


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _docker_command_stage(command: str) -> str | None:
    lowered = command.lower()
    if "docker buildx build" in lowered or "docker build" in lowered:
        return "build"
    if "docker run" in lowered:
        return "run"
    return None


def _count_docker_cache_steps(output: str) -> tuple[int, int]:
    cache_hits = 0
    step_ids: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"^#(?P<step>\d+)\s+(?P<body>.*)$", line.strip())
        if not match:
            continue
        step_id = match.group("step")
        body = match.group("body")
        if step_id == "0":
            continue
        if any(marker in body for marker in ("CACHED", "DONE", "ERROR")) or body.startswith("["):
            step_ids.add(step_id)
        if "CACHED" in body:
            cache_hits += 1
    return cache_hits, len(step_ids)


def _classic_compatible_dockerfile_text(dockerfile_text: str) -> str:
    text = re.sub(r"^# syntax=[^\r\n]+(?:\r?\n){1,2}", "", dockerfile_text, count=1)
    lines = [line for line in text.splitlines() if not line.startswith("# TOPOMEN_BUILD_CACHE")]
    text = "\n".join(lines).rstrip() + "\n"
    text = re.sub(r"(?m)^RUN (?:--mount=[^\\\r\n]+ \\\r?\n\s*)+", "RUN ", text)
    text = re.sub(r"(?m)^RUN (?:--mount=[^\s]+(?:\s+|$))+", "RUN ", text)
    text = text.replace("pip install -r", "pip install --no-cache-dir -r")
    return re.sub(r"(?m)^RUN pip install ((?!--no-cache-dir).*)$", r"RUN pip install --no-cache-dir \1", text)


def _coerce_fallback_command(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _wrap_threaded_block(node_id: str, block: str) -> str:
    return "\n".join(
        [
            f"# TOPOLOGY_NODE: {node_id} START",
            block.rstrip(),
            f"# TOPOLOGY_NODE: {node_id} END",
        ]
    )


def _branch_id_for_node(node_id: str) -> str:
    if node_id == "system_dependency_plan":
        return "system-deps"
    if node_id == "python_dependency_plan":
        return "python-deps"
    return ""
