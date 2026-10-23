"""Decision inputs referring to existing evidence, never a second semantic plan."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from contractedge.contracts.artifacts import ArtifactRef, CallRef, identity


STAGE_STATUSES = frozenset({
    "READY", "PARTIAL_READY", "EVIDENCE_GAP", "DELIVERY_INCOMPLETE", "FORMAT_INVALID",
    "PLAN_INVALID", "EDIT_REJECTED", "EXECUTION_FAILURE", "BEHAVIOR_FAIL", "OBSERVATION_INSUFFICIENT",
    "ENVIRONMENT_ERROR", "BUDGET_EXHAUSTED", "RESPONSE_UNKNOWN", "APPLIED",
    "PUBLIC_REVIEW_PASS", "COMPLETE", "CANDIDATE_REJECTED",
})


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: str
    payload_ref: ArtifactRef | None
    issues: tuple[Mapping[str, Any], ...] = ()
    call_refs: tuple[CallRef, ...] = ()
    snapshot_id: str | None = None
    # These are bounded projections of an already validated feedback record.
    # The referenced payload remains authoritative for obligations and evidence.
    route: str | None = None
    terminal_status: str | None = None
    schema_version: str = field(default="ce.stage_result.v1", init=False)

    def __post_init__(self):
        identity(self.stage)
        if self.status not in STAGE_STATUSES:
            raise ValueError("Unsupported explicit stage status: " + str(self.status))
        if self.route not in {None, "DIAGNOSIS", "CHANGESET", "EXECUTE", "STOP"}:
            raise ValueError("Unsupported stage route")
        if self.snapshot_id is not None:
            identity(self.snapshot_id)
        if self.payload_ref is not None and not isinstance(self.payload_ref, ArtifactRef):
            raise ValueError("Stage payload must use the existing ArtifactRef contract")
        if any(not isinstance(ref, CallRef) for ref in self.call_refs):
            raise ValueError("Stage calls must use actual CallRefs")


def front_status(status, *, response_unknown=False):
    if response_unknown:
        return "RESPONSE_UNKNOWN"
    return {
        "READY": "READY", "READY_PARTIAL": "PARTIAL_READY", "PARTIAL_READY": "PARTIAL_READY",
        "INVALID_OUTPUT": "FORMAT_INVALID", "INVALID_PLAN": "PLAN_INVALID",
        "EVIDENCE_DELIVERY_INCOMPLETE": "DELIVERY_INCOMPLETE", "EVIDENCE_GAP": "EVIDENCE_GAP",
        "BUDGET_EXHAUSTED": "BUDGET_EXHAUSTED", "INFRASTRUCTURE_ERROR": "ENVIRONMENT_ERROR",
    }.get(status, "OBSERVATION_INSUFFICIENT")


def feedback_status(feedback):
    """Classify explicit protocol fields; process exit codes are never causal input."""
    if feedback["failure_kind"] == "ENVIRONMENT":
        return "ENVIRONMENT_ERROR"
    if feedback["build_state"] == "BUDGET_EXCEEDED":
        return "BUDGET_EXHAUSTED"
    if feedback["semantic_outcome"] == "PUBLIC_REVIEW_FAIL":
        return "BEHAVIOR_FAIL"
    if feedback["build_state"] == "FAILED":
        return "EXECUTION_FAILURE"
    if feedback["semantic_outcome"] == "PUBLIC_REVIEW_PASS":
        return "PUBLIC_REVIEW_PASS"
    return "OBSERVATION_INSUFFICIENT"


def stop_status(feedback):
    # Preserve the existing public-loop terminal protocol, including exportable UNKNOWN.
    return ("PUBLIC_REVIEW_COMPLETE" if feedback["semantic_outcome"] == "PUBLIC_REVIEW_PASS" else
            "PUBLIC_EXECUTION_COMPLETE" if feedback["build_state"] == "PASSED" else
            "INFRASTRUCTURE_ERROR" if feedback["failure_kind"] == "ENVIRONMENT" else "STOPPED_UNCERTAIN")
