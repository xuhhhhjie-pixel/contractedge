"""Replay saved public .26 responses to the final planning request; no API."""
import sys,json,hashlib,urllib.request
from pathlib import Path
root=Path('/home/xhj/contractedge-system');out=root/'outputs/reliability-closure-20261020'
method_root=Path(sys.argv[1]);tag=sys.argv[2];sys.path[:0]=[str(method_root/'src'),str(root/'.topomen-deps')]
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.repair import select_observations
from contractedge.contracts.validation import parse_method_input
from contractedge.source.snapshot import compute_snapshot
from contractedge.model.provider import ModelResponse
from contractedge.tokenizers import utf8_byte_token_bound as count
read=lambda p:json.loads(p.read_text());origin=out/'runs/delivery-0.6.26-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
records=read(origin/'model_calls.json');front=read(origin/'rounds/0/front.trace.json');method=read(origin/'method_input.json');source=out/'ack-continuity-v0625-final/source';assert compute_snapshot(source/method['source_root']).snapshot_id==method['initial_snapshot_id']
cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');cfg.update(run_id=front['run_id'],model={'model':cfg['model'],'schema_in_prompt':True},public_execution_capabilities=front['run_context']['retrieval_config']['public_execution_capabilities'])
observations=select_observations(read(origin/'broken-public/observation.json')['observations'],method['requirement_text'],count,cfg['max_public_evidence_tokens'])
class Captured(BaseException):pass
class Provider:
 def __init__(self):self.requests=[]
 def complete_json(self,request):
  self.requests.append(request)
  if len(self.requests)==6:raise Captured()
  old=records[len(self.requests)-1];assert request.prompt_version==old['prompt_version']
  return ModelResponse(old['response_content'],old['response_metadata'])
provider=Provider()
def no_http(*a,**k):raise AssertionError('Network prohibited')
urllib.request.urlopen=no_http
try:run_front_half(parse_method_input(method),source,provider,cfg,count,public_observations=tuple(observations))
except Captured:pass
else:raise AssertionError('Final plan request not reached')
request=provider.requests[-1];body=json.loads(request.messages[1]['content']);diag=json.loads(provider.requests[3].messages[1]['content']);support={e['id']:e for e in body['read_only_source_support']};selected={e['id']:e for e in diag['evidence_catalog'] if e['kind']=='SOURCE_SPAN'};oldbody=json.loads(records[5]['messages'][1]['content']);oldids={e['id'] for e in oldbody['read_only_source_support']};owner=next(e for e in selected.values() if 'sdkapi *svcsdk.Client' in e['excerpt']);delivered=owner['id'] in support
assert compute_snapshot(source/method['source_root']).snapshot_id==method['initial_snapshot_id'];assert all(e['snapshot_id']==method['initial_snapshot_id'] and hashlib.sha256(e['excerpt'].encode()).hexdigest()==e['content_digest'] for e in support.values());inputs=sum(count(m['content']) for m in request.messages);assert inputs+request.max_output_tokens<=cfg['max_total_tokens']
result=dict(api_calls=0,saved_responses_replayed=5,captured_request=6,prompt_version=request.prompt_version,input_bound=inputs,output_reserved=request.max_output_tokens,source_unchanged=True,selected_current_source_count=len(selected),planning_support_count=len(support),all_selected_source_delivered=set(selected)<=set(support),owner_type_delivered=delivered,owner_was_previously_dropped=owner['id'] not in oldids,original_target_permissions_unchanged=body['existing_target_paths']==oldbody['existing_target_paths'],no_preexisting_repair_requirement='not required to pre-exist' in request.messages[0]['content'],scope='Saved responses isolate input continuity only; no new model outcome, helper implementation or behavioral success is inferred.')
(out/('SELECTED_SOURCE_REPLAY_'+tag+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
