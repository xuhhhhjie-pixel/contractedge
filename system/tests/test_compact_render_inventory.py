import json,shutil,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.orchestration.repair import select_observations


class CompactRenderInventory(unittest.TestCase):
    def replay(self, returncode=0, stdout=None):
        raw=Path('outputs/deepseek-final-single-v3-20261020/cases/RW-CAND-130/rounds/0/public/raw').resolve()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);tools=root/'tools';tools.mkdir()
            for name in ['prepare_case.py','collect_patch.py','launch_public_case.py']:(tools/name).touch()
            public=root/'public';public.mkdir();workspace=root/'workspace';source=workspace/'source';source.mkdir(parents=True)
            (source/'source.txt').write_text('public adapter replay');inputs=workspace/'inputs';inputs.mkdir()
            (workspace/'workspace.json').write_text(json.dumps({'case_id':'RW-CAND-130','source_root':str(source),'inputs_root':str(inputs)}))
            def replay(argv,logs,timeout):
                logs.mkdir();(logs/'tool.stdout').write_text('');(logs/'tool.stderr').write_text('')
                output=Path(argv[argv.index('--output')+1]);output.mkdir()
                for p in raw.glob('*.stdout'):shutil.copy2(p,output/p.name)
                if stdout is not None:(output/'control.stdout').write_text(stdout)
                return {'returncode':returncode,'timed_out':False}
            runner=BenchmarkPublicRunner(public,tools,lock_root=root/'locks',query='Both target Jobs and control')
            with patch('contractedge.execution.public.run_tool',replay),patch('contractedge.execution.public.cleanup_owned_containers',return_value={'status':'OWNED_CONTAINERS_CLOSED','removed':[]}):
                report=runner.observe(workspace,root/'out')
            return report

    def test_failed_partial_render_cannot_prove_absence(self):
        report=self.replay(returncode=1)
        self.assertFalse(any('.render-kind-' in o['locator']['path'] for o in report['observations']))

    def test_complete_control_job_inventory_survives_existing_review_budget(self):
            report=self.replay()
            selected=select_observations(report['observations'],'Both target Jobs and control nodeSelector',len,36000)
            inventories=[o for o in selected if o.get('scenario')=='control' and '.render-kind-Job.json' in o['locator']['path']]
            self.assertEqual(len(inventories),1,'Complete control inventory must fit alongside trigger objects')
            value=json.loads(inventories[0]['excerpt']);self.assertTrue(value['complete_inventory'])
            names={r['name'] for r in value['resources']}
            self.assertNotIn('ct-vela-core-cluster-gateway-tls-secret-create',names)
            self.assertNotIn('ct-vela-core-cluster-gateway-tls-secret-patch',names)

    def test_list_wrapped_job_is_not_absence(self):
        report=self.replay(stdout='# Source: chart/jobs.yaml\napiVersion: v1\nkind: List\nitems:\n- apiVersion: batch/v1\n  kind: Job\n  metadata:\n    name: forbidden-job\n')
        inventories=[o for o in report['observations'] if o.get('scenario')=='control' and '.render-kind-Job.json' in o['locator']['path']]
        self.assertEqual(len(inventories),1)
        self.assertEqual(json.loads(inventories[0]['excerpt'])['resources'][0]['name'],'forbidden-job')

    def test_incomplete_list_cannot_prove_absence(self):
        for body in ['kind: List\n', 'kind: List\nitems: [null]\n', '42\n']:
            with self.subTest(body=body):
                report=self.replay(stdout='# Source: chart/jobs.yaml\n'+body)
                self.assertFalse(any(o.get('scenario')=='control' and '.render-kind-' in o['locator']['path'] for o in report['observations']))
