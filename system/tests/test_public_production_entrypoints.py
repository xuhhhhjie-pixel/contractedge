import tempfile,unittest
from pathlib import Path
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.scopes import resolve_scope
from contractedge.source.entrypoints import probe_entrypoint_hints


class PublicProductionEntrypoints(unittest.TestCase):
    def test_explicit_task_path_resolves_without_a_repository_name_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'src/gateway').mkdir(parents=True)
            (root / 'src/gateway/handler.go').write_text('package gateway\nfunc Dispatch() {}\n')
            (root / 'src/gateway/other.go').write_text('package gateway\nfunc Ignore() {}\n')
            snapshot = compute_snapshot(root)
            scope = resolve_scope(root, snapshot, [dict(kind='path',
                value='src/gateway/handler.go', raw_text='src/gateway/handler.go',
                origin_ref='public.scope')])
            self.assertEqual(scope.seed_paths, ('src/gateway/handler.go',))

    def test_registered_entrypoint_search_keeps_its_existing_file_budget(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'worker').mkdir()
            (root / 'worker/00.go').write_text('package worker\nfunc Dispatch() {}\n')
            for index in range(1, 40):
                (root / f'worker/{index:02d}.go').write_text('package worker\nfunc Other() {}\n')
            hints, paths, audit = probe_entrypoint_hints(compute_snapshot(root), [{
                'sha256': 'a' * 64, 'api_entrypoints': [{'package': 'worker', 'symbol': 'Dispatch'}],
            }], max_files=2)
            self.assertEqual(paths, ['worker/00.go'])
            self.assertEqual(audit['inspected_files'], 2)
            self.assertTrue(audit['truncated'])
            self.assertEqual([h['value'] for h in hints], paths)
            paths.append('explicit-task.go')
            self.assertEqual(audit['located_paths'], ['worker/00.go'])

    def test_structured_entrypoint_binds_the_requested_package_declaration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for package in ('gateway', 'unrelated'):
                (root / package).mkdir()
                (root / package / 'client.go').write_text(f'package {package}\nfunc Dispatch() {{}}\n')
            snapshot = compute_snapshot(root)
            hints, paths, _ = probe_entrypoint_hints(snapshot, [{
                'sha256': 'a' * 64,
                'api_entrypoints': [{'package': 'gateway', 'symbol': 'Dispatch'}],
            }])
            self.assertEqual(paths, ['gateway/client.go'])
            scope = resolve_scope(root, snapshot, hints)
            self.assertEqual(scope.seed_paths, ('gateway/client.go',))

    def test_explicit_task_focus_keeps_a_bounded_named_declaration_body(self):
        from contractedge.source.units import extract_units
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = ('package gateway\nfunc ProbeRequest() {\n' + '  noop()\n' * 180
                      + '  dispatchRequest()\n}\n')
            (root / 'gateway.go').write_text(source)
            snapshot = compute_snapshot(root)
            units = extract_units(snapshot, {'seed_paths': ('gateway.go',)},
                query='Inspect `ProbeRequest` before dispatch', max_units=4, max_lines=80,
                focused_paths=('gateway.go',))
            target = next(u for u in units if u.get('symbol') == 'ProbeRequest')
            self.assertIn('dispatchRequest()', target['text'])
            self.assertFalse(target['truncated'])
            self.assertLessEqual(target['locator']['end_line'] - target['locator']['start_line'] + 1, 768)
            self.assertNotIn('boundary_scope_critical', target)

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

    def test_configuration_uppercase_literal_does_not_match_another_type_name(self):
        from contractedge.source.entrypoints import configuration_consumer_hints
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'config.yaml').write_text('mode: PROBE\n')
            (root / 'consumer.go').write_text('package worker\nfunc Consume() { apply(PROBE) }\n')
            (root / 'other.go').write_text('package worker\nfunc Ignore() { apply(Probe); apply(PREVPROBE) }\n')
            _, paths, _ = configuration_consumer_hints(compute_snapshot(root), ['config.yaml'],
                                                       'Configure PROBE')
            self.assertEqual(paths, ['consumer.go'])
