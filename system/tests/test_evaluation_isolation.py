import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


class EvaluationIsolationTests(unittest.TestCase):
    def test_method_runner_does_not_import_evaluator(self):
        subprocess.run([sys.executable, "-c",
                        "import sys; import contractedge.orchestration.runner; "
                        "assert 'contractedge.evaluation.gt_adapter' not in sys.modules"],
                       check=True)

    def test_normalizes_r1_overlay_and_r5_nested_gt_without_coercing_eligibility(self):
        adapter = importlib.import_module("contractedge.evaluation.gt_adapter")
        r1 = {"case_id": "RW-A", "requirement": "must bind value", "oracle_scope": "PARTIAL",
              "full_recall_eligible": False, "relevant_units": [{"id": "U1", "role": "template"}],
              "expected_relations": [{"id": "E1", "source": "U1", "target": "U2", "expected_coordination": "bind"}],
              "violated_relations": [{"id": "V1", "relation": "E1", "actual_broken_behavior": "omitted"}],
              "oracle_limit": "rendered output only"}
        normalized = adapter.normalize_gt(r1, "R1-v4")
        self.assertFalse(normalized["full_recall_eligible"])
        self.assertEqual(normalized["violations"][0]["relation_id"], "E1")
        r5 = {"case_id": "RW-B", "requirement": {"statement": "process consumes config"},
              "classification": {"primary_class": "R5"}, "coordination": {"units": [{"id": "U1"}],
              "expected_relations": [{"id": "E1", "source": "U1", "target": "U2"}],
              "violated_relations": [{"id": "V1", "relation": "E1"}]}}
        normalized_r5 = adapter.normalize_gt(r5, "R5-v28")
        self.assertIsNone(normalized_r5["full_recall_eligible"])
        self.assertEqual(normalized_r5["primary_class"], "R5")

    def test_uses_manifest_case_id_when_legacy_gt_omits_it(self):
        adapter = importlib.import_module("contractedge.evaluation.gt_adapter")
        gt = {"coordination": {"units": [], "expected_relations": [], "violated_relations": []}}

        normalized = adapter.normalize_gt(gt, "R1", release_metadata={"case_id": "RW-CAND-070"})

        self.assertEqual(normalized["case_id"], "RW-CAND-070")

    def test_normalizes_release_oracle_metadata_without_copying_it_to_method_input(self):
        adapter = importlib.import_module("contractedge.evaluation.gt_adapter")
        gt = {"case_id": "RW-R5-METADATA", "coordination": {
            "units": [{"id": "U1"}, {"id": "U2"}],
            "expected_relations": [{"id": "E1", "source": "U1", "target": "U2"}],
            "violated_relations": [{"id": "V1", "relation": "E1"}]}}
        metadata = {"case_id": "RW-R5-METADATA", "primary_class": "R5",
                    "fault_boundary": "runtime config consumer",
                    "observed_depth": "process HTTP response",
                    "oracle_boundary": "Exact-source production HTTP response only."}

        normalized = adapter.normalize_gt(gt, "R5-v28", release_metadata=metadata)

        self.assertEqual(normalized["primary_class"], "R5")
        self.assertEqual(normalized["fault_boundary"], "runtime config consumer")
        self.assertEqual(normalized["observed_depth"], "process HTTP response")
        self.assertEqual(normalized["oracle_limit"], metadata["oracle_boundary"])


if __name__ == "__main__":
    unittest.main()
