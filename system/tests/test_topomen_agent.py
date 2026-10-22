import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from contractedge.execution.topomen import PlanRejected
from contractedge.execution.topomen_agent import TopoMenAgentExecutor, source_identity
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.provider import FixtureProvider, ModelResponse
from contractedge.orchestration.repair import repair_case
from tests.test_benchmark_current import make_public_fixture
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_repair_execution import bound_plan, PublicLoopRunner, AcceptPublicReview


TOPO = Path(os.environ.get('CONTRACTEDGE_TOPOMEN_SOURCE', '/home/xhj/contractedge-integrations/topomen'))
AVAILABLE = TOPO.is_dir() and all(importlib.util.find_spec(p) for p in ['rich', 'dotenv', 'jinja2', 'pydantic'])


def native_config():
    return {'model': 'fixture', 'max_context_tokens': 12000, 'max_output_tokens': 1024,
            'execution_max_output_tokens': 1024, 'topomen_source_root': str(TOPO),
            'topomen_source_sha256': source_identity(TOPO)['source_sha256'], 'execution_backend': 'topomen_agent'}


def source_fixture(root):
    (root / 'templates').mkdir()
    source = root / 'templates/serviceaccount.yaml'; source.write_text('name: default\n')
    plan = bound_plan(root)
    edits = {'edits': [{'change_id': 'C1', 'path': 'templates/serviceaccount.yaml', 'operation': 'MODIFY',
                        'old_text': 'name: default', 'new_text': 'name: selected'}]}
    return source, plan, edits


