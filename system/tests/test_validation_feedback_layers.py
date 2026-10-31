from contractedge.contracts.validation import compact_validation_feedback
from contractedge.orchestration.runner import _format_validation_feedback


def test_schema_errors_block_later_layer_feedback():
    result = compact_validation_feedback([
        {"code": "SCHEMA_INVALID", "path": "changes[0]", "message": "wrong type"},
        {"code": "EVIDENCE_DIGEST_NOT_IN_CANDIDATES", "path": "evidence", "message": "stale"},
        {"code": "PRESERVATION_IMPACT_INCOMPLETE", "path": "preservation", "message": "missing"},
    ])
    assert [row["code"] for row in result] == ["SCHEMA_INVALID"]
    assert result[0]["layer"] == "schema"


def test_evidence_errors_block_semantic_feedback_and_are_bounded():
    issues = [{"code": "EVIDENCE_DIGEST_NOT_IN_CANDIDATES", "path": str(i), "message": "stale"}
              for i in range(20)]
    issues.append({"code": "PRESERVATION_IMPACT_INCOMPLETE", "path": "p", "message": "later"})
    result = compact_validation_feedback(issues, max_per_layer=3)
    assert len(result) == 3
    assert all(row["layer"] == "evidence" for row in result)


def test_diagnosis_feedback_declares_active_layer():
    message = _format_validation_feedback(
        ValueError("CAUSAL_CLAIM_UNSUPPORTED at violations"), {})
    assert message.startswith("Validation layer: semantic.")
    assert "Return the full corrected Diagnosis body" in message
