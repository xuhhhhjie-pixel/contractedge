from dataclasses import replace
import json
import unittest

from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import parse_method_input
from contractedge.diagnosis.prompt import build_diagnosis_request
from contractedge.retrieval.candidates import build_candidate_set
from contractedge.requirement.normalize import normalize_requirement
from tests.test_contracts import valid_method_input, valid_diagnosis


class CompactNativePromptTests(unittest.TestCase):
    def test_in_prompt_schema_also_keeps_large_public_observation_only_once(self):
        method=parse_method_input(valid_method_input());evidence=valid_diagnosis()['evidence_refs'][0]
        method=replace(method,public_evidence=(evidence,))
        candidates=build_candidate_set(method.requirement_text,None,[],[],top_n=12,max_context_tokens=12000,token_counter=len)
        context=RunContext('run',method.case_id,0,method.initial_snapshot_id,None,method.input_digest,
            model_config={'model':'fixture','schema_in_prompt':True})
        request=build_diagnosis_request(method,normalize_requirement({'requirement_text':method.requirement_text}),candidates,context)
        public=json.loads(request.messages[1]['content'])
        self.assertIn('output_schema',public)
        self.assertNotIn('excerpt',public['method_input']['public_evidence'][0])
        self.assertEqual(set(public['method_input']['public_evidence'][0]),{'id'})
        self.assertEqual(public['evidence_catalog'][0]['excerpt'],evidence['excerpt'])

    def test_native_schema_and_public_observation_are_sent_once(self):
        method = parse_method_input(valid_method_input())
        evidence = valid_diagnosis()['evidence_refs'][0]
        method = replace(method, public_evidence=(evidence,))
        candidates = build_candidate_set(method.requirement_text, None, [], [], top_n=12,
            max_context_tokens=12000, token_counter=lambda text: len(text.split()))
        context = RunContext('run', method.case_id, 0, method.initial_snapshot_id, None,
            method.input_digest, model_config={'model': 'native', 'schema_in_prompt': False})
        request = build_diagnosis_request(method, normalize_requirement({'requirement_text': method.requirement_text}),
                                          candidates, context)
        public = json.loads(request.messages[1]['content'])
        self.assertNotIn('output_schema', public)
        self.assertIsNotNone(request.output_schema)
        self.assertNotIn('excerpt', public['method_input']['public_evidence'][0])
        self.assertEqual(public['evidence_catalog'][0]['excerpt'], evidence['excerpt'])

    def test_explicit_public_anchor_survives_more_keyword_matches_elsewhere(self):
        def unit(cid, text, priority=0):
            return {'id': cid, 'path': cid + '.go', 'snapshot_id': 'S0', 'locator': {'start_line': 1},
                    'text': text, 'anchor_priority': priority}
        candidates = build_candidate_set('global node selector affinity tolerations', None,
            [unit('unrelated', 'global node selector affinity tolerations'),
             unit('public-editable-target', 'missing propagation', 2)], [],
            top_n=1, max_context_tokens=100, token_counter=lambda text: len(text.split()))
        self.assertEqual(candidates.selected_ids, ('public-editable-target',))
