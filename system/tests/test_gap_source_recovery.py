from pathlib import Path
import hashlib
import tempfile
import unittest

from contractedge.retrieval.candidates import build_candidate_set, extend_once
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_gap_extension_units, extract_units


class GapSourceRecoveryTests(unittest.TestCase):
    def test_gap_can_read_a_bounded_tail_window_of_a_long_seed_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = 'package main\nfunc main() {\n' + '  noop()\n' * 180 + '  reloadConfig()\n}\n'
            (root / 'main.go').write_text(text)
            snapshot = compute_snapshot(root)
            initial = extract_units(snapshot, {'seed_paths': ['main.go']}, max_lines=20)
            self.assertNotIn('reloadConfig()', initial[0]['text'])
            result, _ = extract_gap_extension_units(snapshot, ['main.go'], initial,
                [{'id': 'G1', 'locator_hint': {'path': 'main.go', 'search_term': 'reloadConfig'}}], max_lines=20, max_declaration_lines=20)
            matching = [u for u in result if 'reloadConfig()' in u['text']]
            self.assertTrue(matching)
            unit = matching[0]
            start, end = unit['locator']['start_line'], unit['locator']['end_line']
            self.assertLessEqual(end - start + 1, 20)
            self.assertEqual(unit['text'], ''.join(text.splitlines(keepends=True)[start - 1:end]))
            self.assertEqual(unit['content_digest'], hashlib.sha256(unit['text'].encode()).hexdigest())
            self.assertEqual(unit['parser_kind'], 'BOUNDED_TEXT_FALLBACK')

    def test_precise_seed_file_search_recovers_variable_use_without_a_declared_symbol_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'docs.md').write_text('Read the configPath documentation.\n')
            (root / 'runner.go').write_text('package runtime\nfunc runner() string {\n configPath := "config"\n return configPath\n}\n')
            snapshot = compute_snapshot(root)
            paths = ['docs.md', 'runner.go']
            initial = extract_units(snapshot, {'seed_paths': paths}, max_units=1)
            self.assertEqual(initial[0]['path'], 'docs.md')
            gap = [{'id': 'G1', 'locator_hint': {'path': 'runner.go', 'symbol': 'configPath'}}]
            result, _ = extract_gap_extension_units(snapshot, paths, initial, gap)
            self.assertTrue(result)
            self.assertTrue(all(u['path'] == 'runner.go' for u in result))
            self.assertTrue(all(u['scope_extension'] == 'EVIDENCE_GAP_SEED_UNIT' for u in result))
            # An exact path + same-gap symbol is a bounded read-only locator,
            # explicitly distinct from a direct-reference edge or edit grant.
            located, _ = extract_gap_extension_units(snapshot, ['docs.md'], initial, gap)
            self.assertEqual(located[0]['scope_extension'], 'EVIDENCE_GAP_EXACT_PATH')
            rejected, _ = extract_gap_extension_units(snapshot, ['docs.md'], initial,
                [{'id': 'G2', 'locator_hint': {'path': 'runner.go'}}])
            self.assertEqual(rejected, [])

    def test_new_gap_source_cannot_be_crowded_out_by_original_documentation_matches(self):
        docs = {'id': 'DOC', 'path': 'a-doc.md', 'text': 'pkg/runner.go configPath',
                'snapshot_id': 'S0', 'locator': {'start_line': 1, 'end_line': 1}}
        source = {'id': 'SRC', 'path': 'pkg/runner.go', 'text': 'configPath',
                  'snapshot_id': 'S0', 'locator': {'start_line': 1, 'end_line': 1},
                  'scope_extension': 'EVIDENCE_GAP_SEED_UNIT', 'evidence_gap_refs': ['G1']}
        candidates = build_candidate_set('configPath', None, [docs], [],
            top_n=1, max_context_tokens=1000, token_counter=len)
        result = extend_once(candidates, [{'id': 'G1', 'locator_hint': {
            'path': 'pkg/runner.go', 'search_term': 'configPath'}}], [source],
            max_context_tokens=1000, token_counter=len)
        self.assertEqual(result.selected_ids, ('SRC',))
