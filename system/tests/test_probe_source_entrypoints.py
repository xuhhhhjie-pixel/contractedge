import tempfile,unittest
from pathlib import Path
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units

class ProbeEntryEvidenceTests(unittest.TestCase):
 def test_focused_public_entry_keeps_relevant_sibling_bodies_in_pool(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'api.go').write_text('package p\nfunc mutator() { Select(configPath); Run(configPath) }\nfunc validator() { Select(configPath); Run(configPath) }\n')
   docs=[]
   for i in range(12):
    p=root/f'doc{i}.txt';p.write_text('public documentation for configPath\n');docs.append(p.name)
   snap=compute_snapshot(root)
   units=extract_units(snap,{'seed_paths':docs+['api.go']},query='Use `configPath`',max_units=8,focused_paths=['api.go'])
   self.assertTrue({'mutator','validator'}<={u.get('symbol') for u in units})
   self.assertLessEqual(len(units),8)
 def test_locator_uses_only_registered_package_api_and_reports_limits(self):
  from contractedge.source.entrypoints import probe_entrypoint_hints
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'pkg').mkdir();(root/'other').mkdir()
   (root/'pkg/api.go').write_text('package p\ntype Renderer struct {}\n')
   (root/'other/api.go').write_text('package q\ntype Renderer struct {}\n')
   snap=compute_snapshot(root);spec=[{'sha256':'a'*64,'api_entrypoints':[{'package':'pkg','symbol':'Renderer'}]}]
   hints,paths,audit=probe_entrypoint_hints(snap,spec)
   self.assertEqual(paths,['pkg/api.go']);self.assertIn('METHOD_PROBE_PUBLIC_API',hints[0]['origin_ref'])
   self.assertEqual(probe_entrypoint_hints(snap,spec,max_files=0)[1],[])
   self.assertTrue(probe_entrypoint_hints(snap,spec,max_files=0)[2]['truncated'])
   with self.assertRaises(ValueError):probe_entrypoint_hints(snap,[{'sha256':'x','api_entrypoints':[{'package':'../private','symbol':'Renderer'}]}])
