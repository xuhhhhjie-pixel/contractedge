from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from threading import Lock

from pydantic import Field

from topomen.collaboration.base_agent import AgentConfig, DefaultAgent
from topomen.collaboration.review import FailureVerifier, MemoryWriteCritic
from topomen.core.exceptions import LimitsExceeded
from topomen.core.paths import EXPERIMENT_TOPOLOGIES_DIR
from topomen.memory import (
    ExperimentRunResult,
    RepairMemory,
    RepairRecord,
    classify_error_type,
    compress_error_message,
    extract_error_signature,
    should_store_repair_record,
    summarize_fix_action,
)
from topomen.topology import (
    BuildTopology,
    FailureAttribution,
    TopologyScheduler,
    attribute_failure_to_node,
    format_runtime_host_command,
    format_topology_prompt_context,
    generate_topology_dockerfile,
    plan_project_topology,
    validate_topology_dockerfile,
    write_topology_artifacts,
)

EXPERIMENT_RUNS_WRITE_LOCK = Lock()


class RepairMemoryAgentConfig(AgentConfig):
    experiment_group: str = "zero-shot"
    project_name: str = ""
    repair_memory_path: Path | None = None
    retrieval_memory_path: Path | None = None
    experiment_runs_path: Path | None = None
    topology_trace_path: Path | None = None
    repair_memory_enabled: bool = True
    rag_top_k: int = 3
    rag_min_score: float = 0.15
    record_successful_repairs: bool = True
    relevant_command_patterns: list[str] = Field(default_factory=lambda: ["docker build", "docker run"])
    track_command_timing: bool = False
    project_key: str = ""
    eval_mode: str = "default"
    allow_same_project_memory: bool = False
    agent_wall_clock_timeout_seconds: int = 3600
    command_timeout_seconds: int = 180
    docker_build_timeout_seconds: int = 1800
    docker_run_timeout_seconds: int = 900
    long_running_intercept_enabled: bool = True
    long_running_command_patterns: list[str] = Field(
        default_factory=lambda: [
            r"docker\s+run\b.*\b(start_backend\.py|flask\s+run|uvicorn\b|gunicorn\b|django\b)",
            r"\bpython\b.*\b-m\s+(http\.server|flask|uvicorn|gunicorn|django)\b",
            r"\bnpm\b\s+(start|run\s+dev|run\s+serve)\b",
            r"\bcelery\b\s+worker\b",
            r"\bredis-server\b",
            r"\bnginx\b",
            r"\btail\s+-f\b",
        ]
    )
    long_running_allow_patterns: list[str] = Field(
        default_factory=lambda: [
            r"python\s+-m\s+compileall\b",
            r"python\s+-m\s+pytest\b",
            r"python\s+-m\s+unittest\b",
            r"python\s+-c\s+\"?import\b",
        ]
    )


class TopologyRepairMemoryAgentConfig(RepairMemoryAgentConfig):
    topology_enabled: bool = True
    topology_artifacts_dir: Path | None = EXPERIMENT_TOPOLOGIES_DIR
    topology_artifacts_svg_enabled: bool = True
    topology_max_workers: int = 4
    topology_memory_evidence_enabled: bool = True
    pattern_memory_enabled: bool = True
    pattern_memory_path: Path | None = None
    runtime_validation_memory_enabled: bool = True
    runtime_validation_memory_path: Path | None = None
    topology_prefill_dockerfile: bool = False
    topology_repair_scope: str = "full_dockerfile"
    topology_guard_enabled: bool = False
    topology_prefill_validate_first: bool = False
    topology_scheduler_enabled: bool = False
    topology_guard_restore_invalid: bool = True
    topology_python_scan_budget: int = 80
    topology_python_scan_max_bytes: int = 262144
    topology_guard_max_violations_per_node: int = 2
    topology_guard_max_skipped_nodes: int = 3
    topology_multistage_enabled: bool = False
    topology_threaded_build_enabled: bool = False
    topology_build_scheduler_strategy: str = "legacy_threaded"
    topology_parallel_build_max_workers: int = 3
    topology_parallel_repair_max_branches: int = 3
    topology_parallel_repair_enabled: bool = False
    topology_build_cache_enabled: bool = False
    topology_build_cache_namespace: str = "topomen"
    topology_build_cache_reuse_existing: bool = True
    topology_docker_registry_mirror_enabled: bool = False
    topology_docker_registry_mirror: str = ""
    topology_docker_buildkit_frontend_image: str = "docker/dockerfile:1.7"


