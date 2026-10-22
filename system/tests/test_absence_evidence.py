import tempfile
import unittest
from pathlib import Path

from contractedge.contracts.evidence import supports_expected_missing_evidence
from contractedge.source.absence import collect_absence_search_evidence
from contractedge.source.snapshot import compute_snapshot


class AbsenceSearchTests(unittest.TestCase):
    def test_exact_path_inventory_lookup_is_not_limited_by_content_scan_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "controller/state.go").parent.mkdir()
            (root / "controller/state.go").write_text("package controller\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            records, _ = collect_absence_search_evidence(snapshot, [
                {"id": "G1", "locator_hint": {"path": "controller/state.go"}},
                {"id": "G2", "locator_hint": {"path": "controller/missing.go"}},
            ], max_files=0)

        self.assertEqual(len(records), 2)
        existing, missing = records
        self.assertTrue(existing["locator"]["complete"])
        self.assertEqual(existing["locator"]["matched_paths"], ["controller/state.go"])
        self.assertTrue(missing["locator"]["complete"])
        self.assertEqual(missing["locator"]["matched_paths"], [])

    def test_exact_path_and_symbol_search_are_snapshot_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "templates").mkdir()
            (root / "templates/chart.yaml").write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
            (root / "src.go").write_text("package demo\nfunc existingUnit() {}\n", encoding="utf-8")
            snapshot = compute_snapshot(root)
            records, audit = collect_absence_search_evidence(snapshot, [
                {"id": "G1", "locator_hint": {"path": "templates/missing.yaml"}},
                {"id": "G2", "locator_hint": {"symbol": "missingUnit"}},
                {"id": "G3", "locator_hint": {"search_term": "maybe absent"}},
            ], max_files=20)

        self.assertEqual(len(records), 2)
        self.assertEqual(audit["query_count"], 2)
        for record in records:
            self.assertEqual(record["snapshot_id"], snapshot.snapshot_id)
            self.assertTrue(supports_expected_missing_evidence(record))
            self.assertEqual(record["locator"]["match_count"], 0)
            self.assertTrue(record["locator"]["complete"])

    def test_positive_and_truncated_searches_do_not_prove_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src.go").write_text("package demo\nfunc presentUnit() {}\n", encoding="utf-8")
            snapshot = compute_snapshot(root)
            positive, _ = collect_absence_search_evidence(snapshot, [
                {"id": "G1", "locator_hint": {"symbol": "presentUnit"}}
            ], max_files=20)
            truncated, _ = collect_absence_search_evidence(snapshot, [
                {"id": "G2", "locator_hint": {"symbol": "missingUnit"}}
            ], max_files=0)
            byte_limited, _ = collect_absence_search_evidence(snapshot, [
                {"id": "G3", "locator_hint": {"symbol": "missingUnit"}}
            ], max_files=20, max_bytes=1)

        self.assertEqual(positive[0]["locator"]["match_count"], 1)
        self.assertFalse(supports_expected_missing_evidence(positive[0]))
        self.assertFalse(truncated[0]["locator"]["complete"])
        self.assertFalse(supports_expected_missing_evidence(truncated[0]))
        self.assertFalse(byte_limited[0]["locator"]["complete"])
        self.assertFalse(supports_expected_missing_evidence(byte_limited[0]))


if __name__ == "__main__":
    unittest.main()
