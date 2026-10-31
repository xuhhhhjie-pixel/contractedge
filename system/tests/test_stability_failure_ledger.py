import json

from contractedge.contracts.failures import classify_exception, make_failure, project_terminal_status
from contractedge.orchestration.semantic_delivery import SemanticDeliveryError
from contractedge.evaluation.gt_adapter import project_statuses, score_run
from contractedge.model.budget import BudgetExceeded
from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelRequest
from contractedge.retrieval.candidates import build_candidate_set, trim_context_bundle


def test_known_failures_are_not_collapsed_into_interruptions():
    assert classify_exception(BudgetExceeded("cap"), stage="planning")[0] == "BUDGET_EXHAUSTED"
    assert classify_exception(ValueError("bad response"), stage="diagnosis")[0] == "INVALID_OUTPUT"
    assert classify_exception(TimeoutError("provider did not answer"), stage="review")[0] == "RESPONSE_UNKNOWN"
    assert classify_exception(RuntimeError("unclassified crash"), stage="execute")[0] == "INTERRUPTED"
    assert classify_exception(SemanticDeliveryError("missing evidence"), stage="review")[0] == "EVIDENCE_DELIVERY_INCOMPLETE"


def test_provider_ledger_marks_unknown_dispatch_without_replaying():
    class Unknown:
        def complete_json(self, request):
            raise TimeoutError("no response")
    request = ModelRequest(model="fixture", prompt_version="p", messages=(), max_output_tokens=1)
    provider = BudgetedProvider(Unknown(), len, max_tokens=100, max_seconds=30, max_calls=2)
    try:
        provider.complete_json(request)
    except TimeoutError:
        pass
    assert provider.calls[0]["dispatch_state"] == "response_unknown"
    assert len(provider.calls) == 1


def test_cleanup_secondary_failure_does_not_replace_process_root():
    primary = make_failure("candidate_process", "TIMEOUT", failure_class="execution")
    cleanup = make_failure("cleanup", "CLEANUP_UNCONFIRMED", failure_class="infrastructure", secondary=True)
    assert project_terminal_status(primary, cleanup) == "TIMEOUT"
    assert project_terminal_status(None, cleanup) == "INFRASTRUCTURE_ERROR"


def test_public_execution_complete_unknown_is_not_semantically_scored():
    trace = {"status": "PUBLIC_EXECUTION_COMPLETE", "public_semantic_status": "UNKNOWN",
             "diagnosis": {"units": [], "expected_relations": [], "violations": []}}
    assert project_statuses(trace) == {
        "method_status": "SEMANTIC_UNKNOWN", "execution_status": "COMPLETE",
        "public_status": "UNKNOWN", "independent_score_status": "NOT_RUN"}
    result = score_run(trace, {"case_id": "C", "expected_relations": [], "violations": [],
                               "precision_eligible": True, "recall_eligible": True})
    assert result["scoring_status"] == "SEMANTIC_UNKNOWN"
    assert result["precision"] is None and result["recall"] is None


def test_candidate_delivery_audit_distinguishes_drop_from_delivery():
    units = [
        {"id": "A", "path": "a.go", "text": "selected consumer", "origin_kind": "source"},
        {"id": "B", "path": "b.go", "text": "unrelated context", "origin_kind": "source"},
    ]
    candidates = build_candidate_set("selected", None, units, [], top_n=2,
                                     max_context_tokens=100, token_counter=len)
    assert {row["id"] for row in candidates.delivery_audit} == {"A", "B"}
    trimmed = trim_context_bundle(candidates, request_tokens=101, limit=100)
    assert any(row["drop_reason"] == "FULL_REQUEST_CONTEXT_BUNDLE"
               for row in trimmed.delivery_audit if not row["delivered"])