class RepairMemoryAgent(DefaultAgent):
    def __init__(self, model, env, *, config_class: type = RepairMemoryAgentConfig, **kwargs):
        super().__init__(model, env, config_class=config_class, **kwargs)
        self.repair_memory = (
            RepairMemory(self.config.repair_memory_path)
            if self.config.repair_memory_enabled and self.config.repair_memory_path
            else None
        )
        self.retrieval_memory = (
            RepairMemory(self.config.retrieval_memory_path, create=False)
            if self.config.repair_memory_enabled and self.config.retrieval_memory_path
            else self.repair_memory
        )
        self.pending_retrieval_context: str | None = None
        self.pending_retrieval_count = 0
        self.failure_iteration = 0
        self.total_retrieved_contexts = 0
        self.node_failure_counts: Counter[str] = Counter()
        self.node_retry_counts: Counter[str] = Counter()
        self._node_error_seen: Counter[str] = Counter()
        self.docker_build_count = 0
        self.docker_run_count = 0
        self.docker_build_seconds = 0.0
        self.docker_run_seconds = 0.0
        self.cache_hit_steps = 0
        self.cache_total_steps = 0
        self.active_failure: dict | None = None
        self.guardrail_violations = 0
        self.prefill_validated_first = False
        self.memory_hits = 0
        self.memory_retrieval_events = 0
        self.memory_retrieved_matches = 0
        self.memory_precise_matches = 0
        self.model_called = False
        self.failure_verifier = FailureVerifier()
        self.memory_critic = MemoryWriteCritic()
        self.verifier_rejections = 0
        self.critic_rejections = 0
        self.accepted_memory_writes = 0
        self._memory_write_fingerprints: Counter[str] = Counter()
        self._agent_loop_started_at = 0.0
        self._guardrail_abort_reason = ""

    def _project_name(self) -> str:
        return self.config.project_name or str(self.extra_template_vars.get("task", "")).strip() or "unknown-project"

    def _project_key(self) -> str:
        if self.config.project_key:
            return self.config.project_key.strip().lower()
        value = self._project_name().strip().lower()
        return re.sub(r"[^a-z0-9_.-]+", "-", value).strip("-")

    def _resolve_command_timeout(self, command: str) -> int:
        stage = _docker_command_stage(command)
        if stage == "build":
            return self.config.docker_build_timeout_seconds
        if stage == "run":
            return self.config.docker_run_timeout_seconds
        return self.config.command_timeout_seconds

    def _should_intercept_long_running_command(self, command: str) -> tuple[bool, str]:
        if not self.config.long_running_intercept_enabled:
            return False, ""
        lowered = command.lower()
        if "complete_task_and_submit_final_output" in lowered:
            return False, ""
        if "docker build" in lowered and "docker run" in lowered:
            return True, "composite_docker_build_and_run"
        for allow_pattern in self.config.long_running_allow_patterns:
            if re.search(allow_pattern, command, flags=re.IGNORECASE):
                return False, ""
        for pattern in self.config.long_running_command_patterns:
            if re.search(pattern, command, flags=re.IGNORECASE):
                return True, pattern
        return False, ""

    def _long_running_intercept_output(self, *, command: str, matched_pattern: str) -> dict:
        guidance = (
            "Command was blocked by long-running guard. "
            "Use a semantic smoke command (import/test/compile) instead of starting a persistent service."
        )
        output = {
            "output": f"{guidance}\nmatched_pattern={matched_pattern}\nblocked_command={command}",
            "returncode": 1,
            "exception_info": "",
        }
        self._record_trace_event(
            "long_running_command_blocked",
            command=command,
            matched_pattern=matched_pattern,
        )
        return output

    def _check_runtime_limits(self) -> None:
        if self._guardrail_abort_reason:
            raise LimitsExceeded(
                {
                    "role": "exit",
                    "content": self._guardrail_abort_reason,
                    "extra": {
                        "exit_status": "GuardrailBudgetExceeded",
                        "submission": self._guardrail_abort_reason,
                    },
                }
            )
        timeout = self.config.agent_wall_clock_timeout_seconds
        if timeout <= 0 or self._agent_loop_started_at <= 0:
            return
        elapsed = time.perf_counter() - self._agent_loop_started_at
        if elapsed <= timeout:
            return
        message = f"Agent wall-clock timeout exceeded: {elapsed:.2f}s > {timeout}s"
        raise LimitsExceeded(
            {
                "role": "exit",
                "content": message,
                "extra": {
                    "exit_status": "AgentWallClockTimeout",
                    "submission": message,
                },
            }
        )

    def _is_relevant_command(self, command: str) -> bool:
        lowered = command.lower()
        return any(pattern.lower() in lowered for pattern in self.config.relevant_command_patterns)

    def _append_jsonl(self, path: Path | None, payload: dict) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with EXPERIMENT_RUNS_WRITE_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False))
                handle.write("\n")

    def _record_trace_event(self, event_type: str, **payload) -> None:
        self._append_jsonl(
            self.config.topology_trace_path,
            {
                "project": self._project_name(),
                "group": self.config.experiment_group,
                "event_type": event_type,
                "repair_memory_enabled": self.config.repair_memory_enabled,
                "memory_hits": self.memory_hits,
                "memory_retrieval_events": self.memory_retrieval_events,
                "memory_retrieved_matches": self.memory_retrieved_matches,
                "memory_precise_matches": self.memory_precise_matches,
                "guardrail_violations": self.guardrail_violations,
                "llm_called": self.model_called,
                **payload,
            },
        )

    def _attribute_failure(self, *, stage: str, error_signature: str, error_message: str, error_type: str) -> FailureAttribution | None:
        return None

    def _enrich_active_failure(self, attribution: FailureAttribution | None) -> None:
        return None

    def _rewrite_failure_attribution(
        self,
        attribution: FailureAttribution | None,
        *,
        stage: str,
        error_signature: str,
        error_type: str,
    ) -> FailureAttribution | None:
        return attribution

    def _format_attribution_context(self) -> list[str]:
        if self.active_failure is None:
            return []
        attribution = self.active_failure.get("attribution")
        if not attribution:
            return []
        lines = [
            "Topology attribution for the current failure. Repair this node first instead of rewriting the whole Dockerfile.",
            f"topology_node_id: {attribution['topology_node_id']}",
            f"attribution_confidence: {attribution['confidence']}",
            f"attribution_reason: {attribution['reason']}",
        ]
        owned = self.active_failure.get("node_owned_artifacts") if self.active_failure else None
        if isinstance(owned, list) and owned:
            lines.append(f"node_owned_artifacts: {', '.join(str(item) for item in owned)}")
        validation_command = self.active_failure.get("node_validation_command") if self.active_failure else ""
        if isinstance(validation_command, str) and validation_command:
            lines.append(f"node_validation_command: {validation_command}")
        node_confidence = self.active_failure.get("node_confidence") if self.active_failure else None
        if isinstance(node_confidence, (int, float)):
            lines.append(f"node_confidence: {node_confidence:.2f}")
        node_risk_tags = self.active_failure.get("node_risk_tags") if self.active_failure else None
        if isinstance(node_risk_tags, list) and node_risk_tags:
            lines.append(f"node_risk_tags: {', '.join(str(item) for item in node_risk_tags)}")
        lines.extend(self._format_node_block_scope(attribution["topology_node_id"]))
        return lines

    def _format_node_block_scope(self, topology_node_id: str) -> list[str]:
        if getattr(self.config, "topology_repair_scope", "full_dockerfile") != "node_block":
            return []
        retry_count = 0
        if self.active_failure:
            retry_count = int(self.active_failure.get("node_retry_count", 0))
        lines = [
            "Repair scope: edit only this Dockerfile node block unless an adjacent dependency block is required.",
            f"Allowed block markers: # TOPOLOGY_NODE: {topology_node_id} START / END",
            "Do not regenerate the whole Dockerfile when a local node-block edit is enough.",
        ]
        if retry_count > 0:
            lines.append(f"Repeated same-node failure count: {retry_count}. Change the node-block strategy before rebuilding.")
        if retry_count >= 2:
            lines.append(
                "Escalation: this node failed repeatedly with the same error signature. "
                "Change strategy and consider adjacent dependency node updates when needed."
            )
            if self.active_failure and not self.active_failure.get("node_retry_escalated_emitted", False):
                self._record_trace_event(
                    "node_retry_escalated",
                    node_id=topology_node_id,
                    retry_count=retry_count,
                )
                self.active_failure["node_retry_escalated_emitted"] = True
        return lines

    def _build_retrieval_context(self, error_text: str) -> None:
        if self.config.experiment_group.lower() == "zero-shot" or not self.config.repair_memory_enabled:
            self.pending_retrieval_context = None
            self.pending_retrieval_count = 0
            return
        lines = self._format_attribution_context()
        matches = []
        if self.retrieval_memory is not None:
            matches = self.retrieval_memory.retrieve(
                error_text,
                top_k=self.config.rag_top_k,
                min_score=self.config.rag_min_score,
                success_only=True,
                project_name=self._project_name(),
                project_key=self._project_key(),
                allow_same_project_memory=self.config.allow_same_project_memory,
                stage=self.active_failure.get("stage", "") if self.active_failure else "",
                error_type=self.active_failure.get("error_type", "") if self.active_failure else "",
                topology_node_id=(
                    self.active_failure.get("attribution", {}).get("topology_node_id", "")
                    if self.active_failure
                    else ""
                ),
            )
            self.memory_retrieval_events += 1
        self.pending_retrieval_count = len(matches)
        self.memory_hits += len(matches)
        self.memory_retrieved_matches += len(matches)
        self.memory_precise_matches += sum(1 for match in matches if match.evidence_level in {"candidate", "prefill"})
        self.total_retrieved_contexts += len(matches)
        if matches:
            self._record_trace_event(
                "repair_memory_retrieved",
                matches=[
                    {
                        "score": match.score,
                        "evidence_level": match.evidence_level,
                        "score_components": match.score_components,
                        "matched_packages": match.matched_packages,
                        "matched_tokens": match.matched_tokens,
                        "matched_error_type": match.matched_error_type,
                        "matched_node": match.matched_node,
                        "error_signature": match.record.error_signature,
                        "error_type": match.record.error_type,
                        "fix_strategy": match.record.fix_strategy,
                        "fix_action": match.record.fix_action,
                    }
                    for match in matches
                ],
            )
        if not matches and not lines:
            self.pending_retrieval_context = None
            return

        if matches:
            if lines:
                lines.append("")
            lines.append("Historical fixes from the repair memory. Reuse them only when they match the current error.")
            for index, match in enumerate(matches, start=1):
                lines.append(f"[{index}] score={match.score}")
                lines.append(f"error_signature: {match.record.error_signature}")
                lines.append(f"error_type: {match.record.error_type}")
                lines.append(f"fix_strategy: {match.record.fix_strategy}")
                lines.append(f"fix_action: {match.record.fix_action}")
                if match.record.metadata:
                    lines.append(f"metadata: {json.dumps(match.record.metadata, ensure_ascii=False)}")
        self.pending_retrieval_context = "\n".join(lines)
        if self.pending_retrieval_context:
            attribution = self.active_failure.get("attribution", {}) if self.active_failure else {}
            self._record_trace_event(
                "node_repair_package_injected",
                node_id=attribution.get("topology_node_id", ""),
                node_retry_count=self.active_failure.get("node_retry_count", 0) if self.active_failure else 0,
                node_owned_artifacts=self.active_failure.get("node_owned_artifacts", []) if self.active_failure else [],
                node_validation_command=self.active_failure.get("node_validation_command", "") if self.active_failure else "",
                node_confidence=self.active_failure.get("node_confidence") if self.active_failure else None,
                retrieval_match_count=self.pending_retrieval_count,
            )

    def query(self) -> dict:
        self._check_runtime_limits()
        if self.pending_retrieval_context:
            self.add_messages(self.model.format_message(role="user", content=self.pending_retrieval_context))
            self.pending_retrieval_context = None
        self.model_called = True
        self._record_trace_event("llm_loop_started")
        return super().query()

    def _record_repair_outcome(self, *, output: dict, stage: str) -> None:
        if self.repair_memory is None or self.active_failure is None:
            return
        if not self.config.record_successful_repairs and output.get("returncode", 0) == 0:
            self.active_failure = None
            return

        candidate_actions = self.active_failure.get("candidate_actions", [])
        fix_action, fix_strategy, fix_metadata = summarize_fix_action(candidate_actions)
        allow_store = should_store_repair_record(
            self.active_failure["error_signature"],
            self.active_failure["error_message"],
            self.active_failure["trigger_command"],
        )
        fingerprint = f"{self.active_failure['error_signature']}::{fix_action}".lower()
        self._memory_write_fingerprints[fingerprint] += 1
        critic_decision = self.memory_critic.review(
            allow_store=allow_store,
            fix_action=fix_action,
            success=output.get("returncode", 0) == 0,
            duplicate_count=self._memory_write_fingerprints[fingerprint],
        )
        self._record_trace_event(
            "critic_checked",
            accepted=critic_decision.accepted,
            reason=critic_decision.reason,
            confidence=critic_decision.confidence,
            error_signature=self.active_failure["error_signature"],
        )
        if not critic_decision.accepted:
            self.critic_rejections += 1
            self._record_trace_event(
                "critic_rejected",
                reason=critic_decision.reason,
                confidence=critic_decision.confidence,
                error_signature=self.active_failure["error_signature"],
            )
            self.active_failure = None
            return
        self._record_trace_event(
            "critic_accepted",
            reason=critic_decision.reason,
            confidence=critic_decision.confidence,
            error_signature=self.active_failure["error_signature"],
        )

        attribution_metadata = self.active_failure.get("attribution_metadata", {})
        record = RepairRecord(
            project=self._project_name(),
            group=self.config.experiment_group,
            iteration=self.active_failure["iteration"],
            stage=stage,
            error_signature=self.active_failure["error_signature"],
            error_message=compress_error_message(self.active_failure["error_message"]),
            fix_action=fix_action,
            error_type=self.active_failure.get("error_type")
            or classify_error_type(self.active_failure["error_signature"], self.active_failure["error_message"]),
            fix_strategy=fix_strategy,
            success=output.get("returncode", 0) == 0,
            metadata={
                "trigger_command": self.active_failure["trigger_command"],
                "validation_command": self.active_failure.get("validation_command", ""),
                "source_project_key": self._project_key(),
                "eval_mode": self.config.eval_mode,
                "experiment_group": self.config.experiment_group,
                **attribution_metadata,
                **fix_metadata,
            },
        )
        self.repair_memory.add_record(record)
        self.accepted_memory_writes += 1
        self.active_failure = None

    def _before_action(self, action: dict) -> dict | None:
        return None

    def _after_action(self, action: dict, output: dict, elapsed: float, action_state: dict | None) -> dict:
        return output

    def _handle_relevant_command_result(self, *, command: str, output: dict) -> None:
        if not self._is_relevant_command(command):
            return

        stage = "run" if "docker run" in command.lower() else "build"
        if output.get("returncode", 0) != 0:
            self._capture_relevant_failure(command=command, output=output, stage=stage)
        elif self.active_failure is not None:
            self.active_failure["validation_command"] = command
            self._record_repair_outcome(output=output, stage=stage)

    def _capture_relevant_failure(
        self,
        *,
        command: str,
        output: dict,
        stage: str,
        attribution_override: FailureAttribution | None = None,
    ) -> None:
        self.failure_iteration += 1
        error_signature = extract_error_signature(output.get("output", ""))
        error_message = output.get("output", "")
        error_type = classify_error_type(error_signature, error_message)
        attribution = attribution_override or self._attribute_failure(
            stage=stage,
            error_signature=error_signature,
            error_message=error_message,
            error_type=error_type,
        )
        attribution = self._rewrite_failure_attribution(
            attribution,
            stage=stage,
            error_signature=error_signature,
            error_type=error_type,
        )
        attribution_payload = attribution.model_dump(mode="json") if attribution else {}
        verifier_decision = self.failure_verifier.verify(
            stage=stage,
            error_type=error_type,
            observed_node_id=attribution.topology_node_id if attribution else "",
        )
        self._record_trace_event(
            "verifier_checked",
            accepted=verifier_decision.accepted,
            reason=verifier_decision.reason,
            confidence=verifier_decision.confidence,
            expected_node_id=verifier_decision.expected_node_id,
            observed_node_id=verifier_decision.observed_node_id,
        )
        if not verifier_decision.accepted:
            self.verifier_rejections += 1
            self._record_trace_event(
                "verifier_rejected",
                reason=verifier_decision.reason,
                confidence=verifier_decision.confidence,
                expected_node_id=verifier_decision.expected_node_id,
                observed_node_id=verifier_decision.observed_node_id,
            )
        else:
            self._record_trace_event(
                "verifier_accepted",
                reason=verifier_decision.reason,
                confidence=verifier_decision.confidence,
            )
        attribution_metadata = _attribution_metadata(attribution)
        if attribution:
            self.node_failure_counts[attribution.topology_node_id] += 1
            error_key = f"{attribution.topology_node_id}:{error_signature}"
            self._node_error_seen[error_key] += 1
            if self._node_error_seen[error_key] > 1:
                self.node_retry_counts[attribution.topology_node_id] += 1
        self.active_failure = {
            "iteration": self.failure_iteration,
            "error_signature": error_signature,
            "error_message": error_message,
            "error_type": error_type,
            "stage": stage,
            "trigger_command": command,
            "validation_command": "",
            "candidate_actions": [],
            "attribution": attribution_payload,
            "attribution_metadata": attribution_metadata,
            "node_retry_count": self.node_retry_counts[attribution.topology_node_id] if attribution else 0,
            "node_retry_escalated_emitted": False,
            "node_owned_artifacts": [],
            "node_validation_command": "",
            "node_confidence": None,
            "node_risk_tags": [],
        }
        self._enrich_active_failure(attribution)
        self._record_trace_event(
            "failure_attributed",
            stage=stage,
            command=command,
            returncode=output.get("returncode", 0),
            error_signature=error_signature,
            error_type=error_type,
            node_id=attribution.topology_node_id if attribution else "",
            attribution=attribution_payload,
        )
        self._build_retrieval_context(output.get("output", ""))

    def execute_actions(self, message: dict) -> list[dict]:
        outputs = []
        actions = message.get("extra", {}).get("actions", [])
        for action in actions:
            command = action.get("command", "")
            if self.active_failure is not None:
                self.active_failure["candidate_actions"].append(command)

            intercepted, matched_pattern = self._should_intercept_long_running_command(command)
            if intercepted:
                started_at = time.perf_counter()
                output = self._long_running_intercept_output(command=command, matched_pattern=matched_pattern)
                action_state = {"skip_output": output}
                elapsed = time.perf_counter() - started_at
            else:
                action_state = self._before_action(action)
                if action_state is not None and "skip_output" in action_state:
                    started_at = time.perf_counter()
                    output = action_state["skip_output"]
                    elapsed = time.perf_counter() - started_at
                else:
                    started_at = time.perf_counter()
                    output = self.env.execute(action, timeout=self._resolve_command_timeout(command))
                    elapsed = time.perf_counter() - started_at
                    output = self._after_action(action, output, elapsed, action_state)
            outputs.append(output)

            if self.config.track_command_timing:
                self._record_command_metrics(command, output, elapsed)

            self._record_trace_event(
                "action_executed",
                command=command,
                returncode=output.get("returncode", 0),
                elapsed=round(elapsed, 4),
            )
            self._handle_relevant_command_result(command=command, output=output)

        return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))

    def run(self, task: str = "", **kwargs) -> dict:
        started_at = time.perf_counter()
        info = self._run_agent_loop(task, **kwargs)
        total_seconds = time.perf_counter() - started_at
        self._record_experiment_result(info=info, total_seconds=total_seconds)
        return info

    def _run_agent_loop(self, task: str = "", **kwargs) -> dict:
        self._agent_loop_started_at = time.perf_counter()
        try:
            return super().run(task, **kwargs)
        finally:
            self._agent_loop_started_at = 0.0

    def _record_experiment_result(self, *, info: dict, total_seconds: float) -> None:
        topology_cache = self.topology.analysis.build_cache if getattr(self, "topology", None) is not None else None
        topology_analysis = self.topology.analysis if getattr(self, "topology", None) is not None else None
        topology_memory_matches = getattr(topology_analysis, "repair_memory_matches", []) if topology_analysis else []
        pattern_memory_matches = getattr(topology_analysis, "pattern_memory_matches", []) if topology_analysis else []
        runtime_validation_memory_matches = (
            getattr(topology_analysis, "runtime_validation_memory_matches", []) if topology_analysis else []
        )
        memory_prefill_packages = (
            [*getattr(topology_analysis, "memory_apt_packages", []), *getattr(topology_analysis, "memory_python_packages", [])]
            if topology_analysis
            else []
        )
        pattern_prefill_packages = (
            [*getattr(topology_analysis, "pattern_apt_packages", []), *getattr(topology_analysis, "pattern_python_packages", [])]
            if topology_analysis
            else []
        )
        result = ExperimentRunResult(
            group=self.config.experiment_group,
            project=self._project_name(),
            success=info.get("exit_status") == "Submitted",
            iterations=self.failure_iteration,
            retrieved_contexts=self.total_retrieved_contexts,
            memory_retrieval_events=self.memory_retrieval_events,
            memory_retrieved_matches=self.memory_retrieved_matches,
            memory_precise_matches=self.memory_precise_matches,
            topology_memory_matches=len(topology_memory_matches),
            memory_prefill_candidates=sum(
                1 for match in topology_memory_matches if match.get("evidence_level") == "prefill"
            ),
            memory_prefill_applied_packages=len(set(memory_prefill_packages)),
            pattern_memory_matches=len(pattern_memory_matches),
            pattern_cross_project_matches=sum(
                1 for match in pattern_memory_matches if int(match.get("unique_project_count") or 0) >= 2
            ),
            pattern_low_support_matches=sum(
                1
                for match in pattern_memory_matches
                if match.get("blocked_reason") == "insufficient_cross_project_support"
            ),
            pattern_negative_evidence_matches=sum(
                1
                for match in pattern_memory_matches
                if match.get("blocked_reason") == "negative_evidence" or int(match.get("failed_project_count") or 0) > 0
            ),
            pattern_prefill_applied_packages=len(set(pattern_prefill_packages)),
            runtime_validation_memory_matches=len(runtime_validation_memory_matches),
            runtime_validation_memory_prefill_candidates=sum(
                1 for match in runtime_validation_memory_matches if match.get("prefill_ready")
            ),
            node_failures=dict(sorted(self.node_failure_counts.items())),
            total_seconds=round(total_seconds, 4),
            docker_build_count=self.docker_build_count,
            docker_run_count=self.docker_run_count,
            docker_build_seconds=round(self.docker_build_seconds, 4),
            docker_run_seconds=round(self.docker_run_seconds, 4),
            cache_hit_steps=self.cache_hit_steps,
            cache_total_steps=self.cache_total_steps,
            node_retries=dict(sorted(self.node_retry_counts.items())),
            guardrail_violations=self.guardrail_violations,
            prefill_validated_first=self.prefill_validated_first,
            verifier_rejections=self.verifier_rejections,
            critic_rejections=self.critic_rejections,
            accepted_memory_writes=self.accepted_memory_writes,
            multistage_used=bool(info.get("multistage_used", False)),
            threaded_build_used=bool(info.get("threaded_build_used", False)),
            parallel_build_branches=int(info.get("parallel_build_branches", 0) or 0),
            parallel_repair_used=bool(info.get("parallel_repair_used", False)),
            repair_branches_tried=int(info.get("repair_branches_tried", 0) or 0),
            build_cache_enabled=bool(info.get("build_cache_enabled", getattr(topology_cache, "enabled", False))),
            build_cache_namespace=str(info.get("build_cache_namespace", getattr(topology_cache, "namespace", "")) or ""),
            build_cache_reuse_existing=bool(
                info.get("build_cache_reuse_existing", getattr(topology_cache, "reuse_existing", False))
            ),
            build_cache_id_scope=str(
                info.get("build_cache_id_scope", getattr(topology_cache, "cache_id_scope", "")) or ""
            ),
            build_cache_ids=dict(info.get("build_cache_ids", _build_cache_ids(topology_cache)) or {}),
            final_image=str(info.get("final_image", "") or ""),
            internal_branch_image_count=int(info.get("internal_branch_image_count", 0) or 0),
        )
        self._append_jsonl(self.config.experiment_runs_path, result.model_dump(mode="json"))
        self._record_trace_event(
            "final_result",
            success=result.success,
            iterations=result.iterations,
            retrieved_contexts=result.retrieved_contexts,
            memory_retrieval_events=result.memory_retrieval_events,
            memory_retrieved_matches=result.memory_retrieved_matches,
            memory_precise_matches=result.memory_precise_matches,
            topology_memory_matches=result.topology_memory_matches,
            memory_prefill_candidates=result.memory_prefill_candidates,
            memory_prefill_applied_packages=result.memory_prefill_applied_packages,
            pattern_memory_matches=result.pattern_memory_matches,
            pattern_cross_project_matches=result.pattern_cross_project_matches,
            pattern_low_support_matches=result.pattern_low_support_matches,
            pattern_negative_evidence_matches=result.pattern_negative_evidence_matches,
            pattern_prefill_applied_packages=result.pattern_prefill_applied_packages,
            runtime_validation_memory_matches=result.runtime_validation_memory_matches,
            runtime_validation_memory_prefill_candidates=result.runtime_validation_memory_prefill_candidates,
            node_failures=result.node_failures,
            total_seconds=result.total_seconds,
            prefill_validated_first=result.prefill_validated_first,
            multistage_used=result.multistage_used,
            threaded_build_used=result.threaded_build_used,
            parallel_build_branches=result.parallel_build_branches,
            parallel_repair_used=result.parallel_repair_used,
            repair_branches_tried=result.repair_branches_tried,
            build_cache_enabled=result.build_cache_enabled,
            build_cache_namespace=result.build_cache_namespace,
            build_cache_reuse_existing=result.build_cache_reuse_existing,
            build_cache_id_scope=result.build_cache_id_scope,
            build_cache_ids=result.build_cache_ids,
            final_image=result.final_image,
            internal_branch_image_count=result.internal_branch_image_count,
        )

    def _record_command_metrics(self, command: str, output: dict, elapsed: float) -> None:
        stage = _docker_command_stage(command)
        if stage == "build":
            self.docker_build_count += 1
            self.docker_build_seconds += elapsed
            cache_hits, cache_total = _count_docker_cache_steps(output.get("output", ""))
            self.cache_hit_steps += cache_hits
            self.cache_total_steps += cache_total
        elif stage == "run":
            self.docker_run_count += 1
            self.docker_run_seconds += elapsed


