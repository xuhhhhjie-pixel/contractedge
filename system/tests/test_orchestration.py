import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from contractedge.contracts.validation import canonical_digest
from contractedge.contracts.validation import parse_method_input, validate_run_trace
from contractedge.source.snapshot import compute_snapshot
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import (
    _bind_diagnosis_envelope, _changeset_retry_feedback, _format_validation_feedback,
    _semantic_review_result, _translate_model_outcome_coverage, run_front_half,
)
from contractedge.retrieval.candidates import CandidateSet
from contractedge.contracts.models import RunContext
from tests.test_contracts import DIGEST, valid_method_input


def test_production_consumer_correction_explains_observation_vs_implementation():
    feedback=_format_validation_feedback(ValueError('PRODUCTION_CONSUMER_UNEVIDENCED at units[1].locator_refs: source span required'),
        {'units':[{},dict(id='U2',role='function output',locator_refs=['OBS1'])],
         'evidence_refs':[dict(id='OBS1',kind='PUBLIC_OBSERVATION')]})
    assert 'U2' in feedback and 'OBS1' in feedback
    assert 'SOURCE_SPAN' in feedback and 'OBSERVED_STATE' in feedback
    assert 'do not' in feedback.lower()


class PipelineFixtureProvider:
    def __init__(self):
        self.diagnosis = None

    def complete_json(self, request):
        if request.prompt_version == "diagnosis.v1.1.24":
            public = json.loads(request.messages[1]["content"])
            context = public["run_context"]
            candidate = public["candidate_set"]["selected_candidates"][0]
            source_evidence = next(item for item in public["evidence_catalog"] if item["kind"] == "SOURCE_SPAN")
            obs_ref = public["method_input"]["public_evidence"][0]
            obs_record = next(item for item in public['evidence_catalog'] if item['id']==obs_ref['id'])
            obs = obs_record["excerpt"]
            obs_digest = obs_record["content_digest"]
            ev = source_evidence["content_digest"]
            self.diagnosis = {
                "requirement_horizon": ["O1"],
                "observed_boundary": "public observable and bounded source span",
                "inferred_causal_path": ["configuration is not bound to rendered field"],
                "unverified_outcomes": [],
                "units": [
                    {"id": "U1", "role": "configuration input", "kind": "configuration", "semantic_role_class": "CONFIGURATION_INPUT", "existence": "PRESENT", "locator_refs": [], "requirement_refs": ["O1"]},
                    {"id": "U2", "role": "rendering template", "kind": "source", "semantic_role_class": "PRODUCTION_CONSUMER", "existence": "PRESENT", "locator_refs": [source_evidence["id"]], "requirement_refs": ["O1"]},
                ],
                "obligation_atoms": [{"id": "A1", "outcome_id": "O1", "statement": "Render the selected ServiceAccount name when enabled.", "source_refs": public["requirement_spec"]["outcomes"][0]["source_refs"], "support": "EXPLICIT"}],
                "expected_relations": [{"id": "E1", "source_unit_ids": ["U1"], "target_unit_ids": ["U2"],
                    "relation_form": "DIRECT_EDGE", "shared_constraint_reason": None,
                    "mechanism": "BINDING", "condition": "enabled", "expected_contract": "selected name flows into rendered metadata.name",
                    "requirement_refs": ["O1"], "obligation_refs": ["A1"], "support_refs": [source_evidence["id"]]}],
                "violations": [{"id": "V1", "relation_id": "E1", "broken_behavior": "rendered name omits selected value",
                    "causal_explanation": "the template binds a constant", "evidence_refs": [obs_record["id"], source_evidence["id"]],
                    "support_basis": "OBSERVED_AND_STATIC", "causal_status": "SUPPORTED"}],
                "outcome_coverage": [{"outcome_id": "O1", "repair_supported": ["V1"], "preserve_required": [],
                                      "preserve_current_satisfaction": [], "unresolved": []}],
                "evidence_refs": [obs_record["id"], source_evidence["id"]],
                "evidence_gaps": [], "status": "READY",
            }
            body = self.diagnosis
            self.diagnosis = {**body, "schema_version": "ce.diagnosis_packet.v1_1",
                "requirement_spec": public["requirement_spec"],
                "run_id": context["run_id"], "snapshot_id": context["snapshot_id"],
                "input_digest": context["input_digest"]}
            return ModelResponse(json.dumps(body), {"provider": "pipeline-fixture", "call": 1})
        diagnosis = self.diagnosis
        changeset_input = json.loads(request.messages[1]["content"])
        context = changeset_input["run_context"]
        candidate = changeset_input["diagnosis_packet"]["evidence_refs"][1]
        plan = {
            "schema_version": "ce.semantic_changeset.v1_1", "run_id": context["run_id"],
            "snapshot_id": context["snapshot_id"], "input_digest": context["input_digest"],
            "diagnosis_digest": canonical_digest(changeset_input["diagnosis_packet"]), "goal": "restore selected-name binding",
            "planned_violation_ids": ["V1"],
            "changes": [{"id": "C1", "target": {"kind": "EXISTING", "path": candidate["locator"]["path"],
                "locator": {"start_line": candidate["locator"]["start_line"], "end_line": candidate["locator"]["end_line"]},
                "unit_refs": ["U2"], "snapshot_id": context["snapshot_id"],
                "content_digest": next(v for v in self._snapshot_digests.values())},
                "operation": "MODIFY", "semantic_action": "bind selected name to rendered metadata.name",
                "intent": "restore the requirement relation", "violation_refs": ["V1"],
                "postcondition_refs": ["P1"], "depends_on": []}],
            "postconditions": [{"id": "P1", "statement": "Rendered metadata.name equals selected name",
                "requirement_refs": ["O1"], "obligation_refs": ["A1"], "relation_refs": ["E1"], "planned_check": "render and inspect output",
                "check_scope": "Helm-rendered output"}],
            "preserve_constraints": [], "open_obligations": [],
            "outcome_coverage": [{"outcome_id": "O1", "repair_supported": ["V1"], "preserve_required": [], "unresolved": []}],
            "status": "READY",
        }
        return ModelResponse(json.dumps(plan), {"provider": "pipeline-fixture", "call": 2})

    _snapshot_digests = {}


