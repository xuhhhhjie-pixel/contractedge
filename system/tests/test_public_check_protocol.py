"""Regressions for the public-check producer -> schema -> support protocol.

The runtime fixture supplies bytes and executes only the production checker.
No candidate, Docker, model API, or money ledger is used.  In particular these
tests never repair a producer's missing scenario by adding one to its records.
"""
import copy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.validation import (
    canonical_digest,
    parse_method_input,
    validate_diagnosis,
)
from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
from contractedge.execution import public_observer
from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.provider import FixtureProvider, OpenAICompatibleProvider
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan
from tests.test_public_observer import (
    _CheckerOnlySession,
    actual_output,
    public_scenarios,
)


# Explicit local suite scope; per-check finite members remain independently declared.
# Migrated from PR #19 per-predicate scenario names, without weakening scope checks.
SCOPES = {
    "selected": {"scenario": "selected-and-preservation"},
    "disabled": {"scenario": "selected-and-preservation"},
    "stable": {"scenario": "selected-and-preservation"},
}
CHECK_IDS = {
    "selected": "contractedge-public-process-v1:selected",
    "disabled": "contractedge-public-process-v1:disabled",
    "stable": "contractedge-public-process-v1:stable",
}


def capabilities():
    return {
        "method_behavior_probes": [],
        "registered_public_checks": public_observer.public_check_capabilities(),
    }


def test_registered_checks_reach_final_schema_without_method_probes_or_traceability():
    diagnosis = validate_diagnosis(
        valid_diagnosis(), parse_method_input(valid_method_input()), None
    )
    diagnosis.raw["requirement_spec"]["traceability_required"] = False
    public_capabilities = capabilities()
    context = SimpleNamespace(
        run_id="public-check-schema",
        case_id="RW-DEMO-001",
        snapshot_id="snap-0",
        input_digest=diagnosis.input_digest,
        model_config={"model": "fixture-public-check"},
        budget={"max_output_tokens": 2048},
        retrieval_config={"public_execution_capabilities": public_capabilities},
    )
    request = build_changeset_request(diagnosis, context)
    # Exercise real transport preparation without dispatch, a key, or HTTP.
    provider = OpenAICompatibleProvider("https://fixture.invalid/v1", "UNUSED_TEST_KEY")
    serialized = json.loads(provider.prepare_request(request).body)
    packet = json.loads(serialized["messages"][1]["content"])
    assert packet["output_schema"] == request.output_schema
    assert packet["public_execution_capabilities"]["method_behavior_probes"] == []
    for group in ("postconditions", "preserve_constraints", "open_obligations"):
        item = packet["output_schema"]["properties"][group]["items"]
        fields = item["properties"]
        assert "behavior_check_refs" in fields, (group, fields.keys())
        assert "check_coverage" in fields, (group, fields.keys())
        assert "coverage_rationale" in fields, (group, fields.keys())
        assert item["additionalProperties"] is False
        refs = Draft202012Validator(fields["behavior_check_refs"])
        assert not list(refs.iter_errors([CHECK_IDS["selected"]]))
        assert list(refs.iter_errors(["contractedge-public-process-v1:invented"]))
        assert not list(
            Draft202012Validator(fields["check_coverage"]).iter_errors("ALL_OF")
        )
        for undeclared in ("PARTIAL", "UNDECLARED"):
            assert list(Draft202012Validator(fields["check_coverage"]).iter_errors(undeclared))
    unobservable = valid_plan()["postconditions"][0]
    unobservable["verification_kind"] = "NOT_PUBLICLY_OBSERVABLE"
    assert not list(Draft202012Validator(
        packet["output_schema"]["properties"]["postconditions"]["items"]
    ).iter_errors(unobservable)), "Available checks must not force an unbound predicate to invent coverage"
    published = packet["public_execution_capabilities"]["registered_public_checks"]
    by_id = {row["id"]: row for row in published["checks"]}
    assert set(by_id) == set(CHECK_IDS.values())
    assert {
        key: by_id[identifier].get("verification_scope")
        for key, identifier in CHECK_IDS.items()
    } == SCOPES


