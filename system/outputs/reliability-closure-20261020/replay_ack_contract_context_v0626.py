"""Capture actual initial request from current source; no synthetic model answer."""
import sys,json,urllib.request
from pathlib import Path
root=Path('/home/xhj/contractedge-system');out=root/'outputs/reliability-closure-20261020';sys.path[:0]=[str(root/'.superpowers/sdd/2026-10-21-cross-stage-repair-closure/candidate/src'),str(root/'.topomen-deps')]
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.repair import select_observations
from contractedge.contracts.validation import parse_method_input
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound as count
read=lambda p:json.loads(p.read_text());origin=out/'runs/delivery-0.6.25-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985';method=read(origin/'method_input.json');front=read(origin/'rounds/0/front.trace.json');source=out/'ack-continuity-v0625-final/source';assert compute_snapshot(source/method['source_root']).snapshot_id==method['initial_snapshot_id']
cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');cfg.update(run_id=front['run_id'],model={'model':cfg['model'],'schema_in_prompt':True},public_execution_capabilities=front['run_context']['retrieval_config']['public_execution_capabilities'])
observations=select_observations(read(origin/'broken-public/observation.json')['observations'],method['requirement_text'],count,cfg['max_public_evidence_tokens'])
class Captured(BaseException):pass
class Provider:
 request=None
 def complete_json(self,request):self.request=request;raise Captured()
provider=Provider()
def no_http(*a,**k):raise AssertionError('Network prohibited')
urllib.request.urlopen=no_http
try:run_front_half(parse_method_input(method),source,provider,cfg,count,public_observations=tuple(observations))
except Captured:pass
else:raise AssertionError('Request not reached')
request=provider.request;body=json.loads(request.messages[1]['content']);windows=body['source_field_contract_contexts'];api=next(w for w in windows if w['locator']['path']=='apis/v1alpha1/metric_alarm.go' and w['field_name']=='Tags');assert 'are ignored' in api['excerpt'] and 'TagResource' in api['excerpt'];assert any('func (rm *resourceManager) newUpdateRequestPayload' in e['excerpt'] for e in body['evidence_catalog']);assert compute_snapshot(source/method['source_root']).snapshot_id==method['initial_snapshot_id'];inputs=sum(count(m['content']) for m in request.messages);assert inputs<=120000 and inputs+request.max_output_tokens<=220000
summary=dict(api_calls=0,input_bound=inputs,output_reserved=request.max_output_tokens,snapshot_unchanged=True,initial_actual_request_captured=True,producer_body_still_delivered=True,field_contexts=len(windows),api_update_contract=api,gap_delivery=request.metadata.get('gap_delivery'),scope='Actual current-snapshot source, public Broken observations and full initial request fitter. No model response, repair result or independent score invented. Separate generic integration verifies Ready and execution delivery.')
(out/'ACK_CONTRACT_CONTEXT_REPLAY_v0626.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items() if k not in ['api_update_contract','gap_delivery']}))
