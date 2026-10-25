"""Same saved public failure, re-read current patched source for re-diagnosis."""
from dataclasses import asdict
import hashlib,json
from pathlib import Path
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.repair import validate_ready_trace
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.contracts.validation import parse_method_input
from contractedge.execution.topomen import build_execution_request
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound
BASE=Path('/home/xhj/contractedge-diagnostic-source-v046-v6-20261026/methods/RW-ALERTMANAGER-4705/RW-ALERTMANAGER-4705')
OUT=Path(__file__).parent/'v048-alert-revised-cross-stage';OUT.mkdir(exist_ok=False)
read=lambda p:json.loads(Path(p).read_text())
method=read(BASE/'rounds/1/front.method_input.json');config=read(BASE/'rounds/1/front.config.json')
feedback=read(BASE/'rounds/1/front.execution_feedback.json');calls=read(BASE/'model_calls.json')[7:10]
SOURCE=BASE/'workspace/source'/method['source_root'];before=compute_snapshot(SOURCE).snapshot_id
assert before==feedback['to_snapshot'] and before!=method['initial_snapshot_id']
class Provider:
 def __init__(self):self.count=0;self.requests=[]
 def complete_json(self,request):
  i=self.count;self.count+=1;self.requests.append(asdict(request));assert i<3
  return ModelResponse(calls[i]['response_content'],calls[i]['response_metadata'])
p=Provider();trace=run_front_half(method,SOURCE.parent,p,config,utf8_byte_token_bound,
 execution_feedback=feedback,public_observations=read(BASE/'rounds/1/front.public_observations.json')).to_wire()
(OUT/'front.trace.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n')
(OUT/'front.requests.json').write_text(json.dumps(p.requests,ensure_ascii=False,indent=2)+'\n')
assert trace['status']=='READY_PARTIAL',trace['errors']
packet,context=validate_ready_trace(trace,parse_method_input(method),SOURCE)
plan=translate_for_topomen(trace['changeset'],'contractedge.topomen_plan.v1',diagnosis=packet.raw,
 source_support=context.retrieval_config['necessary_source_support'],public_capabilities=context.retrieval_config['public_execution_capabilities'])
request=build_execution_request(plan,SOURCE,context.retrieval_config['public_execution_capabilities']['editable_policy'],
 utf8_byte_token_bound,{**config,'model':config['model']['model']},requirement=method['requirement_text'])
(OUT/'execution.request.json').write_text(json.dumps(asdict(request),ensure_ascii=False,indent=2)+'\n')
initial=json.loads(p.requests[0]['messages'][1]['content']);records=[r for r in initial['evidence_catalog'] if r['kind']=='SOURCE_SPAN']
changed=[r for r in records if r['locator'].get('path')=='dispatch/dispatch.go' and 'DrainReplay' in r['excerpt']]
# Check actual changed body bytes, not a guessed helper name.
run_records=[r for r in records if 'func (d *Dispatcher) Run(' in r['excerpt']]
assert run_records and all(r['snapshot_id']==before and r['excerpt'] in (SOURCE/'dispatch/dispatch.go').read_text() for r in run_records)
assert any('drainReplay' in r['excerpt'] or 'replay' in r['excerpt'].lower() for r in run_records)
assert any('go disp.Run(' in r['excerpt'] for r in records)
assert compute_snapshot(SOURCE).snapshot_id==before
summary=dict(status='REVISED_SNAPSHOT_DIAGNOSIS_PLAN_EXECUTION_REPLAY_PASS',snapshot_id=before,
 saved_responses=p.count,new_model_calls=0,new_grader_calls=0,source_unchanged=True,ready_validated=True,
 fresh_changed_source_ids=[r['id'] for r in run_records],formal_ready=False,
 interpretation='Current source re-read and input continuity; replayed repair choices are not new model results')
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