def test_two_declared_check_sources_keep_their_original_authority_in_the_request():
    declared = capabilities()
    declared["method_behavior_probes"] = [{
        "id": "authored-method-fixture", "sha256": "a" * 64,
        "scenario": "method-fixture-scenario", "boundary": "Finite test assertion only",
        "checks": {"local": "This authored finite method predicate holds"},
    }]
    diagnosis = validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)
    context = SimpleNamespace(run_id="catalog", case_id="RW-DEMO-001", snapshot_id="snap-0",
        input_digest=diagnosis.input_digest, model_config={"model": "fixture"},
        budget={"max_output_tokens": 2048}, retrieval_config={"public_execution_capabilities": declared})
    packet = json.loads(build_changeset_request(diagnosis, context).messages[1]["content"])
    catalog = {row["id"]: row for row in packet["public_behavior_check_catalog"]}
    assert catalog["authored-method-fixture:local"]["source_kind"] == "METHOD_BEHAVIOR_PROBE"
    assert catalog[CHECK_IDS["selected"]]["source_kind"] == "REGISTERED_PUBLIC_CHECK"
    assert catalog[CHECK_IDS["selected"]]["producer_sha256"] == declared["registered_public_checks"]["observer_sha256"]
    assert packet["public_execution_capabilities"]["method_behavior_probes"] == declared["method_behavior_probes"]
    assert packet["public_execution_capabilities"]["registered_public_checks"] == declared["registered_public_checks"]


def test_conflicting_full_check_id_is_rejected_during_capability_admission():
    declared = capabilities()
    declared["method_behavior_probes"] = [{
        "id": "contractedge-public-process-v1", "sha256": "b" * 64,
        "scenario": "foreign-method-scenario", "checks": {"selected": "Conflicting producer"},
    }]
    with pytest.raises(ValueError, match="conflicting"):
        resolve_execution_capabilities(declared)


@pytest.fixture
def public_report(tmp_path, monkeypatch, request):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text(
        'raise AssertionError("The candidate must not run in a host test")\n',
        encoding="utf-8",
    )
    (source / "config.yaml").write_text(
        "selected_name: orion\ndefault_name: default\n", encoding="utf-8"
    )
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(public_scenarios()), encoding="utf-8")
    # An indirect parameter changes candidate bytes, never trusted observations.
    selected = getattr(request, "param", "orion")
    runtime = _CheckerOnlySession(
        tmp_path / "runtime", json.dumps(actual_output(selected=selected))
    )
    monkeypatch.setattr(public_observer.RuntimeSession, "open", lambda *a, **k: runtime)
    observer = public_observer.IsolatedPublicObserver(
        image="sha256:" + "a" * 64,
        docker_host="unix:///not-used-by-this-host-fixture.sock",
    )
    report = observer.observe(source, inputs, tmp_path / "observation")
    assert report["status"] == "EXECUTION_PASS", report["errors"]
    assert runtime.candidate_calls == 1
    # This is the runner's capability context, obtained from the real producer.
    # Observation records and their scenario/scope fields remain untouched.
    report.setdefault("public_execution_capabilities", capabilities())
    return report


def obligation(check="selected"):
    return {
        "id": "P1",
        "statement": (
            "Every one of the three published scenarios retains stable mode."
            if check == "stable"
            else "The published " + check + " scenario returns its declared name."
        ),
        "verification_kind": "PUBLIC_BEHAVIOR",
        "verification_scope": copy.deepcopy(SCOPES[check]),
        "behavior_check_refs": [CHECK_IDS[check]],
        "check_coverage": "ALL_OF",
        "coverage_rationale": "This condition is limited to the declared finite check scope.",
    }


class ClaimingProvider(FixtureProvider):
    """An intentionally decisive reviewer; actual support must decide usability."""

    def __init__(self, record, *, status="PASS"):
        super().__init__({
            "checks": [{
                "id": "P1", "status": status, "rationale": "Fixture submitted " + status,
                "evidence": [{"observation_id": record["id"], "quote": record["excerpt"]}],
            }],
            "failure_kind": "UNKNOWN", "rationale": "Fixture review only",
        })
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        return super().complete_json(request)


def review(report, condition, check="selected", *, claim_status="PASS"):
    record = next(row for row in report["observations"] if row["id"] == "OBS-" + check)
    provider = ClaimingProvider(record, status=claim_status)
    reviewer = ModelPublicReviewer(
        provider, len, {"model": "fixture-review", "max_context_tokens": 100000}
    )
    result = reviewer.review(
        "Published finite process behavior",
        {"postconditions": [condition], "public_execution_capabilities": capabilities()},
        report,
    )
    return result, provider


