from pathlib import Path
import tempfile
import unittest

from contractedge.model.provider import ModelResponse
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_run_budget import run_fixture


class TruncatedPlanningProvider(PipelineFixtureProvider):
    def __init__(self, repeats=1, reasoning=True):
        super().__init__(); self.requests = []; self.repeats = repeats; self.reasoning = reasoning

    def complete_json(self, request):
        self.requests.append(request)
        if request.prompt_version.startswith('diagnosis.') and self.repeats:
            self.repeats -= 1
            return ModelResponse('{"units":[', {'finish_reason': 'length', 'usage': {
                'completion_tokens': request.max_output_tokens,
                'completion_tokens_details': {'reasoning_tokens': int(request.max_output_tokens * .85) if self.reasoning else 0}}})
        return super().complete_json(request)


class PlanningTruncationRecoveryTests(unittest.TestCase):
    def run_case(self, provider, **overrides):
        with tempfile.TemporaryDirectory() as td:
            return run_fixture(Path(td), provider, lambda text: len(text.split()),
                planning_truncation_retry='disable_thinking', **overrides)

    def test_existing_format_correction_recovers_partial_json_and_override_does_not_leak(self):
        provider = TruncatedPlanningProvider()
        trace = self.run_case(provider)
        self.assertEqual(trace.status, 'READY', trace.errors)
        self.assertEqual(len(provider.requests), 3)
        first, retry, changeset = provider.requests
        self.assertEqual(first.max_output_tokens, retry.max_output_tokens)
        self.assertEqual(first.output_schema, retry.output_schema)
        self.assertEqual(first.metadata['snapshot_id'], retry.metadata['snapshot_id'])
        self.assertEqual(retry.thinking, 'disabled'); self.assertIsNone(changeset.thinking)
        self.assertEqual(retry.messages[-2]['content'], '{"units":[')
        self.assertEqual(trace.model_calls[1]['stage'], 'PLANNING_TRUNCATION_FORMAT_RETRY')
        self.assertEqual(trace.model_calls[1]['format_retry_index'], 1)

    def test_zero_correction_budget_never_retries(self):
        provider = TruncatedPlanningProvider()
        trace = self.run_case(provider, max_format_retries=0)
        self.assertEqual(trace.status, 'BUDGET_EXHAUSTED'); self.assertEqual(len(provider.requests), 1)

    def test_shared_call_cap_cannot_be_bypassed_by_recovery(self):
        provider = TruncatedPlanningProvider()
        trace = self.run_case(provider, max_model_calls=1)
        self.assertEqual(trace.status, 'BUDGET_EXHAUSTED'); self.assertEqual(len(provider.requests), 1)

    def test_non_reasoning_truncation_does_not_trigger_policy(self):
        provider = TruncatedPlanningProvider(reasoning=False)
        trace = self.run_case(provider)
        self.assertEqual(trace.status, 'BUDGET_EXHAUSTED'); self.assertEqual(len(provider.requests), 1)

    def test_repeated_truncation_uses_only_one_existing_correction(self):
        provider = TruncatedPlanningProvider(repeats=2)
        trace = self.run_case(provider)
        self.assertEqual(trace.status, 'BUDGET_EXHAUSTED'); self.assertEqual(len(provider.requests), 2)


class RecoveredDiagnosisModeTests(unittest.TestCase):
    def test_format_correction_and_planning_extension_keep_recovered_mode(self):
        import json
        from tests.test_planning_evidence_extension import PlanningGapProvider
        class Provider(PlanningGapProvider):
            def __init__(self):
                super().__init__(); self.requests = []
            def complete_json(self, request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    return ModelResponse('{"units":[', {'finish_reason':'length', 'usage':{
                        'completion_tokens':1024, 'completion_tokens_details':{'reasoning_tokens':900}}})
                response = super().complete_json(request)
                if len(self.requests) == 2:
                    body = json.loads(response.content)
                    body['violations'][0]['support_basis'] = 'INVALID'
                    return ModelResponse(json.dumps(body), {'finish_reason':'stop'})
                if request.prompt_version.startswith('diagnosis.') and self.plan_calls:
                    if request.thinking != 'disabled':
                        return ModelResponse('{"units":[', {'finish_reason':'length', 'usage':{
                            'completion_tokens':1024, 'completion_tokens_details':{'reasoning_tokens':900}}})
                return response
        provider = Provider()
        with tempfile.TemporaryDirectory() as td:
            trace = run_fixture(Path(td), provider, lambda s:len(s.split()),
                planning_truncation_retry='disable_thinking',
                planning_truncation_separate_allowance=True, max_model_calls=6)
        self.assertEqual(trace.status, 'READY', trace.errors)
        self.assertEqual([c['stage'] for c in trace.model_calls], [
            'DIAGNOSIS','PLANNING_TRUNCATION_FORMAT_RETRY','DIAGNOSIS_FORMAT_RETRY',
            'SEMANTIC_CHANGESET','DIAGNOSIS_EXTENSION','CHANGESET_REVISION'])
        self.assertEqual(len(provider.requests), 6)
        for i in [1,2,4]: self.assertEqual(provider.requests[i].thinking, 'disabled')
        for i in [0,3,5]: self.assertIsNone(provider.requests[i].thinking)
        self.assertIn('support_basis', provider.requests[2].messages[-1]['content'])
        self.assertTrue(trace.candidate_set.extension_used)
        for i in [2,4]:
            policy=trace.model_calls[i]['request_metadata']['planning_generation_policy']
            self.assertEqual(policy['origin_stage'], 'DIAGNOSIS')
            self.assertEqual(policy['scope'], 'CURRENT_FRONT_HALF_PROMPT_VERSION')
        self.assertEqual(trace.model_calls[1]['truncation_recovery_index'],1)

    def test_diagnosis_triggered_extension_and_its_correction_keep_mode(self):
        from tests.test_evidence_context_correction import ContextErrorsProvider
        class Provider(ContextErrorsProvider):
            def __init__(self):
                super().__init__(); self.all_requests=[]
            def complete_json(self, request):
                self.all_requests.append(request)
                if len(self.all_requests)==1:
                    return ModelResponse('{', {'finish_reason':'length','usage':{
                        'completion_tokens':1024,'completion_tokens_details':{'reasoning_tokens':900}}})
                return super().complete_json(request)
        provider=Provider()
        with tempfile.TemporaryDirectory() as td:
            trace=run_fixture(Path(td), provider, lambda s:len(s.split()),
                planning_truncation_retry='disable_thinking', planning_truncation_separate_allowance=True,
                diagnosis_format_retry_per_context=True)
        self.assertEqual(trace.status,'READY',trace.errors)
        self.assertEqual(len(provider.all_requests),6)
        self.assertTrue(all(r.thinking=='disabled' for r in provider.all_requests[1:5]))
        self.assertIsNone(provider.all_requests[-1].thinking)
        fresh=PipelineFixtureProvider(); seen=[]; original=fresh.complete_json
        def capture(request):
            seen.append(request); return original(request)
        fresh.complete_json=capture
        with tempfile.TemporaryDirectory() as td:
            again=run_fixture(Path(td),fresh,lambda s:len(s.split()),planning_truncation_retry='disable_thinking')
        self.assertEqual(again.status,'READY',again.errors)
        self.assertTrue(all(r.thinking is None for r in seen))
