"""Pure counterexamples plus real production entry checks for C1 extraction."""
from dataclasses import asdict, replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from contractedge.contracts.artifacts import ArtifactRef
from contractedge.contracts.stages import StageResult, STAGE_STATUSES, feedback_status
from contractedge.development import public_demo
from contractedge.orchestration.recorder import RunRecorder
from contractedge.orchestration.state import PolicyLedger, RepairState, RepairEngine
from contractedge.orchestration.transitions import transition


def reference(name="diagnosis", snapshot="S0"):
    return ArtifactRef(name, name + ".json", "a" * 64, snapshot, "b" * 64)


def read(path):
    return json.loads(Path(path).read_text())


def test_current_diagnosis_gate_is_pure_and_stale_diagnosis_cannot_plan():
    state = RepairState("S1", diagnosis_ref=reference(), diagnosis_snapshot="S0")
    before = asdict(state)
    result = StageResult("diagnosis", "READY", reference(), snapshot_id="S0")
    decision = transition(state, result)
    assert decision.action == "REDIAGNOSE"
    assert decision.next_state.diagnosis_ref is None
    assert asdict(state) == before
    assert decision == transition(state, result)
    current = transition(RepairState("S0"), result)
    assert current.action == "PLAN"


def test_rejected_edit_keeps_valid_diagnosis_and_existing_retry_route():
    diagnosis = reference()
    state = RepairState("S0", diagnosis_ref=diagnosis, diagnosis_snapshot="S0")
    rejected = StageResult("feedback", "EDIT_REJECTED", reference("feedback"),
                           snapshot_id="S0", route="EXECUTE")
    decision = transition(state, rejected)
    assert decision.action == "EXECUTE"
    assert decision.next_state.diagnosis_ref == diagnosis
    assert decision.next_state.diagnosis_snapshot == "S0"


def test_stale_plan_feedback_rediagnoses_but_execution_retry_keeps_historical_intent():
    state = RepairState("S1", diagnosis_ref=reference(), diagnosis_snapshot="S0")
    assert transition(state, StageResult("round_start", "READY", None, snapshot_id="S1",
                                        route="CHANGESET")).action == "REDIAGNOSE"
    assert transition(state, StageResult("round_start", "READY", None, snapshot_id="S1",
                                        route="EXECUTE")).action == "EXECUTE"


@pytest.mark.parametrize("status", ["DELIVERY_INCOMPLETE", "FORMAT_INVALID", "ENVIRONMENT_ERROR",
                                   "BUDGET_EXHAUSTED", "RESPONSE_UNKNOWN"])
def test_explicit_stops_never_dispatch_or_infer_causality(status):
    assert transition(RepairState("S0"), StageResult("front", status, None,
                                                   snapshot_id="S0")).action == "STOP"


def test_policy_allowances_are_not_reset_by_evidence_or_rediagnosis():
    policy = PolicyLedger({"max_format_retries": 1, "max_rounds": 2})
    policy.consume("format_correction", "planning:front")
    policy.consume("evidence_extension", "front")
    assert not policy.available("format_correction", "planning:front")
    with pytest.raises(ValueError, match="exhausted"):
        policy.consume("format_correction", "planning:front")
    policy.consume("repair_round", "case")
    with pytest.raises(ValueError, match="exhausted"):
        policy.consume("repair_round", "case")
    assert policy.limit("truncation_recovery") == 1
    assert PolicyLedger({"max_format_retries": 2}).limit("truncation_recovery") == 2


def test_execution_exit_and_uncertain_plan_attribution_are_not_semantic_diagnoses():
    feedback = {"failure_kind": "EXECUTION", "build_state": "FAILED", "semantic_outcome": "UNKNOWN"}
    assert feedback_status(feedback) == "EXECUTION_FAILURE"
    feedback.update(failure_kind="PLAN", build_state="PASSED", semantic_outcome="PUBLIC_REVIEW_FAIL")
    assert feedback_status(feedback) == "BEHAVIOR_FAIL"


def test_rediagnosis_and_execution_retry_share_the_existing_round_cap(tmp_path):
    recorder = RunRecorder(tmp_path, run_id="c1", input_digest="b" * 64)
    engine = RepairEngine(recorder, "S0", {"max_rounds": 2})
    engine.start_round(StageResult("round_start", "READY", None, snapshot_id="S0", route="EXECUTE"),
                       repeated=True)
    assert engine.policy.used("execution_retry", "case") == 1
    assert engine.policy.used("rediagnosis", "case") == 0
    with pytest.raises(ValueError, match="exhausted"):
        engine.start_round(StageResult("round_start", "READY", None, snapshot_id="S0", route="DIAGNOSIS"),
                           repeated=True)
    assert engine.policy.used("rediagnosis", "case") == 0


def test_commit_is_revision_checked_and_retains_existing_artifact_reference(tmp_path):
    recorder = RunRecorder(tmp_path, run_id="c1", input_digest="b" * 64)
    payload = recorder.capture_value({"status": "READY"}, "FINAL_DIAGNOSIS", snapshot_id="S0")
    engine = RepairEngine(recorder, "S0", {})
    decision = engine.step(StageResult("diagnosis", "READY", payload, snapshot_id="S0"))
    committed = read(tmp_path / "journal/transitions/revision-000001.json")
    assert committed["effect_id"] == decision.effect_id
    assert committed["effect_status"].startswith("INTENT_COMMITTED")
    assert committed["result"]["payload_ref"] == asdict(payload)
    assert read(tmp_path / "state.json")["revision"] == 1
    with pytest.raises(ValueError, match="revision conflict"):
        recorder.commit_transition(0, decision)


