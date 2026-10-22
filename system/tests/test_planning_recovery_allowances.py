import json,tempfile,unittest
from pathlib import Path
from tests.test_planning_truncation_recovery import TruncatedPlanningProvider
from tests.test_run_budget import run_fixture
from contractedge.model.provider import ModelResponse


class RecoveredInvalidProvider(TruncatedPlanningProvider):
    def complete_json(self,request):
        response=super().complete_json(request)
        if len(self.requests)==2:
            body=json.loads(response.content);body['units'][0]['name']=None
            return ModelResponse(json.dumps(body),{'finish_reason':'stop'})
        return response


class PlanningRecoveryAllowanceTests(unittest.TestCase):
    def run_case(self,p,**config):
        with tempfile.TemporaryDirectory() as td:
            return run_fixture(Path(td),p,lambda s:len(s.split()),
                planning_truncation_retry='disable_thinking',planning_truncation_separate_allowance=True,
                max_total_tokens=80000,**config)

    def test_known_truncation_recovery_does_not_erase_contract_correction(self):
        p=RecoveredInvalidProvider();trace=self.run_case(p)
        self.assertEqual(trace.status,'READY',trace.errors)
        self.assertEqual([c['stage'] for c in trace.model_calls],
            ['DIAGNOSIS','PLANNING_TRUNCATION_FORMAT_RETRY','DIAGNOSIS_FORMAT_RETRY','SEMANTIC_CHANGESET'])
        self.assertEqual(len(p.requests),4)
        self.assertIn('SCHEMA_INVALID',p.requests[2].messages[-1]['content'])
        self.assertEqual(trace.model_calls[1]['request_metadata']['recovery_allowance'],'SEPARATE_TRUNCATION_ONCE')

    def test_separate_allowance_still_cannot_exceed_global_calls(self):
        p=RecoveredInvalidProvider();trace=self.run_case(p,max_model_calls=2)
        self.assertEqual(trace.status,'BUDGET_EXHAUSTED')
        self.assertEqual(len(p.requests),2)

    def test_repeated_length_response_does_not_start_a_recovery_loop(self):
        p=TruncatedPlanningProvider(repeats=2);trace=self.run_case(p)
        self.assertEqual(trace.status,'BUDGET_EXHAUSTED')
        self.assertEqual(len(p.requests),2)
