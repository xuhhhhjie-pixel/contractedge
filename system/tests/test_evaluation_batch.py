import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from contractedge.evaluation.batch import build_cohort_report


class CohortEvaluationTests(unittest.TestCase):
    def test_keeps_unrun_and_unadjudicated_cases_in_cohort_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gt_path = root / "gt.yaml"
            gt_path.write_text("case_id: CASE-1\ncoordination:\n  units: []\n  expected_relations: []\n  violated_relations: []\n", encoding="utf-8")
            digest = hashlib.sha256(gt_path.read_bytes()).hexdigest()
            trace_path = root / "traces" / "CASE-1.json"
            trace_path.parent.mkdir()
            trace_path.write_text(json.dumps({"case_id": "CASE-1", "status": "READY",
                "diagnosis": {"units": [], "expected_relations": [], "violations": []}}), encoding="utf-8")
            members = [
                {"case_id": "CASE-1", "release_id": "R1-v1", "ground_truth": "gt.yaml",
                 "ground_truth_sha256": digest},
                {"case_id": "CASE-2", "release_id": "R5-v28", "ground_truth": "gt.yaml",
                 "ground_truth_sha256": digest, "primary_class": "R5",
                 "fault_boundary": "runtime consumer", "observed_depth": "HTTP readiness",
                 "oracle_boundary": "readiness only"},
            ]
            traces = [{"case_id": "CASE-1", "trace": "CASE-1.json"}]

            report = build_cohort_report(members, traces, root / "traces", root)

        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["attempted_count"], 1)
        by_id = {row["case_id"]: row for row in report["cases"]}
        self.assertEqual(by_id["CASE-2"]["status"], "NOT_RUN")
        self.assertTrue(by_id["CASE-2"]["denominator_included"])
        self.assertIsNone(by_id["CASE-2"]["violation_precision"])
        self.assertEqual(by_id["CASE-2"]["oracle_boundary"], "readiness only")
        self.assertEqual(report["by_release"]["R5-v28"]["not_run"], 1)

    def test_rejects_trace_for_case_outside_frozen_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            members = [{"case_id": "CASE-1", "release_id": "R1-v1", "ground_truth": "missing.yaml"}]
            with self.assertRaisesRegex(ValueError, "outside the frozen cohort"):
                build_cohort_report(members, [{"case_id": "CASE-X", "trace": "x.json"}], root, root)

    def test_rejects_trace_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gt.yaml").write_text("case_id: CASE-X\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsafe trace path"):
                build_cohort_report(
                    [{"case_id": "CASE-X", "release_id": "R1-v1", "ground_truth": "gt.yaml"}],
                    [{"case_id": "CASE-X", "trace": "../x.json"}], root, root)


if __name__ == "__main__":
    unittest.main()
