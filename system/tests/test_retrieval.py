import unittest

from contractedge.retrieval.candidates import (TokenCounterUnavailable, build_candidate_set,
                                                extend_once, trim_context_bundle)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.units = [
            {"id": "a", "path": "a.go", "locator": {"start_line": 1, "end_line": 3},
             "snapshot_id": "s1", "content_digest": "a" * 64, "text": "func renderServiceAccountName() { serviceAccountName }"},
            {"id": "b", "path": "b.go", "locator": {"start_line": 1, "end_line": 4},
             "snapshot_id": "s1", "content_digest": "b" * 64, "text": "update unrelated cache"},
        ]

    def test_ranking_is_deterministic_deduplicated_and_budgeted(self):
        result = build_candidate_set("render serviceAccountName", "rendered serviceAccountName", self.units,
                                     [{"source": "a", "target": "b", "edge_kind": "SYNTACTIC"}],
                                     top_n=2, max_context_tokens=100, token_counter=lambda text: len(text.split()))
        self.assertEqual(result.selected_ids[0], "a")
        self.assertEqual(result.edges[0]["edge_kind"], "SYNTACTIC")
        self.assertEqual(result.selected_count, len(result.selected_ids))

    def test_no_silent_tokenizer_default(self):
        with self.assertRaises(TokenCounterUnavailable):
            build_candidate_set("x", None, self.units, [], top_n=12, max_context_tokens=100, token_counter=None)

    def test_evidence_extension_is_only_allowed_once(self):
        candidate_set = build_candidate_set("x", None, self.units[:1], [], top_n=1, max_context_tokens=100,
                                           token_counter=lambda text: len(text.split()))
        gap = {"id": "G1", "summary": "resolve helper", "locator_hint": {"symbol": "render"}}
        result = extend_once(candidate_set, [gap], [self.units[1]], max_context_tokens=100,
                             token_counter=lambda text: len(text.split()))
        self.assertTrue(result.extension_used)
        with self.assertRaises(ValueError):
            extend_once(result, [gap], [self.units[0]], max_context_tokens=100,
                        token_counter=lambda text: len(text.split()))

    def test_planning_can_use_one_explicit_reextension(self):
        candidate_set = build_candidate_set("x", None, self.units[:1], [], top_n=2, max_context_tokens=100,
                                           token_counter=lambda text: len(text.split()))
        gap = {"id": "G1", "summary": "resolve helper", "locator_hint": {"symbol": "render"}}
        first = extend_once(candidate_set, [gap], [self.units[1]], max_context_tokens=100,
                            token_counter=lambda text: len(text.split()))
        second = extend_once(first, [gap], [self.units[0]], max_context_tokens=100,
                             token_counter=lambda text: len(text.split()), allow_reextension=True)
        assert second.extension_used

    def test_evidence_extension_allowance_is_profile_bounded(self):
        from contractedge.orchestration.state import PolicyLedger
        assert PolicyLedger({}).limit("evidence_extension") == 1
        assert PolicyLedger({"max_evidence_extensions": 2}).limit("evidence_extension") == 2
        assert PolicyLedger({"max_evidence_extensions": 99}).limit("evidence_extension") == 2

    def test_public_boundary_metadata_does_not_reserve_slots_without_gap_delivery(self):
        units = [
            {"id": f"scope-{i}", "path": f"pkg/stage/{i}.go",
             "locator": {"start_line": 1, "end_line": 2}, "snapshot_id": "s1",
             "content_digest": "a" * 64, "text": "unrelated production handoff",
             "boundary_scope_anchor": True}
            for i in range(4)
        ] + [{"id": "lexical", "path": "other.go", "locator": {"start_line": 1, "end_line": 2},
               "snapshot_id": "s1", "content_digest": "b" * 64,
               "text": "timeout binding options"}]
        result = build_candidate_set("timeout binding options", None, units, [], top_n=4,
                                     max_context_tokens=100,
                                     token_counter=lambda text: len(text.split()))
        self.assertIn("lexical", result.selected_ids)
        self.assertEqual(len(result.selected_ids), 4)
        self.assertEqual(result.required_endpoint_ids, ())

    def test_boundary_metadata_does_not_prefer_unrepresented_paths_over_requirement(self):
        units = [
            {"id": "boundary-a", "path": "a.go", "locator": {"start_line": 1, "end_line": 2},
             "snapshot_id": "s1", "content_digest": "a" * 64, "text": "production handoff",
             "boundary_scope_anchor": True},
            {"id": "boundary-b", "path": "b.go", "locator": {"start_line": 1, "end_line": 2},
             "snapshot_id": "s1", "content_digest": "b" * 64, "text": "production handoff",
             "boundary_scope_anchor": True},
            {"id": "lexical", "path": "metrics.go", "locator": {"start_line": 1, "end_line": 2},
             "snapshot_id": "s1", "content_digest": "c" * 64,
             "text": "timeout binding options timeout binding options"},
        ]
        result = build_candidate_set("timeout binding options", None, units, [], top_n=2,
                                     max_context_tokens=100,
                                     token_counter=lambda text: len(text.split()))
        self.assertEqual(result.selected_ids[0], "lexical")

    def test_critical_boundary_metadata_does_not_protect_optional_context(self):
        units = [
            {"id": "critical", "path": "pkg/injector/sidecar.go",
             "locator": {"start_line": 1, "end_line": 2}, "snapshot_id": "s1",
             "content_digest": "a" * 64, "text": "injector argv handoff",
             "boundary_scope_anchor": True, "boundary_scope_critical": True},
            {"id": "optional", "path": "metrics.go", "locator": {"start_line": 1, "end_line": 2},
             "snapshot_id": "s1", "content_digest": "b" * 64,
             "text": "optional context", "required_for_gap_delivery": True},
        ]
        result = build_candidate_set("injector", None, units, [], top_n=2,
                                     max_context_tokens=100,
                                     token_counter=lambda text: len(text.split()))
        trimmed = trim_context_bundle(result, request_tokens=101, limit=1)
        self.assertNotIn("critical", trimmed.selected_ids)
        self.assertIn("optional", trimmed.selected_ids)

    def test_connected_boundary_metadata_does_not_block_context_trim(self):
        units = [
            {"id": "injector", "path": "pkg/injector/sidecar.go",
             "locator": {"start_line": 1, "end_line": 2}, "snapshot_id": "s1",
             "content_digest": "a" * 64, "text": "injector argv handoff",
             "boundary_scope_anchor": True, "boundary_scope_critical": True},
            {"id": "runtime", "path": "pkg/runtime/config.go",
             "locator": {"start_line": 1, "end_line": 2}, "snapshot_id": "s1",
             "content_digest": "b" * 64, "text": "runtime config handoff",
             "boundary_scope_anchor": True},
        ]
        result = build_candidate_set("handoff", None, units,
                                     [{"source": "injector", "target": "runtime",
                                       "edge_kind": "SYNTACTIC"}], top_n=2,
                                     max_context_tokens=100,
                                     token_counter=lambda text: len(text.split()))
        trimmed = trim_context_bundle(result, request_tokens=101, limit=1)
        self.assertEqual(len(trimmed.selected_ids), 1)
        self.assertTrue(trimmed.context_audit[-1]["removed_ids"])


if __name__ == "__main__":
    unittest.main()

class GapDeclaredSymbolTests(unittest.TestCase):
    def test_exact_declared_symbol_is_retrieved_when_seed_budget_omitted_its_body(self):
        from pathlib import Path
        import tempfile
        from contractedge.source.units import extract_gap_extension_units, parse_source_units
        from contractedge.source.snapshot import compute_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'views.py').write_text('def view():\n    return 1\n')
            (root/'hierarchies.py').write_text('def adopt(child):\n    return child\n')
            snapshot=compute_snapshot(root)
            existing=parse_source_units('views.py',(root/'views.py').read_text(),snapshot.snapshot_id)
            units,audit=extract_gap_extension_units(snapshot,['views.py','hierarchies.py'],existing,
                [{'id':'G1','locator_hint':{'path':'views.py','symbol':'adopt','search_term':'def adopt'}}])
            self.assertTrue(any(u.get('symbol')=='adopt' for u in units),audit)