class InvalidOutputProvider:
    def __init__(self, finish_reason=None):
        self.calls = 0
        self.finish_reason = finish_reason

    def complete_json(self, request):
        self.calls += 1
        metadata = {"provider": "invalid-fixture"}
        if self.finish_reason:
            metadata["finish_reason"] = self.finish_reason
        return ModelResponse("not-json", metadata)


class TimeoutOnRetryProvider:
    def __init__(self):
        self.calls = 0

    def complete_json(self, request):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse("not-json", {"provider": "timeout-fixture", "finish_reason": "stop"})
        raise TimeoutError("simulated retry timeout")


class TwoStepDiagnosisCorrectionProvider(PipelineFixtureProvider):
    def __init__(self):
        super().__init__()
        self.diagnosis_count = 0

    def complete_json(self, request):
        response = super().complete_json(request)
        if request.prompt_version != "diagnosis.v1.1.24":
            return response
        self.diagnosis_count += 1
        if self.diagnosis_count == 1:
            return ModelResponse("not-json", {"provider": "two-step-retry-fixture", "call": 1})
        if self.diagnosis_count == 2:
            payload = json.loads(response.content)
            payload["violations"] = []
            payload["outcome_coverage"] = {"O1": {
                "disposition": "REPAIR_SUPPORTED", "preservation_status": "NOT_APPLICABLE",
                "preservation_evidence_refs": []}}
            payload["status"] = "READY"
            return ModelResponse(json.dumps(payload), {"provider": "two-step-retry-fixture", "call": 2})
        return response


class CumulativeDiagnosisCorrectionProvider(PipelineFixtureProvider):
    """Correct two interacting Diagnosis errors without losing the first correction."""

    def __init__(self):
        super().__init__()
        self.diagnosis_count = 0
        self.previous_body = None
        self.cumulative_feedback_seen = False

    def complete_json(self, request):
        if request.prompt_version != "diagnosis.v1.1.24":
            return super().complete_json(request)
        self.diagnosis_count += 1
        public = json.loads(request.messages[1]["content"])
        if self.diagnosis_count == 1:
            response = super().complete_json(request)
            body = json.loads(response.content)
            body["expected_relations"][0]["shared_constraint_reason"] = "incorrectly set on a direct edge"
            body["units"][1]["semantic_role_class"] = "TEST_ARTIFACT"
            self.previous_body = body
            return ModelResponse(json.dumps(body), {"provider": "cumulative-correction-fixture", "call": 1})

        if self.diagnosis_count == 2:
            body = json.loads(json.dumps(self.previous_body))
            body["expected_relations"][0]["shared_constraint_reason"] = None
            self.previous_body = body
            return ModelResponse(json.dumps(body), {"provider": "cumulative-correction-fixture", "call": 2})

        assistant_drafts = [item["content"] for item in request.messages if item.get("role") == "assistant"]
        user_feedback = "\n".join(item.get("content", "") for item in request.messages
                                   if item.get("role") == "user")
        if assistant_drafts and "SHARED_RELATION_JUSTIFICATION_UNEXPECTED" in user_feedback \
                and "TEST_ARTIFACT_RELATION_ENDPOINT" in user_feedback:
            body = json.loads(assistant_drafts[-1])
            body["units"][1]["semantic_role_class"] = "PRODUCTION_CONSUMER"
            self.cumulative_feedback_seen = True
        else:
            # Model the observed regression when correction context contains only the latest error.
            body = json.loads(json.dumps(self.previous_body))
            body["expected_relations"][0]["shared_constraint_reason"] = "regressed after second correction"
        self.previous_body = body

        context = public["run_context"]
        self.diagnosis = {**body, "schema_version": "ce.diagnosis_packet.v1_1",
            "requirement_spec": public["requirement_spec"], "run_id": context["run_id"],
            "snapshot_id": context["snapshot_id"], "input_digest": context["input_digest"]}
        return ModelResponse(json.dumps(body), {"provider": "cumulative-correction-fixture", "call": 3})