class TopologyRepairMemoryAgent(RepairMemoryAgent):
    def __init__(self, model, env, *, config_class: type = TopologyRepairMemoryAgentConfig, **kwargs):
        super().__init__(model, env, config_class=config_class, **kwargs)
        self.topology: BuildTopology | None = None
        self.topology_artifacts: dict[str, str] = {}
        self._topology_task_path: Path | None = None
        self._last_valid_dockerfile: str | None = None
        self._guardrail_violations_per_node: Counter[str] = Counter()
        self._guardrail_skipped_nodes: set[str] = set()

    def _active_failure_node_id(self) -> str:
        if not self.active_failure:
            return ""
        attribution = self.active_failure.get("attribution") or {}
        node_id = attribution.get("topology_node_id")
        return str(node_id) if isinstance(node_id, str) else ""

    def _register_guardrail_violation(self, node_id: str) -> tuple[bool, bool]:
        if not node_id:
            return False, False
        self._guardrail_violations_per_node[node_id] += 1
        max_per_node = max(0, self.config.topology_guard_max_violations_per_node)
        should_skip = self._guardrail_violations_per_node[node_id] > max_per_node
        if should_skip and node_id not in self._guardrail_skipped_nodes:
            self._guardrail_skipped_nodes.add(node_id)
            self._record_trace_event(
                "guardrail_node_skipped",
                node_id=node_id,
                violations=self._guardrail_violations_per_node[node_id],
                skipped_nodes=sorted(self._guardrail_skipped_nodes),
            )
        max_skipped = max(0, self.config.topology_guard_max_skipped_nodes)
        should_abort = len(self._guardrail_skipped_nodes) > max_skipped
        if should_abort:
            self._guardrail_abort_reason = (
                "Guardrail skip budget exhausted: "
                f"skipped_nodes={sorted(self._guardrail_skipped_nodes)} max={max_skipped}"
            )
            self._record_trace_event(
                "guardrail_abort",
                reason=self._guardrail_abort_reason,
                skipped_nodes=sorted(self._guardrail_skipped_nodes),
            )
        return should_skip, should_abort

    def _enrich_active_failure(self, attribution: FailureAttribution | None) -> None:
        if self.active_failure is None or attribution is None or self.topology is None:
            return
        node = self.topology.node_map().get(attribution.topology_node_id)
        if node is None:
            return
        self.active_failure["node_owned_artifacts"] = list(node.owned_artifacts)
        self.active_failure["node_validation_command"] = node.validation_command
        self.active_failure["node_confidence"] = node.confidence
        self.active_failure["node_risk_tags"] = list(node.risk_tags)

    def _build_topology_context(self, task: str) -> str:
        if not self.config.topology_enabled:
            return ""
        self._topology_task_path = Path(task)
        try:
            memory_path = None
            if self.config.repair_memory_enabled and self.config.topology_memory_evidence_enabled:
                memory_path = self.config.retrieval_memory_path or self.config.repair_memory_path
            pattern_memory_path = self.config.pattern_memory_path if self.config.pattern_memory_enabled else None
            runtime_validation_memory_path = (
                self.config.runtime_validation_memory_path
                if self.config.runtime_validation_memory_enabled
                else None
            )
            self.topology = plan_project_topology(
                task,
                repair_memory_path=memory_path,
                pattern_memory_path=pattern_memory_path,
                runtime_validation_memory_path=runtime_validation_memory_path,
                project_key=self._project_key(),
                allow_same_project_memory=self.config.allow_same_project_memory,
                allow_same_project_pattern_memory=self.config.allow_same_project_memory,
                allow_same_project_runtime_memory=self.config.allow_same_project_memory,
                max_workers=self.config.topology_max_workers,
                python_scan_budget=self.config.topology_python_scan_budget,
                python_scan_max_bytes=self.config.topology_python_scan_max_bytes,
                build_cache_enabled=self.config.topology_build_cache_enabled,
                build_cache_namespace=self.config.topology_build_cache_namespace,
                build_cache_reuse_existing=self.config.topology_build_cache_reuse_existing,
                docker_registry_mirror_enabled=self.config.topology_docker_registry_mirror_enabled,
                docker_registry_mirror=self.config.topology_docker_registry_mirror,
                docker_buildkit_frontend_image=self.config.topology_docker_buildkit_frontend_image,
            )
            self.topology = self.topology.model_copy(
                update={"multistage": self.config.topology_multistage_enabled}
            )
        except Exception as exc:
            return f"Build topology planning failed before agent execution: {type(exc).__name__}: {exc}"

        memory_evidence_items = [
            item.model_dump(mode="json")
            for item in self.topology.analysis.dependency_evidence
            if item.source_type == "repair_memory"
        ]
        pattern_evidence_items = [
            item.model_dump(mode="json")
            for item in self.topology.analysis.dependency_evidence
            if item.source_type == "pattern_memory"
        ]
        self._record_trace_event(
            "topology_created",
            topology_nodes=[node.node_id for node in self.topology.nodes],
            memory_evidence_items=memory_evidence_items,
            memory_prefill_packages={
                "apt": self.topology.analysis.memory_apt_packages,
                "python": self.topology.analysis.memory_python_packages,
            },
            pattern_memory_matches=self.topology.analysis.pattern_memory_matches,
            repair_memory_matches=self.topology.analysis.repair_memory_matches,
            runtime_validation_memory_matches=self.topology.analysis.runtime_validation_memory_matches,
            pattern_evidence_items=pattern_evidence_items,
            pattern_prefill_packages={
                "apt": self.topology.analysis.pattern_apt_packages,
                "python": self.topology.analysis.pattern_python_packages,
            },
            pattern_source_projects=_pattern_source_projects(self.topology.analysis.pattern_memory_matches),
            runtime_validation_plan=self.topology.analysis.runtime_validation.model_dump(mode="json"),
            docker_image_config={
                "registry_mirror_enabled": self.config.topology_docker_registry_mirror_enabled,
                "registry_mirror": self.topology.analysis.docker_registry_mirror,
                "base_image_source": self.topology.analysis.base_image_source,
                "base_image": self.topology.analysis.base_image,
                "buildkit_frontend_image": self.topology.analysis.build_cache.buildkit_frontend_image,
            },
            build_cache_plan=self.topology.analysis.build_cache.model_dump(mode="json"),
            build_cache_enabled=self.topology.analysis.build_cache.enabled,
            build_cache_reuse_existing=self.topology.analysis.build_cache.reuse_existing,
            build_cache_id_scope=self.topology.analysis.build_cache.cache_id_scope,
            build_cache_ids=_build_cache_ids(self.topology.analysis.build_cache),
            multistage=self.topology.multistage,
            threaded_build=self.config.topology_threaded_build_enabled,
            build_scheduler_strategy=self.config.topology_build_scheduler_strategy,
            parallel_build_stages=self.topology.parallel_build_stages,
            scan_strategy=self.topology.analysis.scan_strategy,
            python_files_scanned=self.topology.analysis.python_files_scanned,
            python_scan_budget=self.topology.analysis.python_scan_budget,
            topology_planning_seconds=self.topology.analysis.topology_planning_seconds,
        )
        if self.config.topology_artifacts_dir is not None:
            self.topology_artifacts = write_topology_artifacts(
                self.topology,
                self.config.topology_artifacts_dir,
                name=self.config.project_name or Path(task).name or task,
                render_svg=self.config.topology_artifacts_svg_enabled,
            )
        prefill_note = ""
        if self.config.topology_prefill_dockerfile and not self.config.topology_scheduler_enabled:
            prefill_note = self._prefill_dockerfile(task)
        elif self.config.topology_prefill_dockerfile and self.config.topology_scheduler_enabled:
            prefill_note = (
                f"Topology scheduler will write a TOPOLOGY_NODE-marked Dockerfile under {Path(task) / 'Dockerfile'} "
                "before deterministic build/run validation."
            )
        else:
            self._remember_current_dockerfile_if_valid()
        context = format_topology_prompt_context(self.topology)
        if prefill_note:
            context = "\n".join([context, "", prefill_note])
        return context

    def _prefill_dockerfile(self, task: str) -> str:
        if self.topology is None:
            return ""
        project_path = Path(task)
        if not project_path.exists() or not project_path.is_dir():
            return f"Topology prefill skipped because project path is not a directory: {task}"
        dockerfile_path = project_path / "Dockerfile"
        dockerfile_text = generate_topology_dockerfile(self.topology)
        dockerfile_path.write_text(dockerfile_text, encoding="utf-8")
        self._last_valid_dockerfile = dockerfile_text
        self._record_trace_event(
            "dockerfile_prefilled",
            node_ids=[node.node_id for node in self.topology.nodes if node.dockerfile_block],
            dockerfile_path=str(dockerfile_path),
        )
        return (
            f"Topology-prefill Dockerfile has been written to {dockerfile_path}. "
            "Keep the TOPOLOGY_NODE markers and repair only the matching node block when possible."
        )

    def _attribute_failure(self, *, stage: str, error_signature: str, error_message: str, error_type: str) -> FailureAttribution | None:
        if not self.config.topology_enabled or self.topology is None:
            return None
        return attribute_failure_to_node(
            error_message,
            stage=stage,
            error_type=error_type,
            error_signature=error_signature,
        )

    def _dockerfile_path(self) -> Path | None:
        if self._topology_task_path is None:
            return None
        return self._topology_task_path / "Dockerfile"

    def _allowed_repair_node_id(self) -> str | None:
        if self.config.topology_repair_scope != "node_block" or not self.active_failure:
            return None
        attribution = self.active_failure.get("attribution") or {}
        node_id = attribution.get("topology_node_id")
        if not isinstance(node_id, str):
            return None
        if node_id in self._guardrail_skipped_nodes:
            return None
        return node_id

    def _rewrite_failure_attribution(
        self,
        attribution: FailureAttribution | None,
        *,
        stage: str,
        error_signature: str,
        error_type: str,
    ) -> FailureAttribution | None:
        if attribution is None:
            return None
        original_node_id = attribution.topology_node_id
        if original_node_id not in self._guardrail_skipped_nodes:
            return attribution
        rerouted_node_id = "build_failure_repair" if stage == "build" else "runtime_failure_repair"
        self._record_trace_event(
            "guardrail_node_rerouted",
            original_node_id=original_node_id,
            rerouted_node_id=rerouted_node_id,
            stage=stage,
        )
        return FailureAttribution(
            stage=stage,
            error_signature=error_signature,
            error_type=error_type,
            topology_node_id=rerouted_node_id,
            confidence=max(0.4, attribution.confidence - 0.2),
            reason=(
                f"Guardrail skip policy rerouted from {original_node_id} "
                f"to {rerouted_node_id} after repeated guardrail violations."
            ),
        )

    def _remember_current_dockerfile_if_valid(self) -> None:
        dockerfile_path = self._dockerfile_path()
        if dockerfile_path is None or not dockerfile_path.exists() or self.topology is None:
            return
        dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
        result = validate_topology_dockerfile(dockerfile_text, topology=self.topology)
        if result.valid:
            self._last_valid_dockerfile = dockerfile_text

    def _guardrail_failure_output(self, violations: list[str]) -> dict:
        detail = "\n".join(f"- {violation}" for violation in violations)
        restored = " Restored the last valid topology Dockerfile." if self._last_valid_dockerfile else ""
        node_id = self._active_failure_node_id()
        skip_message = ""
        abort_message = ""
        if node_id:
            skipped, aborted = self._register_guardrail_violation(node_id)
            node_violations = self._guardrail_violations_per_node[node_id]
            if skipped:
                skip_message = (
                    f" Guardrail skip policy: node '{node_id}' reached {node_violations} violations "
                    f"and is now skipped. Continue with other repair nodes."
                )
            if aborted:
                abort_message = f" {self._guardrail_abort_reason}"
        output = (
            "Dockerfile guardrail rejected this action.\n"
            f"{detail}\n"
            f"{restored}\n"
            f"{skip_message}\n"
            f"{abort_message}\n"
            "Repair only the attributed TOPOLOGY_NODE block and keep base_image_selection unchanged."
        )
        self.pending_retrieval_context = output
        self.guardrail_violations += 1
        return {"output": output, "returncode": 1, "exception_info": ""}

    def _validate_and_restore_dockerfile(self) -> dict | None:
        if not self.config.topology_guard_enabled or self.topology is None:
            return None
        dockerfile_path = self._dockerfile_path()
        if dockerfile_path is None or not dockerfile_path.exists():
            return None
        dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
        result = validate_topology_dockerfile(
            dockerfile_text,
            topology=self.topology,
            allowed_node_id=self._allowed_repair_node_id(),
            original_dockerfile=self._last_valid_dockerfile,
            protect_base_image=True,
        )
        if result.valid:
            self._last_valid_dockerfile = dockerfile_text
            self._record_trace_event("guardrail_check", valid=True)
            return None
        if self.config.topology_guard_restore_invalid and self._last_valid_dockerfile is not None:
            dockerfile_path.write_text(self._last_valid_dockerfile, encoding="utf-8")
        self._record_trace_event("guardrail_check", valid=False, violations=result.violations)
        return self._guardrail_failure_output(result.violations)

    def _before_action(self, action: dict) -> dict | None:
        command = action.get("command", "")
        if not self.config.topology_guard_enabled:
            return None
        if "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in command or "docker build" in command.lower():
            guard_output = self._validate_and_restore_dockerfile()
            if guard_output is not None:
                return {"skip_output": guard_output}
        return None

    def _after_action(self, action: dict, output: dict, elapsed: float, action_state: dict | None) -> dict:
        if not self.config.topology_guard_enabled:
            return output
        guard_output = self._validate_and_restore_dockerfile()
        if guard_output is None:
            return output
        return {
            **output,
            "returncode": 1,
            "output": "\n".join(part for part in [output.get("output", ""), guard_output["output"]] if part),
            "exception_info": output.get("exception_info", ""),
        }

    def _prevalidation_run_command(self, image_name: str) -> str:
        if self.topology is None:
            return f"docker run --rm {image_name} 2>&1"
        return format_runtime_host_command(self.topology.analysis.runtime_validation, image_name)

    def _topology_build_cache_enabled(self) -> bool:
        return bool(self.topology is not None and self.topology.analysis.build_cache.enabled)

    def _execute_prevalidation_command(self, command: str) -> dict:
        self._record_trace_event("prevalidation_command_started", command=command)
        started_at = time.perf_counter()
        output = self.env.execute({"command": command}, timeout=self._resolve_command_timeout(command))
        elapsed = time.perf_counter() - started_at
        if self.config.track_command_timing:
            self._record_command_metrics(command, output, elapsed)
        self._handle_relevant_command_result(command=command, output=output)
        self._record_trace_event(
            "prevalidation_command_finished",
            command=command,
            returncode=output.get("returncode", 0),
            elapsed=round(elapsed, 4),
            error_signature=extract_error_signature(output.get("output", "")) if output.get("returncode", 0) != 0 else "",
        )
        return output

    def _prevalidate_topology_dockerfile(self, *, task: str, image_name: str) -> dict:
        if self.config.topology_scheduler_enabled:
            return self._run_topology_scheduler(task=task, image_name=image_name)

        dockerfile_guard_output = self._validate_and_restore_dockerfile()
        if dockerfile_guard_output is not None:
            return {"success": False, "stage": "guardrail", "output": dockerfile_guard_output}

        task_path = Path(task)
        build_tool = "docker buildx build --load --progress=plain" if self._topology_build_cache_enabled() else "docker build"
        build_command = f'cd /d "{task_path}" && {build_tool} -t {image_name} . 2>&1'
        build_output = self._execute_prevalidation_command(build_command)
        if build_output.get("returncode", 0) != 0:
            self._record_trace_event("prevalidation_failed", stage="build", prevalidation_result="failed")
            return {"success": False, "stage": "build", "output": build_output}

        run_command = self._prevalidation_run_command(image_name)
        run_output = self._execute_prevalidation_command(run_command)
        if run_output.get("returncode", 0) != 0:
            self._record_trace_event("prevalidation_failed", stage="run", prevalidation_result="failed")
            return {"success": False, "stage": "run", "output": run_output}

        self.prefill_validated_first = True
        self._record_trace_event("prevalidation_passed", stage="run", prevalidation_result="passed")
        return {"success": True, "stage": "run", "output": run_output}

    def _run_topology_scheduler(self, *, task: str, image_name: str) -> dict:
        if self.topology is None:
            if not self.config.topology_enabled:
                return self._run_no_topology_scheduler(task=task, image_name=image_name)
            return {"success": False, "stage": "scheduler", "output": {"output": "No topology available", "returncode": 1}}
        scheduler = TopologyScheduler(
            topology=self.topology,
            project_path=task,
            image_name=image_name,
            env=self.env,
            trace_callback=lambda event_type, payload: self._record_trace_event(event_type, **payload),
            command_timeout_seconds=self.config.command_timeout_seconds,
            docker_build_timeout_seconds=self.config.docker_build_timeout_seconds,
            docker_run_timeout_seconds=self.config.docker_run_timeout_seconds,
            parallel_repair=self.config.topology_parallel_repair_enabled,
            threaded_build=self.config.topology_threaded_build_enabled,
            build_scheduler_strategy=self.config.topology_build_scheduler_strategy,
            parallel_build_max_workers=self.config.topology_parallel_build_max_workers,
            parallel_repair_max_branches=self.config.topology_parallel_repair_max_branches,
        )
        result = scheduler.run()
        self._consume_scheduler_result(result)
        if not result.preflight_passed:
            return {
                "success": False,
                "stage": "preflight",
                "output": {"output": result.preflight_blocker, "returncode": 1, "exception_info": ""},
                "infra_blocker": True,
                "build_cache_enabled": result.build_cache_enabled,
                "build_cache_namespace": result.build_cache_namespace,
                "build_cache_reuse_existing": result.build_cache_reuse_existing,
                "build_cache_id_scope": result.build_cache_id_scope,
                "build_cache_ids": result.build_cache_ids,
                "final_image": result.final_image,
                "internal_branch_image_count": len(result.internal_branch_images),
                **_scheduler_topology_metadata(result),
            }
        dockerfile_path = self._dockerfile_path()
        guard_passed = any(
            item.node_id == "dockerfile_generation" and item.status == "passed" for item in result.node_results
        )
        if guard_passed and dockerfile_path is not None and dockerfile_path.exists():
            self._last_valid_dockerfile = dockerfile_path.read_text(encoding="utf-8")
        if result.success:
            self.prefill_validated_first = True
            self._record_trace_event("prevalidation_passed", stage="run", prevalidation_result="passed")
            return {
                "success": True,
                "stage": "run",
                "output": result.node_results[-1].output if result.node_results else {},
                "multistage_used": result.multistage_used,
                "threaded_build_used": result.threaded_build_used,
                "parallel_build_branches": result.parallel_build_branches,
                "parallel_repair_used": result.parallel_repair_used,
                "repair_branches_tried": result.repair_branches_tried,
                "build_cache_enabled": result.build_cache_enabled,
                "build_cache_namespace": result.build_cache_namespace,
                "build_cache_reuse_existing": result.build_cache_reuse_existing,
                "build_cache_id_scope": result.build_cache_id_scope,
                "build_cache_ids": result.build_cache_ids,
                "final_image": result.final_image,
                "internal_branch_image_count": len(result.internal_branch_images),
                **_scheduler_topology_metadata(result),
            }
        failed_result = next((item for item in reversed(result.node_results) if item.status == "failed"), None)
        stage = failed_result.stage if failed_result is not None else "scheduler"
        output = failed_result.output if failed_result is not None else {"output": "Topology scheduler failed", "returncode": 1}
        prevalidation_stage = "build" if stage == "guard" else stage
        self._record_trace_event(
            "prevalidation_failed",
            stage=prevalidation_stage,
            prevalidation_result="failed",
            failed_node_id=result.failed_node_id,
        )
        return {
            "success": False,
            "stage": prevalidation_stage,
            "output": output,
            "multistage_used": result.multistage_used,
            "threaded_build_used": result.threaded_build_used,
            "parallel_build_branches": result.parallel_build_branches,
            "parallel_repair_used": result.parallel_repair_used,
            "repair_branches_tried": result.repair_branches_tried,
            "build_cache_enabled": result.build_cache_enabled,
            "build_cache_namespace": result.build_cache_namespace,
            "build_cache_reuse_existing": result.build_cache_reuse_existing,
            "build_cache_id_scope": result.build_cache_id_scope,
            "build_cache_ids": result.build_cache_ids,
            "final_image": result.final_image,
            "internal_branch_image_count": len(result.internal_branch_images),
            **_scheduler_topology_metadata(result),
        }

    def _run_no_topology_scheduler(self, *, task: str, image_name: str) -> dict:
        task_path = Path(task)
        node_states = {"docker_build": "ready", "runtime_validation": "planned"}
        self._record_trace_event(
            "scheduler_started",
            scheduler_mode="no_topology",
            build_scheduler_strategy="no_topology",
            node_states=node_states,
        )

        build_command = f'cd /d "{task_path}" && docker build -t {image_name} . 2>&1'
        build_output = self._execute_prevalidation_command(build_command)
        if build_output.get("returncode", 0) != 0:
            node_states = {"docker_build": "failed", "runtime_validation": "skipped"}
            self._record_trace_event(
                "scheduler_node_finished",
                scheduler_mode="no_topology",
                node_id="docker_build",
                stage="build",
                status="failed",
                command=build_command,
                returncode=build_output.get("returncode", 0),
            )
            self._record_trace_event(
                "scheduler_node_skipped",
                scheduler_mode="no_topology",
                node_id="runtime_validation",
                reason="upstream_build_failed",
            )
            self._record_trace_event(
                "scheduler_failed",
                scheduler_mode="no_topology",
                failed_node_id="docker_build",
                node_states=node_states,
                build_scheduler_strategy="no_topology",
            )
            self._record_trace_event("prevalidation_failed", stage="build", prevalidation_result="failed")
            return {
                "success": False,
                "stage": "build",
                "output": build_output,
                "build_scheduler_strategy": "no_topology",
                "dag_failed_step_id": "docker_build",
            }

        self._record_trace_event(
            "scheduler_node_finished",
            scheduler_mode="no_topology",
            node_id="docker_build",
            stage="build",
            status="passed",
            command=build_command,
            returncode=build_output.get("returncode", 0),
        )

        run_command = self._prevalidation_run_command(image_name)
        run_output = self._execute_prevalidation_command(run_command)
        if run_output.get("returncode", 0) != 0:
            node_states = {"docker_build": "passed", "runtime_validation": "failed"}
            self._record_trace_event(
                "scheduler_node_finished",
                scheduler_mode="no_topology",
                node_id="runtime_validation",
                stage="run",
                status="failed",
                command=run_command,
                returncode=run_output.get("returncode", 0),
                runtime_validation_mode="default",
                command_source="no_topology_default",
            )
            self._record_trace_event(
                "scheduler_failed",
                scheduler_mode="no_topology",
                failed_node_id="runtime_validation",
                node_states=node_states,
                build_scheduler_strategy="no_topology",
            )
            self._record_trace_event("prevalidation_failed", stage="run", prevalidation_result="failed")
            return {
                "success": False,
                "stage": "run",
                "output": run_output,
                "build_scheduler_strategy": "no_topology",
                "dag_failed_step_id": "runtime_validation",
            }

        self._record_trace_event(
            "scheduler_node_finished",
            scheduler_mode="no_topology",
            node_id="runtime_validation",
            stage="run",
            status="passed",
            command=run_command,
            returncode=run_output.get("returncode", 0),
            runtime_validation_mode="default",
            command_source="no_topology_default",
        )
        self.prefill_validated_first = True
        node_states = {"docker_build": "passed", "runtime_validation": "passed"}
        self._record_trace_event(
            "scheduler_passed",
            scheduler_mode="no_topology",
            node_states=node_states,
            build_scheduler_strategy="no_topology",
            final_image=image_name,
        )
        self._record_trace_event("prevalidation_passed", stage="run", prevalidation_result="passed")
        return {
            "success": True,
            "stage": "run",
            "output": run_output,
            "build_scheduler_strategy": "no_topology",
            "final_image": image_name,
        }

    def _consume_scheduler_result(self, result) -> None:
        if not result.preflight_passed:
            return
        for node_result in result.node_results:
            command = node_result.command
            output = node_result.output
            if command and self.config.track_command_timing:
                self._record_command_metrics(command, output, node_result.elapsed)
            if node_result.status != "failed":
                continue
            attribution = node_result.attribution
            stage = "build" if node_result.stage == "guard" else node_result.stage
            trigger_command = command or f"topology scheduler {node_result.stage}"
            self._capture_relevant_failure(
                command=trigger_command,
                output=output,
                stage=stage,
                attribution_override=attribution,
            )
            break
        self.docker_build_count = max(self.docker_build_count, result.docker_build_count)
        self.docker_run_count = max(self.docker_run_count, result.docker_run_count)

    def run(self, task: str = "", **kwargs) -> dict:
        started_at = time.perf_counter()
        topology_context = self._build_topology_context(task)
        if topology_context and "topology_context" not in kwargs:
            kwargs["topology_context"] = topology_context
        image_name = kwargs.get("image_name", "topomen-env-test-image")
        if self.config.topology_prefill_validate_first:
            prevalidation = self._prevalidate_topology_dockerfile(task=task, image_name=image_name)
            prevalidation_metadata = _prevalidation_topology_metadata(prevalidation)
            if prevalidation["success"]:
                if self.config.topology_scheduler_enabled and not self.config.topology_enabled:
                    submission = "No-topology scheduler passed deterministic build/run validation."
                elif self.config.topology_scheduler_enabled:
                    submission = "Topology scheduler passed deterministic build/run validation."
                else:
                    submission = "Topology-prefill Dockerfile passed deterministic build/run prevalidation."
                info = {
                    "exit_status": "Submitted",
                    "submission": submission,
                    "prefill_validated_first": True,
                    **prevalidation_metadata,
                }
                if self.topology_artifacts:
                    info["topology_artifacts"] = self.topology_artifacts
                self._record_experiment_result(info=info, total_seconds=time.perf_counter() - started_at)
                return info
            if prevalidation.get("infra_blocker"):
                info = {
                    "exit_status": "Failed",
                    "submission": prevalidation["output"].get("output", "Environment preflight blocked."),
                    "prefill_validated_first": False,
                    **prevalidation_metadata,
                }
                if self.topology_artifacts:
                    info["topology_artifacts"] = self.topology_artifacts
                self._record_experiment_result(info=info, total_seconds=time.perf_counter() - started_at)
                return info
        else:
            prevalidation_metadata = {}

        info = self._run_agent_loop(task, **kwargs)
        for key, value in prevalidation_metadata.items():
            info.setdefault(key, value)
        self._record_experiment_result(info=info, total_seconds=time.perf_counter() - started_at)
        if self.topology_artifacts:
            info["topology_artifacts"] = self.topology_artifacts
        return info


