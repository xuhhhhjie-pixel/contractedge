import tempfile,unittest
from pathlib import Path
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.entrypoints import probe_entrypoint_hints


class PublicProductionEntrypoints(unittest.TestCase):
    def test_registered_public_producer_can_anchor_python_without_a_probe(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'kopf/_kits').mkdir(parents=True);(p/'kopf/_kits/hierarchies.py').write_text('def adopt(child, owner):\n    return child\n')
            hints,paths,audit=probe_entrypoint_hints(compute_snapshot(p),[{'producer_manifest_sha256':'a'*64,
                'api_entrypoints':[{'package':'kopf','symbol':'adopt','language':'python'}]}])
            self.assertEqual(paths,['kopf/_kits/hierarchies.py']);self.assertIn('METHOD_PUBLIC_PRODUCER_API',hints[0]['origin_ref'])

    def test_go_method_declaration_is_an_entrypoint(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'pkg').mkdir();(p/'pkg/client.go').write_text('package pkg\nfunc (c *Client) Pull() {}\n')
            _,paths,_=probe_entrypoint_hints(compute_snapshot(p),[{'sha256':'a'*64,'api_entrypoints':[{'package':'pkg','symbol':'Pull'}]}])
            self.assertEqual(paths,['pkg/client.go'])

    def test_configuration_identifier_discovers_readonly_production_consumer(self):
        from contractedge.source import entrypoints
        self.assertTrue(hasattr(entrypoints,'configuration_consumer_hints'),'Configuration consumer lookup is absent')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'generator.yaml').write_text('lateInitialize: true\n');(p/'consumer.go').write_text('package p\nfunc Read() { apply(lateInitialize) }\n');(p/'consumer_test.go').write_text('lateInitialize')
            hints,paths,audit=entrypoints.configuration_consumer_hints(compute_snapshot(p),['generator.yaml'],'Handle `lateInitialize`')
            self.assertEqual(paths,['consumer.go']);self.assertIn('not a verified',audit['basis'])
