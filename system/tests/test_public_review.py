import unittest
import json
from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.provider import FixtureProvider


class PublicReviewTests(unittest.TestCase):
    def test_json_object_provider_is_given_the_review_schema_in_its_prompt(self):
        owner = self
        class SchemaAwareProvider(FixtureProvider):
            def complete_json(self, request):
                payload = json.loads(request.messages[1]['content'])
                owner.assertEqual(payload.get('output_schema'), request.output_schema)
                owner.assertEqual(payload.get('declared_artifacts'), [{'id':'INPUT1','content':'enabled: true'}])
                return super().complete_json(request)
        reviewer = self.reviewer('actual output')
        reviewer.provider = SchemaAwareProvider(reviewer.provider.payload)
        reviewer.review('Required behavior', {'postconditions':[{'id':'P1','statement':'required field'}]},
                        {'status':'EXECUTION_PASS','observations':[{'id':'OBS1','evidence_kind':'PUBLIC_STRUCTURE','excerpt':'actual output'}],
                         'declared_artifacts':[{'id':'INPUT1','content':'enabled: true'}]})

    def reviewer(self, quote):
        return ModelPublicReviewer(FixtureProvider({'checks': [{'id': 'P1', 'status': 'PASS',
            'rationale': 'the public observation contains the required field',
            'evidence': [{'observation_id': 'OBS1', 'quote': quote}]}],
            'failure_kind': 'UNKNOWN', 'rationale': 'public review only'}),
            lambda text: len(text.split()), {'model': 'fixture', 'max_context_tokens': 12000,
                                            'review_max_output_tokens': 256})

    def test_fabricated_public_quote_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'fabricated'):
            self.reviewer('imagined output').review('Required behavior',
                {'postconditions': [{'id': 'P1', 'statement': 'required field'}]},
                {'status': 'EXECUTION_PASS', 'observations': [{'id': 'OBS1', 'excerpt': 'actual output'}]})

    def test_passing_public_check_cannot_close_a_distinct_open_obligation(self):
        with self.assertRaisesRegex(ValueError, 'every check'):
            self.reviewer('actual output').review('Required behavior',
                {'postconditions': [{'id': 'P1', 'statement': 'required field'}],
                 'open_obligations': [{'id': 'OPEN1', 'outcome_id': 'O2', 'statement': 'another requirement'}]},
                {'status': 'EXECUTION_PASS', 'observations': [{'id': 'OBS1', 'excerpt': 'actual output'}]})
