import json
import unittest

from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.provider import ModelResponse


class ReviewSequence:
    def __init__(self, correct_second=True):
        self.requests = []
        self.correct_second = correct_second
        self.responses = []

    def complete_json(self, request):
        self.requests.append(request)
        quote = 'actual output' if len(self.requests) > 1 and self.correct_second else 'invented quotation'
        response = json.dumps({'checks': [{'id': 'P1', 'status': 'PASS', 'rationale': 'output evidence',
            'evidence': [{'observation_id': 'OBS1', 'quote': quote}]}],
            'failure_kind': 'UNKNOWN', 'rationale': 'Public evidence only'})
        self.responses.append(response)
        return ModelResponse(response, {'provider': 'review-retry-fixture'})


class PublicReviewRetryTests(unittest.TestCase):
    def review(self, provider, **config):
        return ModelPublicReviewer(provider, len, {'model': 'fixture', 'max_context_tokens': 20000,
            'review_max_output_tokens': 256, 'max_format_retries': 1, **config}).review('Required behavior',
                {'postconditions': [{'id': 'P1', 'statement': 'actual output exists'}]},
                {'status': 'EXECUTION_PASS', 'observations': [{'id': 'OBS1', 'evidence_kind': 'PUBLIC_STRUCTURE', 'excerpt': 'actual output'}]})

    def test_invalid_quote_retries_exact_draft_and_error_without_rewriting_evidence(self):
        provider = ReviewSequence()
        result = self.review(provider)
        self.assertEqual(result['decision'], 'PASS')
        self.assertEqual(len(provider.requests), 2)
        retry = provider.requests[1]
        self.assertEqual(retry.messages[-2]['role'], 'assistant')
        self.assertEqual(retry.messages[-2]['content'], provider.responses[0])
        self.assertIn('fabricated', retry.messages[-1]['content'])
        self.assertEqual(retry.messages[:2], provider.requests[0].messages)
        self.assertEqual(retry.output_schema, provider.requests[0].output_schema)

    def test_repeated_invalid_quote_remains_rejected_after_one_retry(self):
        provider = ReviewSequence(correct_second=False)
        with self.assertRaisesRegex(ValueError, 'fabricated'):
            self.review(provider)
        self.assertEqual(len(provider.requests), 2)

    def test_correction_cannot_reset_the_shared_call_budget(self):
        actual = ReviewSequence()
        bounded = BudgetedProvider(actual, len, max_tokens=100000, max_seconds=30, max_calls=1)
        with self.assertRaises(BudgetExceeded):
            self.review(bounded)
        self.assertEqual(len(actual.requests), 1)
        self.assertEqual(bounded.summary()['model_calls'], 1)

    def test_context_overflow_stops_correction_before_an_extra_call(self):
        provider = ReviewSequence()
        # Measure the first request without allowing correction.
        with self.assertRaises(ValueError):
            self.review(provider, max_format_retries=0)
        initial = provider.requests[0]
        bound = sum(len(m['content']) for m in initial.messages) + initial.max_output_tokens
        provider = ReviewSequence()
        result = self.review(provider, max_context_tokens=bound)
        self.assertEqual(result['decision'], 'UNKNOWN')
        self.assertEqual(result['method'], 'CONTEXT_LIMIT')
        self.assertEqual(len(provider.requests), 1)
