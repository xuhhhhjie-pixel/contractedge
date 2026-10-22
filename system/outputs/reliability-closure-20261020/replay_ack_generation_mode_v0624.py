"""Replay four saved public responses and capture the real fifth request; API=0."""
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
origin=out/'runs/delivery-0.6.23-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
read=lambda p:json.loads(p.read_text())
records=read(origin/'model_calls.json');front=read(origin/'rounds/0/front.trace.json');method=read(origin/'method_input.json')
dest=out/('ack-generation-mode-'+tag);dest.mkdir(exist_ok=False)
archive=root.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public'/method['archive_path']
snapshot=safe_extract(archive,dest/'source');assert snapshot.snapshot_id==method['initial_snapshot_id']
cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');cfg.update(run_id=front['run_id'],model={'model':cfg['model'],'schema_in_prompt':True},public_execution_capabilities=front['run_context']['retrieval_config']['public_execution_capabilities'])
observations=select_observations(read(origin/'broken-public/observation.json')['observations'],method['requirement_text'],count,cfg['max_public_evidence_tokens'])
class Captured(BaseException):pass
class Provider:
 def __init__(self):self.requests=[]
 def complete_json(self,request):
  self.requests.append(request)
  if len(self.requests)==5:raise Captured()
  old=records[len(self.requests)-1];assert request.prompt_version==old['prompt_version']
  return ModelResponse(old['response_content'],old['response_metadata'])
provider=Provider()
def no_http(*a,**k):raise AssertionError('Network prohibited')
urllib.request.urlopen=no_http
try:run_front_half(parse_method_input(method),dest/'source',provider,cfg,count,public_observations=tuple(observations))
except Captured:pass
else:raise AssertionError('Fifth diagnosis request was not reached')
request=provider.requests[-1];body=json.loads(request.messages[1]['content']);oldbody=json.loads(records[4]['messages'][1]['content'])
assert body==oldbody,'Method context changed beyond the transport setting'
assert request.max_output_tokens==records[4]['output_tokens_reserved']
assert request.output_schema==records[4]['output_schema']
assert compute_snapshot(Path(snapshot.source_root)).snapshot_id==method['initial_snapshot_id']
result=dict(api_calls=0,saved_responses_replayed=4,captured_request=5,prompt_version=request.prompt_version,
 thinking=request.thinking,policy=request.metadata.get('planning_generation_policy'),
 exact_original_extension_body=True,exact_original_schema=True,current_source_unchanged=True,
 input_bound=sum(count(m['content']) for m in request.messages),output_reserved=request.max_output_tokens,
 max_model_calls=cfg['max_model_calls'],modes=[r.thinking for r in provider.requests],
 extension_source_paths=sorted({e.get('locator',{}).get('path','') for e in body['evidence_catalog'] if e.get('kind')=='SOURCE_SPAN'}),
 scope='Actual saved failure through real initial diagnosis, schema correction, plan, bounded search and extension request. No invented fifth response or new repair result.')
(dest/'summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
