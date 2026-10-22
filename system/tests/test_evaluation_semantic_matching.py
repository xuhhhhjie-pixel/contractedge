import unittest

from contractedge.evaluation.gt_adapter import normalize_gt, score_run


class SemanticRelationScoringTests(unittest.TestCase):
    def test_normalizes_unit_id_prefixes_in_descriptive_r5_relation_endpoints(self):
        gt = {
            "case_id": "RW-EXAMPLE-1",
            "coordination": {
                "relevant_units": [
                    {"id": "U1", "role": "mounted configuration"},
                    {"id": "U2", "role": "runtime consumer"},
                ],
                "expected_relations": [{"id": "E1", "source": "U1 mounted ConfigMap",
                                        "target": "U2 running process"}],
                "violated_relations": [{"id": "V1", "relation": "E1"}],
            },
        }

        normalized = normalize_gt(gt, "R5")

        self.assertEqual(normalized["expected_relations"][0]["source"], "U1")
        self.assertEqual(normalized["expected_relations"][0]["target"], "U2")

    def test_budget_exhaustion_stays_in_case_denominator_as_its_own_failure_kind(self):
        result = score_run({"status": "BUDGET_EXHAUSTED"}, {
            "case_id": "RW-BUDGET", "release_id": "R2", "expected_relations": [], "violations": [],
            "precision_eligible": True, "recall_eligible": True,
        })
        self.assertTrue(result["denominator_included"])
        self.assertEqual(result["failure_kind"], "BUDGET")

    def test_scoring_record_preserves_class_and_oracle_boundary(self):
        normalized = normalize_gt({"case_id": "RW-LIMIT", "coordination": {
            "units": [], "expected_relations": [], "violated_relations": []}}, "R5",
            release_metadata={"case_id": "RW-LIMIT", "primary_class": "R5",
                             "fault_boundary": "process reload", "observed_depth": "HTTP readiness",
                             "oracle_boundary": "Readiness only"})

        result = score_run({"status": "BUDGET_EXHAUSTED"}, normalized)

        self.assertEqual(result["primary_class"], "R5")
        self.assertEqual(result["fault_boundary"], "process reload")
        self.assertEqual(result["observed_depth"], "HTTP readiness")
        self.assertEqual(result["oracle_boundary"], "Readiness only")
        self.assertEqual(result["oracle_limit"], "Readiness only")
        self.assertIsNone(result["precision"])

    def test_matches_equivalent_relation_with_different_ids_and_wording(self):
        trace = {
            "status": "READY",
            "diagnosis": {
                "units": [
                    {"id": "P-INPUT", "role": "chart value containing the selected ServiceAccount name"},
                    {"id": "P-POD", "role": "Deployment Pod serviceAccountName field"},
                ],
                "expected_relations": [{
                    "id": "P-EDGE", "source_unit_ids": ["P-INPUT"],
                    "target_unit_ids": ["P-POD"], "mechanism": "PROPAGATION",
                    "condition": "when creation is enabled",
                    "expected_contract": "The selected ServiceAccount name flows into the Pod serviceAccountName field.",
                }],
                "violations": [{"id": "P-FAIL", "relation_id": "P-EDGE"}],
            },
        }
        gt = {
            "case_id": "RW-SEMANTIC-1", "release_id": "R1-v4", "status": "READY",
            "expected_relations": [{
                "id": "E-91", "source": "G-VALUES", "target": "G-POD",
                "type": "PROPAGATION",
                "expected_coordination": "The chosen ServiceAccount value is realized at the Deployment pod serviceAccountName binding.",
                "condition": "when ServiceAccount creation is enabled",
            }],
            "units": [
                {"id": "G-VALUES", "role": "selected ServiceAccount name in chart values"},
                {"id": "G-POD", "role": "Deployment pod serviceAccountName binding"},
            ],
            "violations": [{"id": "GT-V-3", "relation_id": "E-91"}],
            "precision_eligible": True, "recall_eligible": True,
        }

        result = score_run(trace, gt)

        self.assertEqual(result["expected_relation_counts"], {"predicted": 1, "reference": 1, "matched": 1})
        self.assertEqual(result["violation_relation_counts"], {"predicted": 1, "reference": 1, "matched": 1})
        self.assertEqual(result["expected_relation_precision"], 1.0)
        self.assertEqual(result["expected_relation_recall"], 1.0)
        self.assertEqual(result["violation_precision"], 1.0)
        self.assertEqual(result["violation_recall"], 1.0)

    def test_leaves_ineligible_recall_unscored(self):
        result = score_run({"status": "EVIDENCE_GAP", "diagnosis": {"units": [], "expected_relations": [], "violations": []}}, {
            "case_id": "RW-PARTIAL", "release_id": "R1-v4", "expected_relations": [],
            "units": [], "violations": [], "precision_eligible": True,
            "recall_eligible": None, "ambiguity": ["recall eligibility not adjudicated"],
        })

        self.assertIsNone(result["expected_relation_recall"])
        self.assertIsNone(result["violation_recall"])
        self.assertEqual(result["eligibility_status"], "RECALL_UNADJUDICATED")

    def test_does_not_match_when_semantic_target_and_constraint_differ(self):
        trace = {"status": "READY", "diagnosis": {
            "units": [{"id": "A", "role": "chart selected ServiceAccount name"},
                      {"id": "B", "role": "Deployment image pull secret"}],
            "expected_relations": [{"id": "P", "source_unit_ids": ["A"], "target_unit_ids": ["B"],
                                    "expected_contract": "selected account name appears as image pull secret"}],
            "violations": [{"id": "PV", "relation_id": "P"}],
        }}
        gt = {"case_id": "RW-DIFFERENT", "release_id": "R1", "units": [
            {"id": "A1", "role": "chart selected ServiceAccount name"},
            {"id": "B1", "role": "Deployment pod serviceAccountName"}],
            "expected_relations": [{"id": "E", "source": "A1", "target": "B1",
                                    "expected_coordination": "selected account name is bound to Pod serviceAccountName"}],
            "violations": [{"id": "V", "relation_id": "E"}],
            "precision_eligible": True, "recall_eligible": True}

        result = score_run(trace, gt)

        self.assertEqual(result["violation_relation_counts"]["matched"], 0)

    def test_reports_close_semantic_matches_as_ambiguous(self):
        trace = {"status": "READY", "diagnosis": {
            "units": [{"id": "A", "role": "chart selected account name"},
                      {"id": "B", "role": "Deployment pod account name"}],
            "expected_relations": [{"id": "P", "source_unit_ids": ["A"], "target_unit_ids": ["B"],
                                    "expected_contract": "selected account name reaches Deployment pod"}],
            "violations": [{"id": "PV", "relation_id": "P"}],
        }}
        gt = {"case_id": "RW-AMBIGUOUS", "release_id": "R1", "units": [
            {"id": "A1", "role": "chart selected account name"},
            {"id": "B1", "role": "Deployment pod account name"}],
            "expected_relations": [
                {"id": "E1", "source": "A1", "target": "B1", "expected_coordination": "selected account name reaches Deployment pod"},
                {"id": "E2", "source": "A1", "target": "B1", "expected_coordination": "selected account name reaches Deployment pod"}],
            "violations": [{"id": "V1", "relation_id": "E1"}],
            "precision_eligible": True, "recall_eligible": True}

        result = score_run(trace, gt)

        self.assertEqual(result["ambiguous_relation_matches"][0]["predicted_relation_id"], "P")
        self.assertEqual(result["violation_relation_counts"]["matched"], 0)
        self.assertIsNone(result["expected_relation_precision"])
        self.assertIsNone(result["expected_relation_recall"])
        self.assertIsNone(result["violation_precision"])
        self.assertIsNone(result["violation_recall"])
        self.assertEqual(result["scoring_status"], "AMBIGUOUS_VIOLATION_MATCH")


if __name__ == "__main__":
    unittest.main()
