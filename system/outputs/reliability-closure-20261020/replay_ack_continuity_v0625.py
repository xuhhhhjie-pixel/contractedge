"""Replay two actual .24 responses and inspect the current planning-extension boundary; API=0."""
import sys,json,hashlib,urllib.request
from pathlib import Path
root=Path('/home/xhj/contractedge-system');out=root/'outputs/reliability-closure-20261020'
method_root=Path(sys.argv[1]);tag=sys.argv[2];sys.path[:0]=[str(method_root/'src'),str(root/'.topomen-deps')]
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.repair import select_observations
from contractedge.contracts.validation import parse_method_input
from contractedge.dataset.archive import safe_extract
from contractedge.source.snapshot import compute_snapshot
from contractedge.model.provider import ModelResponse
from contractedge.tokenizers import utf8_byte_token_bound as count
origin=out/'runs/delivery-0.6.24-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
read=lambda p:json.loads(p.read_text())
records=read(origin/'model_calls.json');front=read(origin/'rounds/0/front.trace.json');method=read(origin/'method_input.json')
dest=out/('ack-continuity-'+tag);dest.mkdir(exist_ok=False)
archive=root.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public'/method['archive_path']
snapshot=safe_extract(archive,dest/'source');assert snapshot.snapshot_id==method['initial_snapshot_id']
cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');cfg.update(run_id=front['run_id'],model={'model':cfg['model'],'schema_in_prompt':True},public_execution_capabilities=front['run_context']['retrieval_config']['public_execution_capabilities'])
observations=select_observations(read(origin/'broken-public/observation.json')['observations'],method['requirement_text'],count,cfg['max_public_evidence_tokens'])
class Captured(BaseException):pass
class Provider:
 def __init__(self):self.requests=[]
 def complete_json(self,request):
  self.requests.append(request)
  if len(self.requests)==3:raise Captured()
  old=records[len(self.requests)-1];assert request.prompt_version==old['prompt_version']
  return ModelResponse(old['response_content'],old['response_metadata'])
provider=Provider()
def no_http(*a,**k):raise AssertionError('Network prohibited')
urllib.request.urlopen=no_http
try:run_front_half(parse_method_input(method),dest/'source',provider,cfg,count,public_observations=tuple(observations))
except Captured:pass
else:raise AssertionError('Third diagnosis request was not reached')
request=provider.requests[-1];body=json.loads(request.messages[1]['content']);oldbody=json.loads(records[2]['messages'][1]['content'])
view=body['diagnosis_continuity'];assert view['claims_for_reassessment'];assert body['method_input']==oldbody['method_input'];assert view['snapshot_id']==method['initial_snapshot_id'];catalog={e['id']:e for e in body['evidence_catalog']};assert all(ref in catalog for c in view['claims_for_reassessment'] for ref in c['current_evidence_refs']);assert any('any tags you' in e.get('excerpt','') and 'are ignored' in e.get('excerpt','') for e in body['evidence_catalog'])
assert request.max_output_tokens==records[2]['output_tokens_reserved']
assert request.output_schema['required']==records[2]['output_schema']['required']
assert compute_snapshot(Path(snapshot.source_root)).snapshot_id==method['initial_snapshot_id']
result=dict(api_calls=0,saved_responses_replayed=2,captured_request=3,prompt_version=request.prompt_version,
 thinking=request.thinking,policy=request.metadata.get('planning_generation_policy'),
 exact_original_requirement=True,required_output_contract_unchanged=True,prior_claims=len(view['claims_for_reassessment']),unavailable_prior_refs=[ref for c in view['claims_for_reassessment'] for ref in c['unavailable_prior_evidence_refs']],api_contract_still_delivered=True,current_source_unchanged=True,
 input_bound=sum(count(m['content']) for m in request.messages),output_reserved=request.max_output_tokens,
 max_model_calls=cfg['max_model_calls'],modes=[r.thinking for r in provider.requests],
 extension_source_paths=sorted({e.get('locator',{}).get('path','') for e in body['evidence_catalog'] if e.get('kind')=='SOURCE_SPAN'}),
 scope='Actual .24 failure replay through original diagnosis, plan and real bounded retrieval into extension request. Prior claims are review tasks, not current proof. No third response or repair success invented.')
(dest/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
