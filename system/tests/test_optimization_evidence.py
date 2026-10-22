import json
from pathlib import Path
import tempfile
import unittest

from contractedge.contracts.validation import diagnosis_preflight_issues
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.dataset.benchmark import public_execution_capabilities, editable_policy
from contractedge.retrieval.candidates import build_candidate_set, trim_context_bundle
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units, extract_gap_extension_units
from tests.test_benchmark_current import make_public_fixture


def unit(identifier, path, start, end, text='reload groups'):
    return {'id': identifier, 'path': path, 'snapshot_id': 'S0', 'text': text,
            'locator': {'start_line': start, 'end_line': end}}


class EvidenceOptimizationTests(unittest.TestCase):
    def test_repeated_windows_cannot_crowd_out_a_related_distinct_file(self):
        units = [unit('a', 'reload.go', 1, 80), unit('b', 'reload.go', 2, 81),
                 unit('c', 'state.go', 1, 50, 'groups')]
        result = build_candidate_set('reload groups', None, units, [], top_n=2,
                                     max_context_tokens=10000, token_counter=len)
        self.assertEqual(result.selected_ids, ('a', 'c'))
        # Redundancy is a soft penalty, never a ban on a necessary second window.
        only = build_candidate_set('reload groups', None, units[:2], [], top_n=2,
                                   max_context_tokens=10000, token_counter=len)
        self.assertEqual(set(only.selected_ids), {'a', 'b'})

    def test_unit_pool_reaches_next_file_before_exhausting_first_document(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'docs.md').write_text('explanation\n' * 300)
            (root / 'consumer.py').write_text('def consume(value):\n    return value\n')
            result = extract_units(compute_snapshot(root), {'seed_paths': ['docs.md', 'consumer.py']},
                                   max_units=2, max_lines=20)
            self.assertEqual([u['path'] for u in result], ['docs.md', 'consumer.py'])

    def test_exact_gap_reads_require_same_gap_query_and_preserve_source_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'config.yaml').write_text('enabled: true\n')
            (root / 'consumer.py').write_text('def consume(value):\n    return value\n')
            snap = compute_snapshot(root)
            before = dict(snap.file_digests)
            gap = [{'id': 'G1', 'locator_hint': {'path': 'consumer.py', 'symbol': 'consume'}}]
            units, _ = extract_gap_extension_units(snap, ['config.yaml'], [], gap)
            self.assertTrue(units)
            self.assertEqual(units[0]['scope_extension'], 'EVIDENCE_GAP_EXACT_PATH')
            self.assertEqual(units[0]['evidence_gap_refs'], ['G1'])
            self.assertEqual(compute_snapshot(root).file_digests, before)
            for gaps in [
                [{'id': 'G1', 'locator_hint': {'path': '../consumer.py', 'symbol': 'consume'}}],
                [{'id': 'G1', 'locator_hint': {'path': 'consumer.py'}},
                 {'id': 'G2', 'locator_hint': {'symbol': 'consume'}}],
                [{'id': 'G1', 'locator_hint': {'path': 'consumer.py', 'symbol': 'nonexistent'}}],
                [{'id': 'G1', 'locator_hint': {'path': 'consumer.py', 'symbol': 'consum'}}],
            ]:
                result, _ = extract_gap_extension_units(snap, ['config.yaml'], [], gaps)
                self.assertEqual(result, [])

    def test_context_trimming_preserves_reference_pair_and_records_removed_bundle(self):
        units = [unit('input', 'values.yaml', 1, 2, 'routing'),
                 unit('consumer', 'consume.py', 1, 3, 'routing'), unit('extra', 'notes.md', 1, 3, 'notes')]
        candidates = build_candidate_set('routing', None, units,
            [{'source': 'input', 'target': 'consumer', 'edge_kind': 'INFERRED'}],
            top_n=3, max_context_tokens=10000, token_counter=len)
        result = trim_context_bundle(candidates, request_tokens=200, limit=100)
        self.assertEqual(set(result.selected_ids), {'input', 'consumer'})
        self.assertEqual(len(result.edges), 1)
        self.assertEqual(result.drop_reasons['extra'], 'FULL_REQUEST_CONTEXT_BUNDLE')
        self.assertEqual(result.context_audit[0]['removed_ids'], ['extra'])

    def test_independent_relation_error_is_visible_with_a_schema_type_error(self):
        payload = {'schema_version': 'ce.diagnosis_packet.v1_1',
            'expected_relations': [{'source_unit_ids': ['U1', 'U2'], 'target_unit_ids': ['U3'],
                                    'relation_form': 'DIRECT_EDGE'}],
            'evidence_gaps': [{'id': 'G1', 'summary': 'locate', 'locator_hint': {'symbol': None}}]}
        before = json.dumps(payload, sort_keys=True)
        issues = diagnosis_preflight_issues(payload)
        self.assertIn('SCHEMA_INVALID', {i.code for i in issues})
        self.assertIn('MULTI_ENDPOINT_RELATION_REQUIRES_SHARED_JUSTIFICATION', {i.code for i in issues})
        self.assertEqual(json.dumps(payload, sort_keys=True), before)

    def test_capability_projection_uses_only_pinned_public_contract(self):
        with tempfile.TemporaryDirectory() as td:
            public, task = make_public_fixture(Path(td))
            method, audit = method_input_from_task(public, 'CASE-1')
            caps = audit['public_execution_capabilities']
            self.assertEqual(caps['source']['path'], 'environment.json')
            self.assertEqual(caps['scenarios'][0]['argv'], ['template', '/work'])
            self.assertEqual(caps['editable_policy']['allowed_paths'], ['templates/serviceaccount.yaml'])
            self.assertNotIn('PRIVATE_METADATA', json.dumps(caps))
            self.assertNotIn('public_execution_capabilities', method)  # Original input digest is unchanged.

    def test_task_only_dedicated_runner_has_a_pinned_capability_source(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); text = 'public_self_check_runner: tools/public/run.py\n'
            (root / 'task.yaml').write_text(text)
            manifest = {'files': ['task.yaml'], 'file_sha256': {'task.yaml': hashlib.sha256(text.encode()).hexdigest()}}
            caps = public_execution_capabilities(root, {'public_self_check_runner': 'tools/public/run.py',
                'observation_limit': 'No live convergence'}, manifest)
            self.assertEqual(caps['source']['path'], 'task.yaml')
            self.assertEqual(caps['source']['sha256'], manifest['file_sha256']['task.yaml'])
            self.assertEqual(caps['public_runner'], 'tools/public/run.py')
            self.assertEqual(caps['scenarios'], [])

    def test_dedicated_contract_forbidden_candidate_paths_are_not_lost(self):
        policy = editable_policy({}, {'editable_paths': ['pkg/*'], 'forbidden_candidate_paths': ['pkg/oracle/*']})
        self.assertEqual(policy['allowed_paths'], ['pkg/*'])
        self.assertEqual(policy['forbidden_paths'], ['pkg/oracle/*'])
