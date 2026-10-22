import json
from pathlib import Path
import tempfile
import unittest

from contractedge.dataset.benchmark import file_sha, method_input_from_task, prepare_benchmark
from contractedge.evaluation.benchmark import evaluate_benchmark
from contractedge.evaluation.gt_adapter import normalize_gt
from tests.test_benchmark_current import make_public_fixture


class CurrentEvaluationTests(unittest.TestCase):
    def test_current_cohort_rejects_legacy_inputs_repaired_snapshots_and_changed_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'benchmark'
            public, _ = make_public_fixture(root)
            evaluation = root / 'evaluation'; evaluation.mkdir()
            (evaluation / 'gt.json').write_text(json.dumps({'case_id': 'CASE-1', 'primary_class': 'R1'}))
            (evaluation / 'manifest.jsonl').write_text(json.dumps({'case_id': 'CASE-1', 'primary_class': 'R1',
                'gt': 'gt.json', 'gt_sha256': file_sha(evaluation / 'gt.json')}) + '\n')
            entry = json.loads((root / 'RELEASE_ENTRY.json').read_text())
            entry['evaluation_manifest_sha256'] = file_sha(evaluation / 'manifest.jsonl')
            (root / 'RELEASE_ENTRY.json').write_text(json.dumps(entry))
            current, _ = method_input_from_task(public, 'CASE-1')
            trace = {'schema_version': 'ce.run_trace.v1_1', 'run_id': 'current-run', 'case_id': 'CASE-1',
                'input_digest': current['input_digest'], 'snapshot_id': current['initial_snapshot_id'],
                'status': 'EVIDENCE_GAP', 'requirement_spec': None, 'scope_resolution': None,
                'candidate_set': None, 'diagnosis': None, 'changeset': None, 'model_calls': [],
                'errors': [], 'structural_validation': None, 'semantic_review': None,
                'retrieval_rounds': [], 'run_context': None}
            traces = Path(tmp) / 'traces'; traces.mkdir()
            manifest = traces / 'manifest.jsonl'
            manifest.write_text(json.dumps({'case_id': 'CASE-1', 'trace': 'trace.json'}) + '\n')
            for mutation, message in [({'input_digest': '0' * 64}, 'frozen benchmark input'),
                                      ({'snapshot_id': 'repaired-tree'}, 'Broken snapshot'),
                                      ({'requirement_spec': {'raw_text': 'old task text'}}, 'frozen public task')]:
                with self.subTest(mutation=mutation):
                    (traces / 'trace.json').write_text(json.dumps({**trace, **mutation}))
                    with self.assertRaisesRegex(ValueError, message):
                        evaluate_benchmark(root, manifest)
            (traces / 'trace.json').write_text(json.dumps(trace))
            self.assertEqual(evaluate_benchmark(root, manifest)['attempted'], 1)
            prepare_benchmark(root, Path(tmp) / 'projected')
            member = json.loads((Path(tmp) / 'projected/public/manifest.jsonl').read_text())
            projected = json.loads((Path(tmp) / 'projected/public' / member['method_input']).read_text())
            (traces / 'trace.json').write_text(json.dumps({**trace, 'input_digest': projected['input_digest']}))
            self.assertEqual(evaluate_benchmark(root, manifest)['attempted'], 1)

    def test_prose_gt_is_preserved_without_inventing_relation_endpoints(self):
        result = normalize_gt({'case_id': 'CASE', 'expected_relations': ['mounted input -> consumer state'],
                               'oracle_boundary': 'Local production process'}, 'current-benchmark')
        self.assertEqual(result['expected_relations'], [])
        self.assertEqual(result['unstructured_relation_annotations'], ['mounted input -> consumer state'])
        self.assertIsNone(result['precision_eligible'])
        self.assertIsNone(result['recall_eligible'])
        self.assertEqual(result['oracle_boundary'], 'Local production process')

    def test_current_frozen_cohort_keeps_missing_members_in_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_public_fixture(root)
            evaluation = root / 'evaluation'; evaluation.mkdir()
            (evaluation / 'gt.json').write_text(json.dumps({'case_id': 'CASE-1', 'primary_class': 'R1',
                'expected_relations': ['Public declaration to actual output']}))
            (evaluation / 'manifest.jsonl').write_text(json.dumps({'case_id': 'CASE-1', 'primary_class': 'R1',
                'gt': 'gt.json', 'gt_sha256': file_sha(evaluation / 'gt.json')}) + '\n')
            entry = json.loads((root / 'RELEASE_ENTRY.json').read_text())
            entry['evaluation_manifest_sha256'] = file_sha(evaluation / 'manifest.jsonl')
            (root / 'RELEASE_ENTRY.json').write_text(json.dumps(entry))
            result = evaluate_benchmark(root)
            self.assertEqual(result['case_count'], 1)
            self.assertEqual(result['attempted'], 0)
            self.assertEqual(result['cases'][0]['status'], 'NOT_RUN')
            self.assertTrue(result['cases'][0]['denominator_included'])
            self.assertIsNone(result['cases'][0]['precision'])
