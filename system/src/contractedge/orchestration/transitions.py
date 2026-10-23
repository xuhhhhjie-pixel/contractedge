"""Pure routing extracted from runner and repair; no dispatch, edit or I/O."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from contractedge.contracts.stages import StageResult
from contractedge.contracts.validation import canonical_digest
from contractedge.orchestration.state import RepairState


@dataclass(frozen=True)
class TransitionDecision:
    next_state: RepairState
    action: str
    reason: str
    result: StageResult
    effect_id: str


def transition(state: RepairState, result: StageResult) -> TransitionDecision:
    refs = state.artifact_refs
    if result.payload_ref is not None and result.payload_ref not in refs:
        refs += (result.payload_ref,)
    next_state = replace(state, revision=state.revision + 1,
                         artifact_refs=refs, status=result.status)
    if result.stage == "feedback":
        next_state = replace(next_state, current_snapshot=result.snapshot_id,
            obligation_refs=state.obligation_refs + (() if result.payload_ref is None else (result.payload_ref,)))
    action, reason = "STOP", result.status
    if result.status in {"RESPONSE_UNKNOWN", "BUDGET_EXHAUSTED", "ENVIRONMENT_ERROR",
                         "DELIVERY_INCOMPLETE", "FORMAT_INVALID"}:
        # Unknown dispatch and environmental failures never manufacture retries.
        pass
    elif result.stage == "diagnosis":
        if result.snapshot_id != state.current_snapshot:
            action, reason = "REDIAGNOSE", "DIAGNOSIS_SNAPSHOT_STALE"
            next_state = replace(next_state, diagnosis_ref=None, diagnosis_snapshot=None)
        elif result.status in {"READY", "PARTIAL_READY"}:
            action, reason = "PLAN", "VALID_CURRENT_DIAGNOSIS"
            next_state = replace(next_state, diagnosis_ref=result.payload_ref,
                                 diagnosis_snapshot=result.snapshot_id)
        else:
            reason = "DIAGNOSIS_NOT_READY"
    elif result.stage == "round_start":
        next_state = replace(next_state, current_snapshot=result.snapshot_id)
        if result.route == "DIAGNOSIS":
            action = "DIAGNOSE"
        elif result.route == "CHANGESET":
            action = "REVISE_PLAN" if state.diagnosis_snapshot == result.snapshot_id else "REDIAGNOSE"
            reason = "VALID_CURRENT_DIAGNOSIS" if action == "REVISE_PLAN" else "DIAGNOSIS_SNAPSHOT_STALE"
        elif result.route == "EXECUTE":
            action = "EXECUTE"
        else:
            action = "DIAGNOSE" if state.diagnosis_snapshot is None else "EXECUTE"
    elif result.stage == "plan" and result.status == "READY":
        action, reason = "EXECUTE", "ACCEPTED_CURRENT_PLAN"
    elif result.stage == "incumbent":
        next_state = replace(next_state, incumbent=result.payload_ref, selected=result.payload_ref,
                             current_snapshot=result.snapshot_id)
        action, reason = "DIAGNOSE", "ACTUAL_INCUMBENT_OBSERVED"
    elif result.stage == "observation":
        next_state = replace(next_state, current_snapshot=result.snapshot_id)
        action, reason = "REVIEW", "ACTUAL_PUBLIC_OBSERVATION_RECORDED"
    elif result.stage == "candidate_selection":
        if result.status == "COMPLETE":
            next_state = replace(next_state, incumbent=result.payload_ref, selected=result.payload_ref, trial=None)
        action, reason = "CONTINUE", "EXISTING_CANDIDATE_DECISION"
    elif result.stage == "rollback":
        next_state = replace(next_state, current_snapshot=result.snapshot_id, trial=None)
        action, reason = "CONTINUE", "ACTUAL_INCUMBENT_RESTORED"
    elif result.stage == "feedback":
        # Feedback carries the still-authoritative preservation and pending sets.
        # A local PASS never clears them or proves a parent obligation closed.
        action = {"DIAGNOSIS": "DIAGNOSE", "CHANGESET": "REVISE_PLAN",
                  "EXECUTE": "EXECUTE", "STOP": "STOP"}[result.route]
        reason = "EXISTING_VALIDATED_FEEDBACK_ROUTE"
        if action == "REVISE_PLAN" and state.diagnosis_snapshot != result.snapshot_id:
            action, reason = "REDIAGNOSE", "DIAGNOSIS_SNAPSHOT_STALE"
        if result.status == "EDIT_REJECTED":
            # Rejected edits did not change source or invalidate a valid diagnosis.
            reason = "EDIT_REJECTED_DIAGNOSIS_RETAINED"
        if result.terminal_status is not None and action == "STOP":
            next_state = replace(next_state, status=result.terminal_status)
    elif result.stage == "selection":
        next_state = replace(next_state, current_snapshot=result.snapshot_id,
                             selected=result.payload_ref, trial=None,
                             status=result.terminal_status or result.status)
        action, reason = "COMPLETE", "ACTUAL_SELECTION_RECORDED"
    elif result.stage == "execute":
        rejected = result.status in {"EDIT_REJECTED", "PLAN_INVALID"}
        next_state = replace(next_state, current_snapshot=result.snapshot_id,
                             trial=None if rejected else result.payload_ref)
        action, reason = ("FEEDBACK" if rejected else "OBSERVE"), "ACTUAL_EXECUTION_RECORDED"
    identity = {"revision": state.revision, "snapshot": state.current_snapshot,
                "result": asdict(result), "action": action, "reason": reason}
    return TransitionDecision(next_state, action, reason, result, canonical_digest(identity))