class OneExtensionProvider(PipelineFixtureProvider):
    def __init__(self):
        super().__init__()
        self.diagnosis_count = 0
        self.extension_request = None

    def complete_json(self, request):
        response = super().complete_json(request)
        if request.prompt_version == "diagnosis.v1.1.24":
            self.diagnosis_count += 1
            if self.diagnosis_count == 2:
                self.extension_request = json.loads(request.messages[1]["content"])
            if self.diagnosis_count == 1:
                public = json.loads(request.messages[1]["content"])
                payload = json.loads(response.content)
                payload["status"] = "EVIDENCE_GAP"
                payload["evidence_gaps"] = [{"id": "G1", "summary": "Need the nested configuration unit",
                    "locator_hint": {"path": "templates/optional-serviceaccount.yaml"}}]
                context = public["run_context"]
                self.diagnosis = {**payload, "schema_version": "ce.diagnosis_packet.v1_1",
                    "requirement_spec": public["requirement_spec"],
                    "run_id": context["run_id"], "snapshot_id": context["snapshot_id"],
                    "input_digest": context["input_digest"]}
                return ModelResponse(json.dumps(payload), {"provider": "extension-fixture", "call": 1})
        return response


class InvalidExtensionThenCorrectedProvider(OneExtensionProvider):
    def complete_json(self, request):
        before = self.diagnosis_count
        response = super().complete_json(request)
        if request.prompt_version == "diagnosis.v1.1.24" and before == 1:
            payload = json.loads(response.content)
            payload["units"].append({"id": "U99", "role": "unverified absent template",
                "kind": "source", "existence": "EXPECTED_MISSING", "locator_refs": [],
                "requirement_refs": ["O1"]})
            return ModelResponse(json.dumps(payload), {"provider": "extension-invalid-fixture", "call": 2})
        return response


class NeighborExtensionProvider(PipelineFixtureProvider):
    def __init__(self):
        super().__init__()
        self.diagnosis_count = 0
        self.selected_paths = []

    def complete_json(self, request):
        if request.prompt_version == "diagnosis.v1.1.24":
            public = json.loads(request.messages[1]["content"])
            self.selected_paths.append([item["path"] for item in public["candidate_set"]["selected_candidates"]])
        response = super().complete_json(request)
        if request.prompt_version == "diagnosis.v1.1.24":
            self.diagnosis_count += 1
            if self.diagnosis_count == 1:
                payload = json.loads(response.content)
                payload["status"] = "EVIDENCE_GAP"
                payload["evidence_gaps"] = [{"id": "G1", "summary": "Inspect the direct callee implementation",
                    "locator_hint": {"search_term": "buildService"}}]
                context = public["run_context"]
                self.diagnosis = {**payload, "schema_version": "ce.diagnosis_packet.v1_1",
                    "requirement_spec": public["requirement_spec"],
                    "run_id": context["run_id"], "snapshot_id": context["snapshot_id"],
                    "input_digest": context["input_digest"]}
                return ModelResponse(json.dumps(payload), {"provider": "neighbor-extension-fixture", "call": 1})
        return response


class OneChangeSetRevisionProvider(PipelineFixtureProvider):
    def __init__(self):
        super().__init__()
        self.changeset_count = 0

    def complete_json(self, request):
        response = super().complete_json(request)
        if request.prompt_version == "changeset.v1.1.8":
            self.changeset_count += 1
            if self.changeset_count == 1:
                return ModelResponse("invalid plan", {"provider": "revision-fixture", "call": 1})
        return response


