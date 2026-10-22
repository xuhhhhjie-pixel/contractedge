import tempfile,unittest
from pathlib import Path
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_gap_extension_units
from contractedge.source.parsers import parse_source_units

class ProductionPathDiscoveryTests(unittest.TestCase):
 def test_connected_file_query_reaches_indirect_branch_without_claiming_gap_proof(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'loader.go').write_text('package p\nfunc NewRunner() { LoadConfig() }\n')
   (root/'executor.go').write_text('''package p
func validator() { NewRunner(); SelectInput(); refresh(configPath) }
func chain() { NewRunner() }
func mutator() { chain(); SelectInput(); refresh(configPath) }
''')
   (root/'unrelated.go').write_text('package p\nfunc unrelated() { Use(configPath) }\n')
   snap=compute_snapshot(root);units=parse_source_units('loader.go',(root/'loader.go').read_text(),snap.snapshot_id)
   found,audit=extract_gap_extension_units(snap,['loader.go'],units,[{'id':'G1','summary':'config ownership','locator_hint':{'path':'loader.go','search_term':'configPath'}}])
   by={u.get('symbol'):u for u in found}
   self.assertIn('mutator',by)
   self.assertNotIn('unrelated',by)
   self.assertEqual(by['mutator']['evidence_gap_refs'],[])
   self.assertEqual(by['mutator']['association_status'],'LEXICAL_CONNECTED_FILE_QUERY; not a verified causal edge or edit grant')

class ProductionBranchPlanTests(unittest.TestCase):
 def test_shared_consumers_require_explicit_review_and_cannot_silently_disappear(self):
  from contractedge.retrieval.frontier import production_frontier
  from contractedge.changeset.validate import validate_semantic_changeset
  from contractedge.contracts.validation import validate_diagnosis,parse_method_input
  from tests.test_contracts import valid_diagnosis,valid_method_input,valid_plan
  text='package p\nfunc A() { Select(configPath); Run(configPath) }\nfunc B() { Select(configPath); Run(configPath) }\n'
  units=parse_source_units('consumer.go',text,'s');frontier=production_frontier(units,'Use `configPath` for the owning function')
  self.assertEqual({r['symbol'] for r in frontier},{'A','B'})
  packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
  result=validate_semantic_changeset(valid_plan(),packet,None,frontier=frontier)
  self.assertIn('PRODUCTION_BRANCH_UNACCOUNTED',[e.code for e in result.errors])
 def test_unresolved_branch_must_point_to_a_real_open_obligation(self):
  from contractedge.changeset.validate import validate_semantic_changeset
  from contractedge.contracts.validation import validate_diagnosis,parse_method_input
  from tests.test_contracts import valid_diagnosis,valid_method_input,valid_plan
  packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None);p=valid_plan()
  f=[{'source_evidence_id':'SRC-X','path':'consumer.go','locator':{'start_line':2,'end_line':4}}]
  p['production_path_review']=[{'source_evidence_id':'SRC-X','disposition':'UNRESOLVED','rationale':'Need other branch', 'change_refs':[],'open_obligation_refs':['fake']}]
  r=validate_semantic_changeset(p,packet,None,frontier=f)
  self.assertIn('PRODUCTION_BRANCH_OPEN_INVALID',[e.code for e in r.errors])
 def test_preserve_or_out_of_scope_review_remains_a_claim_not_forced_edit(self):
  from contractedge.changeset.validate import validate_semantic_changeset
  from contractedge.contracts.validation import validate_diagnosis,parse_method_input
  from tests.test_contracts import valid_diagnosis,valid_method_input,valid_plan
  packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None);p=valid_plan()
  frontier=[{'source_evidence_id':'SRC-X','path':'consumer.go','locator':{'start_line':2,'end_line':4}}]
  p['production_path_review']=[{'source_evidence_id':'SRC-X','disposition':'OUT_OF_SCOPE','rationale':'This supplied branch serves a different object; lexical overlap alone is insufficient.', 'change_refs':[],'open_obligation_refs':[]}]
  self.assertTrue(validate_semantic_changeset(p,packet,None,frontier=frontier).valid)
  p['production_path_review'][0].update(disposition='MODIFY',change_refs=['C1'])
  r=validate_semantic_changeset(p,packet,None,frontier=frontier)
  self.assertIn('PRODUCTION_BRANCH_CHANGE_INVALID',[e.code for e in r.errors])
