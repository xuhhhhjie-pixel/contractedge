"""Offline old-response replay; capture the final request before its third call."""
from dataclasses import asdict
import hashlib,json
from pathlib import Path
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound

BASE=Path('/home/xhj/contractedge-diagnostic-source-v046-v6-20261026/methods/RW-ALERTMANAGER-4705/RW-ALERTMANAGER-4705')
SOURCE=Path('/home/xhj/contractedge-system/outputs/reliability-closure-20261020/public-source-alertmanager/alertmanager-e9278051f805b4b40b809155009a535fc1c58ddc')
OUTPUT=Path(__file__).parent/'original-alert-extension-capture-v3'
OUTPUT.mkdir(exist_ok=False)
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
calls=read(BASE/'model_calls.json');config=read(BASE/'rounds/0/front.config.json');method=read(BASE/'rounds/0/front.method_input.json')
initial=compute_snapshot(SOURCE).snapshot_id;assert initial==method['initial_snapshot_id']
class Captured(BaseException):pass
class Provider:
 def __init__(self):self.index=0;self.captured=None
 def complete_json(self,request):
  i=self.index;self.index+=1
  if i==2:
   self.captured=asdict(request)
   (OUTPUT/'request.json').write_text(json.dumps(self.captured,ensure_ascii=False,indent=2)+'\n')
   raise Captured()
  assert i<2
  return ModelResponse(calls[i]['response_content'],calls[i]['response_metadata'])
p=Provider()
try:
 trace=run_front_half(method,SOURCE.parent,p,config,utf8_byte_token_bound,
   execution_feedback=read(BASE/'rounds/0/front.execution_feedback.json'),
   public_observations=read(BASE/'rounds/0/front.public_observations.json'))
 (OUTPUT/'unexpected.trace.json').write_text(json.dumps(trace.to_wire(),ensure_ascii=False,indent=2)+'\n')
 raise AssertionError('Did not reach extension request: '+trace.status+str(trace.errors))
except Captured:pass
request=json.loads(p.captured['messages'][1]['content']);records=[r for r in request['evidence_catalog'] if r['kind']=='SOURCE_SPAN']
old=json.loads(calls[2]['messages'][1]['content']);oldrecords=[r for r in old['evidence_catalog'] if r['kind']=='SOURCE_SPAN']
checks={
 'reload_publication':'go disp.Run(',
 'initialization':'func (d *Dispatcher) Run(',
 'processing':'func (d *Dispatcher) processAlert(',
 'replay_subscription':'func (a *Alerts) Subscribe(',
 'groups_read':'func (d *Dispatcher) Groups(',
 'api_observer':'getAlertGroupsHandler',
}
rows=[]
for fact,token in checks.items():
 matches=[dict(id=r['id'],locator=r['locator'],snapshot_id=r['snapshot_id'],content_digest=r['content_digest']) for r in records if token in r['excerpt']]
 before=[r['id'] for r in oldrecords if token in r['excerpt']]
 rows.append(dict(fact=fact,old_request_source_ids=before,current_source_matches=matches))
assert all(r['current_source_matches'] for r in rows),rows
assert not rows[0]['old_request_source_ids']
assert len(request['candidate_set']['selected_ids'])<=12
assert compute_snapshot(SOURCE).snapshot_id==initial
summary=dict(status='CAPTURED_FINAL_EXTENSION_REQUEST_WITH_PRIOR_AND_NEW_ENDPOINTS',snapshot_id=initial,
 old_model_calls_sha256=sha(BASE/'model_calls.json'),captured_request_sha256=sha(OUTPUT/'request.json'),
 saved_responses=2,new_model_calls=0,new_grader_calls=0,selected_count=len(request['candidate_set']['selected_ids']),
 rows=rows,formal_ready=False,interpretation='Evidence delivery only; saved outputs are not new model or repair results')
(OUTPUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='rows'}))
