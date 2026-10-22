import unittest

from contractedge.contracts.models import RunContext
from contractedge.contracts.evidence import static_search_content_digest
from contractedge.contracts.validation import parse_method_input
from contractedge.diagnosis.parse import parse_diagnosis_response, parse_json_response
from contractedge.diagnosis.prompt import build_diagnosis_request
from contractedge.model.provider import FixtureProvider, ModelRequest
from contractedge.diagnosis.evidence import bind_evidence_ids, build_evidence_catalog
from contractedge.diagnosis.validate import validate_diagnosis
from contractedge.retrieval.candidates import CandidateSet
from contractedge.source.scopes import ScopeResolution
from tests.test_contracts import valid_diagnosis, valid_method_input
from contractedge.requirement.normalize import normalize_requirement


class DiagnosisProtocolTests(unittest.TestCase):
    def test_diagnosis_request_receives_scope_search_limits_and_unresolved_hints(self):
        method = parse_method_input(valid_method_input())
        requirement = normalize_requirement({"requirement_text": method.requirement_text})
        context = RunContext("run-scope", method.case_id, 0, "snap-0", None, method.input_digest,
                             model_config={"model": "fixture"}, budget={"max_output_tokens": 2000})
        scope = ScopeResolution(
            seed_paths=("controllers/applicationset.go",),
            unresolved=({"kind": "concept", "value": "ApplicationSet deletion lifecycle",
                         "origin_ref": "scope[0]", "search_complete": True, "truncated": False,
                         "results_capped": True},),
            matched_by_hint={},
            search_audit=({"kind": "concept", "query": "ApplicationSet deletion lifecycle",
                           "origin_ref": "scope[0]", "match_mode": "CONCEPT_TERMS",
                           "query_terms": ["application", "deletion", "lifecycle"],
                           "matched_paths": [], "files_inspected": 100, "bytes_inspected": 1024,
                           "search_complete": True, "truncated": False,
                           "results_capped": True, "coverage_incomplete": True},),
        )

        request = build_diagnosis_request(method, requirement,
            CandidateSet((), (), (), {}, (), 0, 5000, 12), context, scope_resolution=scope)

        import json
        public = json.loads(request.messages[1]["content"])
        self.assertEqual(public["scope_resolution"]["unresolved_hints"][0]["search_complete"], True)
        self.assertTrue(public["scope_resolution"]["unresolved_hints"][0]["results_capped"])
        self.assertTrue(public["scope_resolution"]["search_audit"][0]["coverage_incomplete"])
        self.assertIn("not evidence that a role or implementation is absent", request.messages[0]["content"])

    def test_runner_binds_only_exact_evidence_catalog_ids(self):
        method = parse_method_input(valid_method_input())
        candidates = {"candidates": ({"id": "CU1", "path": "templates/a.yaml",
            "locator": {"start_line": 1, "end_line": 1}, "content_digest": "a" * 64,
            "text": "kind: ConfigMap"},)}
        catalog = build_evidence_catalog(method, candidates, "snap-0")
        self.assertEqual(bind_evidence_ids(["SRC-CU1"], catalog), [catalog[0]])
        with self.assertRaisesRegex(ValueError, "unknown catalog ID"):
            bind_evidence_ids(["SRC-CU-NOT-SELECTED"], catalog)

    def test_request_contains_only_method_input_requirement_and_selected_candidates(self):
        method = parse_method_input(valid_method_input())
        requirement = normalize_requirement({"requirement_text": method.requirement_text})
        context = RunContext("run-1", method.case_id, 0, "snap-0", None, method.input_digest,
                             model_config={"model": "fixture"}, budget={"max_output_tokens": 2000})
        candidates = {"selected_ids": ("CU1",), "candidates": ({
                          "id": "CU1", "path": "templates/serviceaccount.yaml",
                          "locator": {"start_line": 1, "end_line": 2}, "snapshot_id": "snap-0",
                          "content_digest": "a" * 64, "text": "public source"},),
                      "dropped_ids": ("CU2",), "context_tokens": 3}
        request = build_diagnosis_request(method, requirement, candidates, context)
        self.assertIn("public source", request.messages[1]["content"])
        self.assertNotIn("unrelated cache", request.messages[1]["content"])
        import json
        public = json.loads(request.messages[1]["content"])
        self.assertEqual(public["output_schema"]["type"], "object")
        self.assertNotIn("$defs", public["output_schema"])
        self.assertNotIn("anyOf", str(public["output_schema"]))
        self.assertIn("outcome_coverage", public["output_schema"]["required"])
        source_evidence = next(item for item in public["evidence_catalog"] if item["kind"] == "SOURCE_SPAN")
        self.assertEqual(source_evidence["kind"], "SOURCE_SPAN")
        self.assertEqual(source_evidence["locator"]["path"], "templates/serviceaccount.yaml")
        self.assertEqual(source_evidence["excerpt"], "public source")
        self.assertEqual(public["allowed_evidence_ids"], [item["id"] for item in public["evidence_catalog"]])
        self.assertEqual(public["output_schema"]["properties"]["evidence_refs"]["items"]["type"], "string")
        self.assertEqual(public["output_schema"]["properties"]["units"]["items"]["properties"]["requirement_refs"]["items"]["enum"], ["O1"])
        self.assertEqual(public["output_schema"]["properties"]["expected_relations"]["items"]["properties"]["requirement_refs"]["items"]["enum"], ["O1"])
        coverage_schema = public["output_schema"]["properties"]["outcome_coverage"]["properties"]["O1"]
        self.assertEqual(coverage_schema["properties"]["disposition"]["enum"],
                         ["REPAIR_SUPPORTED", "PRESERVE_REQUIRED", "UNRESOLVED"])
        self.assertNotIn("repair_supported", coverage_schema["properties"])
        self.assertNotIn("supporting_violation_ids", coverage_schema["properties"])
        self.assertEqual(public["output_schema"]["properties"]["requirement_horizon"]["items"]["enum"],
                         [requirement["outcomes"][0]["statement"]])
        evidence_ids = set(public["allowed_evidence_ids"])
        self.assertEqual(public["output_schema"]["properties"]["units"]["items"]["properties"]["locator_refs"]["items"]["enum"],
                         sorted(evidence_ids))
        self.assertEqual(public["output_schema"]["properties"]["violations"]["items"]["properties"]["evidence_refs"]["items"]["enum"],
                         sorted(evidence_ids))
        self.assertEqual(public["output_schema"]["properties"]["units"]["items"]["properties"]["id"]["pattern"], r"^U[1-9][0-9]*$")
        self.assertIn("obligation_atoms", public["output_schema"]["required"])
        self.assertIn("semantic_role_class", public["output_schema"]["properties"]["units"]["items"]["required"])
        self.assertEqual(public["output_schema"]["properties"]["units"]["items"]["properties"]["semantic_role_class"]["enum"],
                         ["CONFIGURATION_INPUT", "PRODUCTION_CONSUMER", "GENERATED_STATE", "RUNTIME_CONSUMER", "OBSERVED_STATE", "TEST_ARTIFACT", "OTHER"])
        self.assertIn("obligation_refs", public["output_schema"]["properties"]["expected_relations"]["items"]["required"])
        self.assertNotIn("text", public["candidate_set"]["selected_candidates"][0])
        for field in ("schema_version", "run_id", "snapshot_id", "input_digest", "requirement_spec"):
            self.assertNotIn(field, public["output_schema"]["required"])
            self.assertNotIn(field, public["output_schema"]["properties"])
        self.assertIn("The runner adds schema_version", request.messages[0]["content"])
        self.assertIn("Never use case_id, run_id", request.messages[0]["content"])
        self.assertIn("choose exactly one disposition", request.messages[0]["content"])
        self.assertIn("runner converts this keyed representation", request.messages[0]["content"])
        self.assertIn("mark that O REPAIR_SUPPORTED", request.messages[0]["content"])
        self.assertIn("include each exact outcome ID as one object key", request.messages[0]["content"])
        self.assertIn("Do not include request-context fields", request.messages[0]["content"])
        self.assertIn("candidate_set", request.messages[0]["content"])
        self.assertIn("allowed_evidence_ids", request.messages[0]["content"])
        self.assertIn("EXPECTED_MISSING requires direct inventory or observation evidence", request.messages[0]["content"])
        self.assertIn("Split independently required behaviors into separate atomic obligations", request.messages[0]["content"])
        self.assertIn("Never use TEST_ARTIFACT as a relation endpoint or as a production role", request.messages[0]["content"])
        self.assertIn("Represent independent required outputs or independent broken edges as separate ExpectedRelations", request.messages[0]["content"])
        self.assertIn("Keep a genuinely shared constraint as one multi-endpoint relation", request.messages[0]["content"])
        self.assertIn("Do not encode a sequence of transformations or observations as one multi-endpoint relation", request.messages[0]["content"])
        self.assertEqual(request.prompt_version, "diagnosis.v1.1.24")

    def test_expected_missing_schema_requires_complete_absence_evidence(self):
        method = parse_method_input(valid_method_input())
        requirement = normalize_requirement({"requirement_text": method.requirement_text})
        context = RunContext("run-absence", method.case_id, 0, "snap-0", None, method.input_digest,
                             model_config={"model": "fixture"}, budget={"max_output_tokens": 2000})
        candidates = CandidateSet((), (), (), {}, (), 0, 5000, 12)

        request = build_diagnosis_request(method, requirement, candidates, context)
        import json
        initial = json.loads(request.messages[1]["content"])
        existence = initial["output_schema"]["properties"]["units"]["items"]["properties"]["existence"]["enum"]
        self.assertEqual(existence, ["PRESENT", "UNKNOWN"])

        locator = {"query_kind": "EXACT_PATH", "query": "templates/account.yaml",
                                     "scope": "snapshot_file_inventory", "inspected_file_count": 12,
                                     "match_count": 0, "matched_paths": [], "complete": True,
                                     "truncated": False}
        excerpt = "Complete exact_path search found 0 matching path(s)."
        exact_absence = {"id": "SEARCH-123", "snapshot_id": "snap-0", "kind": "STATIC_SEARCH",
                         "locator": locator,
                         "content_digest": static_search_content_digest("snap-0", locator, excerpt),
                         "excerpt": excerpt,
                         "origin": "bounded snapshot search"}
        extended = CandidateSet((), (), (), {}, (), 0, 5000, 12,
                                extension_used=True, supplemental_evidence=(exact_absence,))
        request = build_diagnosis_request(method, requirement, extended, context)
        public = json.loads(request.messages[1]["content"])
        existence = public["output_schema"]["properties"]["units"]["items"]["properties"]["existence"]["enum"]
        self.assertEqual(existence, ["PRESENT", "EXPECTED_MISSING", "UNKNOWN"])
        self.assertIn("SEARCH-123", public["allowed_evidence_ids"])

        forged = {**exact_absence, "id": "SEARCH-FORGED", "content_digest": "f" * 64}
        forged_set = CandidateSet((), (), (), {}, (), 0, 5000, 12,
                                 extension_used=True, supplemental_evidence=(forged,))
        request = build_diagnosis_request(method, requirement, forged_set, context)
        public = json.loads(request.messages[1]["content"])
        existence = public["output_schema"]["properties"]["units"]["items"]["properties"]["existence"]["enum"]
        self.assertEqual(existence, ["PRESENT", "UNKNOWN"])

        incomplete = {**exact_absence, "id": "SEARCH-124", "locator": {**exact_absence["locator"],
                      "complete": False, "truncated": True}}
        incomplete_set = CandidateSet((), (), (), {}, (), 0, 5000, 12,
                                     extension_used=True, supplemental_evidence=(incomplete,))
        request = build_diagnosis_request(method, requirement, incomplete_set, context)
        public = json.loads(request.messages[1]["content"])
        existence = public["output_schema"]["properties"]["units"]["items"]["properties"]["existence"]["enum"]
        self.assertEqual(existence, ["PRESENT", "UNKNOWN"])

    def test_model_outcome_coverage_schema_uses_exact_outcome_keys(self):
        method = parse_method_input(valid_method_input())
        requirement = normalize_requirement({"requirement_text": method.requirement_text})
        context = RunContext("run-coverage", method.case_id, 0, "snap-0", None, method.input_digest,
                             model_config={"model": "fixture"}, budget={"max_output_tokens": 2000})
        request = build_diagnosis_request(method, requirement,
                                          CandidateSet((), (), (), {}, (), 0, 5000, 12), context)
        import json
        public = json.loads(request.messages[1]["content"])
        coverage = public["output_schema"]["properties"]["outcome_coverage"]
        self.assertEqual(coverage["type"], "object")
        self.assertEqual(coverage["required"], ["O1"])
        self.assertEqual(set(coverage["properties"]), {"O1"})
        self.assertFalse(coverage["additionalProperties"])
        self.assertEqual(request.prompt_version, "diagnosis.v1.1.24")

    def test_fixture_provider_retains_response_metadata_and_json_parser_is_strict(self):
        request = ModelRequest(model="fixture", prompt_version="diagnosis.v1.1.11", messages=(),
                               max_output_tokens=1000, temperature=0)
        response = FixtureProvider(valid_diagnosis()).complete_json(request)
        self.assertEqual(response.metadata["provider"], "fixture")
        self.assertEqual(parse_json_response(response.content)["status"], "READY")
        self.assertEqual(parse_diagnosis_response('{"diagnosis":{"status":"READY"}}')["status"], "READY")
        self.assertEqual(parse_diagnosis_response('{"status":"READY","diagnosis":"field"}')
                         ["diagnosis"], "field")
        with self.assertRaises(ValueError):
            parse_json_response("```json\n{}\n```")

    def test_rejects_public_observation_not_present_in_copied_method_evidence(self):
        method = parse_method_input(valid_method_input())
        candidate = {"id": "CU1", "path": "templates/serviceaccount.yaml",
                     "locator": {"start_line": 1, "end_line": 8}, "snapshot_id": "snap-0",
                     "content_digest": "a" * 64, "text": "metadata.name is hard-coded."}
        candidate_set = CandidateSet((candidate,), ("CU1",), (), {}, (), 1, 100, 1)
        with self.assertRaisesRegex(Exception, "PUBLIC_EVIDENCE_NOT_IN_INPUT"):
            validate_diagnosis(valid_diagnosis(), method, None, candidate_set)


if __name__ == "__main__":
    unittest.main()

class PublicEvidenceIdentityTests(unittest.TestCase):
    def test_identical_output_in_two_scenarios_keeps_distinct_valid_locators(self):
        from dataclasses import replace
        payload=valid_diagnosis()
        first=payload['evidence_refs'][0]
        second={**first,'id':'EV3','locator':{'path':'control.json','field':'rendered'}}
        method=replace(parse_method_input(valid_method_input()),public_evidence=(first,second))
        candidate=payload['evidence_refs'][1]
        candidates=CandidateSet(({'id':'CU1','path':candidate['locator']['path'],'content_digest':candidate['content_digest']},),('CU1',),(),{},(),1,100,1)
        packet=validate_diagnosis(payload,method,None,candidates)
        self.assertEqual(packet.evidence_refs[0]['locator'],first['locator'])
