import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from contractedge.contracts.validation import canonical_digest
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.provider import ModelRequest, ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_method_input
from tests.test_orchestration import PipelineFixtureProvider


def run_fixture(root, provider, counter, **overrides):
    (root / 'templates').mkdir()
    source = root / 'templates/serviceaccount.yaml'
    source.write_text('apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n')
    provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(source.read_bytes()).hexdigest()}
    value = valid_method_input()
    value['scope_hints'] = [{'kind': 'path', 'value': 'templates/serviceaccount.yaml',
        'raw_text': 'templates/serviceaccount.yaml', 'origin_ref': 'public.scope'}]
    value['initial_snapshot_id'] = compute_snapshot(root).snapshot_id
    excerpt = 'The rendered name is default instead of selected.'
    value['public_evidence'] = [{'id': 'OBS1', 'kind': 'PUBLIC_OBSERVATION',
        'snapshot_id': value['initial_snapshot_id'], 'locator': {'path': 'observation.txt'},
        'content_digest': hashlib.sha256(excerpt.encode()).hexdigest(), 'excerpt': excerpt, 'origin': 'public fixture'}]
    value.pop('input_digest'); value['input_digest'] = canonical_digest(value)
    return run_front_half(value, root, provider, {'model': {'model': 'fixture'},
        'max_context_tokens': 12000, 'max_output_tokens': 1024,
        'max_total_tokens': 80000, **overrides}, counter)


class RunBudgetTests(unittest.TestCase):
    def test_front_half_also_enforces_model_call_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = run_fixture(Path(tmp), PipelineFixtureProvider(), lambda text: len(text.split()), max_model_calls=1)
        self.assertEqual(trace.status, 'BUDGET_EXHAUSTED', trace.errors)
        self.assertEqual([call['stage'] for call in trace.model_calls], ['DIAGNOSIS'])

    def test_oversized_changeset_input_is_stopped_before_provider_call(self):
        def count(text):
            return 13000 if '"diagnosis_packet"' in text else len(text.split())
        with tempfile.TemporaryDirectory() as tmp:
            trace = run_fixture(Path(tmp), PipelineFixtureProvider(), count)
        self.assertEqual(trace.status, 'BUDGET_EXHAUSTED', trace.errors)
        self.assertEqual([call['stage'] for call in trace.model_calls], ['DIAGNOSIS'])

    def test_last_planning_response_after_deadline_cannot_be_ready(self):
        clock = [0.0]
        class SlowFinalResponse(PipelineFixtureProvider):
            timeout_seconds = 900
            def complete_json(self, request):
                response = super().complete_json(request)
                if not request.prompt_version.startswith('diagnosis.'):
                    clock[0] = 100.0
                return response
        with tempfile.TemporaryDirectory() as tmp, patch('contractedge.orchestration.runner.time.monotonic', side_effect=lambda: clock[0]):
            trace = run_fixture(Path(tmp), SlowFinalResponse(), lambda t: len(t.split()), max_elapsed_seconds=1)
        self.assertEqual(trace.status, 'BUDGET_EXHAUSTED', trace.errors)

    def test_shared_budget_limits_provider_timeout_to_remaining_deadline(self):
        class HttpPort:
            timeout_seconds = 900
            seen_timeout = None
            def complete_json(self, request):
                self.seen_timeout = self.timeout_seconds
                return ModelResponse('{}', {})
        provider = HttpPort()
        budget = BudgetedProvider(provider, len, max_tokens=100, max_seconds=10)
        budget.started -= 8
        budget.complete_json(ModelRequest('fixture', 'test', ({'role': 'user', 'content': 'x'},), 2))
        self.assertLessEqual(provider.seen_timeout, 2)
        self.assertGreater(provider.seen_timeout, 0)
        self.assertEqual(provider.timeout_seconds, 900)
