import hashlib
import json
import unittest

from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.provider import FixtureProvider
from contractedge.orchestration.feedback import make_feedback, review_plan_with_retained


class OpenObligationReviewTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'postconditions': [{'id': 'P1', 'statement': 'Owner reference exists', 'requirement_refs': ['O1']}],
            'open_obligations': [{'id': 'A2-OPEN', 'outcome_id': 'O1', 'obligation_refs': ['A2'],
                                 'statement': 'Child keeps its explicit namespace', 'reason': 'No supported initial repair'}]}
        text = 'ownerReferences: parent\nnamespace: selected\n'
        self.observation = {'evidence_kind': 'PUBLIC_STRUCTURE', 'id': 'OBS1', 'kind': 'PUBLIC_OBSERVATION', 'snapshot_id': 'S1',
            'locator': {'path': 'output.txt'}, 'excerpt': text,
            'content_digest': hashlib.sha256(text.encode()).hexdigest(), 'origin': 'fresh public execution'}

    def check(self, identifier, quote, status='PASS'):
        return {'id': identifier, 'status': status, 'rationale': 'Fresh output',
                'evidence': [{'observation_id': 'OBS1', 'quote': quote}] if quote else []}

    def review(self, checks):
        class Recording(FixtureProvider):
            def complete_json(self, request):
                self.request = request
                return super().complete_json(request)
        provider = Recording({'checks': checks, 'failure_kind': 'UNKNOWN', 'rationale': 'Public output only'})
        result = ModelPublicReviewer(provider, len, {'model': 'fixture', 'max_context_tokens': 20000}).review(
            'Full original requirement', self.plan, {'status': 'EXECUTION_PASS', 'observations': [self.observation]})
        return result, provider

    def feedback(self, review, plan=None):
        return make_feedback(case_id='CASE', run_id='run', input_digest='a' * 64, round_id=0,
            from_snapshot='S0', to_snapshot='S1', patch_digest='b' * 64,
            execution_status='EXECUTION_PASS', observations=[self.observation],
            plan=plan or self.plan, review=review)

    def test_open_claim_is_explicitly_reviewed_and_requires_its_own_fresh_quote(self):
        review, provider = self.review([self.check('P1', 'ownerReferences: parent'),
                                       self.check('open:A2-OPEN', 'namespace: selected')])
        payload = json.loads(provider.request.messages[1]['content'])
        self.assertIn('open:A2-OPEN', [c['id'] for c in payload['checks']])
        self.assertEqual(review['decision'], 'PASS')
        result = self.feedback(review)
        self.assertEqual(result['semantic_outcome'], 'PUBLIC_REVIEW_PASS')
        self.assertEqual(result['pending_obligations'], [])
        self.assertTrue(any(c['historical_obligation_refs'] == ['A2'] for c in result['retained_constraints']))
        self.assertEqual(len(self.plan['open_obligations']), 1)  # The historical plan is not rewritten.

    def test_unknown_open_claim_stays_pending(self):
        review, _ = self.review([self.check('P1', 'ownerReferences: parent'),
                                 self.check('open:A2-OPEN', None, 'UNKNOWN')])
        self.assertEqual(review['decision'], 'UNKNOWN')
        result = self.feedback(review)
        self.assertEqual([p['id'] for p in result['pending_obligations']], ['open:A2-OPEN'])

    def test_omitted_open_check_is_not_inferred_from_another_pass(self):
        with self.assertRaisesRegex(ValueError, 'every check'):
            self.review([self.check('P1', 'ownerReferences: parent')])

    def test_feedback_cannot_accept_a_fabricated_open_claim_even_with_pass_decision(self):
        result = self.feedback({'decision': 'PASS', 'checks': [self.check('P1', 'ownerReferences: parent'),
            self.check('open:A2-OPEN', 'namespace: invented')]})
        self.assertEqual(result['semantic_outcome'], 'UNKNOWN')
        self.assertTrue(result['pending_obligations'])
        self.assertFalse(any(c['historical_obligation_refs'] == ['A2'] for c in result['retained_constraints']))

    def test_new_edit_must_preserve_a_previously_verified_open_claim(self):
        review, _ = self.review([self.check('P1', 'ownerReferences: parent'),
                                 self.check('open:A2-OPEN', 'namespace: selected')])
        accepted = self.feedback(review)
        replanned = review_plan_with_retained({'postconditions': [{'id': 'P2', 'statement': 'Other repair'}]}, accepted)
        result = self.feedback({'decision': 'PASS', 'checks': [self.check('P2', 'ownerReferences: parent')]}, replanned)
        self.assertEqual(result['semantic_outcome'], 'UNKNOWN')
        self.assertTrue(any(p['statement'] == 'Child keeps its explicit namespace' for p in result['pending_obligations']))
