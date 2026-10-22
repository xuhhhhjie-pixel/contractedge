import unittest

from contractedge.contracts.models import ContractError
from contractedge.contracts.evidence import static_search_content_digest
from contractedge.contracts.validation import (
    canonical_digest,
    parse_method_input,
    validate_changeset,
    validate_diagnosis,
)


DIGEST = "a" * 64


def valid_method_input():
    payload = {
        "schema_version": "ce.method_input.v1_1",
        "case_id": "RW-DEMO-001",
        "release_id": "R1-v4",
        "original_broken_revision": "deadbee",
        "archive_path": "public/broken.tar.gz",
        "archive_sha256": DIGEST,
        "source_root": ".",
        "initial_snapshot_id": "snap-0",
        "requirement_text": "When enabled, render the selected ServiceAccount.",
        "public_observable_text": None,
        "scope_hints": [],
        "public_evidence": [],
        "declared_artifacts": [],
        "input_digest": "",
    }
    payload["input_digest"] = canonical_digest({key: value for key, value in payload.items() if key != "input_digest"})
    return payload


def valid_diagnosis():
    return {
        "schema_version": "ce.diagnosis_packet.v1_1",
        "run_id": "run-1",
        "snapshot_id": "snap-0",
        "input_digest": valid_method_input()["input_digest"],
        "requirement_spec": {
            "schema_version": "ce.requirement_spec.v1_1",
            "raw_text": "When enabled, render the selected ServiceAccount.",
            "intent_summary": "Render the selected ServiceAccount when enabled.",
            "outcomes": [
                {"id": "O1", "statement": "Render the selected ServiceAccount when enabled.",
                 "condition": "enabled", "source_refs": ["requirement_text:0:49"], "support": "EXPLICIT"}
            ],
            "constraints": [],
            "trigger": "enabled",
            "unknowns": [],
            "status": "READY",
        },
        "requirement_horizon": ["O1"],
        "observed_boundary": "rendered manifests",
        "inferred_causal_path": [],
        "unverified_outcomes": [],
        "obligation_atoms": [{"id": "A1", "outcome_id": "O1",
            "statement": "Render the selected ServiceAccount when enabled.",
            "source_refs": ["requirement_text:0:49"], "support": "EXPLICIT"}],
        "units": [
            {"id": "U1", "role": "values input", "kind": "configuration",
             "semantic_role_class": "CONFIGURATION_INPUT",
             "existence": "PRESENT", "locator_refs": [], "requirement_refs": ["O1"]},
            {"id": "U2", "role": "chart template", "kind": "template",
             "semantic_role_class": "PRODUCTION_CONSUMER",
             "existence": "PRESENT", "locator_refs": ["EV2"], "requirement_refs": ["O1"]},
        ],
        "expected_relations": [
            {"id": "E1", "source_unit_ids": ["U1"], "target_unit_ids": ["U2"],
             "relation_form": "DIRECT_EDGE", "shared_constraint_reason": None,
             "mechanism": "BINDING", "condition": "enabled",
             "expected_contract": "The selected name flows into the rendered ServiceAccount.",
             "requirement_refs": ["O1"], "obligation_refs": ["A1"], "support_refs": ["EV2"]}
        ],
        "violations": [
            {"id": "V1", "relation_id": "E1", "broken_behavior": "The object is absent.",
             "causal_explanation": "The selected name is omitted by the generation template.",
             "evidence_refs": ["EV1", "EV2"], "support_basis": "OBSERVED_AND_STATIC",
             "causal_status": "SUPPORTED"}
        ],
        "outcome_coverage": [
            {"outcome_id": "O1", "repair_supported": ["V1"],
             "preserve_required": [], "preserve_current_satisfaction": [], "unresolved": []}
        ],
        "evidence_refs": [
            {"id": "EV1", "snapshot_id": "snap-0", "kind": "PUBLIC_OBSERVATION",
             "locator": {"path": "observation.json", "field": "rendered"},
             "content_digest": DIGEST, "excerpt": "ServiceAccount was not rendered.",
             "origin": "public input"},
            {"id": "EV2", "snapshot_id": "snap-0", "kind": "SOURCE_SPAN",
             "locator": {"path": "templates/serviceaccount.yaml", "start_line": 1, "end_line": 8},
             "content_digest": DIGEST, "excerpt": "metadata.name is hard-coded.",
             "origin": "broken source"}
        ],
        "evidence_gaps": [],
        "status": "READY",
    }