class ValidationFeedbackTests(unittest.TestCase):
    def test_changeset_adapter_error_code_is_preserved_for_revision(self):
        feedback = _changeset_retry_feedback(ValueError(
            "MODEL_EXISTING_TARGET_PATH_AMBIGUOUS: referenced SemanticUnits must locate one current source file"))

        self.assertEqual(feedback, [{"code": "MODEL_EXISTING_TARGET_PATH_AMBIGUOUS",
            "message": "MODEL_EXISTING_TARGET_PATH_AMBIGUOUS: referenced SemanticUnits must locate one current source file"}])

    def test_unsupported_missing_unit_feedback_requires_inventory_or_unknown(self):
        feedback = _format_validation_feedback(
            ValueError("MISSING_UNIT_UNSUPPORTED at units[2].locator_refs: EXPECTED_MISSING requires evidence"),
            {},
        )
        self.assertIn("Do not infer absence from selected-candidate omission", feedback)
        self.assertIn("change existence to UNKNOWN", feedback)
        self.assertIn("SOURCE_INVENTORY, STATIC_SEARCH, or PUBLIC_OBSERVATION", feedback)

    def test_supported_violation_feedback_explains_coverage_and_status_repair(self):
        feedback = _format_validation_feedback(
            ValueError("OUTCOME_SUPPORTED_VIOLATION_UNCOVERED at outcome_coverage[0]"),
            {"outcome_coverage": [{"outcome_id": "O1"}]},
        )
        self.assertIn("causal_status=SUPPORTED Violation", feedback)
        self.assertIn("select REPAIR_SUPPORTED", feedback)
        self.assertIn("mark the Violation TENTATIVE", feedback)

    def test_unsupported_repair_disposition_feedback_requires_unresolved_without_violation(self):
        feedback = _format_validation_feedback(
            ValueError("MODEL_COVERAGE_SUPPORT_MISSING at outcome_coverage[0]: no supported Violation"),
            {"outcome_coverage": {"O1": {"disposition": "REPAIR_SUPPORTED"}}, "violations": []},
        )
        self.assertIn("cannot be REPAIR_SUPPORTED", feedback)
        self.assertIn("disposition to UNRESOLVED", feedback)
        self.assertIn("Do not invent a Violation", feedback)
        self.assertIn("EVIDENCE_GAP", feedback)
        self.assertIn("rejected response contains 0 violation records", feedback.casefold())
        self.assertIn("set o1 to unresolved", feedback.casefold())

    def test_single_disposition_translates_to_exclusive_v1_1_coverage(self):
        payload = {"expected_relations": [{"id": "E1", "requirement_refs": ["O1"]}],
                   "violations": [{"id": "V1", "relation_id": "E1", "causal_status": "SUPPORTED"}],
                   "outcome_coverage": [{"outcome_id": "O1", "disposition": "REPAIR_SUPPORTED",
                    "preservation_status": "NOT_APPLICABLE", "preservation_evidence_refs": []}]}

        translated = _translate_model_outcome_coverage(payload)

        self.assertEqual(translated["outcome_coverage"], [{"outcome_id": "O1",
            "repair_supported": ["V1"], "preserve_required": [],
            "preserve_current_satisfaction": [], "unresolved": []}])

    def test_keyed_model_coverage_translates_each_outcome_once(self):
        payload = {"expected_relations": [{"id": "E1", "requirement_refs": ["O1"]}],
                   "violations": [{"id": "V1", "relation_id": "E1", "causal_status": "SUPPORTED"}],
                   "outcome_coverage": {"O1": {"disposition": "REPAIR_SUPPORTED",
                       "preservation_status": "NOT_APPLICABLE", "preservation_evidence_refs": []}}}

        translated = _translate_model_outcome_coverage(payload)

        self.assertEqual(translated["outcome_coverage"], [{"outcome_id": "O1",
            "repair_supported": ["V1"], "preserve_required": [],
            "preserve_current_satisfaction": [], "unresolved": []}])

    def test_outcome_conflict_feedback_explains_exclusive_choices_without_rewriting(self):
        feedback = _format_validation_feedback(
            ValueError("OUTCOME_CATEGORY_CONFLICT at outcome_coverage[0]: conflict"),
            {"outcome_coverage": [{"outcome_id": "O1", "repair_supported": ["V1"],
                                   "preserve_required": [], "unresolved": ["O1"]}]},
        )

        self.assertIn("Outcome O1 is assigned to more than one category", feedback)
        self.assertIn("keep supported violation IDs", feedback)
        self.assertIn("Return the full corrected JSON object", feedback)


class WrappedDiagnosisOnceProvider(PipelineFixtureProvider):
    def __init__(self):
        super().__init__()
        self.did_wrap = False

    def complete_json(self, request):
        response = super().complete_json(request)
        if request.prompt_version == "diagnosis.v1.1.24" and not self.did_wrap:
            self.did_wrap = True
            return ModelResponse(json.dumps({"unexpected_wrapper": json.loads(response.content)}),
                                 {"provider": "wrapped-fixture", "call": 1})
        return response


