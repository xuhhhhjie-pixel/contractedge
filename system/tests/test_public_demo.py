"""Public developer examples exercise actual processes and the production loop."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from contractedge.development import public_demo
from contractedge.dataset.benchmark import file_sha

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'scripts/run_public_demo.py'


def read(path):
    return json.loads(path.read_text())


class PublicDemoTests(unittest.TestCase):
    def run_cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(ENTRY), *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, timeout=90)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_discovery_and_all_three_real_public_repairs(self):
        result = self.run_cli('--list')
        self.assertEqual(set(json.loads(result.stdout)), {'config-binding', 'reload-state', 'two-consumers'})
        with tempfile.TemporaryDirectory() as tmp:
            for case in ['config-binding', 'reload-state', 'two-consumers']:
                with self.subTest(case=case):
                    output = Path(tmp) / case
                    self.run_cli('--case', case, '--output', output)
                    summary = read(output / 'summary.json')
                    self.assertEqual(summary['status'], 'PUBLIC_REVIEW_COMPLETE')
                    self.assertTrue(summary['zero_cost'])
                    report = read(output / 'repair/repair.trace.json')
                    self.assertTrue((output / 'repair/candidate.patch').read_text().startswith('diff --git'))
                    self.assertNotEqual(report['initial_snapshot_id'], report['final_snapshot_id'])
                    self.assertEqual(report['last_feedback']['pending_obligations'], [])
                    calls = read(output / 'combined.model_calls.json')
                    self.assertEqual(len(calls), summary['model_calls'])
                    self.assertTrue(all('request' in c and 'response_content' in c for c in calls))
                    self.assertTrue((output / 'repair/rounds/0/public/process.json').is_file())
                    if case == 'config-binding':
                        execution = read(output / 'repair/rounds/0/execution.json')
                        self.assertEqual(execution['backend']['agent_class'], 'topomen.collaboration.base_agent.DefaultAgent')
                    if case == 'two-consumers':
                        self.assertEqual(summary['rounds'], 2)
                        first = read(output / 'repair/rounds/0/feedback.json')
                        self.assertEqual(first['route'], 'EXECUTE')
                        self.assertEqual({c['id'] for c in first['public_check_results'] if c['status']=='PASS'}, {'P2', 'P3'})
                        self.assertTrue(first['pending_obligations'])
                    self.run_cli('--case', case, '--output', output, success=False)

    def test_fresh_process_ready_continuation_preserves_complete_ledger_and_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved, continued = Path(tmp) / 'saved', Path(tmp) / 'continued'
            self.run_cli('--case', 'config-binding', '--save-ready', '--output', saved)
            checkpoint = read(saved / 'ready.checkpoint.json')
            old_bytes = (saved / 'model_calls.json').read_bytes()
            old_calls = read(saved / 'model_calls.json')
            self.assertEqual(checkpoint['status'], 'READY')
            self.assertTrue(all('request' in c and 'response_content' in c for c in old_calls))
            self.run_cli('--resume-ready', saved, '--output', continued)
            self.assertEqual((saved / 'model_calls.json').read_bytes(), old_bytes)
            combined = read(continued / 'combined.model_calls.json')
            self.assertEqual(combined[:len(old_calls)], old_calls)
            self.assertEqual(read(continued / 'initial.model_calls.json'), old_calls)
            report = read(continued / 'repair/repair.trace.json')
            self.assertEqual(report['status'], 'PUBLIC_REVIEW_COMPLETE', report['errors'])
            self.assertEqual(report['budget']['resumed_model_calls'], len(old_calls))
            reserved = sum(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] for c in combined)
            self.assertEqual(report['budget']['reserved_tokens'], reserved)
            self.assertEqual(report['budget']['max_calls'], checkpoint['budget']['max_calls'])
            self.assertGreaterEqual(report['budget']['elapsed_seconds'], checkpoint['budget']['elapsed_seconds'])

    def test_checkpoint_tamper_and_provider_override_are_rejected_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = Path(tmp) / 'saved'
            self.run_cli('--case', 'reload-state', '--save-ready', '--output', saved)
            config = Path(tmp) / 'override.json'
            config.write_text('{}')
            self.run_cli('--resume-ready', saved, '--provider-config', config,
                         '--output', Path(tmp) / 'override', success=False)
            target = saved / 'frozen-public/public/tasks/reload-state/runtime-inputs.json'
            target.write_text(target.read_text() + ' ')
            self.run_cli('--resume-ready', saved, '--output', Path(tmp) / 'tamper', success=False)
            self.assertFalse((Path(tmp) / 'tamper/repair').exists())

    def test_public_preservation_control_detects_actual_candidate_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'run'
            self.run_cli('--case', 'config-binding', '--output', output)
            workspace = output / 'repair/workspace'
            record = read(workspace / 'workspace.json')
            source = Path(record['source_root']) / 'app.py'
            source.write_text('import os\nassert not os.environ.get("CONTRACTEDGE_DEMO_TEST_SENTINEL")\n' + source.read_text())
            runner = public_demo.PublicExampleRunner(output / 'frozen-public/public', 'config-binding')
            with patch.dict(os.environ, {'CONTRACTEDGE_DEMO_TEST_SENTINEL': 'fictional-environment-test'}):
                clean = runner.observe(workspace, output / 'repair/environment-public')
            self.assertEqual(clean['status'], 'EXECUTION_PASS')
            source.write_text(source.read_text().replace('"stable"', '"regressed"'))
            observation = runner.observe(workspace, output / 'repair/regression-public')
            stable = next(json.loads(r['excerpt']) for r in observation['observations'] if r['id']=='OBS-stable')
            self.assertEqual(stable['status'], 'FAIL')

    def test_unknown_dispatch_is_never_automatically_resent(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = Path(tmp) / 'saved'
            self.run_cli('--case', 'reload-state', '--save-ready', '--output', saved)
            calls = read(saved / 'model_calls.json')
            calls[-1].pop('response_content')
            trace = read(saved / 'ready.trace.json')
            trace['model_calls'] = calls
            public_demo.write_json(saved / 'model_calls.json', calls)
            public_demo.write_json(saved / 'ready.trace.json', trace)
            checkpoint = read(saved / 'ready.checkpoint.json')
            for name in ['model_calls.json', 'ready.trace.json']:
                checkpoint['files'][name] = file_sha(saved / name)
            public_demo.write_json(saved / 'ready.checkpoint.json', checkpoint)
            result = self.run_cli('--resume-ready', saved, '--output', Path(tmp) / 'continued', success=False)
            self.assertIn('Unknown/incomplete dispatch', result.stderr)
            self.assertFalse((Path(tmp) / 'continued/repair').exists())

    def test_ready_never_recreates_a_missing_money_allowance(self):
        from contractedge.model.money import MoneyBudget
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            money = {'path': str(root / 'money.json'), 'limit_cny': 1, 'model': 'fixture',
                     'input_cny_per_million': 1, 'output_cny_per_million': 1}
            config = {'provider': 'openai_compatible', 'model': 'fixture', 'max_total_tokens': 1500000,
                      'max_elapsed_seconds': 180, 'max_model_calls': 16, 'max_rounds': 2, 'money_budget': money}
            public_demo.write_json(root / 'provider.json', config)
            def offline_provider(settings, case, runner):
                MoneyBudget(**settings['money_budget'])
                return public_demo.SyntheticExampleProvider(case, runner), public_demo.count_tokens
            with patch.object(public_demo, '_provider', offline_provider):
                public_demo.run_demo('reload-state', root / 'saved', save_ready=True,
                                     provider_config=root / 'provider.json')
            with patch.object(public_demo, '_provider') as factory:
                for output, options in [(root / 'direct', {'case': 'reload-state', 'provider_config': root / 'provider.json'}),
                                        (root / 'live-continuation', {'case': None, 'resume_ready': root / 'saved'})]:
                    with self.assertRaisesRegex(ValueError, 'requires a sandbox observer'):
                        public_demo.run_demo(output=output, **options)
                factory.assert_not_called()
            self.assertFalse((root / 'saved/continuation.claim.json').exists())
            (root / 'money.json').unlink()
            with patch.object(public_demo, '_provider') as factory:
                with self.assertRaisesRegex(ValueError, 'money ledger is missing or changed'):
                    public_demo.run_demo(None, root / 'continued', resume_ready=root / 'saved')
                factory.assert_not_called()

    def test_timeout_consumes_ready_once_and_retains_unknown_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_cli('--case', 'reload-state', '--save-ready', '--output', root / 'saved')
            class TimeoutProvider:
                def complete_json(self, request):
                    raise TimeoutError('fictional dispatched request timeout')
            with patch.object(public_demo, '_provider', return_value=(TimeoutProvider(), public_demo.count_tokens)):
                summary = public_demo.run_demo(None, root / 'first', resume_ready=root / 'saved')
            self.assertEqual(summary['status'], 'INFRASTRUCTURE_ERROR')
            calls = read(root / 'first/combined.model_calls.json')
            self.assertEqual(len(calls), 3)
            self.assertIn('TimeoutError', calls[-1]['error'])
            self.assertNotIn('response_content', calls[-1])
            with patch.object(public_demo, '_provider') as factory:
                with self.assertRaisesRegex(ValueError, 'already claimed'):
                    public_demo.run_demo(None, root / 'second', resume_ready=root / 'saved')
                factory.assert_not_called()
            claim = read(root / 'saved/continuation.claim.json')
            self.assertEqual(claim['output'], str(root / 'first'))
            self.assertEqual(read(root / 'first/combined.model_calls.json'), calls)

    def test_concurrent_ready_claim_admits_exactly_one_continuation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_cli('--case', 'reload-state', '--save-ready', '--output', root / 'saved')
            processes = [subprocess.Popen([sys.executable, str(ENTRY), '--resume-ready', str(root / 'saved'),
                '--output', str(root / name)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for name in ['one', 'two']]
            outputs = [p.communicate(timeout=90) for p in processes]
            self.assertEqual(sorted(p.returncode for p in processes), [0, 2], outputs)
            claim = read(root / 'saved/continuation.claim.json')
            self.assertEqual(read(Path(claim['output']) / 'summary.json')['status'], 'PUBLIC_REVIEW_COMPLETE')
            self.assertEqual(sum((root / name / 'repair').exists() for name in ['one', 'two']), 1)

    def test_api_config_rejects_null_money_and_credential_urls_before_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {'provider': 'openai_compatible', 'model': 'fixture', 'base_url': 'https://api.example.invalid',
                'api_key_env': 'PUBLIC_DEMO_TEST_KEY', 'max_total_tokens': 1500000, 'max_elapsed_seconds': 180,
                'max_model_calls': 16, 'max_rounds': 2, 'money_budget': {'path': str(root / 'money.json'),
                    'limit_cny': 1, 'model': 'fixture', 'input_cny_per_million': 1, 'output_cny_per_million': 1}}
            invalid = [[1], {**config, 'money_budget': None}, {**config, 'money_budget': {}},
                {**config, 'base_url': 'https://fictional-user:fictional-password@api.example.invalid'},
                {**config, 'base_url': 'https://api.example.invalid?api_key=fictional-value'},
                {**config, 'base_url': 'https://api.example.invalid#fictional-value'},
                {**config, 'api_key_env': 'sk-fictional-value'}]
            with patch.object(public_demo, '_provider') as factory:
                for index, value in enumerate(invalid):
                    with self.subTest(index=index):
                        public_demo.write_json(root / 'provider.json', value)
                        output = root / str(index)
                        with self.assertRaises(ValueError):
                            public_demo.run_demo('reload-state', output, save_ready=True, provider_config=root / 'provider.json')
                        self.assertFalse((output / 'run.config.json').exists())
                factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
