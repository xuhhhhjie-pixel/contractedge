import tempfile,unittest,hashlib
from pathlib import Path
from contractedge.execution.probe_specs import select_probes


class ProbeProfilesV2(unittest.TestCase):
    def test_kpt_v2_has_distinct_retention_and_transformation_checks(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'pkg/lib/kptops').mkdir(parents=True);(p/'pkg/fn').mkdir(parents=True)
            (p/'pkg/lib/kptops/render.go').touch();(p/'pkg/fn/eval.go').touch()
            specs=select_probes('configPath functionConfig owning later render',p,['kpt-configpath-v2'])
            self.assertEqual(len(specs),1);checks=specs[0]['checks']
            self.assertIn('mutator.config_retained',checks);self.assertIn('mutator.workload_transformed',checks)
            self.assertIn('validator.workload_retained',checks)
            self.assertNotIn('mutator.final_resources',checks)
            self.assertEqual(specs[0]['sha256'],hashlib.sha256(specs[0]['program'].encode()).hexdigest())

    def test_alertmanager_profile_is_frozen_to_public_api_program(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'cmd/alertmanager').mkdir(parents=True);(p/'cmd/alertmanager/main.go').touch()
            specs=select_probes('After a valid Alertmanager configuration reload, active alert groups remain visible through the groups API.',p,['alertmanager-reload-v1'])
            self.assertEqual(len(specs),1)
            self.assertIn('groups.visible_during_reload',specs[0]['checks'])
            self.assertIn('sampling',specs[0]['boundary'])
            self.assertIn('/api/v2/alerts/groups',specs[0]['program'])
            self.assertIn('PROBE_ERROR',specs[0]['program'])

    def test_atomic_probe_records_do_not_repeat_whole_state_or_lose_failed_values(self):
        import json
        from contractedge.execution.probes import behavior_records
        source=Path('outputs/final-optimization-20261020/calibration-R3-KPT-4673/probe/report.json')
        report=json.loads(source.read_text())
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'kpt-configpath-v2').mkdir()
            records=behavior_records(report,root)
            self.assertEqual(len(records),14)
            for p,m in records.items():
                self.assertLess(len(p.read_bytes()),2000)
            failure=next(json.loads(p.read_text()) for p,m in records.items() if m['behavior_check']['id'].endswith('mutator.owner_excludes_config'))
            self.assertIn('settings',failure['observed']['ordinary_inputs'])