def test_partial_public_pass_retains_pending_and_preservation_authority(tmp_path):
    output = tmp_path / "run"
    result = public_demo.run_demo("two-consumers", output)
    assert result["status"] == "PUBLIC_REVIEW_COMPLETE"
    root = output / "repair"
    first = read(root / "rounds/0/feedback.json")
    assert first["pending_obligations"]
    commits = [read(p) for p in sorted((root / "journal/transitions").glob("revision-*.json"))]
    first_feedback = next(c for c in commits if c["result"]["stage"] == "feedback")
    assert first_feedback["action"] == "EXECUTE"
    refs = first_feedback["next_state"]["obligation_refs"]
    assert refs and refs[-1] == first_feedback["result"]["payload_ref"]
    receipt = read(root / refs[-1]["relative_path"])
    authoritative = read(root / receipt["output_refs"][0]["relative_path"])
    assert authoritative["pending_obligations"] == first["pending_obligations"]
    final = read(root / "state.json")
    assert refs[-1] in final["obligation_refs"]
    assert final["selected"]["kind"] == "STAGE_RECEIPT"
    assert final["current_snapshot"] == read(root / "repair.trace.json")["final_snapshot_id"]


def test_real_entry_rejects_diagnosis_if_source_changes_before_planning(tmp_path):
    factory = public_demo._provider
    def provider(config, case, runner):
        inner, counter = factory(config, case, runner)
        class Changed:
            def complete_json(self, request):
                response = inner.complete_json(request)
                if request.prompt_version.startswith("diagnosis."):
                    path = runner.source / "app.py"
                    path.write_text(path.read_text() + "\n# external concurrent edit\n")
                return response
        return Changed(), counter
    with patch.object(public_demo, "_provider", provider):
        result = public_demo.run_demo("reload-state", tmp_path / "run")
    assert result["status"] == "EVIDENCE_GAP"
    calls = read(tmp_path / "run/repair/model_calls.json")
    assert len(calls) == 1
    assert not any(c["prompt_version"].startswith("changeset.") for c in calls)
    commits = [read(p) for p in (tmp_path / "run/repair/journal/transitions").glob("revision-*.json")]
    assert any(c["reason"] == "DIAGNOSIS_SNAPSHOT_STALE" and c["action"] == "REDIAGNOSE" for c in commits)


def test_real_entry_edit_rejection_preserves_diagnosis_without_new_diagnosis_call(tmp_path):
    from contractedge.execution.topomen_agent import TopoMenAgentExecutor
    from contractedge.execution.topomen import PlanRejected
    original = TopoMenAgentExecutor.execute
    attempts = []
    def reject_once(self, *args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise PlanRejected("fixture concrete edit rejected before source change")
        return original(self, *args, **kwargs)
    with patch.object(TopoMenAgentExecutor, "execute", reject_once):
        result = public_demo.run_demo("config-binding", tmp_path / "run")
    assert result["status"] == "PUBLIC_REVIEW_COMPLETE", result
    root = tmp_path / "run/repair"
    calls = read(root / "model_calls.json")
    assert sum(c["prompt_version"].startswith("diagnosis.") for c in calls) == 1
    commits = [read(p) for p in sorted((root / "journal/transitions").glob("revision-*.json"))]
    rejected = next(c for c in commits if c["result"]["stage"] == "feedback" and c["result"]["status"] == "EDIT_REJECTED")
    assert rejected["next_state"]["diagnosis_ref"] is not None
    assert rejected["next_state"]["diagnosis_snapshot"] == rejected["next_state"]["current_snapshot"]


def test_unknown_execution_response_retains_actual_call_ref_without_retry(tmp_path):
    factory = public_demo._provider
    def provider(config, case, runner):
        inner, counter = factory(config, case, runner)
        class UnknownExecution:
            def complete_json(self, request):
                if request.prompt_version == "topomen-source-execution.v1":
                    raise TimeoutError("fixture execution dispatch outcome unknown")
                return inner.complete_json(request)
        return UnknownExecution(), counter
    with patch.object(public_demo, "_provider", provider):
        result = public_demo.run_demo("config-binding", tmp_path / "run")
    assert result["status"] == "INFRASTRUCTURE_ERROR"
    root = tmp_path / "run/repair"
    calls = read(root / "model_calls.json")
    assert len(calls) == 3
    commits = [read(p) for p in sorted((root / "journal/transitions").glob("revision-*.json"))]
    failure = next(c for c in commits if c["result"]["stage"] == "failure")
    assert failure["result"]["status"] == "RESPONSE_UNKNOWN"
    assert failure["action"] == "STOP"
    assert [r["absolute_call_index"] for r in failure["result"]["call_refs"]] == [2]
    assert read(root / "repair.trace.json")["budget"]["reserved_tokens"] == sum(
        c["prompt_tokens_reserved"] + c["output_tokens_reserved"] for c in calls)


def test_invalid_model_format_is_recorded_as_format_invalid_at_real_entry(tmp_path):
    from contractedge.model.provider import ModelResponse
    class Malformed:
        def complete_json(self, request):
            return ModelResponse('{"malformed_diagnosis":true}', {"zero_cost": True})
    with patch.object(public_demo, "_provider", lambda *args: (Malformed(), public_demo.count_tokens)):
        result = public_demo.run_demo("reload-state", tmp_path / "run")
    assert result["status"] == "INVALID_OUTPUT"
    commits = [read(p) for p in (tmp_path / "run/repair/journal/transitions").glob("revision-*.json")]
    assert any(c["result"]["stage"] == "front" and c["result"]["status"] == "FORMAT_INVALID" for c in commits)
    assert not any(c["result"]["status"] == "ENVIRONMENT_ERROR" for c in commits)
