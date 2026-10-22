import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from contractedge.changeset.topomen import translate_for_topomen
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.benchmark import file_sha, method_input_from_task
from contractedge.execution.topomen import accept_plan, apply_edits, PlanRejected
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.provider import ModelRequest, ModelResponse
from contractedge.orchestration.feedback import make_feedback, validate_feedback
from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot
from tests.test_benchmark_current import make_public_fixture
from tests.test_contracts import valid_plan
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import PlanningGapProvider
from contractedge.orchestration.runner import run_front_half


def bound_plan(root):
    plan = valid_plan()
    snapshot = compute_snapshot(root)
    plan['snapshot_id'] = snapshot.snapshot_id
    plan['changes'][0]['target'].update(snapshot_id=snapshot.snapshot_id,
        content_digest=snapshot.file_digests['templates/serviceaccount.yaml'])
    return translate_for_topomen(plan, 'contractedge.topomen_plan.v1')


class SourceConsumerTests(unittest.TestCase):
    def test_new_target_creates_regular_file_without_fabricating_an_existing_locator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'templates').mkdir()
            (root / 'templates/serviceaccount.yaml').write_text('name: default\n')
            plan = bound_plan(root)
            plan['operations'][0].update(operation='CREATE', target={'kind': 'NEW',
                'parent_path': 'templates', 'proposed_path': 'templates/new.yaml',
                'expected_role': 'new resource generator', 'anchor_unit_refs': ['U2'],
                'missing_unit_refs': ['U3'], 'justification_refs': ['EV1']})
            result = apply_edits(plan, root, {'allowed_paths': ['templates/**']}, {'edits': [{
                'change_id': 'C1', 'path': 'templates/new.yaml', 'operation': 'CREATE',
                'old_text': '', 'new_text': 'name: selected\n'}]})
            self.assertEqual(result['status'], 'APPLIED')
            self.assertEqual((root / 'templates/new.yaml').read_text(), 'name: selected\n')
            self.assertEqual((root / 'templates/new.yaml').stat().st_mode & 0o777, 0o644)

    def test_remove_gets_explicit_rejection_for_current_benchmark_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'templates').mkdir()
            (root / 'templates/serviceaccount.yaml').write_text('name: default\n')
            plan = bound_plan(root); plan['operations'][0]['operation'] = 'REMOVE'
            with self.assertRaisesRegex(PlanRejected, 'whole-file deletion'):
                accept_plan(plan, root, {'allowed_paths': ['**']})
            self.assertTrue((root / 'templates/serviceaccount.yaml').exists())

    def test_plan_applies_literal_edit_and_rejects_stale_second_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'templates').mkdir()
            source = root / 'templates/serviceaccount.yaml'
            source.write_text('name: default\n')
            plan = bound_plan(root)
            result = apply_edits(plan, root, {'allowed_paths': ['templates/**']}, {'edits': [{
                'change_id': 'C1', 'path': 'templates/serviceaccount.yaml', 'operation': 'MODIFY',
                'old_text': 'name: default', 'new_text': 'name: selected'}]})
            self.assertNotEqual(result['from_snapshot'], result['to_snapshot'])
            self.assertEqual(source.read_text(), 'name: selected\n')
            with self.assertRaisesRegex(PlanRejected, 'stale'):
                accept_plan(plan, root, {'allowed_paths': ['**']})

    def test_invalid_second_edit_keeps_entire_source_batch_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'templates').mkdir()
            source = root / 'templates/serviceaccount.yaml'; source.write_text('name: default\n')
            plan = bound_plan(root)
            edit = {'change_id': 'C1', 'path': 'templates/serviceaccount.yaml', 'operation': 'MODIFY',
                    'old_text': 'name: default', 'new_text': 'name: selected'}
            with self.assertRaisesRegex(PlanRejected, 'exactly once'):
                apply_edits(plan, root, {'allowed_paths': ['**']}, {'edits': [edit, {**edit, 'old_text': 'not present'}]})
            self.assertEqual(source.read_text(), 'name: default\n')

    def test_scope_and_symlink_are_checked_before_model_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'templates').mkdir()
            source = root / 'templates/serviceaccount.yaml'; source.write_text('name: default\n')
            plan = bound_plan(root)
            with self.assertRaisesRegex(PlanRejected, 'outside'):
                accept_plan(plan, root, {'allowed_paths': ['elsewhere/*']})
            source.unlink(); source.symlink_to('/etc/passwd')
            with self.assertRaises(PlanRejected):
                accept_plan(plan, root, {'allowed_paths': ['**']})