class OrchestrationTests(unittest.TestCase):
    def test_runner_binds_nested_diagnosis_citations_missing_from_top_level_union(self):
        method = parse_method_input(valid_method_input())
        candidate = {
            "id": "CU-one", "snapshot_id": "snap-0", "path": "templates/chart.yaml",
            "locator": {"start_line": 1, "end_line": 2}, "content_digest": DIGEST,
            "text": "kind: ConfigMap\n", "kind": "SOURCE_TEXT",
        }
        candidates = CandidateSet((candidate,), ("CU-one",), (), {}, (), 1, 100, 1)
        context = RunContext("run-1", method.case_id, 0, "snap-0", None,
                             method.input_digest)
        payload = {
            "units": [{"id": "U1", "locator_refs": ["SRC-CU-one"], "requirement_refs": []}],
            "expected_relations": [{"id": "E1", "support_refs": ["SRC-CU-one"]}],
            "violations": [{"id": "V1", "evidence_refs": ["SRC-CU-one"]}],
            "evidence_refs": [],
        }

        body, _ = _bind_diagnosis_envelope(payload, method, context, {}, candidates)

        self.assertEqual([row["id"] for row in body["evidence_refs"]], ["SRC-CU-one"])

    def test_runner_still_rejects_unknown_nested_diagnosis_citations(self):
        method = parse_method_input(valid_method_input())
        candidates = CandidateSet((), (), (), {}, (), 0, 100, 1)
        context = RunContext("run-1", method.case_id, 0, "snap-0", None,
                             method.input_digest)
        payload = {"units": [{"id": "U1", "locator_refs": ["SRC-NOT-SELECTED"]}],
                   "evidence_refs": []}

        with self.assertRaisesRegex(ValueError, "unknown catalog ID 'SRC-NOT-SELECTED'"):
            _bind_diagnosis_envelope(payload, method, context, {}, candidates)

    def test_non_independent_semantic_acceptance_is_not_recorded_as_accept(self):
        method = parse_method_input(valid_method_input())
        candidates = CandidateSet((), (), (), {}, (), 0, 100, 1)
        review = _semantic_review_result(
            lambda _: {"decision": "ACCEPT", "method": "same_model_self_review",
                       "reviewer_id": "model", "independent": False,
                       "rationale": "The model agrees with its own plan."},
            method, {"raw_text": "requirement"}, {"id": "diagnosis"}, {"id": "plan"}, candidates)

        self.assertEqual(review["decision"], "UNCERTAIN")
        self.assertEqual(review["submitted_decision"], "ACCEPT")
        self.assertFalse(review["independent"])
        self.assertIn("not a correctness proof", review["interpretation"])

    def test_retry_timeout_retains_snapshot_and_failed_call_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.go"
            source.write_text("package main\nfunc render() {}\n", encoding="utf-8")
            method = valid_method_input()
            method["scope_hints"] = [{"kind": "path", "value": "main.go", "raw_text": "main.go", "origin_ref": "scope"}]
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            trace = run_front_half(method, root, TimeoutOnRetryProvider(),
                {"run_id": "run-timeout", "model": {"model": "fixture"}, "max_context_tokens": 5000},
                token_counter=lambda text: len(text.split()))
        self.assertEqual(trace.status, "INFRASTRUCTURE_ERROR")
        self.assertEqual(trace.snapshot_id, method["initial_snapshot_id"])
        self.assertIsNotNone(trace.requirement_spec)
        self.assertEqual([call["stage"] for call in trace.model_calls],
                         ["DIAGNOSIS", "DIAGNOSIS_FORMAT_RETRY"])
        self.assertEqual(trace.model_calls[-1]["error"], "TimeoutError: simulated retry timeout")
        self.assertIn("TimeoutError: simulated retry timeout", trace.errors)
        self.assertEqual(validate_run_trace(trace.to_wire())["snapshot_id"], method["initial_snapshot_id"])

    def test_exhausted_total_token_budget_returns_without_calling_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.go"
            source.write_text("package main\nfunc render() {}\n", encoding="utf-8")
            method = valid_method_input()
            method["scope_hints"] = [{"kind": "path", "value": "main.go", "raw_text": "main.go", "origin_ref": "scope"}]
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            provider = InvalidOutputProvider()
            trace = run_front_half(method, root, provider,
                {"run_id": "run-budget", "model": {"model": "fixture"}, "max_context_tokens": 1000,
                 "max_total_tokens": 1}, token_counter=lambda text: len(text.split()))
        self.assertEqual(trace.status, "BUDGET_EXHAUSTED")
        self.assertEqual(provider.calls, 0)
        self.assertEqual(trace.run_context["budget"]["max_total_tokens"], 1)

    def test_truncated_generation_is_reported_as_budget_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.go"
            source.write_text("package main\nfunc render() {}\n", encoding="utf-8")
            method = valid_method_input()
            method["scope_hints"] = [{"kind": "path", "value": "main.go", "raw_text": "main.go", "origin_ref": "scope"}]
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            provider = InvalidOutputProvider(finish_reason="length")
            trace = run_front_half(method, root, provider,
                {"run_id": "run-truncated", "model": {"model": "fixture"}, "max_context_tokens": 5000},
                token_counter=lambda text: len(text.split()))
        self.assertEqual(trace.status, "BUDGET_EXHAUSTED")
        self.assertEqual(provider.calls, 1)

    def test_requirement_diagnosis_and_changeset_run_in_one_bounded_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates/serviceaccount.yaml"
            source.parent.mkdir()
            source.write_text("apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            PipelineFixtureProvider._snapshot_digests = {"templates/serviceaccount.yaml": digest}
            method = valid_method_input()
            method["requirement_text"] = "When enabled, render the selected ServiceAccount."
            method["public_observable_text"] = "The rendered ServiceAccount name is default instead of selected."
            method["scope_hints"] = [{"kind": "path", "value": "templates/serviceaccount.yaml",
                                      "raw_text": "templates/serviceaccount.yaml", "origin_ref": "artifact_scope[0]"}]
            method["input_digest"] = DIGEST
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            excerpt = method["public_observable_text"]
            evidence_payload = {"id": "PUB1", "kind": "PUBLIC_OBSERVATION", "excerpt": excerpt,
                                "origin": "public fixture"}
            evidence_bytes = (json.dumps(evidence_payload, sort_keys=True, indent=2) + "\n").encode()
            method["public_evidence"] = [{"id": "PUB1", "snapshot_id": method["initial_snapshot_id"],
                "kind": "PUBLIC_OBSERVATION", "locator": {"path": "evidence/PUB1.json"},
                "content_digest": hashlib.sha256(evidence_bytes).hexdigest(), "excerpt": excerpt,
                "origin": "public fixture"}]
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            provider = WrappedDiagnosisOnceProvider()
            reviews = []

            def semantic_reviewer(review_input):
                reviews.append(review_input)
                return {"decision": "ACCEPT", "method": "independent_fixture_review",
                        "reviewer_id": "reviewer-fixture-01", "independent": True,
                        "rationale": "The cited source and observation support the proposed relation and target."}

            trace = run_front_half(method, root, provider, {"run_id": "run-test", "model": {"model": "fixture"},
                "top_n": 12, "max_context_tokens": 5000,
                "max_scope_search_files": 123, "max_scope_search_bytes": 4567,
                "max_scope_search_file_bytes": 890, "max_scope_matches_per_hint": 9},
                token_counter=lambda text: len(text.split()),
                semantic_reviewer=semantic_reviewer)
            self.assertEqual(trace.status, "READY", f"{trace.errors}")
            self.assertEqual(trace.diagnosis["run_id"], trace.run_id)
            self.assertEqual(trace.diagnosis["snapshot_id"], method["initial_snapshot_id"])
            self.assertEqual(trace.diagnosis["requirement_spec"]["raw_text"], method["requirement_text"])
            successful_diagnosis_call = next(call for call in trace.model_calls
                                             if "runner_binding" in call)
            self.assertEqual(successful_diagnosis_call["runner_binding"]["model_supplied_fields"], [])
            self.assertEqual([call["stage"] for call in trace.model_calls],
                             ["DIAGNOSIS", "DIAGNOSIS_FORMAT_RETRY", "SEMANTIC_CHANGESET"])
            self.assertEqual(trace.changeset["planned_violation_ids"], ["V1"])
            self.assertEqual(trace.semantic_review["decision"], "ACCEPT")
            self.assertEqual(trace.semantic_review["method"], "independent_fixture_review")
            self.assertTrue(trace.semantic_review["independent"])
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0]["case_id"], method["case_id"])
            self.assertNotIn("ground_truth", reviews[0])
            self.assertNotIn("repair_family", reviews[0])
            wire = trace.to_wire()
            self.assertEqual(wire["schema_version"], "ce.run_trace.v1_1")
            self.assertEqual(wire["run_context"]["snapshot_id"], method["initial_snapshot_id"])
            self.assertEqual(wire["run_context"]["model_config"]["model"], "fixture")
            self.assertEqual(wire["run_context"]["retrieval_config"]["max_scope_search_files"], 123)
            self.assertEqual(wire["run_context"]["retrieval_config"]["max_scope_search_bytes"], 4567)
            self.assertEqual(wire["run_context"]["retrieval_config"]["max_scope_search_file_bytes"], 890)
            self.assertEqual(wire["run_context"]["retrieval_config"]["max_scope_matches_per_hint"], 9)
            self.assertEqual(wire["run_context"]["prompt_version"], "contractedge-front-half.v1_1")
            self.assertEqual(validate_run_trace(wire)["run_id"], trace.run_id)
            json.dumps(wire)

    def test_malformed_model_json_is_not_reported_as_infrastructure_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.go"
            source.write_text("package main\nfunc render() {}\n", encoding="utf-8")
            method = valid_method_input()
            method["scope_hints"] = [{"kind": "path", "value": "main.go", "raw_text": "main.go", "origin_ref": "scope"}]
            method["input_digest"] = DIGEST
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            trace = run_front_half(method, root, InvalidOutputProvider(),
                                   {"run_id": "run-invalid", "max_context_tokens": 5000},
                                   token_counter=lambda text: len(text.split()))
            self.assertEqual(trace.status, "INVALID_OUTPUT")
            self.assertIn("Return the corrected top-level DiagnosisPacket", trace.model_calls[0]["validation_feedback"])
            self.assertIn("validation_error", trace.model_calls[0])

    def test_diagnosis_uses_remaining_bounded_format_retry_after_first_correction_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates/serviceaccount.yaml"
            source.parent.mkdir()
            source.write_text("apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n", encoding="utf-8")
            PipelineFixtureProvider._snapshot_digests = {
                "templates/serviceaccount.yaml": hashlib.sha256(source.read_bytes()).hexdigest()}
            method = valid_method_input()
            method["requirement_text"] = "When enabled, render the selected ServiceAccount."
            method["public_observable_text"] = "The rendered ServiceAccount name is default instead of selected."
            method["scope_hints"] = [{"kind": "path", "value": "templates/serviceaccount.yaml",
                                      "raw_text": "templates/serviceaccount.yaml", "origin_ref": "artifact_scope[0]"}]
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            method["public_evidence"] = [{"id": "PUB1", "snapshot_id": method["initial_snapshot_id"],
                "kind": "PUBLIC_OBSERVATION", "locator": {"path": "evidence/PUB1.json"},
                "content_digest": hashlib.sha256(b"public observation").hexdigest(),
                "excerpt": method["public_observable_text"], "origin": "fixture"}]
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            provider = TwoStepDiagnosisCorrectionProvider()
            trace = run_front_half(method, root, provider,
                {"run_id": "run-two-diagnosis-retries", "model": {"model": "fixture"}, "top_n": 12,
                 "max_context_tokens": 12000, "max_format_retries": 2, "max_total_tokens": 40000},
                token_counter=lambda text: len(text.split()))

        self.assertEqual(trace.status, "READY", f"{trace.errors}")
        self.assertEqual([call["stage"] for call in trace.model_calls],
                         ["DIAGNOSIS", "DIAGNOSIS_FORMAT_RETRY", "DIAGNOSIS_FORMAT_RETRY", "SEMANTIC_CHANGESET"])
        self.assertIn("Return the corrected top-level DiagnosisPacket", trace.model_calls[0]["validation_feedback"])
        self.assertIn("0 Violation records", trace.model_calls[1]["validation_feedback"])
        self.assertEqual(provider.diagnosis_count, 3)

    def test_diagnosis_corrections_retain_prior_draft_and_all_open_validation_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates/serviceaccount.yaml"
            source.parent.mkdir()
            source.write_text("apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n", encoding="utf-8")
            PipelineFixtureProvider._snapshot_digests = {
                "templates/serviceaccount.yaml": hashlib.sha256(source.read_bytes()).hexdigest()}
            method = valid_method_input()
            method["requirement_text"] = "When enabled, render the selected ServiceAccount."
            method["public_observable_text"] = "The rendered ServiceAccount name is default instead of selected."
            method["scope_hints"] = [{"kind": "path", "value": "templates/serviceaccount.yaml",
                                      "raw_text": "templates/serviceaccount.yaml", "origin_ref": "artifact_scope[0]"}]
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            excerpt = method["public_observable_text"]
            evidence_payload = {"id": "PUB1", "kind": "PUBLIC_OBSERVATION", "excerpt": excerpt,
                                "origin": "public fixture"}
            evidence_bytes = (json.dumps(evidence_payload, sort_keys=True, indent=2) + "\n").encode()
            method["public_evidence"] = [{"id": "PUB1", "snapshot_id": method["initial_snapshot_id"],
                "kind": "PUBLIC_OBSERVATION", "locator": {"path": "evidence/PUB1.json"},
                "content_digest": hashlib.sha256(evidence_bytes).hexdigest(), "excerpt": excerpt,
                "origin": "public fixture"}]
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            provider = CumulativeDiagnosisCorrectionProvider()
            trace = run_front_half(method, root, provider,
                {"run_id": "run-cumulative-diagnosis-corrections", "model": {"model": "fixture"},
                 "max_context_tokens": 12000, "max_format_retries": 2, "max_total_tokens": 40000},
                token_counter=lambda text: len(text.split()))

        self.assertEqual(trace.status, "READY", f"{trace.errors}")
        self.assertEqual(provider.diagnosis_count, 3)
        self.assertTrue(provider.cumulative_feedback_seen)
        self.assertIsNone(trace.diagnosis["expected_relations"][0]["shared_constraint_reason"])
        self.assertEqual(trace.diagnosis["units"][1]["semantic_role_class"], "PRODUCTION_CONSUMER")

    def test_evidence_gap_runs_one_bounded_diagnosis_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates/serviceaccount.yaml"
            source.parent.mkdir()
            source.write_text("apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n", encoding="utf-8")
            PipelineFixtureProvider._snapshot_digests = {"templates/serviceaccount.yaml": hashlib.sha256(source.read_bytes()).hexdigest()}
            method = valid_method_input()
            method["requirement_text"] = "When enabled, render the selected ServiceAccount."
            method["public_observable_text"] = "The rendered ServiceAccount name is default instead of selected."
            method["scope_hints"] = [{"kind": "path", "value": "templates/serviceaccount.yaml",
                                      "raw_text": "templates/serviceaccount.yaml", "origin_ref": "artifact_scope[0]"}]
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            method["public_evidence"] = [{"id": "PUB1", "snapshot_id": method["initial_snapshot_id"],
                "kind": "PUBLIC_OBSERVATION", "locator": {"path": "evidence/PUB1.json"},
                "content_digest": hashlib.sha256(b"public observation").hexdigest(),
                "excerpt": method["public_observable_text"], "origin": "fixture"}]
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            provider = InvalidExtensionThenCorrectedProvider()
            trace = run_front_half(method, root, provider,
                {"run_id": "run-extension", "model": {"model": "fixture"}, "top_n": 1,
                 "max_context_tokens": 5000, "max_format_retries": 1},
                token_counter=lambda text: len(text.split()))
        self.assertEqual(trace.status, "READY", f"{trace.errors}")
        self.assertEqual([call["stage"] for call in trace.model_calls],
                         ["DIAGNOSIS", "DIAGNOSIS_EXTENSION", "DIAGNOSIS_FORMAT_RETRY", "SEMANTIC_CHANGESET"])
        self.assertTrue(trace.candidate_set.extension_used)
        self.assertEqual(provider.diagnosis_count, 3)
        evidence = provider.extension_request["evidence_catalog"]
        absence = next(item for item in evidence if item["kind"] == "STATIC_SEARCH")
        self.assertTrue(absence["locator"]["complete"])
        self.assertEqual(absence["locator"]["match_count"], 0)
        allowed_existence = provider.extension_request["output_schema"]["properties"]["units"]["items"]["properties"]["existence"]["enum"]
        self.assertIn("EXPECTED_MISSING", allowed_existence)

    def test_evidence_gap_searches_a_query_matched_direct_source_neighbor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = root / "src/controller.go"
            service = root / "src/service.go"
            controller.parent.mkdir()
            controller.write_text("package app\nfunc Reconcile() { ensureService() }\n", encoding="utf-8")
            service.write_text("package app\nfunc ensureService() { buildService() }\n", encoding="utf-8")
            PipelineFixtureProvider._snapshot_digests = {
                "src/service.go": hashlib.sha256(service.read_bytes()).hexdigest()}
            method = valid_method_input()
            method["public_observable_text"] = "The controller does not reach ensureService."
            method["scope_hints"] = [{"kind": "path", "value": "src/controller.go",
                                      "raw_text": "src/controller.go", "origin_ref": "scope"}]
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            evidence_payload = {"id": "PUB1", "kind": "PUBLIC_OBSERVATION",
                                "excerpt": method["public_observable_text"], "origin": "fixture"}
            evidence_bytes = (json.dumps(evidence_payload, sort_keys=True, indent=2) + "\n").encode()
            method["public_evidence"] = [{"id": "PUB1", "snapshot_id": method["initial_snapshot_id"],
                "kind": "PUBLIC_OBSERVATION", "locator": {"path": "evidence/PUB1.json"},
                "content_digest": hashlib.sha256(evidence_bytes).hexdigest(),
                "excerpt": method["public_observable_text"], "origin": "fixture"}]
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            provider = NeighborExtensionProvider()

            trace = run_front_half(method, root, provider,
                {"run_id": "run-neighbor-extension", "model": {"model": "fixture"},
                 "top_n": 1, "max_units": 8, "max_extension_files": 0,
                 "max_context_tokens": 5000}, token_counter=lambda text: len(text.split()))

        self.assertEqual(trace.status, "READY", f"{trace.errors}; selected={[u['path'] for u in trace.candidate_set.candidates]}; plan={trace.changeset}")
        self.assertEqual(provider.selected_paths[0], ["src/controller.go"])
        self.assertEqual(provider.selected_paths[1], ["src/service.go"])

    def test_changeset_format_failure_gets_one_revision_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "templates/serviceaccount.yaml"
            source.parent.mkdir()
            source.write_text("apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n", encoding="utf-8")
            PipelineFixtureProvider._snapshot_digests = {"templates/serviceaccount.yaml": hashlib.sha256(source.read_bytes()).hexdigest()}
            method = valid_method_input()
            method["requirement_text"] = "When enabled, render the selected ServiceAccount."
            method["public_observable_text"] = "The rendered ServiceAccount name is default instead of selected."
            method["scope_hints"] = [{"kind": "path", "value": "templates/serviceaccount.yaml",
                                      "raw_text": "templates/serviceaccount.yaml", "origin_ref": "artifact_scope[0]"}]
            method["initial_snapshot_id"] = compute_snapshot(root).snapshot_id
            method["public_evidence"] = [{"id": "PUB1", "snapshot_id": method["initial_snapshot_id"],
                "kind": "PUBLIC_OBSERVATION", "locator": {"path": "evidence/PUB1.json"},
                "content_digest": hashlib.sha256(b"public observation").hexdigest(),
                "excerpt": method["public_observable_text"], "origin": "fixture"}]
            method.pop("input_digest", None)
            method["input_digest"] = canonical_digest(method)
            provider = OneChangeSetRevisionProvider()
            trace = run_front_half(method, root, provider,
                {"run_id": "run-revision", "model": {"model": "fixture"}, "top_n": 12,
                 "max_context_tokens": 5000}, token_counter=lambda text: len(text.split()))
        self.assertEqual(trace.status, "READY", f"{trace.errors}")
        self.assertEqual([call["stage"] for call in trace.model_calls],
                         ["DIAGNOSIS", "SEMANTIC_CHANGESET", "CHANGESET_REVISION"])
        self.assertEqual(provider.changeset_count, 2)
        self.assertEqual(trace.structural_validation, "PASS")
        self.assertEqual(trace.semantic_review["decision"], "UNCERTAIN")
        self.assertEqual(trace.model_calls[1]["validation_feedback"][0]["code"], "INVALID_JSON")


if __name__ == "__main__":
    unittest.main()
