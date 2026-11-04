"""Pure routing extracted from runner and repair; no dispatch, edit or I/O."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Mapping

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


def resolve_repair_route(trace, feedback, *, current_snapshot, mode='full',
                         retry_restored_candidate=False, previous_attempt=None):
    """Resolve continuation from the selected source, never the rejected trial.

    Feedback proposes a route. This is the sole repair-loop policy boundary;
    transition() then verifies diagnosis freshness before issuing the effect.
    Both ordinary plan revision and prerequisite review use that same gate.
    """
    plan = (trace or {}).get('changeset') or {}
    review_prerequisites = bool(mode == 'full' and trace is not None and retry_restored_candidate
        and plan_reassessment_required(plan, current_snapshot, previous_attempt))
    if review_prerequisites:
        route = 'CHANGESET'
    elif mode == 'full' and trace is not None and behavior_plan_reassessment_required(plan, current_snapshot, feedback):
        feedback = prerequisite_review_feedback(feedback)
        route = 'DIAGNOSIS'
    else:
        route = ('EXECUTE' if retry_restored_candidate else feedback['route'] if feedback
                 else 'EXECUTE' if trace is not None else 'DIAGNOSIS')
    return dict(route=route, feedback=feedback, review_prerequisites=review_prerequisites)


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


def plan_reassessment_required(plan: Mapping, current_snapshot: str, previous: Mapping | None) -> bool:
    """Request a prerequisite review, without attributing the runtime failure.

    Compiler errors and unrelated/unknown probe errors keep the existing edit
    route. A verified readiness failure questions whether a required runtime
    prerequisite was established; the planner may retain or revise its intent.
    """
    if not any(p.get('disposition') == 'REQUIRED' for p in plan.get('precondition_review', [])):
        return False
    previous = previous or {}
    trial = previous.get('rejected_trial') or {}
    decision = trial.get('decision') or trial.get('candidate_decision') or {}
    if trial.get('decision') and trial.get('candidate_decision') and trial['decision'] != trial['candidate_decision']:
        return False
    historical = trial.get('feedback') or {}
    if not (previous.get('source_snapshot') == current_snapshot
            and decision.get('decision') == 'UNCOMPARABLE'
            and decision.get('from_snapshot') == current_snapshot
            and trial.get('snapshot_id') and trial['snapshot_id'] != current_snapshot
            and decision.get('trial_snapshot') == trial['snapshot_id'] == historical.get('to_snapshot')
            and historical.get('build_state') == 'FAILED'):
        return False
    from contractedge.execution.public import is_registered_readiness_failure
    return any(isinstance(f, Mapping) and f.get('kind') == 'CANDIDATE_RUNTIME_FAILURE'
        and f.get('snapshot_id') == trial['snapshot_id'] and is_registered_readiness_failure(f)
        for f in historical.get('observation_diagnostics', []))


def behavior_plan_reassessment_required(plan: Mapping, current_snapshot: str, feedback: Mapping | None) -> bool:
    """A bound necessary failure can question intent even when startup succeeds."""
    if (not feedback or feedback.get('to_snapshot') != current_snapshot
            or feedback.get('semantic_outcome') != 'PUBLIC_REVIEW_FAIL'
            or not any(p.get('disposition') == 'REQUIRED' for p in plan.get('precondition_review', []))):
        return False
    from contractedge.execution.support import deterministic_check_result
    return any((result := deterministic_check_result(condition, feedback.get('public_observations', []),
        snapshot_id=current_snapshot, capabilities=feedback.get('public_execution_capabilities')))
        and result['status'] == 'FAIL'
        for kind in ('postconditions', 'preserve_constraints', 'open_obligations')
        for condition in plan.get(kind, []))


def prerequisite_review_feedback(feedback: Mapping) -> dict:
    """Use the existing uncertain diagnosis-stage route, not a causal verdict."""
    result = {**feedback, 'route': 'DIAGNOSIS', 'next_action': 'DIAGNOSIS',
        'failure_kind': 'DIAGNOSIS', 'route_confidence': 'LOW',
        'rationale': 'A current necessary behavior failed under prerequisite-dependent intent. Reassess current '
        'source and accepted assumptions before another edit; this route is not proof that the plan caused it. '
        'DIAGNOSIS selects the work stage at LOW confidence, not proven source blame. This review instruction '
        'supersedes the old execution-first instruction. The unchanged original feedback retains its attribution '
        'and rationale; do not treat that historical instruction as the current action.'}
    result.pop('routing_policy', None)
    return result

