import copy
import json
from pathlib import Path
import tempfile
import unittest

from contractedge.contracts.models import ContractError
from contractedge.contracts.validation import parse_method_input, validate_diagnosis
from contractedge.dataset.benchmark import file_sha
from contractedge.execution.topomen import build_execution_request
from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.feedback import make_feedback, review_plan_with_retained
from contractedge.orchestration.repair import repair_case
from tests.test_contracts import valid_diagnosis, valid_method_input
from tests.test_optimization_contract_flow import observation
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_repair_execution import PublicLoopRunner, bound_plan
from tests.test_topomen_agent import native_config


class FreshReviewer:
    def review(self, requirement, plan, observed):
        record = observed['observations'][0]
        return {'decision': 'PASS', 'failure_kind': 'UNKNOWN', 'checks': [
            {'id': p['id'], 'status': 'PASS', 'evidence': [
                {'observation_id': record['id'], 'quote': record['excerpt']}]} for p in plan['postconditions']]}


class OptimizationIntegrityTests(unittest.TestCase):
    def test_unobservable_boundary_survives_rediagnosis_and_cannot_be_cleared_by_build_quote(self):
        class Never:
            def complete_json(self, request):
                raise AssertionError('Unavailable observation must not trigger a paid review')
        plan = {'postconditions': [{'id': 'P', 'statement': 'State survives reload',
            'verification_kind': 'NOT_PUBLICLY_OBSERVABLE', 'planned_check': 'Inspect runtime state',
            'check_scope': 'Runtime'}]}
        def feedback(current, review):
            return make_feedback(case_id='CASE', run_id='RUN', input_digest='a'*64, round_id=0,
                from_snapshot='S0', to_snapshot='S1', patch_digest='b'*64, execution_status='EXECUTION_PASS',
                observations=[observation('build ok')], plan=current, review=review)
        first = feedback(plan, {'decision': 'UNKNOWN', 'checks': []})
        for next_plan in [{'postconditions': []},
                          {'postconditions': [{'id': 'P2', 'statement': 'State survives reload'}]}]:
            effective = review_plan_with_retained(next_plan, first)
            checks = effective['postconditions'] or effective['open_obligations']
            self.assertEqual(checks[0]['verification_kind'], 'NOT_PUBLICLY_OBSERVABLE')
            review = ModelPublicReviewer(Never(), len, {'model': 'fixture', 'max_context_tokens': 10000}).review(
                'State survives reload', effective,
                {'status': 'EXECUTION_PASS', 'observations': [observation('build ok')]})
            self.assertEqual(review['decision'], 'UNKNOWN')
            check_id = checks[0]['id'] if effective['postconditions'] else 'open:' + checks[0]['id']
            malicious = feedback(effective, {'decision': 'PASS', 'checks': [{'id': check_id, 'status': 'PASS',
                'evidence': [{'observation_id': 'OBS', 'quote': 'build ok'}]}]})
            self.assertEqual(malicious['semantic_outcome'], 'UNKNOWN')
            self.assertTrue(malicious['pending_obligations'])

    def test_independent_graph_errors_are_reported_together_without_relaxing_paths(self):
        packet = valid_diagnosis()
        packet['obligation_atoms'].append({**packet['obligation_atoms'][0], 'id': 'A2'})
        packet['units'][1]['semantic_role_class'] = 'GENERATED_STATE'
        with self.assertRaises(ContractError) as caught:
            validate_diagnosis(packet, parse_method_input(valid_method_input()), None)
        self.assertEqual(caught.exception.code, 'OBLIGATION_RELATION_MISSING')
        self.assertIn('PRODUCTION_PATH_INCOMPLETE', str(caught.exception))

    def test_pass_and_fail_need_fresh_exact_observation_quotes(self):
        for verdict in ['PASS', 'FAIL']:
            for obs, evidence in [([], [{'observation_id': 'OLD', 'quote': 'selected'}]),
                    ([observation()], [{'observation_id': 'OBS', 'quote': 'fabricated'}]),
                    ([observation(snapshot='OLD')], [{'observation_id': 'OBS', 'quote': 'selected'}])]:
                # Stale records are excluded at the orchestration boundary.
                fresh = [o for o in obs if o['snapshot_id'] == 'S1']
                original = {'decision': verdict, 'failure_kind': 'DIAGNOSIS',
                            'checks': [{'id': 'P', 'status': verdict, 'evidence': evidence}]}
                saved = copy.deepcopy(original)
                feedback = make_feedback(case_id='CASE', run_id='RUN', input_digest='a'*64,
                    round_id=0, from_snapshot='S0', to_snapshot='S1', patch_digest='b'*64,
                    execution_status='EXECUTION_PASS', observations=fresh,
                    plan={'postconditions': [{'id': 'P', 'statement': 'selected'}]}, review=original)
                self.assertEqual(feedback['semantic_outcome'], 'UNKNOWN')
                self.assertEqual(feedback['route'], 'STOP')
                self.assertEqual(feedback['public_check_results'][0]['status'], 'UNKNOWN')
                self.assertEqual(feedback['retained_constraints'], [])
                self.assertEqual(original, saved)

    def test_partial_reasoning_truncation_fallback_requires_same_snapshot_and_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root/'templates').mkdir()
            (root/'templates/serviceaccount.yaml').write_text('name: default\n')
            plan = bound_plan(root)
            prior = {'model_response': '{"edits":[', 'source_snapshot': plan['snapshot_id'],
                     'response_metadata': {'finish_reason': 'length', 'usage': {'completion_tokens': 100,
                         'completion_tokens_details': {'reasoning_tokens': 75}}}}
            config = {'model': 'fixture', 'provider': 'openai_compatible', 'max_context_tokens': 50000,
                      'execution_truncation_retry': 'disable_thinking'}
            def request(previous, cfg):
                return build_execution_request(plan, root, {'allowed_paths': ['templates/**']}, len,
                                               cfg, requirement='selected', previous_attempt=previous)
            self.assertEqual(request(prior, config).thinking, 'disabled')
            for changed, cfg in [({**prior, 'source_snapshot': 'OLD'}, config),
                                 (prior, {**config, 'execution_truncation_retry': None}),
                                 ({**prior, 'response_metadata': {'finish_reason': 'stop'}}, config)]:
                self.assertIsNone(request(changed, cfg).thinking)

    def run_truncated(self, root, *, repeat=False, transport_error=False, max_calls=12):
        from tests.test_benchmark_current import make_public_fixture
        public, _ = make_public_fixture(root/'benchmark')
        owner = self
        class Provider(PipelineFixtureProvider):
            executions = 0
            def complete_json(self, request):
                if request.prompt_version == 'topomen-source-execution.v1':
                    self.executions += 1
                    if transport_error:
                        raise TimeoutError('unknown transport response')
                    if self.executions == 1 or repeat:
                        return ModelResponse('{"edits":[', {'finish_reason': 'length', 'usage': {
                            'completion_tokens': 100, 'completion_tokens_details': {'reasoning_tokens': 75}}})
                    owner.assertEqual(request.thinking, 'disabled')
                    return ModelResponse(json.dumps({'edits': [{'change_id': 'C1',
                        'path': 'templates/serviceaccount.yaml', 'operation': 'MODIFY',
                        'old_text': 'name: default', 'new_text': 'name: selected'}]}), {})
                self._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(
                    root/'run/workspace/source/repo/templates/serviceaccount.yaml')}
                return super().complete_json(request)
        provider = Provider()
        report = repair_case(public, root, 'CASE-1', root/'run', provider, lambda t: len(t.split()),
            {**native_config(), 'provider': 'openai_compatible', 'max_rounds': 1,
             'max_model_calls': max_calls, 'execution_format_retry_in_round': True,
             'execution_truncation_retry': 'disable_thinking'}, public_runner=PublicLoopRunner(public),
            reviewer=FreshReviewer())
        return report, provider

    def test_native_format_correction_stays_inside_one_semantic_round(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); report, provider = self.run_truncated(root)
            self.assertEqual(report['status'], 'PUBLIC_REVIEW_COMPLETE', report['errors'])
            self.assertEqual(len(report['rounds']), 1)
            self.assertEqual(report['budget']['model_calls'], 4)
            self.assertEqual(provider.executions, 2)
            self.assertTrue((root/'run/rounds/0/execution-attempts/0.json').is_file())
            self.assertTrue((root/'run/rounds/0/execution-attempts/1.json').is_file())

    def test_repeated_format_failure_stops_with_original_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); report, provider = self.run_truncated(root, repeat=True)
            self.assertEqual(report['status'], 'INVALID_OUTPUT')
            self.assertEqual(provider.executions, 2)
            self.assertEqual(report['initial_snapshot_id'], report['final_snapshot_id'])

    def test_in_round_correction_keeps_call_budget_and_never_retries_unknown_transport(self):
        for args, calls, status in [({'max_calls': 3}, 1, 'BUDGET_EXHAUSTED'),
                                     ({'transport_error': True}, 1, 'INFRASTRUCTURE_ERROR')]:
            with tempfile.TemporaryDirectory() as td:
                report, provider = self.run_truncated(Path(td), **args)
                self.assertEqual(report['status'], status, report['errors'])
                self.assertEqual(provider.executions, calls)
