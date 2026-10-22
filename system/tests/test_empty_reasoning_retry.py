from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from contractedge.execution.topomen import build_execution_request
from contractedge.model.provider import ModelRequest, OpenAICompatibleProvider
from contractedge.source.snapshot import compute_snapshot
from tests.test_repair_execution import bound_plan


class EmptyReasoningRetryTests(unittest.TestCase):
    def request(self, *, content='', completion=1000, reasoning=1000, source_current=True, enabled=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'templates').mkdir()
            (root / 'templates/serviceaccount.yaml').write_text('name: default\n')
            prior = {'model_response': content, 'source_snapshot': compute_snapshot(root).snapshot_id if source_current else 'old',
                'response_metadata': {'finish_reason': 'length', 'usage': {'completion_tokens': completion,
                    'completion_tokens_details': {'reasoning_tokens': reasoning}}}}
            config = {'model': 'test', 'provider': 'openai_compatible', 'max_context_tokens': 30000,
                      'execution_max_output_tokens': 1000}
            if enabled:
                config['execution_empty_reasoning_retry'] = 'disable_thinking'
            return build_execution_request(bound_plan(root), root, {'allowed_paths': ['templates/**']},
                len, config, requirement='Selected name', previous_attempt=prior)

    def test_only_empty_entirely_reasoning_length_retry_uses_explicit_fallback(self):
        request = self.request()
        self.assertEqual(getattr(request, 'thinking', None), 'disabled')
        self.assertEqual(request.max_output_tokens, 1000)
        self.assertIn('execution_retry_policy', request.metadata)
        for values in [{'content': '{"edits":['}, {'reasoning': 999}, {'source_current': False}, {'enabled': False}]:
            with self.subTest(values=values):
                self.assertIsNone(getattr(self.request(**values), 'thinking', None))

    def test_transport_override_is_local_and_does_not_leak_to_next_stage(self):
        captured = []
        def send(request, **kwargs):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}],
                                         'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}).encode())
        provider = OpenAICompatibleProvider('https://test.invalid/v1', 'CE_TEST_KEY', thinking='enabled', reasoning_effort='low')
        request = self.request()
        with patch.dict(os.environ, {'CE_TEST_KEY': 'test-only'}), patch('urllib.request.urlopen', side_effect=send):
            provider.complete_json(request)
            provider.complete_json(replace(request, thinking=None, metadata={}))
        self.assertEqual(captured[0]['thinking'], {'type': 'disabled'})
        self.assertNotIn('reasoning_effort', captured[0])
        self.assertEqual(captured[1]['thinking'], {'type': 'enabled'})
        self.assertEqual(captured[1]['reasoning_effort'], 'low')
        self.assertEqual(provider.thinking, 'enabled')

    def test_invalid_override_is_rejected_before_http(self):
        request = replace(self.request(), thinking='invalid')
        provider = OpenAICompatibleProvider('https://test.invalid/v1', 'CE_TEST_KEY')
        with patch.dict(os.environ, {'CE_TEST_KEY': 'test-only'}), patch('urllib.request.urlopen') as send:
            with self.assertRaises(ValueError):
                provider.complete_json(request)
            send.assert_not_called()
