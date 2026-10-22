import json
import unittest

from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.provider import ModelResponse
from tests.test_optimization_contract_flow import observation


PLAN = {'postconditions': [{'id': 'P', 'statement': 'Selected value'}],
        'preserve_constraints': [{'id': 'K', 'statement': 'Keep disabled behavior'}]}
GOOD = {'checks': [{'id': cid, 'status': 'PASS', 'rationale': 'Observed',
        'evidence': [{'observation_id': 'OBS', 'quote': 'selected'}]} for cid in ['P', 'preserve:K']],
        'failure_kind': 'UNKNOWN', 'rationale': 'Observed'}
CONFIG = {'model': 'fixture', 'max_context_tokens': 50000, 'review_max_output_tokens': 1000,
          'max_format_retries': 1, 'provider': 'openai_compatible',
          'review_truncation_retry': 'disable_thinking'}


class Sequence:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def review(provider, config=CONFIG):
    return ModelPublicReviewer(provider, len, config).review('Selected value', PLAN,
        {'status': 'EXECUTION_PASS', 'observations': [observation()]})


class PublicReviewIntegrityRetryTests(unittest.TestCase):
    def test_comparison_feedback_identifies_the_failed_public_spec(self):
        bad = json.loads(json.dumps(GOOD))
        bad['checks'][0]['comparisons'] = [{'artifact_id': 'INPUT', 'input_pointer': '/selected',
            'observation_id': 'OBS', 'output_pointer': '/field/selected'}]
        unknown = json.loads(json.dumps(GOOD))
        for check in unknown['checks']:
            check.update(status='UNKNOWN', evidence=[])
        provider = Sequence([ModelResponse(json.dumps(bad), {}), ModelResponse(json.dumps(unknown), {})])
        ModelPublicReviewer(provider, len, CONFIG).review('Selected value', PLAN,
            {'status': 'EXECUTION_PASS', 'observations': [observation()],
             'declared_artifacts': [{'id': 'INPUT', 'content': 'selected: false\n'}]})
        feedback = provider.requests[1].messages[-1]['content']
        self.assertIn('DIFFERENT', feedback)
        self.assertIn('/field/selected', feedback)
        self.assertIn('INPUT', feedback)

    def test_one_correction_reports_all_independent_quote_errors(self):
        bad = json.loads(json.dumps(GOOD))
        for check in bad['checks']:
            check['evidence'][0]['quote'] = 'fabricated'
        provider = Sequence([ModelResponse(json.dumps(bad), {}), ModelResponse(json.dumps(GOOD), {})])
        result = review(provider)
        feedback = provider.requests[1].messages[-1]['content']
        self.assertIn('P at OBS', feedback)
        self.assertIn('preserve:K at OBS', feedback)
        self.assertEqual(result['decision'], 'PASS')
        self.assertEqual(len(provider.requests), 2)

    def test_reasoning_truncation_uses_existing_correction_with_local_override(self):
        truncated = ModelResponse('', {'finish_reason': 'length', 'usage': {'completion_tokens': 1000,
            'completion_tokens_details': {'reasoning_tokens': 1000}}})
        provider = Sequence([truncated, ModelResponse(json.dumps(GOOD), {}), ModelResponse(json.dumps(GOOD), {})])
        self.assertEqual(review(provider)['decision'], 'PASS')
        self.assertEqual(provider.requests[1].thinking, 'disabled')
        self.assertEqual(provider.requests[1].max_output_tokens, 1000)
        review(provider)
        self.assertIsNone(provider.requests[2].thinking)

    def test_no_retry_for_unknown_transport_or_no_remaining_allowance(self):
        provider = Sequence([TimeoutError('unknown response'), ModelResponse(json.dumps(GOOD), {})])
        with self.assertRaises(TimeoutError):
            review(provider)
        self.assertEqual(len(provider.requests), 1)
        provider = Sequence([ModelResponse('', {'finish_reason': 'length'})])
        with self.assertRaises(ValueError):
            review(provider, {**CONFIG, 'max_format_retries': 0})
        self.assertEqual(len(provider.requests), 1)

    def test_correction_cannot_exceed_shared_call_cap_or_trigger_without_opt_in(self):
        provider = Sequence([ModelResponse('not-json', {})])
        budget = BudgetedProvider(provider, len, max_tokens=100000, max_seconds=30, max_calls=1)
        with self.assertRaises(BudgetExceeded):
            review(budget)
        self.assertEqual(len(provider.requests), 1)
        truncated = ModelResponse('', {'finish_reason': 'length', 'usage': {'completion_tokens': 1000,
            'completion_tokens_details': {'reasoning_tokens': 1000}}})
        provider = Sequence([truncated, ModelResponse(json.dumps(GOOD), {})])
        review(provider, {**CONFIG, 'review_truncation_retry': None})
        self.assertIsNone(provider.requests[1].thinking)