def valid_plan():
    diagnosis = valid_diagnosis()
    return {
        "schema_version": "ce.semantic_changeset.v1_1",
        "run_id": "run-1",
        "snapshot_id": "snap-0",
        "input_digest": valid_method_input()["input_digest"],
        "diagnosis_digest": canonical_digest(diagnosis),
        "goal": "Restore selected-name rendering while preserving the enabled condition.",
        "planned_violation_ids": ["V1"],
        "changes": [
            {"id": "C1", "target": {"kind": "EXISTING", "path": "templates/serviceaccount.yaml",
             "locator": {"start_line": 1, "end_line": 8}, "unit_refs": ["U2"],
             "snapshot_id": "snap-0", "content_digest": DIGEST},
             "operation": "MODIFY", "semantic_action": "bind selected name to metadata.name",
             "intent": "Restore the required name binding.", "violation_refs": ["V1"],
             "postcondition_refs": ["P1"], "depends_on": []}
        ],
        "postconditions": [
            {"id": "P1", "statement": "The enabled render contains the selected ServiceAccount name.",
             "requirement_refs": ["O1"], "obligation_refs": ["A1"], "relation_refs": ["E1"],
             "planned_check": "render and inspect metadata.name", "check_scope": "rendered chart"}
        ],
        "preserve_constraints": [],
        "open_obligations": [],
        "outcome_coverage": [
            {"outcome_id": "O1", "repair_supported": ["V1"],
             "preserve_required": [], "unresolved": []}
        ],
        "status": "READY",
    }