@unittest.skipUnless(AVAILABLE, 'Real TopoMen checkout and optional runtime dependencies required')
class RealTopoMenAgentTests(unittest.TestCase):
    def test_audited_source_cannot_be_replaced_by_same_timestamp_stale_bytecode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(TOPO / 'src', root / 'src', ignore=shutil.ignore_patterns('__pycache__'))
            entry = root / 'src/topomen/collaboration/base_agent.py'
            original, stat = entry.read_text(), entry.stat()
            self.assertIn('self.n_calls = 0', original)
            entry.write_text(original.replace('self.n_calls = 0', 'self.n_calls = 9', 1))
            os.utime(entry, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            py_compile.compile(str(entry), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
            entry.write_text(original)
            os.utime(entry, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            identity = source_identity(root)
            self.assertEqual(identity['source_sha256'], native_config()['topomen_source_sha256'])
            code = '''import json,sys
from contractedge.execution.topomen_agent import _native_agent
prefix=sys.pycache_prefix
agent,_,_=_native_agent({'topomen_source_root':sys.argv[1],'topomen_source_sha256':sys.argv[2]})
instance=agent(model=None,env=None,system_template='s',instance_template='u')
print(json.dumps({'calls':instance.n_calls,'prefix_restored':prefix==sys.pycache_prefix}))
'''
            result = subprocess.run([sys.executable, '-c', code, str(root), identity['source_sha256']],
                                    capture_output=True, text=True, check=True)
            actual = json.loads(result.stdout)
            self.assertEqual(actual['calls'], 0)
            self.assertTrue(actual['prefix_restored'])

    def test_preflight_rejection_after_success_does_not_reuse_a_previous_submitted_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, plan, edits = source_fixture(root)
            executor = TopoMenAgentExecutor(FixtureProvider(edits), lambda t: len(t.split()), native_config())
            executor.execute(plan, root, {'allowed_paths': ['**']}, requirement='selected')
            self.assertIsNotNone(executor.last_trace)
            with self.assertRaisesRegex(PlanRejected, 'stale'):
                executor.execute(plan, root, {'allowed_paths': ['**']}, requirement='selected')
            self.assertIsNone(executor.last_trace)

    def test_real_agent_runs_one_shared_budgeted_query_and_applies_scoped_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, plan, edits = source_fixture(root)
            budget = BudgetedProvider(FixtureProvider(edits), lambda t: len(t.split()), max_tokens=16000, max_seconds=30)
            traces = []
            executor = TopoMenAgentExecutor(budget, lambda t: len(t.split()), native_config(), on_trace=traces.append)
            with patch.dict(os.environ, {'CONTRACTEDGE_PRIVATE_SENTINEL': 'private-test-value'}):
                result = executor.execute(plan, root, {'allowed_paths': ['templates/**']}, requirement='Use selected name')
            self.assertEqual(source.read_text(), 'name: selected\n')
            self.assertEqual(result['status'], 'APPLIED')
            self.assertEqual(result['backend']['agent_class'], 'topomen.collaboration.base_agent.DefaultAgent')
            self.assertEqual(result['topomen_trace']['info']['config']['agent_type'], 'topomen.collaboration.base_agent.DefaultAgent')
            self.assertEqual(result['topomen_trace']['info']['model_stats']['api_calls'], 1)
            self.assertEqual(result['topomen_trace']['info']['exit_status'], 'Submitted')
            self.assertEqual(len(budget.calls), 1)
            self.assertEqual(len(traces), 1)
            self.assertNotIn('private-test-value', json.dumps(result))

    def test_source_hash_drift_fails_before_any_model_call_or_file_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, plan, edits = source_fixture(root)
            budget = BudgetedProvider(FixtureProvider(edits), len, max_tokens=16000, max_seconds=30)
            config = {**native_config(), 'topomen_source_sha256': '0' * 64}
            with self.assertRaisesRegex(RuntimeError, 'source identity'):
                TopoMenAgentExecutor(budget, len, config).execute(plan, root, {'allowed_paths': ['**']}, requirement='selected')
            self.assertEqual(source.read_text(), 'name: default\n')
            self.assertEqual(len(budget.calls), 0)

    def test_shell_action_is_rejected_without_editing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, plan, edits = source_fixture(root)
            response = {**edits, 'command': 'touch unplanned-file'}
            executor = TopoMenAgentExecutor(FixtureProvider(response), lambda t: len(t.split()), native_config())
            with self.assertRaises(PlanRejected):
                executor.execute(plan, root, {'allowed_paths': ['**']}, requirement='selected')
            self.assertEqual(source.read_text(), 'name: default\n')
            self.assertFalse((root / 'unplanned-file').exists())

    def test_original_global_budget_exhaustion_propagates_through_native_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, plan, edits = source_fixture(root)
            budget = BudgetedProvider(FixtureProvider(edits), len, max_tokens=1, max_seconds=30)
            with self.assertRaises(BudgetExceeded):
                TopoMenAgentExecutor(budget, lambda t: len(t.split()), native_config()).execute(
                    plan, root, {'allowed_paths': ['**']}, requirement='selected')
            self.assertEqual(source.read_text(), 'name: default\n')
            self.assertEqual(len(budget.calls), 0)

    def test_repair_selects_native_backend_and_retains_real_agent_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); public, _ = make_public_fixture(root / 'benchmark')
            class Provider(PipelineFixtureProvider):
                def complete_json(self, request):
                    if request.prompt_version == 'topomen-source-execution.v1':
                        return ModelResponse(json.dumps({'edits': [{'change_id': 'C1',
                            'path': 'templates/serviceaccount.yaml', 'operation': 'MODIFY',
                            'old_text': 'name: default', 'new_text': 'name: selected'}]}), {})
                    from contractedge.dataset.benchmark import file_sha
                    self._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(root / 'run/workspace/source/repo/templates/serviceaccount.yaml')}
                    return super().complete_json(request)
            report = repair_case(public, root, 'CASE-1', root / 'run', Provider(), lambda t: len(t.split()),
                {**native_config(), 'max_rounds': 1}, public_runner=PublicLoopRunner(public), reviewer=AcceptPublicReview())
            self.assertEqual(report['status'], 'PUBLIC_REVIEW_COMPLETE', report['errors'])
            execution = json.loads((root / 'run/rounds/0/execution.json').read_text())
            self.assertEqual(execution['backend']['agent_class'], 'topomen.collaboration.base_agent.DefaultAgent')
            self.assertEqual(report['budget']['model_calls'], 3)
            self.assertTrue((root / 'run/topomen-native-last-trace.json').is_file())

    def test_native_execution_retry_receives_rejected_draft_with_large_feedback(self):
        import hashlib
        from contractedge.dataset.benchmark import file_sha
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);public,_=make_public_fixture(root/'benchmark');owner=self
            class LargeOutputRunner(PublicLoopRunner):
                def observe(self, workspace, output):
                    result=super().observe(workspace,output)
                    text='unrelated build output '*30000
                    result['observations'].append(dict(id='OBS2',snapshot_id=result['snapshot_id'],
                        kind='PUBLIC_OBSERVATION',locator={'path':'large-public-output.txt'},
                        content_digest=hashlib.sha256(text.encode()).hexdigest(),excerpt=text,origin='public fixture'))
                    return result
            class RetryProvider(PipelineFixtureProvider):
                execution_calls=0
                def complete_json(self,request):
                    if request.prompt_version=='topomen-source-execution.v1':
                        self.execution_calls+=1
                        body=json.loads(request.messages[1]['content'])
                        if self.execution_calls==2:
                            previous=body['previous_execution_attempt']
                            owner.assertIn('wrong literal anchor',previous['model_response'])
                            owner.assertIn('exactly once',previous['validation_error'])
                            owner.assertLess(len(request.messages[1]['content']),160000)
                        old='wrong literal anchor' if self.execution_calls==1 else 'name: default'
                        return ModelResponse(json.dumps({'edits':[dict(change_id='C1',path='templates/serviceaccount.yaml',
                            operation='MODIFY',old_text=old,new_text='name: selected')]}),{})
                    self._snapshot_digests={'templates/serviceaccount.yaml':file_sha(root/'run/workspace/source/repo/templates/serviceaccount.yaml')}
                    return super().complete_json(request)
            provider=RetryProvider()
            result=repair_case(public,root,'CASE-1',root/'run',provider,len,
                {**native_config(),'max_context_tokens':160000,'max_total_tokens':2000000,'max_rounds':2},
                public_runner=LargeOutputRunner(public),reviewer=AcceptPublicReview())
            self.assertEqual(result['status'],'PUBLIC_REVIEW_COMPLETE',result['errors'])
            self.assertEqual(provider.execution_calls,2)
            self.assertEqual(result['budget']['model_calls'],4)
            self.assertEqual(result['rounds'][0]['route'],'EXECUTE')
            self.assertEqual(result['last_feedback']['pending_obligations'],[])

    def test_native_empty_reasoning_retry_keeps_budget_and_passes_transport_override(self):
        from tests.test_orchestration import PipelineFixtureProvider
        from tests.test_benchmark_current import make_public_fixture
        from tests.test_repair_execution import PublicLoopRunner, AcceptPublicReview
        from contractedge.dataset.benchmark import file_sha
        from contractedge.orchestration.repair import repair_case
        owner = self
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); public, _ = make_public_fixture(root / 'benchmark')
            class Provider(PipelineFixtureProvider):
                execution_calls = 0
                def complete_json(self, request):
                    if request.prompt_version == 'topomen-source-execution.v1':
                        self.execution_calls += 1
                        if self.execution_calls == 1:
                            return ModelResponse('', {'finish_reason': 'length', 'usage': {'completion_tokens': 1024,
                                'completion_tokens_details': {'reasoning_tokens': 1024}}})
                        owner.assertEqual(request.thinking, 'disabled')
                        return ModelResponse(json.dumps({'edits': [{'change_id': 'C1', 'path': 'templates/serviceaccount.yaml',
                            'operation': 'MODIFY', 'old_text': 'name: default', 'new_text': 'name: selected'}]}), {})
                    self._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(
                        root / 'run/workspace/source/repo/templates/serviceaccount.yaml')}
                    return super().complete_json(request)
            provider = Provider()
            result = repair_case(public, root, 'CASE-1', root / 'run', provider, len,
                {**native_config(), 'provider': 'openai_compatible', 'max_context_tokens': 160000,
                 'max_total_tokens': 2000000, 'max_rounds': 2, 'execution_empty_reasoning_retry': 'disable_thinking'},
                public_runner=PublicLoopRunner(public), reviewer=AcceptPublicReview())
            self.assertEqual(result['status'], 'PUBLIC_REVIEW_COMPLETE', result['errors'])
            self.assertEqual(result['budget']['model_calls'], 4)
            self.assertEqual(provider.execution_calls, 2)
