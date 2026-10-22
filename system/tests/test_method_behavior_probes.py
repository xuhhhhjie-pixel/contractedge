import hashlib,json,tempfile,unittest
from pathlib import Path
from contractedge.execution.support import support_error
from contractedge.execution.probe_specs import KPT_CONFIGPATH_GO,KPT_CHECKS,select_probes
from contractedge.execution.probes import behavior_records

class MethodProbeGateTests(unittest.TestCase):
 def observation(self,check,status):
  return {'id':check,'evidence_kind':'PUBLIC_BEHAVIOR','scenario':'method-kpt-configpath-v1',
          'behavior_check':{'id':check,'status':status,'probe_sha256':'a'*64}}
 def test_failed_behavior_probe_cannot_be_cited_as_pass(self):
  o=self.observation('p:owner','FAIL');ob={'verification_kind':'PUBLIC_BEHAVIOR','behavior_check_refs':['p:owner']}
  claim={'status':'PASS','evidence':[{'observation_id':'p:owner'}]}
  self.assertEqual(support_error(ob,claim,[o]),'METHOD_BEHAVIOR_RESULT_UNSUPPORTED')
  self.assertIsNone(support_error(ob,{**claim,'status':'FAIL'},[o]))
 def test_required_probe_checks_cannot_be_replaced_with_an_unrelated_pass(self):
  o=self.observation('p:control','PASS');ob={'verification_kind':'PUBLIC_BEHAVIOR','behavior_check_refs':['p:owner']}
  self.assertEqual(support_error(ob,{'status':'PASS','evidence':[{'observation_id':'p:control'}]},[o]),'METHOD_BEHAVIOR_CHECKS_INCOMPLETE')
 def test_method_probe_requires_explicit_bindings_and_all_bound_results(self):
  a=self.observation('p:a','PASS');b=self.observation('p:b','FAIL');c={'status':'PASS','evidence':[{'observation_id':'p:a'},{'observation_id':'p:b'}]}
  self.assertEqual(support_error({'verification_kind':'PUBLIC_BEHAVIOR'},c,[a,b]),'METHOD_BEHAVIOR_BINDINGS_MISSING')
  self.assertEqual(support_error({'behavior_check_refs':['p:a','p:b']},c,[a,b]),'METHOD_BEHAVIOR_RESULT_UNSUPPORTED')
 def test_unregistered_probe_output_is_never_promoted(self):
  with tempfile.TemporaryDirectory() as td:
   result={'status':'COMPLETE','snapshot_id':'s','probes':[{'probe_id':'fake','status':'COMPLETE','checks':[{'id':'x','status':'PASS'}]}]}
   self.assertEqual(behavior_records(result,Path(td)),{})
 def test_unknown_profile_is_rejected_and_nonmatching_requirement_is_not_enabled(self):
  with tempfile.TemporaryDirectory() as td:
   self.assertEqual(select_probes('Render a pod',Path(td),['kpt-configpath-v1']),[])
   with self.assertRaises(ValueError):select_probes('any',Path(td),['model-invented-command'])

class MethodProbeProducerTests(unittest.TestCase):
 def test_only_registered_complete_checks_are_typed_and_snapshot_bound(self):
  with tempfile.TemporaryDirectory() as td:
   out=Path(td);(out/'kpt-configpath-v1').mkdir()
   report={'status':'COMPLETE','snapshot_id':'current','probes':[{'probe_id':'kpt-configpath-v1','status':'COMPLETE',
     'probe_sha256':hashlib.sha256(KPT_CONFIGPATH_GO.encode()).hexdigest(),'scenario':'method-kpt-configpath-v1',
     'boundary':'local runtime stand-ins only','checks':[{'id':key,'status':'PASS','statement':statement,'observed':{}} for key,statement in KPT_CHECKS.items()]}]}
   records=behavior_records(report,out);self.assertEqual(len(records),len(KPT_CHECKS))
   for path,meta in records.items():
    self.assertEqual(meta['evidence_kind'],'PUBLIC_BEHAVIOR')
    self.assertEqual(json.loads(path.read_text())['snapshot_id'],'current')
   report['probes'][0]['checks'].pop()
   self.assertEqual(behavior_records(report,out),{})

class MethodProbeFeedbackTests(unittest.TestCase):
 def test_binding_survives_retained_and_pending_feedback(self):
  from contractedge.orchestration.feedback import review_plan_with_retained
  prior={'retained_constraints':[{'statement':'preserve owner isolation','source_snapshot':'s0','verification_kind':'PUBLIC_BEHAVIOR',
    'behavior_check_refs':['kpt-configpath-v1:mutator.owner_excludes_config']}],
    'pending_obligations':[{'id':'OB1','statement':'restore another branch','verification_kind':'PUBLIC_BEHAVIOR',
    'behavior_check_refs':['kpt-configpath-v1:validator.owner_excludes_config']}], 'to_snapshot':'s0'}
  plan={'postconditions':[],'preserve_constraints':[],'open_obligations':[]}
  result=review_plan_with_retained(plan,prior)
  self.assertEqual(result['preserve_constraints'][0]['behavior_check_refs'],prior['retained_constraints'][0]['behavior_check_refs'])
  self.assertEqual(result['open_obligations'][0]['behavior_check_refs'],prior['pending_obligations'][0]['behavior_check_refs'])

class MethodProbeReviewIntegrationTests(unittest.TestCase):
 def test_real_failed_probe_is_not_accepted_as_pass_by_review_or_feedback(self):
  from tests.test_observation_capability_gate import record,claim,plan,feedback
  from contractedge.execution.review import ModelPublicReviewer
  from contractedge.model.provider import FixtureProvider
  obs=record('{"id":"owner","status":"FAIL","ordinary_inputs":["settings","workload"]}','PUBLIC_BEHAVIOR','method-kpt-configpath-v1')
  obs['behavior_check']={'id':'kpt-configpath-v1:mutator.owner_excludes_config','status':'FAIL','probe_sha256':'a'*64}
  p=plan('PUBLIC_BEHAVIOR',behavior_check_refs=[obs['behavior_check']['id']],verification_scope={'scenario':'method-kpt-configpath-v1'})
  c=claim('"status":"FAIL"')
  reviewer=ModelPublicReviewer(FixtureProvider({'checks':[c],'failure_kind':'UNKNOWN','rationale':'Incorrect positive'}),len,{'model':'fixture','max_context_tokens':30000})
  reviewed=reviewer.review('Exclude owning config resource',p,{'status':'EXECUTION_PASS','observations':[obs]})
  self.assertEqual(reviewed['decision'],'UNKNOWN')
  self.assertEqual(reviewed['checks'][0]['submitted_status'],'PASS')
  f=feedback(p,c,obs);self.assertEqual(f['semantic_outcome'],'UNKNOWN');self.assertEqual(len(f['pending_obligations']),1)
  from contractedge.orchestration.feedback import make_feedback
  c['status']='FAIL'
  f=make_feedback(case_id='CASE',run_id='run',input_digest='a'*64,round_id=0,from_snapshot='S0',to_snapshot='S1',
    patch_digest='b'*64,execution_status='EXECUTION_PASS',observations=[obs],plan=p,
    review={'decision':'FAIL','checks':[c],'failure_kind':'EXECUTION'})
  self.assertEqual(f['semantic_outcome'],'PUBLIC_REVIEW_FAIL')
  self.assertEqual(f['route'],'EXECUTE')
