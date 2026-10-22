import copy
import json
import unittest
from types import SimpleNamespace

from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.evidence import static_search_content_digest
from contractedge.contracts.validation import canonical_digest, validate_changeset, validate_diagnosis
from contractedge.contracts.validation import parse_method_input
from contractedge.contracts.models import Snapshot
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan


class SemanticChangeSetTests(unittest.TestCase):
    def test_mixed_outcome_can_preserve_an_atom_outside_its_supported_repairs(self):
        payload=valid_diagnosis()
        payload['obligation_atoms'].append({'id':'A2','outcome_id':'O1',
            'statement':'Keep the disabled branch absent.','source_refs':['requirement_text:0:49'],'support':'EXPLICIT'})
        payload['expected_relations'].append({**copy.deepcopy(payload['expected_relations'][0]),
            'id':'E2','obligation_refs':['A2'],'expected_contract':'Keep disabled branch behavior.'})
        diagnosis=validate_diagnosis(payload,parse_method_input(valid_method_input()),None)
        context=SimpleNamespace(run_id='run-1',case_id='RW-DEMO',snapshot_id='snap-0',
            input_digest=diagnosis.input_digest,model_config={'model':'fixture'},budget={'max_output_tokens':1000})
        request=build_changeset_request(diagnosis,context)
        preserve=request.output_schema['properties']['preserve_constraints']
        self.assertGreater(preserve.get('maxItems',32),0)
        self.assertIn('O1',preserve['items']['properties']['outcome_id']['enum'])
        plan=valid_plan()
        plan['diagnosis_digest']=canonical_digest(payload)
        plan['preserve_constraints']=[{'id':'KEEP1','outcome_id':'O1','obligation_refs':['A2'],
            'statement':'Keep the disabled branch absent.','current_satisfaction':'OBSERVED','evidence_refs':['EV1']}]
        result=validate_changeset(plan,diagnosis,None)
        self.assertTrue(result.valid,result.errors)

    def setUp(self):
        self.diagnosis = validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)

    def test_changeset_request_carries_its_exact_output_schema(self):
        context = SimpleNamespace(run_id="run-1", case_id="RW-DEMO", snapshot_id="snap-0",
            input_digest="a" * 64, model_config={"model": "fixture"}, budget={"max_output_tokens": 1000})
        request = build_changeset_request(self.diagnosis, context)
        packet = json.loads(request.messages[1]["content"])
        self.assertEqual(request.prompt_version, "changeset.v1.1.10")
        self.assertEqual(packet["output_schema"]["type"], "object")
        self.assertNotIn("$defs", packet["output_schema"])
        target = packet["output_schema"]["properties"]["changes"]["items"]["properties"]["target"]
        self.assertEqual(target["properties"]["target_kind"]["enum"], ["EXISTING"])
        self.assertEqual(target["properties"]["unit_refs"]["items"]["enum"], ["U2"])
        self.assertIn("source_path", target["properties"])
        self.assertNotIn("path", target["properties"])
        self.assertNotIn("parent_path", target["properties"])
        self.assertIn("runner resolves those", request.messages[0]["content"])
        self.assertIn("Trace each proposed setting or field through its production consumer to the generated or runtime outcome", request.messages[0]["content"])
        self.assertIn("Do not add an unused value or declaration whose consumer path is absent from the supplied evidence", request.messages[0]["content"])
        self.assertIn("Every planned postcondition must cite the atomic obligation IDs it restores", request.messages[0]["content"])
        self.assertIn("Do not treat a test-only edit as restoration of a production relation", request.messages[0]["content"])
        self.assertIn("obligation_refs", packet["output_schema"]["properties"]["postconditions"]["items"]["required"])

    def test_existing_target_schema_names_one_source_path_and_groups_units(self):
        diagnosis_payload = valid_diagnosis()
        diagnosis_payload["evidence_refs"].append({"id": "EV3", "snapshot_id": "snap-0",
            "kind": "SOURCE_SPAN", "locator": {"path": "values.yaml", "start_line": 1, "end_line": 4},
            "content_digest": "b" * 64, "excerpt": "serviceAccount.create: true", "origin": "broken source"})
        diagnosis_payload["units"][0]["locator_refs"] = ["EV3"]
        diagnosis = validate_diagnosis(diagnosis_payload, parse_method_input(valid_method_input()), None)
        context = SimpleNamespace(run_id="run-path", case_id="RW-DEMO", snapshot_id="snap-0",
            input_digest="a" * 64, model_config={"model": "fixture"}, budget={"max_output_tokens": 1000})

        request = build_changeset_request(diagnosis, context)

        packet = json.loads(request.messages[1]["content"])
        target = packet["output_schema"]["properties"]["changes"]["items"]["properties"]["target"]
        self.assertEqual(target["properties"]["source_path"]["enum"],
                         ["templates/serviceaccount.yaml", "values.yaml"])
        self.assertEqual(packet["existing_target_paths"], [
            {"path": "templates/serviceaccount.yaml", "unit_ids": ["U2"]},
            {"path": "values.yaml", "unit_ids": ["U1"]},
        ])
        self.assertIn("every unit in that change must resolve to that one source_path", request.messages[0]["content"])
        self.assertIn("Trace each proposed setting or field through its production consumer to the generated or runtime outcome", request.messages[0]["content"])
        self.assertIn("Do not add an unused value or declaration whose consumer path is absent from the supplied evidence", request.messages[0]["content"])
        self.assertEqual(request.prompt_version, "changeset.v1.1.10")

    def test_rejects_dependency_cycle(self):
        plan = valid_plan()
        second = copy.deepcopy(plan["changes"][0])
        second["id"] = "C2"
        second["depends_on"] = ["C1"]
        plan["changes"][0]["depends_on"] = ["C2"]
        plan["changes"].append(second)
        result = validate_changeset(plan, self.diagnosis, None)
        self.assertFalse(result.valid)
        self.assertIn("DEPENDENCY_CYCLE", {issue.code for issue in result.errors})

    def test_requires_every_requirement_outcome_to_be_covered(self):
        plan = valid_plan()
        plan["outcome_coverage"] = []
        result = validate_changeset(plan, self.diagnosis, None)
        self.assertFalse(result.valid)
        self.assertIn("OUTCOME_NOT_COVERED", {issue.code for issue in result.errors})

    def test_postconditions_must_cover_every_planned_atomic_obligation(self):
        diagnosis_payload = valid_diagnosis()
        diagnosis_payload["obligation_atoms"].append({"id": "A2", "outcome_id": "O1",
            "statement": "Preserve the enabled condition.", "source_refs": ["requirement_text:0:49"],
            "support": "DERIVED"})
        diagnosis_payload["expected_relations"].append({"id": "E2", "source_unit_ids": ["U1"],
            "target_unit_ids": ["U2"], "mechanism": "BINDING", "condition": "enabled",
            "relation_form": "DIRECT_EDGE", "shared_constraint_reason": None,
            "expected_contract": "The enabled condition is preserved.", "requirement_refs": ["O1"],
            "obligation_refs": ["A2"], "support_refs": ["EV2"]})
        diagnosis = validate_diagnosis(diagnosis_payload, parse_method_input(valid_method_input()), None)
        plan = valid_plan()
        plan["diagnosis_digest"] = canonical_digest(diagnosis.raw)
        result = validate_changeset(plan, diagnosis, None)
        self.assertFalse(result.valid)
        self.assertIn("OBLIGATION_NOT_COVERED", {issue.code for issue in result.errors})

    def test_new_target_requires_missing_role_and_existing_parent(self):
        diagnosis_payload = valid_diagnosis()
        diagnosis_payload["units"][1]["existence"] = "EXPECTED_MISSING"
        diagnosis_payload["units"][1]["role"] = "chart template"
        locator = {"query_kind": "EXACT_PATH", "query": "templates/serviceaccount.yaml",
                   "scope": "snapshot_file_inventory", "inspected_file_count": 1,
                   "match_count": 0, "matched_paths": [], "complete": True, "truncated": False}
        excerpt = "Complete exact_path search found 0 matching path(s)."
        digest = static_search_content_digest("snap-0", locator, excerpt)
        diagnosis_payload["evidence_refs"].append({"id": "SEARCH-ABSENT", "snapshot_id": "snap-0",
            "kind": "STATIC_SEARCH", "locator": locator, "content_digest": digest,
            "excerpt": excerpt, "origin": "runner-generated exact search over the current Broken source snapshot"})
        diagnosis_payload["units"][1]["locator_refs"] = ["SEARCH-ABSENT"]
        diagnosis = validate_diagnosis(diagnosis_payload, parse_method_input(valid_method_input()), None)
        plan = valid_plan()
        plan["diagnosis_digest"] = canonical_digest(diagnosis.raw)
        plan["changes"][0]["operation"] = "CREATE"
        plan["changes"][0]["target"] = {"kind": "NEW", "parent_path": "templates",
            "proposed_path": "templates/serviceaccount.yaml", "expected_role": "chart template",
            "anchor_unit_refs": ["U1"], "missing_unit_refs": ["U2"], "justification_refs": ["EV2"]}
        snapshot = Snapshot("snap-0", ".", {"templates/values.yaml": "c" * 64})
        result = validate_changeset(plan, diagnosis, snapshot)
        self.assertTrue(result.valid, result.errors)

    def test_new_target_cannot_use_unknown_instead_of_expected_missing(self):
        diagnosis_payload = valid_diagnosis()
        diagnosis_payload["units"][1]["existence"] = "UNKNOWN"
        diagnosis = validate_diagnosis(diagnosis_payload, parse_method_input(valid_method_input()), None)
        plan = valid_plan()
        plan["diagnosis_digest"] = canonical_digest(diagnosis.raw)
        plan["changes"][0]["operation"] = "CREATE"
        plan["changes"][0]["target"] = {"kind": "NEW", "parent_path": "templates",
            "proposed_path": "templates/serviceaccount.yaml", "expected_role": "chart template",
            "anchor_unit_refs": ["U1"], "missing_unit_refs": ["U2"], "justification_refs": ["EV2"]}
        snapshot = Snapshot("snap-0", ".", {"templates/values.yaml": "c" * 64})
        result = validate_changeset(plan, diagnosis, snapshot)
        self.assertFalse(result.valid)
        self.assertIn("NEW_TARGET_MISSING_ROLE_INVALID", {issue.code for issue in result.errors})

    def test_new_target_must_be_created_under_its_declared_parent(self):
        diagnosis_payload = valid_diagnosis()
        diagnosis_payload["units"][1]["existence"] = "EXPECTED_MISSING"
        diagnosis_payload["units"][1]["role"] = "chart template"
        locator = {"query_kind": "EXACT_PATH", "query": "templates/serviceaccount.yaml",
                   "scope": "snapshot_file_inventory", "inspected_file_count": 1,
                   "match_count": 0, "matched_paths": [], "complete": True, "truncated": False}
        excerpt = "Complete exact_path search found 0 matching path(s)."
        digest = static_search_content_digest("snap-0", locator, excerpt)
        diagnosis_payload["evidence_refs"].append({"id": "SEARCH-ABSENT", "snapshot_id": "snap-0",
            "kind": "STATIC_SEARCH", "locator": locator, "content_digest": digest,
            "excerpt": excerpt, "origin": "runner-generated exact search over the current Broken source snapshot"})
        diagnosis_payload["units"][1]["locator_refs"] = ["SEARCH-ABSENT"]
        diagnosis = validate_diagnosis(diagnosis_payload, parse_method_input(valid_method_input()), None)
        plan = valid_plan()
        plan["diagnosis_digest"] = canonical_digest(diagnosis.raw)
        plan["changes"][0]["operation"] = "CREATE"
        plan["changes"][0]["target"] = {"kind": "NEW", "parent_path": "templates",
            "proposed_path": "outside/serviceaccount.yaml", "expected_role": "chart template",
            "anchor_unit_refs": ["U1"], "missing_unit_refs": ["U2"], "justification_refs": ["EV2"]}
        snapshot = Snapshot("snap-0", ".", {"templates/values.yaml": "c" * 64})

        result = validate_changeset(plan, diagnosis, snapshot)

        self.assertFalse(result.valid)
        self.assertIn("NEW_TARGET_PARENT_MISMATCH", {issue.code for issue in result.errors})


if __name__ == "__main__":
    unittest.main()
