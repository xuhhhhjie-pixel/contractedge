"""Exercise recorder hooks through the actual public repair production entry."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from contractedge.development import public_demo
from contractedge.contracts.validation import canonical_digest
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.recorder import RunRecorder
from contractedge.orchestration.repair import repair_case


def read(path):
    return json.loads(Path(path).read_bytes())


def stages(root, stage):
    return [read(path) for path in sorted((root / 'journal/stages' / stage).glob('*/*.json'))]


class StageRecorderProductionTests(unittest.TestCase):
    def recorder(self, root):
        identity = read(root / 'journal/run.identity.json')
        return RunRecorder(root, run_id=identity['run_id'], input_digest=identity['input_digest'])

    def verify_every_reference(self, root):
        recorder = self.recorder(root)
        identities = {}
        for path in (root / 'journal/stages').glob('*/*/*.json'):
            stage = read(path)
            for field in ('input_refs', 'output_refs', 'projection_refs'):
                for ref in stage[field]:
                    recorder.verify_artifact(ref)
            for ref in stage['call_refs']:
                recorder.verify_call(ref)
                self.assertIsNotNone(ref['ledger_segment_digest'])
                key = (ref['segment_index'], ref['absolute_call_index'])
                value = (ref['request_digest'], ref['transport_digest'])
                self.assertEqual(identities.setdefault(key, value), value)
        return identities

    def test_real_full_repair_links_final_requests_execute_review_feedback_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'run'
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'candidate_guard_enabled': True}))
            result = public_demo.run_demo('config-binding', output, provider_config=config)
            self.assertEqual(result['status'], 'PUBLIC_REVIEW_COMPLETE', result)
            root = output / 'repair'
            required = {'begin', 'dispatch', 'diagnosis', 'planning', 'front', 'front_delivery',
                        'consumer_projection', 'execute_attempt', 'execute', 'observe', 'review',
                        'feedback', 'candidate_decision', 'selection'}
            self.assertTrue(required.issubset({p.name for p in (root / 'journal/stages').iterdir()}))
            identities = self.verify_every_reference(root)
            actual = read(root / 'model_calls.json')
            self.assertEqual(set(identities), {(0, i) for i in range(len(actual))})
            self.assertEqual(len(actual), 4)
            budget = read(root / 'repair.trace.json')['budget']
            self.assertEqual(budget['model_calls'], len(actual))
            self.assertEqual(budget['reserved_tokens'], sum(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] for c in actual))
            diagnosis = stages(root, 'diagnosis')[-1]
            plan = stages(root, 'planning')[-1]
            self.assertEqual([r['absolute_call_index'] for r in diagnosis['call_refs']], [0])
            self.assertEqual([r['absolute_call_index'] for r in plan['call_refs']], [1])
            final_diagnosis = read(root / diagnosis['output_refs'][0]['relative_path'])
            plan_call = actual[plan['call_refs'][0]['absolute_call_index']]
            final_request = json.loads(plan_call['request']['messages'][1]['content'])
            self.assertEqual(canonical_digest(final_request['diagnosis_packet']), canonical_digest(final_diagnosis))
            self.assertEqual(plan['call_refs'][0]['request_digest'], canonical_digest(plan_call['request']))
            selected = stages(root, 'selection')[-1]
            self.assertEqual(selected['snapshot_id'], read(root / 'repair.trace.json')['final_snapshot_id'])
            self.assertTrue(any(ref['relative_path'].endswith('/candidate.patch') for ref in selected['output_refs']))

    def test_format_correction_is_separate_call_final_diagnosis_uses_retry(self):
        factory = public_demo._provider
        class MalformedOnce:
            def __init__(self, inner):
                self.inner, self.failed = inner, False
            def complete_json(self, request):
                if request.prompt_version.startswith('diagnosis.') and not self.failed:
                    self.failed = True
                    return ModelResponse('{"malformed_diagnosis":true}', {'zero_cost': True})
                return self.inner.complete_json(request)
        def provider(config, case, runner):
            inner, counter = factory(config, case, runner)
            return MalformedOnce(inner), counter
        with tempfile.TemporaryDirectory() as tmp, patch.object(public_demo, '_provider', provider):
            output = Path(tmp) / 'run'
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'max_format_retries': 1}))
            summary = public_demo.run_demo('reload-state', output, provider_config=config)
            self.assertEqual(summary['status'], 'PUBLIC_REVIEW_COMPLETE', summary)
            root = output / 'repair'
            self.verify_every_reference(root)
            actual = read(root / 'model_calls.json')
            self.assertEqual(len(actual), 5)
            self.assertEqual(stages(root, 'diagnosis')[-1]['call_refs'][0]['absolute_call_index'], 1)
            self.assertEqual(stages(root, 'planning')[-1]['call_refs'][0]['absolute_call_index'], 2)
            self.assertEqual([r['call_refs'][0]['absolute_call_index'] for r in stages(root, 'dispatch')], [0,1,2])
            self.assertTrue(all(r['status'] == 'RESPONSE_RECEIVED' for r in stages(root, 'dispatch')))
            self.assertNotEqual(actual[0]['request_digest'], actual[1]['request_digest'])

    def test_compatibility_views_cannot_rewrite_old_receipts_or_request_seals(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'run'
            public_demo.run_demo('reload-state', output)
            root = output / 'repair'
            before = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / 'journal').rglob('*.json')}
            for name in ('model_calls.json', 'rounds/0/front.trace.json', 'rounds/0/feedback.json', 'repair.trace.json'):
                (root / name).write_text('{"tampered_compatibility_view":true}\n')
            self.verify_every_reference(root)
            self.assertEqual(before, {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / 'journal').rglob('*.json')})

    def test_second_round_reuses_historical_plan_without_relabelling_or_fake_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'run'
            summary = public_demo.run_demo('two-consumers', output)
            self.assertEqual(summary['status'], 'PUBLIC_REVIEW_COMPLETE', summary)
            self.assertEqual(summary['rounds'], 2)
            root = output / 'repair'
            self.verify_every_reference(root)
            self.assertEqual(len(read(root / 'model_calls.json')), 6)
            self.assertEqual(len(stages(root, 'diagnosis')), 1)
            self.assertEqual(len(stages(root, 'planning')), 1)
            second_execution = next(r for r in stages(root, 'execute') if r['attempt_id'] == 'round-1')
            self.assertEqual([r['absolute_call_index'] for r in second_execution['call_refs']], [4])
            second_review = next(r for r in stages(root, 'review') if r['attempt_id'] == 'round-1')
            self.assertEqual([r['absolute_call_index'] for r in second_review['call_refs']], [5])
            second = next(r for r in stages(root, 'consumer_projection') if r['attempt_id'] == 'round-1')
            original = read(root / 'rounds/1/front.trace.json')['snapshot_id']
            reference = next(r for r in second['input_refs'] if r['relative_path'].endswith('/front.trace.json'))
            self.assertEqual(reference['snapshot_id'], original)
            self.assertNotEqual(second['snapshot_id'], original)

    def test_ready_resume_has_new_absolute_segment_and_retains_legacy_missing_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved, continued = Path(tmp)/'saved', Path(tmp)/'continued'
            public_demo.run_demo('reload-state', saved, save_ready=True)
            trace = read(saved / 'ready.trace.json')
            for call in trace['model_calls']:
                for key in ('request', 'request_digest', 'transport_request', 'transport_body_sha256'):
                    call.pop(key, None)
            config = read(saved / 'run.config.json')
            public = saved / 'frozen-public/public'
            runner = public_demo.PublicExampleRunner(public, 'reload-state')
            provider = public_demo.SyntheticExampleProvider('reload-state', runner)
            report = repair_case(public, saved / 'frozen-public', 'reload-state', continued, provider,
                public_demo.count_tokens, config, initial_trace=trace, public_runner=runner)
            self.assertEqual(report['status'], 'PUBLIC_REVIEW_COMPLETE', report['errors'])
            identities = self.verify_every_reference(continued)
            prior = len(trace['model_calls'])
            self.assertTrue(all((0,i) in identities for i in range(prior)))
            actual_new = read(continued / 'model_calls.json')
            self.assertTrue(all((1,prior+i) in identities for i in range(len(actual_new))))
            imported = stages(continued, 'front_import')[-1]
            self.assertEqual(imported['status'], 'LEGACY_RECORDED')
            self.assertTrue(all(ref['request_digest'] is None for ref in imported['call_refs']))
            old = read(continued / imported['call_refs'][0]['ledger_segment_path'])
            self.assertEqual(old, trace['model_calls'])
            self.assertTrue(all('request' not in call for call in old))

    def test_unknown_provider_response_is_recorded_once_without_fake_output_or_retry(self):
        class Unknown:
            count = 0
            def complete_json(self, request):
                self.count += 1
                raise TimeoutError('fixture unknown dispatch')
        provider = Unknown()
        with tempfile.TemporaryDirectory() as tmp, patch.object(public_demo, '_provider', lambda *args: (provider, public_demo.count_tokens)):
            output = Path(tmp) / 'run'
            summary = public_demo.run_demo('config-binding', output)
            root = output / 'repair'
            self.assertEqual(provider.count, 1)
            self.assertEqual(summary['status'], 'INFRASTRUCTURE_ERROR')
            self.assertIn('fixture unknown dispatch', ' '.join(summary['errors']))
            self.verify_every_reference(root)
            calls = read(root / 'model_calls.json')
            self.assertEqual(len(calls), 1)
            self.assertNotIn('response_content', calls[0])
            self.assertEqual(stages(root, 'dispatch')[0]['status'], 'ERROR')
            self.assertEqual(stages(root, 'diagnosis')[0]['output_refs'], [])
            self.assertEqual(stages(root, 'begin')[0]['call_refs'], [])
            self.assertFalse((root / 'journal/stages/execute').exists())

    def test_recording_failure_preserves_original_unknown_dispatch_exception(self):
        original = RunRecorder.update_ledger
        def broken(recorder, calls, **kwargs):
            if any(call.get('error') for call in calls):
                raise OSError('fixture journal unavailable after provider failure')
            return original(recorder, calls, **kwargs)
        class Unknown:
            def complete_json(self, request):
                raise TimeoutError('fixture original provider failure')
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(public_demo, '_provider', lambda *args: (Unknown(), public_demo.count_tokens)), \
                patch.object(RunRecorder, 'update_ledger', broken):
            output = Path(tmp) / 'run'
            summary = public_demo.run_demo('config-binding', output)
            self.assertEqual(summary['status'], 'INFRASTRUCTURE_ERROR')
            self.assertIn('fixture original provider failure', ' '.join(summary['errors']))
            report = read(output / 'repair/repair.trace.json')
            self.assertTrue(any('journal unavailable' in error for error in report['recording_errors']))

    def test_prior_validation_exception_cannot_hide_correction_reservation_write_failure(self):
        original = RunRecorder.update_ledger
        calls = []
        def broken(recorder, ledger, **kwargs):
            if len(ledger) == 2:
                raise OSError('fixture correction reservation cannot persist')
            return original(recorder, ledger, **kwargs)
        class Malformed:
            def complete_json(self, request):
                calls.append(request)
                return ModelResponse('{"malformed_diagnosis":true}', {'zero_cost': True})
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(public_demo, '_provider', lambda *args: (Malformed(), public_demo.count_tokens)), \
                patch.object(RunRecorder, 'update_ledger', broken):
            output, config = Path(tmp)/'run', Path(tmp)/'config.json'
            config.write_text(json.dumps({'max_format_retries':1}))
            summary = public_demo.run_demo('reload-state', output, provider_config=config)
            self.assertEqual(len(calls), 1, 'Correction must stop before provider dispatch without durable reservation')
            self.assertEqual(summary['status'], 'INFRASTRUCTURE_ERROR')
            self.assertIn('correction reservation cannot persist', ' '.join(summary['errors']))


if __name__ == '__main__':
    unittest.main()
