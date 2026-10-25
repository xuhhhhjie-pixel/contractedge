"""Replay saved responses through Ready and build execution input; no edits."""
from dataclasses import asdict
import hashlib,json
from pathlib import Path
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.contracts.validation import parse_method_input
from contractedge.execution.topomen import build_execution_request
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import validate_ready_trace
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound
BASE=Path('/home/xhj/contractedge-diagnostic-source-v046-v6-20261026/methods/RW-ALERTMANAGER-4705/RW-ALERTMANAGER-4705')
SOURCE=Path('/home/xhj/contractedge-system/outputs/reliability-closure-20261020/public-source-alertmanager/alertmanager-e9278051f805b4b40b809155009a535fc1c58ddc')
OUT=Path(__file__).parent/'v048-alert-initial-cross-stage';OUT.mkdir(exist_ok=False)
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
calls=read(BASE/'model_calls.json');config=read(BASE/'rounds/0/front.config.json');method=read(BASE/'rounds/0/front.method_input.json')
class Provider:
 def __init__(self):self.index=0;self.requests=[]
 def complete_json(self,request):
  i=self.index;self.index+=1;self.requests.append(asdict(request));assert i<5
  return ModelResponse(calls[i]['response_content'],calls[i]['response_metadata'])
p=Provider();trace=run_front_half(method,SOURCE.parent,p,config,utf8_byte_token_bound,
 execution_feedback=read(BASE/'rounds/0/front.execution_feedback.json'),public_observations=read(BASE/'rounds/0/front.public_observations.json')).to_wire()
(OUT/'front.trace.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n')
(OUT/'front.requests.json').write_text(json.dumps(p.requests,ensure_ascii=False,indent=2)+'\n')
assert trace['status']=='READY_PARTIAL',trace['errors']
packet,context=validate_ready_trace(trace,parse_method_input(method),SOURCE)
support=context.retrieval_config['necessary_source_support']
plan=translate_for_topomen(trace['changeset'],'contractedge.topomen_plan.v1',diagnosis=packet.raw,
 source_support=support,public_capabilities=context.retrieval_config['public_execution_capabilities'])
execution_config={**config,'model':config['model']['model']}
request=build_execution_request(plan,SOURCE,context.retrieval_config['public_execution_capabilities']['editable_policy'],
 utf8_byte_token_bound,execution_config,requirement=method['requirement_text'])
(OUT/'execution.request.json').write_text(json.dumps(asdict(request),ensure_ascii=False,indent=2)+'\n')
def source_records(request):
 body=json.loads(request['messages'][1]['content']);return body.get('evidence_catalog',body.get('read_only_source_support',[]))
checks={key:bool(any('go disp.Run(' in r.get('excerpt','') for r in source_records(req)))
 for key,req in [('initial_diagnosis',p.requests[0]),('extension_diagnosis',p.requests[2]),('plan',p.requests[3]),('plan_correction',p.requests[4]),('execution',asdict(request))]}
assert all(checks.values()),checks
assert compute_snapshot(SOURCE).snapshot_id==method['initial_snapshot_id']
summary=dict(status='SAVED_RESPONSE_REPLAY_READY_AND_EXECUTION_DELIVERY_PASS',checks=checks,
 selected_count=len(trace['candidate_set']['selected_ids']),saved_responses=p.index,new_model_calls=0,new_grader_calls=0,
 source_unchanged=True,original_calls_sha256=sha(BASE/'model_calls.json'),execution_request_sha256=sha(OUT/'execution.request.json'),
 formal_ready=False,interpretation='Input continuity and Ready validation; old model choices are not a new repair success')
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