def _prevalidation_topology_metadata(prevalidation: dict) -> dict:
    keys = (
        "multistage_used",
        "threaded_build_used",
        "parallel_build_branches",
        "parallel_repair_used",
        "repair_branches_tried",
        "build_cache_enabled",
        "build_cache_namespace",
        "build_cache_reuse_existing",
        "build_cache_id_scope",
        "build_cache_ids",
        "final_image",
        "internal_branch_image_count",
        "build_scheduler_strategy",
        "ready_queue_max_size",
        "resource_wait_seconds",
        "critical_path_seconds",
        "node_wait_seconds",
        "node_build_seconds",
        "reused_internal_images",
        "internal_image_reused_count",
        "rebuild_saved_nodes",
        "cache_hit_steps",
        "cache_total_steps",
        "cache_hit_by_node",
        "cache_total_by_node",
        "cache_hit_rate_by_node",
        "dag_failed_step_id",
        "repaired_step_ids",
        "reused_step_ids",
    )
    return {key: prevalidation[key] for key in keys if key in prevalidation}


def _scheduler_topology_metadata(result) -> dict:
    return {
        "build_scheduler_strategy": result.build_scheduler_strategy,
        "ready_queue_max_size": result.ready_queue_max_size,
        "resource_wait_seconds": result.resource_wait_seconds,
        "critical_path_seconds": result.critical_path_seconds,
        "node_wait_seconds": result.node_wait_seconds,
        "node_build_seconds": result.node_build_seconds,
        "reused_internal_images": result.reused_internal_images,
        "internal_image_reused_count": result.internal_image_reused_count,
        "rebuild_saved_nodes": result.rebuild_saved_nodes,
        "cache_hit_steps": result.cache_hit_steps,
        "cache_total_steps": result.cache_total_steps,
        "cache_hit_by_node": result.cache_hit_by_node,
        "cache_total_by_node": result.cache_total_by_node,
        "cache_hit_rate_by_node": result.cache_hit_rate_by_node,
        "dag_failed_step_id": result.dag_failed_step_id,
        "repaired_step_ids": result.repaired_step_ids,
        "reused_step_ids": result.reused_step_ids,
    }


def _attribution_metadata(attribution: FailureAttribution | None) -> dict[str, str]:
    if attribution is None:
        return {}
    return {
        "topology_node_id": attribution.topology_node_id,
        "attribution_confidence": f"{attribution.confidence:.2f}",
        "attribution_reason": attribution.reason,
    }


def _pattern_source_projects(pattern_matches: list[dict]) -> list[str]:
    projects: list[str] = []
    for match in pattern_matches:
        for project in match.get("source_projects") or []:
            if project not in projects:
                projects.append(project)
    return projects


def _build_cache_ids(cache_plan) -> dict[str, str]:
    if not getattr(cache_plan, "enabled", False):
        return {}
    return {
        "apt_cache_id": cache_plan.apt_cache_id,
        "apt_lists_cache_id": cache_plan.apt_lists_cache_id,
        "pip_cache_id": cache_plan.pip_cache_id,
        "dependency_hash": cache_plan.dependency_hash,
        "namespace": cache_plan.namespace,
        "cache_id_scope": cache_plan.cache_id_scope,
    }


def _docker_command_stage(command: str) -> str | None:
    lowered = command.lower()
    if "docker build" in lowered:
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