class PublicLoopRunner:
    """Actual file/patch/process boundary with a tiny public fixture, no Docker."""
    def __init__(self, public, raw_only=False):
        self.public = public
        self.raw_only = raw_only

    def prepare(self, case_id, workspace, log):
        workspace.mkdir()
        archive = self.public / 'tasks/CASE-1/source.tar.gz'
        source = Path(safe_extract(archive, workspace / 'source').source_root)
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                        'commit', '-qm', 'Broken'], check=True)
        (workspace / 'inputs').mkdir()
        record = {'case_id': case_id, 'source_root': str(source), 'inputs_root': str(workspace / 'inputs'),
                  'source_sha256': file_sha(archive), 'private_materials_loaded': False}
        (workspace / 'workspace.json').write_text(json.dumps(record))
        return record

    def export(self, workspace, patch, log):
        source = json.loads((workspace / 'workspace.json').read_text())['source_root']
        diff = subprocess.check_output(['git', '-C', source, 'diff', 'HEAD', '--'])
        patch.write_bytes(diff)
        return {'patch_sha256': hashlib.sha256(diff).hexdigest(), 'patch_bytes': len(diff), 'patch_path': str(patch)}

    def observe(self, workspace, output):
        output.mkdir(parents=True)
        source = Path(json.loads((workspace / 'workspace.json').read_text())['source_root'])
        # The public fixture actually inspects candidate code in a child process.
        result = subprocess.run([sys.executable, '-c',
            'import pathlib,sys;t=pathlib.Path(sys.argv[1]).read_text();print(t);sys.exit(0 if sys.argv[2]=="raw" or "name: selected" in t else 1)',
            str(source / 'templates/serviceaccount.yaml'), 'raw' if self.raw_only else 'check'], text=True, capture_output=True)
        excerpt = result.stdout
        snapshot = compute_snapshot(source).snapshot_id
        record = {'evidence_kind': 'PUBLIC_STRUCTURE', 'id': 'OBS1', 'snapshot_id': snapshot, 'kind': 'PUBLIC_OBSERVATION',
                  'locator': {'path': 'candidate-output.txt'}, 'content_digest': hashlib.sha256(excerpt.encode()).hexdigest(),
                  'excerpt': excerpt, 'origin': 'public process fixture'}
        return {'status': 'EXECUTION_PASS' if result.returncode == 0 else 'EXECUTION_FAILED',
                'snapshot_id': snapshot, 'observations': [record],
                'input_digest':'fixture-input','environment_digest':'fixture-env'}


class TwoStepExecutor:
    def __init__(self): self.calls = 0

    def execute(self, plan, source, policy, **kwargs):
        self.calls += 1
        old, new = ('name: default', 'name: intermediate') if self.calls == 1 else ('name: intermediate', 'name: selected')
        return apply_edits(plan, source, policy, {'edits': [{'change_id': 'C1',
            'path': 'templates/serviceaccount.yaml', 'operation': 'MODIFY', 'old_text': old, 'new_text': new}]})


class AcceptPublicReview:
    def review(self, requirement, plan, observation):
        return {'decision': 'PASS', 'checks': [{'id': 'P1', 'status': 'PASS',
                'evidence': current_public_citation(observation)}],
                'failure_kind': 'UNKNOWN', 'method': 'PUBLIC_FIXTURE_REVIEW'}


