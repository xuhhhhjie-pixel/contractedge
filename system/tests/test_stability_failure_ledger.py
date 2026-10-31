import json

from contractedge.contracts.failures import (FailureRecord, ObservationRejected,
    classify_exception, make_failure, project_terminal_status)
from contractedge.contracts.validation import validate_run_trace
from contractedge.orchestration.semantic_delivery import SemanticDeliveryError
from contractedge.evaluation.gt_adapter import project_statuses, score_run
from contractedge.model.budget import BudgetExceeded
from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelRequest
from contractedge.retrieval.candidates import build_candidate_set, trim_context_bundle
from contractedge.orchestration.runner import RunTrace, _front_failure_projection
from contractedge.contracts.runtime import RuntimeRejected
from contractedge.execution.review import PublicReviewFormatError
from contractedge.orchestration.repair import PlanRejected


def test_known_failures_are_not_collapsed_into_interruptions():
    assert classify_exception(BudgetExceeded("cap"), stage="planning")[0] == "BUDGET_EXHAUSTED"
    assert classify_exception(ValueError("bad response"), stage="diagnosis")[0] == "INVALID_OUTPUT"
    assert classify_exception(TimeoutError("provider did not answer"), stage="review")[0] == "RESPONSE_UNKNOWN"
    assert classify_exception(RuntimeError("unclassified crash"), stage="execute")[0] == "INTERRUPTED"
    assert classify_exception(SemanticDeliveryError("missing evidence"), stage="review")[0] == "EVIDENCE_DELIVERY_INCOMPLETE"
    assert classify_exception(ObservationRejected("observer incomplete"), stage="observe")[0] == "INVALID_OBSERVATION"
    assert classify_exception(ValueError("Unknown execution backend: bad"), stage="front")[0] == "INFRASTRUCTURE_ERROR"


def test_eight_failure_classes_keep_their_engine_layer_and_dispatch_fact():
    cases = [
        (ValueError('bad JSON'), 'INVALID_OUTPUT', 'model_format'),
        (PlanRejected('plan boundary'), 'INVALID_PLAN', 'model_format'),
        (BudgetExceeded('cap'), 'BUDGET_EXHAUSTED', 'infrastructure'),
        (SemanticDeliveryError('required source missing'), 'EVIDENCE_DELIVERY_INCOMPLETE', 'evidence'),
        (TimeoutError('response unknown'), 'RESPONSE_UNKNOWN', 'unknown'),
        (RuntimeRejected('runtime admission'), 'INFRASTRUCTURE_ERROR', 'infrastructure'),
        (PublicReviewFormatError('invalid quote', code='QUOTE_INVALID'), 'INVALID_OUTPUT', 'model_format'),
    ]
    for error, code, failure_class in cases:
        status, record = classify_exception(error, stage='test', dispatch_state='response_known')
        assert status == code and record['failure_class'] == failure_class
    execution = make_failure('candidate_process', 'EXECUTION_FAILED', failure_class='execution',
                             dispatch_state='response_known')
    cleanup = make_failure('cleanup', 'CLEANUP_UNCONFIRMED', failure_class='infrastructure',
                           dispatch_state='response_known', secondary=True)
    assert execution['failure_class'] == 'execution' and cleanup['secondary'] is True


def test_failure_record_is_engine_only_and_preserves_primary_secondary_fact():
    primary = FailureRecord('execute', 'TIMEOUT', failure_class='execution', snapshot_id='a' * 64)
    secondary = FailureRecord('cleanup', 'CLEANUP_UNCONFIRMED', failure_class='infrastructure',
                              secondary=True)
    assert primary.primary is True and secondary.primary is False
    assert primary.to_wire()['snapshot_id'] == 'a' * 64
    assert make_failure('cleanup', 'CLEANUP_UNCONFIRMED', failure_class='infrastructure',
                        secondary=True)['secondary'] is True


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


def test_execution_and_delivery_failures_never_enter_relation_matching():
    gt = {"case_id": "C", "expected_relations": [{"id": "E1", "source": "A", "target": "B"}],
          "violations": [{"relation_id": "E1"}], "precision_eligible": True,
          "recall_eligible": True}
    diagnosis = {"units": [{"id": "A"}, {"id": "B"}],
                 "expected_relations": [{"id": "P1", "source_unit_ids": ["A"],
                                         "target_unit_ids": ["B"]}],
                 "violations": [{"relation_id": "P1"}]}
    for status, kind in (("EXECUTION_FAILED", "EXECUTION"),
                         ("TIMEOUT", "EXECUTION"),
                         ("OUTPUT_LIMIT", "EXECUTION"),
                         ("EVIDENCE_DELIVERY_INCOMPLETE", "EVIDENCE")):
        result = score_run({"status": status, "diagnosis": diagnosis}, gt)
        assert result["failure_kind"] == kind
        assert result["precision"] is None and result["recall"] is None
        assert "expected_relation_counts" not in result


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


def test_front_trace_persists_failure_record_and_unknown_dispatch():
    trace = RunTrace('run', 'case', 'a' * 64, 'b' * 64, 'INFRASTRUCTURE_ERROR',
                     None, None, None, None, None,
                     ({'stage': 'DIAGNOSIS', 'dispatch_state': 'response_unknown',
                       'error': 'TimeoutError: no response'},),
                     ('provider timed out',))
    wire = _front_failure_projection(trace).to_wire()
    assert wire['failure_records'][0]['code'] == 'RESPONSE_UNKNOWN'
    assert wire['failure_records'][0]['dispatch_state'] == 'response_unknown'
    assert wire['primary_failure']['snapshot_id'] == 'b' * 64
    assert validate_run_trace(wire)['failure_records'][0]['stage'] == 'provider'