@pytest.mark.parametrize("check", ["selected", "disabled", "stable"])
def test_actual_observer_records_support_their_explicit_finite_scope(public_report, check):
    result, provider = review(public_report, obligation(check), check)
    assert result["decision"] == "PASS", result
    assert result["method"] == "BOUND_CHECK_AGGREGATION"
    assert provider.requests == []
    record = next(row for row in public_report["observations"] if row["id"] == "OBS-" + check)
    assert record["scenario"] == SCOPES[check]["scenario"]
    final = public_observer.enforce_public_profile(result, public_report)
    assert final["decision"] == "PASS", final
    assert final["required_public_profile"]["binding_valid"] is True


@pytest.mark.parametrize("operation_scope", ["current", "stale-snapshot", "other-scenario"])
def test_mechanical_aggregation_keeps_uncited_operation_failures_in_scope(public_report, operation_scope):
    from contractedge.execution.support import deterministic_check_result, support_error
    from tests.test_operation_consistency import failed

    condition = obligation()
    context = {"snapshot_id": public_report["snapshot_id"], "capabilities": capabilities()}
    # The bound producer result alone establishes the exact finite predicate.
    claim = deterministic_check_result(condition, public_report["observations"], **context)
    assert claim is not None and claim["status"] == "PASS"

    operation = failed()
    operation.update(id="OBS-uncited-operation", snapshot_id=public_report["snapshot_id"],
                     scenario=SCOPES["selected"]["scenario"])
    if operation_scope == "stale-snapshot":
        operation["snapshot_id"] = "stale-" + public_report["snapshot_id"]
    elif operation_scope == "other-scenario":
        operation["scenario"] = "unrelated-execution-scenario"
    observations = [*public_report["observations"], operation]
    # This record is not a bound-check citation. Passing only the filtered
    # records to support_error would incorrectly discard the current failure.
    assert operation["id"] not in {row["observation_id"] for row in claim["evidence"]}
    result = deterministic_check_result(condition, observations, **context)
    if operation_scope == "current":
        assert support_error(condition, claim, observations, **context) == "OPERATION_SUCCESS_CONFLICT"
        assert result is None
    else:
        assert support_error(condition, claim, observations, **context) is None
        assert result is not None and result["status"] == "PASS"


@pytest.mark.parametrize("public_report", ["wrong"], indirect=True)
@pytest.mark.parametrize("coverage", ["ALL_OF", "NECESSARY_ONLY"])
def test_actual_failed_value_supports_a_declared_necessary_failure(public_report, coverage):
    condition = obligation()
    condition["check_coverage"] = coverage
    condition["coverage_rationale"] = (
        "The selected-name check is necessary for this exact finite scenario predicate."
    )
    record = next(row for row in public_report["observations"] if row["id"] == "OBS-selected")
    assert record["behavior_check"]["status"] == "FAIL"
    result, provider = review(public_report, condition)
    assert result["decision"] == "FAIL", result
    assert result["checks"][0]["status"] == "FAIL"
    assert result["method"] == "BOUND_NEGATIVE_CHECK_AGGREGATION"
    assert provider.requests == []
    final = public_observer.enforce_public_profile(result, public_report)
    assert final["decision"] == "FAIL", final
    assert final["required_public_profile"]["binding_valid"] is True


@pytest.mark.parametrize("fault", ["wrong-obligation-scope", "missing-observation-scope"])
def test_equal_values_cannot_clear_wrong_or_missing_scope(public_report, fault):
    report, condition = copy.deepcopy(public_report), obligation()
    if fault == "wrong-obligation-scope":
        condition["verification_scope"] = {"scenario": "not-a-published-scope"}
    else:
        next(row for row in report["observations"] if row["id"] == "OBS-selected").pop(
            "scenario", None
        )
    result, provider = review(report, condition)
    assert result["decision"] == "UNKNOWN", result
    assert result["checks"][0]["status"] == "UNKNOWN"
    assert len(provider.requests) <= 1


