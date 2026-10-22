import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from contractedge.changeset.topomen import translate_for_topomen
from contractedge.execution.comparison import compare_declared_input
from contractedge.execution.review import ModelPublicReviewer
from contractedge.execution.topomen import build_execution_request, apply_edits
from contractedge.model.provider import FixtureProvider
from contractedge.orchestration.feedback import make_feedback, review_plan_with_retained, validate_feedback
from contractedge.orchestration.repair import rebind_execution_plan
from contractedge.contracts.models import ContractError
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_diagnosis, valid_plan
from tests.test_repair_execution import bound_plan


def observation(text='{"field":{"selected":true}}', snapshot='S1'):
    return {'id': 'OBS', 'kind': 'PUBLIC_OBSERVATION', 'snapshot_id': snapshot,
            'evidence_kind': 'EXECUTION_ONLY' if text == 'build ok' else 'PUBLIC_STRUCTURE',
            'locator': {'path': 'public.json'}, 'excerpt': text,
            'content_digest': hashlib.sha256(text.encode()).hexdigest(), 'origin': 'public fixture'}


class ContractFlowOptimizationTests(unittest.TestCase):
    def test_translation_preserves_trigger_dependency_and_open_atoms(self):
        diagnosis, changeset = valid_diagnosis(), valid_plan()
        changeset['open_obligations'] = [{'id': 'OPEN', 'statement': 'Keep explicit namespace', 'obligation_refs': ['A2']}]
        before = copy.deepcopy(changeset)
        result = translate_for_topomen(changeset, 'contractedge.topomen_plan.v1', diagnosis=diagnosis,
                                       public_capabilities={'observation_limit': 'Build only'})
        self.assertEqual(result['coordination_context']['expected_relations'][0]['condition'], 'enabled')
        self.assertEqual(result['operations'][0]['depends_on'], changeset['changes'][0]['depends_on'])
        self.assertEqual(result['open_obligations'], changeset['open_obligations'])
        self.assertEqual(result['public_execution_capabilities']['observation_limit'], 'Build only')
        self.assertEqual(changeset, before)

    def test_execution_reads_bounded_support_without_granting_edits_or_reusing_stale_quotes(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td); (source / 'templates').mkdir()
            (source / 'templates/serviceaccount.yaml').write_text('name: default\n')
            (source / 'interfaces.py').write_text('def namespace(owner):\n    return owner.scope\n')
            plan = bound_plan(source)
            text = (source / 'interfaces.py').read_text()
            evidence = {'id': 'SRC', 'kind': 'SOURCE_SPAN', 'snapshot_id': plan['snapshot_id'],
                        'locator': {'path': 'interfaces.py'}, 'excerpt': text,
                        'content_digest': hashlib.sha256(text.encode()).hexdigest()}
            plan['coordination_context'] = {'source_support': [evidence], 'diagnosis_snapshot': plan['snapshot_id']}
            config = {'model': 'fixture', 'max_context_tokens': 20000, 'execution_max_output_tokens': 1000}
            request = build_execution_request(plan, source, {'allowed_paths': ['templates/*']}, len, config, requirement='selected')
            payload = json.loads(request.messages[1]['content'])
            self.assertEqual(payload['read_only_source_support'][0]['excerpt'], text)
            self.assertNotIn('interfaces.py', request.output_schema['properties']['edits']['items']['properties']['path']['enum'])
            (source / 'interfaces.py').write_text(text + '# changed\n')
            rebound = rebind_execution_plan(plan, source, {})
            request = build_execution_request(rebound, source, {'allowed_paths': ['templates/*']}, len, config, requirement='selected')
            self.assertEqual(json.loads(request.messages[1]['content'])['read_only_source_support'], [])
            self.assertEqual(request.metadata['read_only_support_audit'][0]['result'], 'HISTORICAL_SNAPSHOT')

    def test_unresolved_obligation_survives_rediagnosis_without_forged_current_atom_refs(self):
        plan = {'postconditions': [{'id': 'P1', 'statement': 'Keep namespace', 'requirement_refs': ['O1'],
                                    'obligation_refs': ['OLD-A1'], 'relation_refs': ['OLD-E1']}]}
        first = make_feedback(case_id='CASE', run_id='RUN', input_digest='a'*64, round_id=0,
            from_snapshot='S0', to_snapshot='S1', patch_digest='b'*64, execution_status='EXECUTION_PASS',
            observations=[observation()], plan=plan, review={'decision': 'UNKNOWN', 'checks': []})
        next_plan = review_plan_with_retained({'postconditions': [{'id': 'P2', 'statement': 'Repair selector'}]}, first)
        historical = next_plan['open_obligations'][0]
        self.assertEqual(historical['statement'], 'Keep namespace')
        self.assertEqual(historical['historical_obligation_refs'], ['OLD-A1'])
        self.assertNotIn('obligation_refs', historical)
        self.assertEqual(historical['source_snapshot'], 'S1')

    def test_feedback_links_failure_to_relation_change_and_actual_observation(self):
        d, p = valid_diagnosis(), valid_plan()
        check = {'id': 'P1', 'status': 'FAIL', 'evidence': [{'observation_id': 'OBS', 'quote': 'selected'}]}
        result = make_feedback(case_id='CASE', run_id='RUN', input_digest='a'*64, round_id=0,
            from_snapshot='S0', to_snapshot='S1', patch_digest='b'*64, execution_status='EXECUTION_PASS',
            observations=[observation()], plan=p, diagnosis=d,
            review={'decision': 'FAIL', 'failure_kind': 'PLAN', 'checks': [check]})
        link = result['coordination_links'][0]
        self.assertEqual(link['change_refs'], ['C1']); self.assertEqual(link['violation_refs'], ['V1'])
        self.assertEqual(link['relation_refs'], ['E1']); self.assertEqual(link['observation_refs'], ['OBS'])
        self.assertEqual(result['route'], 'CHANGESET')
        result['coordination_links'][0]['observation_snapshot'] = 'OLD'
        with self.assertRaises(ContractError):
            validate_feedback(result, case_id='CASE', input_digest='a'*64)

    def test_unobservable_behavior_is_unknown_without_a_paid_review(self):
        class Never:
            def complete_json(self, request):
                raise AssertionError('No review can create unavailable runtime evidence')
        reviewer = ModelPublicReviewer(Never(), len, {'model': 'fixture', 'max_context_tokens': 10000})
        result = reviewer.review('State survives reload', {'postconditions': [{'id': 'P',
            'statement': 'State survives reload', 'verification_kind': 'NOT_PUBLICLY_OBSERVABLE'}]},
            {'status': 'EXECUTION_PASS', 'observations': [observation('build ok')]})
        self.assertEqual(result['checks'][0]['status'], 'UNKNOWN')
        self.assertEqual(result['method'], 'PUBLIC_BOUNDARY_ONLY')

    def test_structural_comparison_uses_actual_inputs_and_rejects_missing_equal_missing(self):
        artifact = {'id': 'INPUT', 'content': 'selected: true\n', 'content_digest': 'a'*64}
        spec = {'artifact_id': 'INPUT', 'input_pointer': '/selected', 'observation_id': 'OBS', 'output_pointer': '/field/selected'}
        self.assertEqual(compare_declared_input(spec, [artifact], [observation()])['status'], 'EQUAL')
        self.assertEqual(compare_declared_input(spec, [artifact], [observation('{"field":{"selected":1}}')])['status'], 'DIFFERENT')
        self.assertEqual(compare_declared_input({**spec, 'input_pointer': '/absent', 'output_pointer': '/absent'},
                                              [artifact], [observation()])['status'], 'UNAVAILABLE')

    def test_review_cannot_claim_pass_with_contradictory_deterministic_comparison(self):
        provider = FixtureProvider({'checks': [{'id': 'P', 'status': 'PASS', 'rationale': 'match',
            'evidence': [{'observation_id': 'OBS', 'quote': 'selected'}],
            'comparisons': [{'artifact_id': 'INPUT', 'input_pointer': '/selected', 'observation_id': 'OBS',
                             'output_pointer': '/field/selected'}]}], 'failure_kind': 'UNKNOWN', 'rationale': 'review'})
        reviewer = ModelPublicReviewer(provider, len, {'model': 'fixture', 'max_context_tokens': 20000})
        with self.assertRaisesRegex(ValueError, 'comparison'):
            reviewer.review('Keep selected setting', {'postconditions': [{'id': 'P', 'statement': 'Keep selected setting'}]},
                {'status': 'EXECUTION_PASS', 'observations': [observation()],
                 'declared_artifacts': [{'id': 'INPUT', 'content': 'selected: false\n'}]})