def current_public_citation(observation):
    record = observation['observations'][0]
    return [{'observation_id': record['id'], 'quote': record['excerpt']}]


class RepairLoopTests(unittest.TestCase):
    def test_rediagnosis_cannot_finish_after_reviewer_omits_a_historical_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public, _ = make_public_fixture(root / 'benchmark')
            active_source = root / 'run/workspace/source/repo/templates/serviceaccount.yaml'
            class ReplannedProvider(PipelineFixtureProvider):
                diagnoses = 0
                def complete_json(self, request):
                    self._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(active_source)}
                    response = super().complete_json(request)
                    if request.prompt_version.startswith('diagnosis.'):
                        self.diagnoses += 1
                        return response
                    body = json.loads(response.content)
                    second = {**body['postconditions'][0], 'id': 'P2', 'statement': 'Selected name is restored'}
                    body['postconditions'] = [*body['postconditions'], second] if self.diagnoses == 1 else [second]
                    body['changes'][0]['postcondition_refs'] = ['P1', 'P2'] if self.diagnoses == 1 else ['P2']
                    return ModelResponse(json.dumps(body), {})
            class OmittingReviewer:
                calls = 0
                last_plan = None
                def review(self, requirement, plan, observation):
                    self.calls += 1
                    self.last_plan = plan
                    return {'decision': 'FAIL' if self.calls == 1 else 'PASS', 'failure_kind': 'DIAGNOSIS',
                            'checks': [{'id': 'P1', 'status': 'PASS', 'evidence': current_public_citation(observation)},
                                       {'id': 'P2', 'status': 'FAIL', 'evidence': current_public_citation(observation)}]
                                      if self.calls == 1 else [{'id': 'P2', 'status': 'PASS',
                                                              'evidence': current_public_citation(observation)}]}
            reviewer = OmittingReviewer()
            report = repair_case(public, root, 'CASE-1', root / 'run', ReplannedProvider(),
                lambda t: len(t.split()), {'model': 'fixture', 'max_context_tokens': 12000,
                    'max_output_tokens': 2048, 'max_rounds': 2},
                public_runner=PublicLoopRunner(public, raw_only=True), executor=TwoStepExecutor(), reviewer=reviewer)
            self.assertEqual(report['status'], 'PUBLIC_EXECUTION_COMPLETE', report['errors'])
            self.assertEqual(report['last_feedback']['semantic_outcome'], 'UNKNOWN')
            self.assertTrue(reviewer.last_plan['preserve_constraints'])
            self.assertTrue(any(p['kind'] == 'PRESERVE' for p in report['last_feedback']['pending_obligations']))

    def test_ready_trace_after_planning_evidence_extension_can_resume_without_resetting_costs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public, _ = make_public_fixture(root / 'benchmark')
            runner = PublicLoopRunner(public)
            workspace = root / 'front-workspace'
            prepared = runner.prepare('CASE-1', workspace, root / 'prepare')
            source = Path(prepared['source_root'])
            observation = runner.observe(workspace, root / 'initial-observation')
            provider = PlanningGapProvider()
            provider._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(source / 'templates/serviceaccount.yaml')}
            method, _ = method_input_from_task(public, 'CASE-1')
            config = {'model': 'fixture', 'max_context_tokens': 12000, 'max_output_tokens': 2048,
                      'max_total_tokens': 80000, 'max_rounds': 2}
            trace = run_front_half(method, source.parent, provider,
                {**config, 'model': {'model': 'fixture'}}, lambda text: len(text.split()),
                public_observations=observation['observations']).to_wire()
            self.assertEqual(trace['status'], 'READY', trace['errors'])
            self.assertTrue(trace['candidate_set']['extension_used'])
            report = repair_case(public, root, 'CASE-1', root / 'run', provider,
                lambda text: len(text.split()), config, initial_trace=trace,
                public_runner=runner, executor=TwoStepExecutor(), reviewer=AcceptPublicReview())
            self.assertEqual(report['status'], 'PUBLIC_REVIEW_COMPLETE', report['errors'])
            self.assertEqual(report['budget']['resumed_model_calls'], 4)
            self.assertGreater(report['budget']['reserved_tokens'], 0)

    def test_diagnosis_feedback_reads_s1_with_new_round_context_and_fixed_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public, _ = make_public_fixture(root / 'benchmark')
            active_source = root / 'run/workspace/source/repo/templates/serviceaccount.yaml'
            class CurrentSourceProvider(PipelineFixtureProvider):
                def complete_json(self, request):
                    self._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(active_source)}
                    return super().complete_json(request)
            class DiagnoseThenAccept:
                calls = 0
                def review(self, requirement, plan, observation):
                    self.calls += 1
                    return {'decision': 'FAIL' if self.calls == 1 else 'PASS',
                            'checks': [{'id': 'P1', 'status': 'FAIL' if self.calls == 1 else 'PASS',
                                        'evidence': current_public_citation(observation)}],
                            'failure_kind': 'DIAGNOSIS', 'rationale': 'Public output still has the intermediate name'}
            report = repair_case(public, root, 'CASE-1', root / 'run', CurrentSourceProvider(),
                lambda text: len(text.split()), {'model': 'fixture', 'max_context_tokens': 12000,
                    'max_output_tokens': 2048, 'max_rounds': 2}, public_runner=PublicLoopRunner(public, raw_only=True),
                executor=TwoStepExecutor(), reviewer=DiagnoseThenAccept())
            self.assertEqual(report['status'], 'PUBLIC_REVIEW_COMPLETE', report['errors'])
            self.assertEqual(report['rounds'][0]['route'], 'DIAGNOSIS')
            first = json.loads((root / 'run/rounds/0/front.trace.json').read_text())
            second = json.loads((root / 'run/rounds/1/front.trace.json').read_text())
            self.assertEqual(second['run_context']['round_id'], 1)
            self.assertEqual(second['run_context']['parent_snapshot_id'], first['snapshot_id'])
            self.assertNotEqual(second['snapshot_id'], first['snapshot_id'])
            self.assertEqual(second['input_digest'], first['input_digest'])
            self.assertEqual(second['requirement_spec']['raw_text'], first['requirement_spec']['raw_text'])

    def test_two_rounds_use_real_edits_process_outputs_feedback_and_original_patch_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public, task = make_public_fixture(root / 'benchmark')
            provider = PipelineFixtureProvider()
            provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(
                b'apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n').hexdigest()}
            executor = TwoStepExecutor()
            report = repair_case(public, root, 'CASE-1', root / 'run', provider,
                lambda text: len(text.split()), {'model': 'fixture', 'max_context_tokens': 12000,
                    'max_output_tokens': 2048, 'max_rounds': 2},
                public_runner=PublicLoopRunner(public), executor=executor, reviewer=AcceptPublicReview())
            self.assertEqual(report['status'], 'PUBLIC_REVIEW_COMPLETE', report['errors'])
            self.assertEqual(executor.calls, 2)
            self.assertEqual(report['rounds'][0]['route'], 'EXECUTE')
            self.assertEqual(report['rounds'][0]['to_snapshot'], report['rounds'][1]['from_snapshot'])
            self.assertEqual(report['last_feedback']['pending_obligations'], [])
            self.assertIn('+  name: selected', (root / 'run/candidate.patch').read_text())
            self.assertIn('-  name: default', (root / 'run/candidate.patch').read_text())
            self.assertNotIn('intermediate', (root / 'run/candidate.patch').read_text())
            self.assertEqual(report['input_digest'], method_input_from_task(public, 'CASE-1')[0]['input_digest'])

    def test_raw_execution_pass_retains_pending_checks_and_unknown_semantics(self):
        feedback = make_feedback(case_id='CASE', run_id='run', input_digest='a' * 64, round_id=0,
            from_snapshot='S0', to_snapshot='S1', patch_digest='b' * 64, execution_status='EXECUTION_PASS',
            observations=[], plan={'postconditions': [{'id': 'P1', 'statement': 'required behavior'}]})
        self.assertEqual(feedback['semantic_outcome'], 'UNKNOWN')
        self.assertEqual(feedback['route'], 'STOP')
        self.assertEqual(len(feedback['pending_obligations']), 1)
        with self.assertRaisesRegex(ValueError, 'stale'):
            validate_feedback(feedback, case_id='CASE', input_digest='a' * 64, to_snapshot='S2')

    def test_environment_failure_never_routes_to_diagnosis(self):
        feedback = make_feedback(case_id='CASE', run_id='run', input_digest='a' * 64, round_id=0,
            from_snapshot='S0', to_snapshot='S0', patch_digest='b' * 64, execution_status='INFRA_ERROR',
            observations=[], plan={})
        self.assertEqual(feedback['failure_kind'], 'ENVIRONMENT')
        self.assertEqual(feedback['route'], 'STOP')

    def test_global_budget_is_shared_across_requests(self):
        class Provider:
            def complete_json(self, request): return ModelResponse('{}', {})
        budget = BudgetedProvider(Provider(), lambda text: len(text), max_tokens=10, max_seconds=60)
        request = ModelRequest('fixture', 'stage', ({'role': 'user', 'content': '1234'},), 2)
        budget.complete_json(request)
        with self.assertRaises(BudgetExceeded): budget.complete_json(request)
        self.assertEqual(len(budget.calls), 1)


