import unittest

from contractedge.retrieval.candidates import TokenCounterUnavailable, build_candidate_set, extend_once


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
