import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from contractedge.dataset.benchmark import file_sha, method_input_from_task
from contractedge.contracts.validation import parse_method_input, canonical_digest
from contractedge.orchestration.feedback import make_feedback, feedback_prompt_view
from contractedge.orchestration.repair import revise_plan
from contractedge.orchestration.runner import run_front_half
from tests.test_benchmark_current import make_public_fixture
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_repair_execution import PublicLoopRunner


class FeedbackContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        public, _ = make_public_fixture(root / 'benchmark')
        runner = PublicLoopRunner(public)
        workspace = root / 'workspace'
        self.source = Path(runner.prepare('CASE-1', workspace, root / 'prepare')['source_root'])
        observation = runner.observe(workspace, root / 'observe')
        self.provider = PipelineFixtureProvider()
        self.provider._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(self.source / 'templates/serviceaccount.yaml')}
        self.method, _ = method_input_from_task(public, 'CASE-1')
        self.config = {'model': {'model': 'fixture'}, 'max_context_tokens': 100000,
                       'max_output_tokens': 2048, 'max_total_tokens': 500000}
        self.trace = run_front_half(self.method, self.source.parent, self.provider,
            self.config, len, public_observations=observation['observations']).to_wire()
        self.assertEqual(self.trace['status'], 'READY', self.trace['errors'])
        large = {**observation['observations'][0], 'id': 'OBS-LARGE', 'excerpt': 'x' * 618000}
        large['content_digest'] = hashlib.sha256(large['excerpt'].encode()).hexdigest()
        self.feedback = make_feedback(case_id='CASE-1', run_id=self.trace['run_id'],
            input_digest=self.trace['input_digest'], round_id=0,
            from_snapshot=self.trace['snapshot_id'], to_snapshot=self.trace['snapshot_id'],
            patch_digest='b' * 64, execution_status='EXECUTION_PASS',
            observations=[*observation['observations'], large], plan=self.trace['changeset'],
            review={'decision': 'FAIL', 'checks': [{'id': 'P1', 'status': 'FAIL'}], 'failure_kind': 'DIAGNOSIS'})
        self.observations = observation['observations']

    def test_changeset_revision_keeps_obligations_without_repeating_large_raw_output(self):
        seen = []
        original = self.provider.complete_json
        def complete(request):
            seen.append(request)
            return original(request)
        self.provider.complete_json = complete
        result = revise_plan(self.trace, parse_method_input(self.method), self.source, self.feedback,
                             self.provider, len, self.config)
        self.assertEqual(result['status'], 'READY')
        serialized = json.dumps(seen[0].messages)
        self.assertLess(len(serialized), 100000)
        self.assertIn('OBS-LARGE', serialized)
        self.assertIn('pending_obligations', serialized)

    def test_prompt_projection_preserves_every_non_observation_field_and_original_feedback(self):
        before = json.dumps(self.feedback, sort_keys=True)
        projected = feedback_prompt_view(self.feedback)
        for key, value in self.feedback.items():
            if key != 'public_observations':
                self.assertEqual(projected[key], value)
        self.assertEqual(projected['full_feedback_digest'], canonical_digest(self.feedback))
        self.assertEqual(projected['public_observation_refs'][-1]['content_digest'],
                         self.feedback['public_observations'][-1]['content_digest'])
        self.assertNotIn('public_observations', projected)
        self.assertEqual(before, json.dumps(self.feedback, sort_keys=True))

    def test_rediagnosis_retains_candidates_and_uses_fresh_selected_quotes(self):
        result = run_front_half(self.method, self.source.parent, self.provider,
            {**self.config, 'run_id': self.trace['run_id']}, len,
            execution_feedback=self.feedback, public_observations=self.observations).to_wire()
        self.assertEqual(result['status'], 'READY', result['errors'])
        self.assertTrue(result['candidate_set']['selected_ids'])
        self.assertEqual(result['run_context']['round_id'], 1)