class ContractValidationTests(unittest.TestCase):
    def test_method_input_rejects_unknown_schema_version(self):
        payload = valid_method_input()
        payload["schema_version"] = "ce.method_input.v99"
        with self.assertRaises(ContractError) as caught:
            parse_method_input(payload)
        self.assertEqual(caught.exception.code, "SCHEMA_VERSION_UNSUPPORTED")

    def test_method_input_accepts_explicit_null_and_empty_arrays(self):
        parsed = parse_method_input(valid_method_input())
        self.assertIsNone(parsed.public_observable_text)
        self.assertEqual(parsed.scope_hints, ())
        self.assertEqual(parsed.public_evidence, ())

    def test_method_input_rejects_mismatched_input_digest(self):
        payload = valid_method_input()
        payload["requirement_text"] += " modified"
        with self.assertRaises(ContractError) as caught:
            parse_method_input(payload)
        self.assertEqual(caught.exception.code, "INPUT_DIGEST_MISMATCH")

    def test_declared_artifact_content_is_digest_bound(self):
        payload = valid_method_input()
        payload["declared_artifacts"] = [{"id": "INPUT1", "path": "declared/input.yaml",
            "media_type": "application/yaml", "content_digest": "b" * 64,
            "content": "kind: ConfigMap\n"}]
        payload["input_digest"] = canonical_digest({key: value for key, value in payload.items() if key != "input_digest"})
        with self.assertRaises(ContractError) as caught:
            parse_method_input(payload)
        self.assertEqual(caught.exception.code, "ARTIFACT_DIGEST_MISMATCH")

    def test_diagnosis_rejects_unknown_relation_endpoint(self):
        payload = valid_diagnosis()
        payload["expected_relations"][0]["target_unit_ids"] = ["U404"]
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "REFERENCE_NOT_FOUND")

    def test_multi_endpoint_chain_must_be_declared_as_indivisible_shared_contract(self):
        payload = valid_diagnosis()
        payload["units"].append({"id": "U3", "role": "generated resource", "kind": "resource",
            "semantic_role_class": "GENERATED_STATE", "existence": "PRESENT",
            "locator_refs": ["EV1"], "requirement_refs": ["O1"]})
        payload["expected_relations"][0]["source_unit_ids"] = ["U1", "U2"]
        payload["expected_relations"][0]["target_unit_ids"] = ["U3"]
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "MULTI_ENDPOINT_RELATION_REQUIRES_SHARED_JUSTIFICATION")

    def test_indivisible_shared_relation_requires_an_explicit_reason(self):
        payload = valid_diagnosis()
        relation = payload["expected_relations"][0]
        relation["relation_form"] = "INDIVISIBLE_SHARED"
        relation["shared_constraint_reason"] = "The declared name is one shared equality contract across the template and generated object."
        packet = validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(packet.expected_relations[0]["relation_form"], "INDIVISIBLE_SHARED")

    def test_indivisible_shared_relation_without_reason_is_rejected(self):
        payload = valid_diagnosis()
        relation = payload["expected_relations"][0]
        relation["relation_form"] = "INDIVISIBLE_SHARED"
        relation["shared_constraint_reason"] = None
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "SHARED_RELATION_JUSTIFICATION_MISSING")

    def test_supported_configuration_violation_requires_evidenced_production_consumer_path(self):
        payload = valid_diagnosis()
        payload["obligation_atoms"] = [{"id": "A1", "outcome_id": "O1",
            "statement": "Render the selected ServiceAccount when enabled.",
            "source_refs": ["requirement_text:0:49"], "support": "EXPLICIT"}]
        payload["units"][0]["semantic_role_class"] = "CONFIGURATION_INPUT"
        payload["units"][1]["semantic_role_class"] = "GENERATED_STATE"
        payload["units"][1]["locator_refs"] = ["EV1"]
        payload["expected_relations"][0]["obligation_refs"] = ["A1"]

        with self.assertRaisesRegex(ContractError, "PRODUCTION_PATH_INCOMPLETE"):
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)

    def test_test_artifact_cannot_be_a_production_relation_endpoint(self):
        payload = valid_diagnosis()
        payload["obligation_atoms"] = [{"id": "A1", "outcome_id": "O1",
            "statement": "Render the selected ServiceAccount when enabled.",
            "source_refs": ["requirement_text:0:49"], "support": "EXPLICIT"}]
        payload["units"][0]["semantic_role_class"] = "CONFIGURATION_INPUT"
        payload["units"][1]["semantic_role_class"] = "TEST_ARTIFACT"
        payload["expected_relations"][0]["obligation_refs"] = ["A1"]

        with self.assertRaisesRegex(ContractError, "TEST_ARTIFACT_RELATION_ENDPOINT"):
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)

    def test_diagnosis_preserves_unknown_as_unknown(self):
        packet = validate_diagnosis(
            valid_diagnosis(), parse_method_input(valid_method_input()), None
        )
        payload = valid_diagnosis()
        payload["units"][1]["existence"] = "UNKNOWN"
        packet = validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(packet.units[1].existence, "UNKNOWN")
        self.assertNotEqual(packet.units[1].existence, "EXPECTED_MISSING")

    def test_diagnosis_rejects_snapshot_mismatch(self):
        payload = valid_diagnosis()
        payload["evidence_refs"][0]["snapshot_id"] = "snap-old"
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "SNAPSHOT_MISMATCH")

    def test_diagnosis_rejects_unknown_status(self):
        payload = valid_diagnosis()
        payload["status"] = "PASS"
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "SCHEMA_INVALID")

    def test_expected_missing_requires_evidence(self):
        payload = valid_diagnosis()
        payload["units"][0]["existence"] = "EXPECTED_MISSING"
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "MISSING_UNIT_UNSUPPORTED")

    def test_expected_missing_accepts_complete_snapshot_bound_exact_search(self):
        payload = valid_diagnosis()
        locator = {"query_kind": "EXACT_PATH", "query": "templates/missing.yaml",
                   "scope": "snapshot_file_inventory", "inspected_file_count": 8,
                   "match_count": 0, "matched_paths": [], "complete": True, "truncated": False}
        excerpt = "Complete exact_path search found 0 matching path(s)."
        digest = static_search_content_digest("snap-0", locator, excerpt)
        payload["evidence_refs"].append({"id": "SEARCH-ABSENT", "snapshot_id": "snap-0",
            "kind": "STATIC_SEARCH", "locator": locator, "content_digest": digest,
            "excerpt": excerpt, "origin": "runner-generated exact search over the current Broken source snapshot"})
        payload["units"][0]["existence"] = "EXPECTED_MISSING"
        payload["units"][0]["locator_refs"] = ["SEARCH-ABSENT"]
        packet = validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(packet.units[0].existence, "EXPECTED_MISSING")

    def test_expected_missing_rejects_truncated_search(self):
        payload = valid_diagnosis()
        locator = {"query_kind": "EXACT_PATH", "query": "templates/missing.yaml",
                   "scope": "snapshot_file_inventory", "inspected_file_count": 2,
                   "match_count": 0, "matched_paths": [], "complete": False, "truncated": True}
        excerpt = "Bounded exact_path search was incomplete: file limit reached."
        digest = static_search_content_digest("snap-0", locator, excerpt)
        payload["evidence_refs"].append({"id": "SEARCH-INCOMPLETE", "snapshot_id": "snap-0",
            "kind": "STATIC_SEARCH", "locator": locator, "content_digest": digest,
            "excerpt": excerpt, "origin": "runner-generated exact search over the current Broken source snapshot"})
        payload["units"][0]["existence"] = "EXPECTED_MISSING"
        payload["units"][0]["locator_refs"] = ["SEARCH-INCOMPLETE"]
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "MISSING_UNIT_UNSUPPORTED")

    def test_observed_only_cannot_support_causal_violation(self):
        payload = valid_diagnosis()
        payload["violations"][0]["support_basis"] = "OBSERVED_ONLY"
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "CAUSAL_CLAIM_UNSUPPORTED")

    def test_supported_relation_violation_must_be_covered_by_its_requirement_outcome(self):
        payload = valid_diagnosis()
        payload["status"] = "EVIDENCE_GAP"
        payload["outcome_coverage"][0]["repair_supported"] = []
        payload["outcome_coverage"][0]["unresolved"] = ["O1"]
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "OUTCOME_SUPPORTED_VIOLATION_UNCOVERED")

    def test_ready_diagnosis_must_cover_every_outcome(self):
        payload = valid_diagnosis()
        payload["outcome_coverage"] = []
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "OUTCOME_COVERAGE_INCOMPLETE")

    def test_preserved_outcome_records_current_satisfaction(self):
        payload = valid_diagnosis()
        payload["outcome_coverage"][0]["repair_supported"] = []
        payload["outcome_coverage"][0]["preserve_required"] = ["O1"]
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, "PRESERVE_SATISFACTION_MISSING")

    def test_changeset_rejects_stale_existing_target(self):
        payload = valid_plan()
        payload["changes"][0]["target"]["snapshot_id"] = "snap-old"
        diagnosis = validate_diagnosis(
            valid_diagnosis(), parse_method_input(valid_method_input()), None
        )
        result = validate_changeset(payload, diagnosis, None)
        self.assertFalse(result.valid)
        self.assertEqual(result.errors[0].code, "SNAPSHOT_MISMATCH")

    def test_changeset_accepts_supported_ready_plan(self):
        diagnosis = validate_diagnosis(
            valid_diagnosis(), parse_method_input(valid_method_input()), None
        )
        result = validate_changeset(valid_plan(), diagnosis, None)
        self.assertTrue(result.valid, result.errors)


if __name__ == "__main__":
    unittest.main()