if __name__ == '__main__':
    unittest.main()

class ExecutionRetryContextTests(unittest.TestCase):
    def test_large_public_feedback_keeps_check_state_without_duplicating_raw_output(self):
        from contractedge.execution.topomen import build_execution_request
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'templates').mkdir();(root/'templates/serviceaccount.yaml').write_text('name: default\n')
            feedback={'route':'EXECUTE','rationale':'old_text must occur exactly once',
                'public_observations':[{'id':'OBS1','snapshot_id':'S0','content_digest':'a'*64,
                    'locator':{'path':'render.stdout'},'excerpt':'x'*500000}],
                'pending_obligations':[{'id':'P1','statement':'selected name'}],
                'retained_constraints':[{'id':'KEEP1','statement':'keep disabled branch'}]}
            request=build_execution_request(bound_plan(root),root,{'allowed_paths':['templates/**']},len,
                {'model':'test','max_context_tokens':30000,'execution_max_output_tokens':1000},
                requirement='Render selected ServiceAccount',observations=(),feedback=feedback)
            payload=json.loads(request.messages[1]['content'])
            self.assertLess(len(request.messages[1]['content']),30000)
            self.assertEqual(payload['execution_feedback']['pending_obligations'],feedback['pending_obligations'])
            self.assertEqual(payload['execution_feedback']['retained_constraints'],feedback['retained_constraints'])
            self.assertEqual(payload['execution_feedback_digest'],__import__('contractedge.contracts.validation',fromlist=['canonical_digest']).canonical_digest(feedback))

    def test_execution_correction_receives_the_exact_previous_rejected_draft(self):
        from contractedge.execution.topomen import build_execution_request
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'templates').mkdir();(root/'templates/serviceaccount.yaml').write_text('name: default\n')
            previous={'model_response':'{"edits":[{"old_text":"wrong"}]}','error':'old_text must occur exactly once', 'source_snapshot':compute_snapshot(root).snapshot_id}
            request=build_execution_request(bound_plan(root),root,{'allowed_paths':['templates/**']},len,
                {'model':'test','max_context_tokens':30000,'execution_max_output_tokens':1000},
                requirement='Render selected ServiceAccount',previous_attempt=previous)
            self.assertEqual(json.loads(request.messages[1]['content'])['previous_execution_attempt'],previous)
