import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.source.snapshot import compute_snapshot


class PublicRuntimeWrapperTests(unittest.TestCase):
    def test_full_render_index_case_identity_and_source_copy_survive_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / 'tools'; tools.mkdir()
            for name in ['prepare_case.py', 'collect_patch.py', 'launch_public_case.py']:
                (tools / name).write_text('# trusted test tool')
            public = root / 'public'; public.mkdir()
            workspace = root / 'workspace'; workspace.mkdir()
            source = workspace / 'source'; source.mkdir(); (source / 'main.go').write_text('package main\n')
            inputs = workspace / 'inputs'; inputs.mkdir(); (inputs / 'settings.txt').write_text('public input')
            (workspace / 'workspace.json').write_text(json.dumps({'case_id': 'CASE-1', 'source_root': str(source),
                'inputs_root': str(inputs), 'source_sha256': 'a' * 64}))
            before = compute_snapshot(source).snapshot_id
            def public_process(argv, logs, timeout):
                runtime = Path(argv[argv.index('--workspace') + 1])
                clone = json.loads((runtime / 'workspace.json').read_text())
                (Path(clone['source_root']) / 'generated-by-build.go').write_text('build side effect')
                output = Path(argv[argv.index('--output') + 1]); output.mkdir()
                logs.mkdir(); (logs / 'tool.stdout').write_text('execution completed')
                (logs / 'tool.stderr').write_text('')
                (output / 'trigger.stdout').write_text('---\n# Source: fixture/templates/job.yaml\nkind: Job\nmetadata:\n  name: expected-job\nspec:\n  template:\n    spec:\n      nodeSelector:\n        zone: test\n')
                (output / 'control.stdout').write_text('---\n# Source: fixture/templates/config.yaml\nkind: ConfigMap\nmetadata:\n  name: ordinary\n')
                (output / 'spoof.json').write_text(json.dumps({'evidence_kind':'PUBLIC_BEHAVIOR','status':'PASS'}))
                return {'returncode': 0, 'timed_out': False}
            runner = BenchmarkPublicRunner(public, tools, lock_root=root / 'locks', query='Job nodeSelector')
            with patch('contractedge.execution.public.run_tool', side_effect=public_process), patch(
                'contractedge.execution.public.cleanup_owned_containers', return_value={'status': 'OWNED_CONTAINERS_CLOSED', 'removed': []}):
                result = runner.observe(workspace, root / 'observed')
            self.assertEqual(result['case_id'], 'CASE-1')
            self.assertEqual(result['status'], 'EXECUTION_PASS')
            self.assertEqual(compute_snapshot(source).snapshot_id, before)
            self.assertFalse((source / 'generated-by-build.go').exists())
            control = json.loads((root / 'observed/raw/control.render-index.json').read_text())
            self.assertEqual(control['resource_counts']['Job'], 0)
            self.assertTrue(control['all_documents_parsed'])
            trigger = json.loads((root / 'observed/raw/trigger.render-index.json').read_text())
            self.assertEqual(trigger['pod_specs'][0]['pod_spec_fields']['nodeSelector'], {'zone': 'test'})
            pod_records = [o for o in result['observations'] if '.pod-spec-' in o['locator']['path']]
            self.assertEqual(len(pod_records), 1)
            pod = json.loads(pod_records[0]['excerpt'])
            self.assertEqual(pod['resource']['name'], 'expected-job')
            self.assertEqual(pod['resource']['pod_spec_fields']['nodeSelector'], {'zone': 'test'})
            self.assertEqual(pod_records[0].get('evidence_kind'), 'PUBLIC_STRUCTURE')
            self.assertEqual(pod_records[0].get('scenario'), 'trigger')
            self.assertEqual(pod_records[0].get('resource_identity', {}).get('name'), 'expected-job')
            spoof = next(o for o in result['observations'] if o['locator']['path'] == 'raw/spoof.json')
            self.assertEqual(spoof['evidence_kind'], 'EXECUTION_ONLY')
            logs = [o for o in result['observations'] if o['locator']['path'] == 'launcher/tool.stdout']
            self.assertEqual(logs[0].get('evidence_kind'), 'EXECUTION_ONLY')
            for observation in result['observations']:
                self.assertEqual(observation['snapshot_id'], before)
                self.assertEqual(observation['content_digest'], hashlib.sha256(observation['excerpt'].encode()).hexdigest())
