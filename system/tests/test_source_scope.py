import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from contractedge.source.snapshot import compute_snapshot
from contractedge.source.scopes import resolve_scope


class SourceScopeTests(unittest.TestCase):
    def test_concept_scope_uses_tokens_when_the_full_phrase_is_not_in_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "z_controllers/applicationset_controller.go"
            target.parent.mkdir()
            target.write_text(
                "package controller\nfunc reconcileApplicationSet() { /* deletion lifecycle */ }\n",
                encoding="utf-8",
            )
            decoy = root / "a_docs/lifecycle.md"
            decoy.parent.mkdir()
            decoy.write_text("ApplicationSet lifecycle overview\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            result = resolve_scope(root, snapshot, [
                {"kind": "concept", "value": "ApplicationSet deletion lifecycle", "raw_text": "scope"},
            ])

        self.assertIn("z_controllers/applicationset_controller.go", result.seed_paths)
        self.assertEqual(result.seed_paths[0], "z_controllers/applicationset_controller.go")
        self.assertEqual(result.search_audit[0]["match_mode"], "CONCEPT_TERMS")
        self.assertGreaterEqual(len(result.search_audit[0]["query_terms"]), 2)

    def test_match_cap_is_reported_as_incomplete_scope_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a", "b", "c"):
                path = root / f"{name}.py"
                path.write_text("def reconcile_scheduler():\n    return 'queue scheduler'\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            result = resolve_scope(root, snapshot, [
                {"kind": "concept", "value": "scheduler queue", "raw_text": "scope"},
            ], max_matches_per_hint=1)

        self.assertEqual(len(result.seed_paths), 1)
        self.assertEqual(len(result.unresolved), 1)
        self.assertTrue(result.unresolved[0]["results_capped"])
        self.assertTrue(result.unresolved[0]["search_complete"])
        self.assertEqual(result.search_audit[0]["matches_total"], 3)
        self.assertTrue(result.search_audit[0]["coverage_incomplete"])

    def test_content_scope_budget_exhaustion_is_reported_as_incomplete_not_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("no relevant symbol here\n", encoding="utf-8")
            (root / "b.txt").write_text("ReconcileScheduler handles the event\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            result = resolve_scope(root, snapshot, [
                {"kind": "symbol", "value": "ReconcileScheduler", "raw_text": "symbol"},
            ], max_search_files=1, max_search_bytes=1024)
            byte_limited = resolve_scope(root, snapshot, [
                {"kind": "symbol", "value": "ReconcileScheduler", "raw_text": "symbol"},
            ], max_search_files=10, max_search_bytes=1, max_search_file_bytes=1024)

        self.assertEqual(result.seed_paths, ())
        self.assertEqual(len(result.unresolved), 1)
        self.assertFalse(result.unresolved[0]["search_complete"])
        self.assertEqual(result.search_audit[0]["files_inspected"], 1)
        self.assertTrue(result.search_audit[0]["truncated"])
        self.assertEqual(byte_limited.seed_paths, ())
        self.assertFalse(byte_limited.unresolved[0]["search_complete"])
        self.assertEqual(byte_limited.search_audit[0]["bytes_inspected"], 0)

    def test_path_only_hints_do_not_read_repository_file_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "templates").mkdir()
            (root / "templates/deploy.yaml").write_text("kind: Deployment\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            with patch.object(Path, "read_text", side_effect=AssertionError("path resolution read source contents")):
                result = resolve_scope(root, snapshot, [
                    {"kind": "path", "value": "templates/deploy.yaml", "raw_text": "path"},
                ])

        self.assertEqual(result.seed_paths, ("templates/deploy.yaml",))

    def test_snapshot_is_repeatable_and_changes_with_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            first = compute_snapshot(root)
            second = compute_snapshot(root)
            self.assertEqual(first.snapshot_id, second.snapshot_id)
            (root / "main.go").write_text("package main\nfunc f() {}\n", encoding="utf-8")
            self.assertNotEqual(compute_snapshot(root).snapshot_id, first.snapshot_id)

    def test_path_selector_resolves_file_and_unresolved_hint_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "templates").mkdir()
            (root / "templates/deploy.yaml").write_text("kind: Deployment\n", encoding="utf-8")
            snapshot = compute_snapshot(root)
            result = resolve_scope(root, snapshot, [
                {"kind": "path", "value": "templates/deploy.yaml:spec.template", "raw_text": "path:selector"},
                {"kind": "symbol", "value": "MissingSymbol", "raw_text": "symbol"},
            ])
            self.assertEqual(result.seed_paths, ("templates/deploy.yaml",))
            self.assertEqual(len(result.unresolved), 1)

    def test_path_description_resolves_a_unique_source_root_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "source/controller/sync.go"
            target.parent.mkdir(parents=True)
            target.write_text("package controller\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            result = resolve_scope(root, snapshot, [
                {"kind": "path", "value": "controller/sync.go normalization", "raw_text": "scope"},
            ])

        self.assertEqual(result.seed_paths, ("source/controller/sync.go",))
        self.assertEqual(result.search_audit[0]["match_mode"], "PATH_UNIQUE_SUFFIX")

    def test_path_description_resolves_exact_path_at_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "controller/sync.go"
            target.parent.mkdir()
            target.write_text("package controller\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            result = resolve_scope(root, snapshot, [
                {"kind": "path", "value": "controller/sync.go normalization", "raw_text": "scope"},
            ])

        self.assertEqual(result.seed_paths, ("controller/sync.go",))
        self.assertEqual(result.search_audit[0]["match_mode"], "PATH_DESCRIPTION_TOKEN")

    def test_ambiguous_path_suffix_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for prefix in ("one", "two"):
                target = root / prefix / "controller/sync.go"
                target.parent.mkdir(parents=True)
                target.write_text("package controller\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            result = resolve_scope(root, snapshot, [
                {"kind": "path", "value": "controller/sync.go normalization", "raw_text": "scope"},
            ])

        self.assertEqual(result.seed_paths, ())
        self.assertEqual(len(result.unresolved), 1)


if __name__ == "__main__":
    unittest.main()