@pytest.mark.parametrize(
    "fault", ["missing-refs", "empty-refs", "unknown-ref", "missing-scope", "empty-scope",
              "missing-coverage", "missing-rationale", "empty-rationale", "blank-rationale",
              "necessary-only", "supporting", "legacy-undeclared", "legacy-partial"]
)
def test_positive_reviewer_cannot_supply_missing_binding_or_sufficiency(public_report, fault):
    condition = obligation()
    if fault == "missing-refs":
        condition.pop("behavior_check_refs")
    elif fault == "empty-refs":
        condition["behavior_check_refs"] = []
    elif fault == "unknown-ref":
        condition["behavior_check_refs"] = ["contractedge-public-process-v1:invented"]
    elif fault == "missing-scope":
        condition.pop("verification_scope")
    elif fault == "empty-scope":
        condition["verification_scope"] = {}
    elif fault == "missing-coverage":
        condition.pop("check_coverage")
    elif fault == "missing-rationale":
        condition.pop("coverage_rationale")
    elif fault == "empty-rationale":
        condition["coverage_rationale"] = ""
    elif fault == "blank-rationale":
        condition["coverage_rationale"] = " \t\n"
    else:
        # PARTIAL/UNDECLARED are deliberately invalid legacy/control values;
        # they are not new model-schema enum values.  Both must fail closed.
        condition["check_coverage"] = {
            "necessary-only": "NECESSARY_ONLY", "supporting": "SUPPORTING",
            "legacy-undeclared": "UNDECLARED", "legacy-partial": "PARTIAL",
        }[fault]
    result, provider = review(public_report, condition)
    assert result["decision"] == "UNKNOWN", result
    assert result["checks"][0]["status"] == "UNKNOWN"
    assert len(provider.requests) <= 1


@pytest.mark.parametrize("public_report", ["wrong"], indirect=True)
@pytest.mark.parametrize(
    "coverage", [None, "PARTIAL", "UNDECLARED", "SUPPORTING"]
)
def test_negative_reviewer_cannot_supply_missing_failure_coverage(public_report, coverage):
    condition = obligation()
    if coverage is None:
        condition.pop("check_coverage")
    else:
        condition["check_coverage"] = coverage
    result, provider = review(public_report, condition, claim_status="FAIL")
    assert result["decision"] == "UNKNOWN", result
    assert result["checks"][0]["status"] == "UNKNOWN"
    assert result["checks"][0]["submitted_status"] == "FAIL"
    assert len(provider.requests) == 1
    # The independent mandatory public profile may still report its real FAIL;
    # that cannot supply this obligation's absent semantic coverage declaration.


def test_stale_observation_cannot_clear_model_fallback_or_final_profile(public_report):
    report = copy.deepcopy(public_report)
    record = next(row for row in report["observations"] if row["id"] == "OBS-selected")
    assert record["snapshot_id"] == report["snapshot_id"]
    record["snapshot_id"] = "stale-" + report["snapshot_id"]
    report["required_public_profile"]["observations_digest"] = canonical_digest(
        report["observations"]
    )
    result, provider = review(report, obligation())
    assert len(provider.requests) == 1  # Fresh aggregation cannot consume the old record.
    assert result["checks"][0]["submitted_status"] == "PASS"
    assert result["checks"][0]["status"] == "UNKNOWN"
    assert result["decision"] == "UNKNOWN", result
    final = public_observer.enforce_public_profile(result, report)
    assert final["decision"] == "UNKNOWN", final
    assert final["required_public_profile"]["binding_valid"] is False
    # Assert the final gate independently so the support rejection cannot mask it.
    forced = public_observer.enforce_public_profile({"decision": "PASS"}, report)
    assert forced["decision"] != "PASS", forced
    assert forced["required_public_profile"]["binding_valid"] is False


@pytest.mark.parametrize("fault", ["probe-digest", "scenario", "missing-scenario"])
def test_rehashed_profile_cannot_register_a_forged_probe_or_scope(public_report, fault):
    assert public_observer.enforce_public_profile({"decision": "UNKNOWN"}, public_report)[
        "decision"
    ] == "UNKNOWN"
    report = copy.deepcopy(public_report)
    record = next(row for row in report["observations"] if row["id"] == "OBS-selected")
    if fault == "probe-digest":
        record["behavior_check"]["probe_sha256"] = "b" * 64
    elif fault == "scenario":
        record["scenario"] = "disabled"
    else:
        record.pop("scenario", None)
    report["required_public_profile"]["observations_digest"] = canonical_digest(
        report["observations"]
    )
    reviewed, provider = review(report, obligation())
    assert reviewed["decision"] == "UNKNOWN", reviewed
    assert reviewed["checks"][0]["status"] == "UNKNOWN"
    assert len(provider.requests) <= 1
    try:
        final = public_observer.enforce_public_profile(reviewed, report)
        assert final["decision"] != "PASS", final
        assert final["required_public_profile"]["binding_valid"] is False
        # Also isolate the profile gate from the support result above.
        result = public_observer.enforce_public_profile({"decision": "PASS"}, report)
    except ValueError:
        return  # Explicit protocol rejection is also fail-closed.
    assert result["decision"] != "PASS", result
    assert result["required_public_profile"]["binding_valid"] is False
