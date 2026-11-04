"""A present source branch may still lack a legal semantic change target."""
from copy import deepcopy
import hashlib

import pytest

from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_diagnosis
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan


def binding_fixture(suffix="go", add_target=False):
    path = f"lib/worker.{suffix}"
    raw = valid_diagnosis()
    raw["evidence_refs"][1]["locator"]["path"] = path
    text = "alternate implementation branch"
    evidence = dict(id="EV3", snapshot_id=raw["snapshot_id"], kind="SOURCE_SPAN",
        locator=dict(path=path, start_line=100, end_line=110), excerpt=text,
        content_digest=hashlib.sha256(text.encode()).hexdigest(), origin="public fixture")
    raw["evidence_refs"].append(evidence)
    if add_target:
        raw["units"].append({**deepcopy(raw["units"][1]), "id": "U3", "locator_refs": ["EV3"]})
    packet = validate_diagnosis(raw, parse_method_input(valid_method_input()), None)
    plan = valid_plan()
    plan["diagnosis_digest"] = canonical_digest(packet.raw)
    plan["changes"][0]["target"]["path"] = path
    plan["production_path_review"] = [dict(source_evidence_id="EV3", disposition="MODIFY",
        rationale="Apply the correction to the alternate branch too.", change_refs=["C1"], open_obligation_refs=[])]
    frontier = [dict(source_evidence_id="EV3", snapshot_id=packet.snapshot_id, path=path,
        symbol="alternate", locator=dict(start_line=100, end_line=110),
        content_digest=evidence["content_digest"], text=text)]
    return packet, plan, frontier


@pytest.mark.parametrize("suffix", ["go", "py", "ts"])
def test_present_branch_without_semantic_target_has_specific_binding_feedback(suffix):
    packet, plan, frontier = binding_fixture(suffix)
    before = deepcopy(plan)
    result = validate_semantic_changeset(plan, packet, None, frontier=frontier)
    codes = {issue.code for issue in result.errors}
    assert "PRODUCTION_BRANCH_CHANGE_INVALID" in codes
    assert "PRODUCTION_BRANCH_TARGET_UNAVAILABLE" in codes
    message = next(e.message for e in result.errors if e.code == "PRODUCTION_BRANCH_TARGET_UNAVAILABLE")
    assert "EV3" in message and "100" in message and "110" in message
    assert "diagnosis" in message.lower()
    assert plan == before


def test_wrong_reference_reports_the_existing_eligible_unit_without_rediagnosis_claim():
    packet, plan, frontier = binding_fixture(add_target=True)
    result = validate_semantic_changeset(plan, packet, None, frontier=frontier)
    assert "PRODUCTION_BRANCH_TARGET_UNAVAILABLE" not in {e.code for e in result.errors}
    message = next(e.message for e in result.errors if e.code == "PRODUCTION_BRANCH_CHANGE_INVALID")
    assert "U3" in message and "EV3" in message


def test_binding_correct_unit_restores_coverage_without_relaxing_branch_gate():
    packet, plan, frontier = binding_fixture(add_target=True)
    plan["changes"][0]["target"]["unit_refs"] = ["U3"]
    result = validate_semantic_changeset(plan, packet, None, frontier=frontier)
    assert result.valid, result.errors


def test_only_exact_current_present_source_can_request_target_rebinding():
    from contractedge.changeset.validate import production_branch_rebinding_gaps
    packet, plan, frontier = binding_fixture()
    before = deepcopy(plan)
    gaps = production_branch_rebinding_gaps(plan, packet, frontier)
    assert len(gaps) == 1
    assert gaps[0]["locator_hint"] == dict(path="lib/worker.go", symbol="alternate")
    assert "diagnosis" in gaps[0]["summary"].lower()
    assert plan == before


@pytest.mark.parametrize("failure", ["available_unit", "stale", "digest", "range", "unknown_change", "wrong_path", "not_modify"])
def test_binding_reassessment_is_not_a_general_retry_or_permission_bypass(failure):
    from contractedge.changeset.validate import production_branch_rebinding_gaps
    packet, plan, frontier = binding_fixture(add_target=failure == "available_unit")
    if failure == "stale": frontier[0]["snapshot_id"] = "old"
    if failure == "digest": frontier[0]["content_digest"] = "0" * 64
    if failure == "range": frontier[0]["locator"]["start_line"] = 99
    if failure == "unknown_change": plan["production_path_review"][0]["change_refs"] = ["absent"]
    if failure == "wrong_path": plan["changes"][0]["target"]["path"] = "other.go"
    if failure == "not_modify": plan["production_path_review"][0]["disposition"] = "PRESERVE"
    assert production_branch_rebinding_gaps(plan, packet, frontier) == []
