import json,tempfile,unittest
from pathlib import Path
from contractedge.model.provider import ModelResponse
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_run_budget import run_fixture

class ContextErrorsProvider(PipelineFixtureProvider):
 def __init__(self,repeat=False):super().__init__();self.requests=[];self.repeat=repeat
 def complete_json(self,request):
  self.requests.append(request);response=super().complete_json(request)
  if not request.prompt_version.startswith('diagnosis.'):return response
  body=json.loads(response.content);n=sum(r.prompt_version.startswith('diagnosis.') for r in self.requests)
  if n==1:body['units'][0]['unexpected']=None
  if n==2:
   body['status']='EVIDENCE_GAP';body['evidence_gaps']=[{'id':'G1','summary':'Check optional template','locator_hint':{'path':'templates/optional-serviceaccount.yaml'}}]
  if n==3 or (n==4 and self.repeat):
   body['units'].append({**body['units'][0],'id':'U3'})
   body['expected_relations'][0]['source_unit_ids']=['U1','U3']
  return ModelResponse(json.dumps(body),{'finish_reason':'stop'})

class EvidenceContextCorrectionTests(unittest.TestCase):
 def run_case(self,p,**config):
  with tempfile.TemporaryDirectory() as td:return run_fixture(Path(td),p,lambda s:len(s.split()),**config)
 def test_fresh_evidence_context_gets_one_bounded_correction(self):
  p=ContextErrorsProvider();t=self.run_case(p,diagnosis_format_retry_per_context=True)
  self.assertEqual(t.status,'READY',t.errors)
  self.assertEqual([c['stage'] for c in t.model_calls],['DIAGNOSIS','DIAGNOSIS_FORMAT_RETRY','DIAGNOSIS_EXTENSION','DIAGNOSIS_FORMAT_RETRY','SEMANTIC_CHANGESET'])
  self.assertEqual(t.model_calls[3]['request_metadata']['correction_allowance'],'FRESH_EVIDENCE_CONTEXT_ONCE')
  self.assertIn('MULTI_ENDPOINT',p.requests[3].messages[-1]['content'])
 def test_default_retains_previous_shared_allowance(self):
  p=ContextErrorsProvider();t=self.run_case(p);self.assertEqual(t.status,'INVALID_OUTPUT');self.assertEqual(len(p.requests),3)
 def test_shared_call_cap_and_one_correction_cannot_be_bypassed(self):
  p=ContextErrorsProvider();t=self.run_case(p,diagnosis_format_retry_per_context=True,max_model_calls=3)
  self.assertEqual(t.status,'BUDGET_EXHAUSTED');self.assertEqual(len(p.requests),3)
  p=ContextErrorsProvider(repeat=True);t=self.run_case(p,diagnosis_format_retry_per_context=True)
  self.assertEqual(t.status,'INVALID_OUTPUT');self.assertEqual(len(p.requests),4)
