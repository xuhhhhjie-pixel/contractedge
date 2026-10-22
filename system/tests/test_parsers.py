import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from contractedge.contracts.models import Snapshot
from contractedge.retrieval.edges import build_direct_reference_edges
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_declared_artifact_units, extract_gap_extension_units, extract_units


class BoundedExtractionTests(unittest.TestCase):
    def test_seed_scope_priority_is_preserved_when_unit_budget_is_exhausted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a_decoy.go").write_text("package p\nfunc unrelated() {}\n", encoding="utf-8")
            (root / "z_target.go").write_text("package p\nfunc reconcileTarget() {}\n", encoding="utf-8")
            snapshot = compute_snapshot(root)

            units = extract_units(snapshot, {"seed_paths": ("z_target.go", "a_decoy.go")}, max_units=1)

        self.assertEqual(units[0]["path"], "z_target.go")

    def test_yaml_line_offsets_are_indexed_once_for_many_nodes(self):
        import contractedge.source.parsers as parsers

        source = "\n".join(f"key{i}:\n  child: value{i}" for i in range(80))
        original_finditer = parsers.re.finditer
        newline_scans = 0

        def counted_finditer(pattern, text, *args, **kwargs):
            nonlocal newline_scans
            if pattern == "\n":
                newline_scans += 1
            return original_finditer(pattern, text, *args, **kwargs)

        with patch.object(parsers.re, "finditer", side_effect=counted_finditer):
            units = parsers.parse_source_units("large.yaml", source, "snapshot")

        self.assertGreater(len(units), 1)
        self.assertEqual(newline_scans, 1)

    def test_yaml_unit_positions_do_not_rescan_source_for_each_node(self):
        import contractedge.source.parsers as parsers

        class CountedText(str):
            def __new__(cls, value):
                instance = super().__new__(cls, value)
                instance.newline_count_calls = 0
                instance.splitlines_calls = 0
                return instance

            def count(self, substring, start=0, end=None):
                if substring == "\n":
                    self.newline_count_calls += 1
                if end is None:
                    return super().count(substring, start)
                return super().count(substring, start, end)

            def splitlines(self, *args, **kwargs):
                self.splitlines_calls += 1
                return super().splitlines(*args, **kwargs)

        source = CountedText("\n".join(f"key{i}:\n  child: value{i}" for i in range(80)))
        units = parsers.parse_source_units("large.yaml", source, "snapshot")

        self.assertGreater(len(units), 1)
        self.assertEqual(source.newline_count_calls, 0)
        self.assertEqual(source.splitlines_calls, 1)

    def test_text_fallback_is_bounded_and_marks_non_ast_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.go"
            source.write_text("\n".join(f"line {i} serviceAccountName" for i in range(240)), encoding="utf-8")
            snapshot = Snapshot("snap", str(root), {"main.go": "a" * 64})
            units = extract_units(snapshot, {"seed_paths": ("main.go",)}, max_units=4, max_lines=30)
            self.assertLessEqual(len(units), 4)
            self.assertTrue(all(unit["parser_kind"] == "BOUNDED_TEXT_FALLBACK" for unit in units))
            self.assertTrue(all(unit["locator"]["start_line"] <= unit["locator"]["end_line"] for unit in units))

    def test_go_declaration_units_are_lexical_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "controller.go"
            source.write_text("package p\nfunc Reconcile() {\n  ensureService()\n}\n", encoding="utf-8")
            snapshot = Snapshot("snap", str(root), {"controller.go": "a" * 64})
            units = extract_units(snapshot, {"seed_paths": ("controller.go",)}, max_units=4)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["parser_kind"], "GO_LEXICAL_DECLARATION")
        self.assertEqual(units[0]["symbol"], "Reconcile")
        self.assertIn("ensureService", units[0]["references"])

    def test_typescript_interface_and_object_type_alias_are_lexical_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "contracts.ts"
            source.write_text(
                "export interface PodSpec { serviceAccountName?: string }\n"
                "export type ServiceRef = { name: string; }\n",
                encoding="utf-8",
            )
            snapshot = Snapshot("ts-contracts", str(root), {"contracts.ts": "a" * 64})
            units = extract_units(snapshot, {"seed_paths": ("contracts.ts",)}, max_units=8)

        declarations = {unit.get("symbol"): unit for unit in units}
        self.assertEqual(set(declarations), {"PodSpec", "ServiceRef"})
        self.assertTrue(all(unit["parser_kind"] == "JS_TS_LEXICAL_DECLARATION"
                            for unit in declarations.values()))
        self.assertTrue(all(unit["kind"] == "FUNCTION_OR_TYPE" for unit in declarations.values()))

    def test_yaml_json_and_helm_units_report_parser_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "values.yaml").write_text("controller:\n  image: app:v1", encoding="utf-8")
            (root / "config.json").write_text('{"replicas": 2}\n', encoding="utf-8")
            (root / "_helpers.tpl").write_text('{{- define "app.labels" -}}\napp: demo\n{{- end -}}\n', encoding="utf-8")
            snapshot = Snapshot("snap", str(root), {
                "values.yaml": "a" * 64, "config.json": "b" * 64, "_helpers.tpl": "c" * 64,
            })
            yaml_units = extract_units(snapshot, {"seed_paths": ("values.yaml",)})
            json_units = extract_units(snapshot, {"seed_paths": ("config.json",)})
            helm_units = extract_units(snapshot, {"seed_paths": ("_helpers.tpl",)})
        self.assertTrue(any(unit["parser_kind"] == "YAML_NODE_TREE" for unit in yaml_units))
        self.assertTrue(any("image: app:v1" in unit["text"] for unit in yaml_units))
        self.assertTrue(any(unit["parser_kind"] == "JSON_NODE_TREE" for unit in json_units))
        helper = next(unit for unit in helm_units if unit["parser_kind"] == "HELM_TEMPLATE_BLOCK")
        self.assertEqual(helper["symbol"], "app.labels")

    def test_unrendered_helm_yaml_is_not_claimed_as_a_yaml_node_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "deployment.yaml").write_text(
                "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ include \\\"app.fullname\\\" . }}\n"
                "spec:\n  replicas: {{ .Values.replicaCount }}\n  template:\n    spec:\n"
                "      {{- if .Values.serviceAccount.create }}\n"
                "      serviceAccountName: {{ include \\\"app.serviceAccountName\\\" . }}\n"
                "      {{- end }}\n",
                encoding="utf-8",
            )
            snapshot = Snapshot("helm-yaml", str(root), {"deployment.yaml": "a" * 64})
            units = extract_units(snapshot, {"seed_paths": ("deployment.yaml",)})

        self.assertTrue(units)
        self.assertTrue(all(unit["parser_kind"] in {"HELM_YAML_TEMPLATE_FALLBACK", "HELM_RESOURCE_DOCUMENT_TEXT"} for unit in units))
        self.assertTrue(all(unit["kind"] == "SOURCE_TEXT" for unit in units))
        self.assertFalse(any(unit["parser_kind"] == "YAML_NODE_TREE" for unit in units))

    def test_structured_units_obey_line_and_unit_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.yaml").write_text("a:\n  b:\n    c: one\n    d: two\n", encoding="utf-8")
            snapshot = Snapshot("snap", str(root), {"manifest.yaml": "a" * 64})
            units = extract_units(snapshot, {"seed_paths": ("manifest.yaml",)}, max_units=2, max_lines=2)
        self.assertLessEqual(len(units), 2)
        self.assertTrue(all(unit["locator"]["end_line"] - unit["locator"]["start_line"] + 1 <= 2 for unit in units))

    def test_reference_edges_are_direct_and_marked_as_inferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.go").write_text("package p\nfunc Reconcile() { ensureService() }\n", encoding="utf-8")
            (root / "b.go").write_text("package p\nfunc ensureService() { build() }\n", encoding="utf-8")
            (root / "c.go").write_text("package p\nfunc build() {}\n", encoding="utf-8")
            snapshot = Snapshot("snap", str(root), {name: "a" * 64 for name in ("a.go", "b.go", "c.go")})
            from contractedge.source.units import extract_units
            units = extract_units(snapshot, {"seed_paths": ("a.go",)})
        edges = build_direct_reference_edges(units)
        self.assertEqual({unit["path"] for unit in units}, {"a.go", "b.go"})
        self.assertTrue(any(edge["mechanism"] == "LEXICAL_SYMBOL_REFERENCE" and edge["edge_kind"] == "INFERRED"
                            for edge in edges))
        self.assertFalse(any(edge["evidence"]["reference"] == "build" and
                             edge["evidence"]["source_path"] == "a.go" for edge in edges))

    def test_evidence_gap_search_adds_only_query_matching_direct_neighbor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "controller.go").write_text(
                "package p\nfunc Reconcile() { ensureService() }\n", encoding="utf-8")
            (root / "service.go").write_text(
                "package p\nfunc ensureService() { buildService() }\n", encoding="utf-8")
            (root / "unrelated.go").write_text(
                "package p\nfunc reconcileDatabase() { migrate() }\n", encoding="utf-8")
            snapshot = Snapshot("snap-gap", str(root), {
                "controller.go": "a" * 64, "service.go": "b" * 64, "unrelated.go": "c" * 64})
            seed = extract_units(snapshot, {"seed_paths": ("controller.go",)}, max_extension_files=0)

            extension, audit = extract_gap_extension_units(snapshot, ("controller.go",), seed, [
                {"id": "G1", "summary": "Inspect the ensureService implementation", "locator_hint": {"symbol": "ensureService"}}
            ])

        self.assertEqual({unit["path"] for unit in extension}, {"service.go"})
        self.assertTrue(all(unit["scope_extension"] == "EVIDENCE_GAP_DIRECT_NEIGHBOR" for unit in extension))
        self.assertEqual(audit["matched_files"], ["service.go"])

    def test_evidence_gap_can_retrieve_an_unselected_direct_unit_from_a_seed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "controller.go").write_text(
                "package p\nfunc Reconcile() { helper() }\nfunc helper() { apply() }\n",
                encoding="utf-8",
            )
            snapshot = compute_snapshot(root)
            seed = extract_units(snapshot, {"seed_paths": ("controller.go",)}, max_units=1,
                                 max_extension_files=0)

            extension, audit = extract_gap_extension_units(snapshot, ("controller.go",), seed, [
                {"id": "G3", "summary": "Inspect helper", "locator_hint": {
                    "path": "controller.go", "symbol": "helper"}},
            ], max_files=1)

        self.assertEqual(len(extension), 1)
        self.assertEqual(extension[0]["symbol"], "helper")
        self.assertEqual(extension[0]["path"], "controller.go")
        self.assertEqual(extension[0]["scope_extension"], "EVIDENCE_GAP_SEED_UNIT")
        self.assertEqual(audit["matched_files"], ["controller.go"])

    def test_evidence_gap_symbol_selects_only_the_matching_neighbor_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "controller.go").write_text(
                "package p\nfunc Reconcile() { helper(); audit() }\n", encoding="utf-8")
            (root / "service.go").write_text(
                "package p\nfunc helper() {}\nfunc audit() {}\n", encoding="utf-8")
            snapshot = compute_snapshot(root)
            seed = extract_units(snapshot, {"seed_paths": ("controller.go",)}, max_extension_files=0)

            extension, _ = extract_gap_extension_units(snapshot, ("controller.go",), seed, [
                {"id": "G4", "summary": "Inspect helper", "locator_hint": {
                    "path": "service.go", "symbol": "helper"}},
            ])

        self.assertEqual([(unit["path"], unit["symbol"]) for unit in extension], [("service.go", "helper")])

    def test_evidence_gap_does_not_extend_a_second_hop_from_an_existing_neighbor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "controller.go").write_text(
                "package p\nfunc Reconcile() { ensureService() }\n", encoding="utf-8")
            (root / "service.go").write_text(
                "package p\nfunc ensureService() { buildService() }\n", encoding="utf-8")
            (root / "builder.go").write_text(
                "package p\nfunc buildService() {}\n", encoding="utf-8")
            snapshot = Snapshot("snap-gap-2", str(root), {
                "controller.go": "a" * 64, "service.go": "b" * 64, "builder.go": "c" * 64})
            first_round = extract_units(snapshot, {"seed_paths": ("controller.go",)}, max_extension_files=1)
            self.assertIn("service.go", {unit["path"] for unit in first_round})

            extension, _ = extract_gap_extension_units(snapshot, ("controller.go",), first_round, [
                {"id": "G2", "summary": "Inspect buildService", "locator_hint": {"symbol": "buildService"}}
            ])

        self.assertEqual(extension, [])

    def test_declared_deployment_input_becomes_immutable_candidate_units(self):
        artifacts = [{"id": "INPUT1", "path": "declared-artifacts/input.yaml",
                      "media_type": "application/yaml", "content_digest": "a" * 64,
                      "content": "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: app-config\n"}]
        units = extract_declared_artifact_units(artifacts, "snap")
        self.assertTrue(units)
        self.assertTrue(all(unit["origin_kind"] == "DECLARED_ARTIFACT" and unit["mutable"] is False for unit in units))
        self.assertTrue(any("metadata" in unit.get("node_path", "") for unit in units))


if __name__ == "__main__":
    unittest.main()
