"""Read-only executor input projection using a saved accepted plan; API=0."""
import sys,runpy,json
from pathlib import Path
root=Path('/home/xhj/contractedge-system');out=root/'outputs/reliability-closure-20261020'
sys.argv=['replay',str(root/'.superpowers/sdd/2026-10-21-cross-stage-repair-closure/candidate'),'native-v0627']
v=runpy.run_path(str(out/'replay_selected_source_v0627.py'))
from contractedge.execution.topomen import build_execution_request,PlanRejected
from contractedge.source.snapshot import compute_snapshot
plan=json.loads((out/'runs/delivery-0.6.25-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985/rounds/0/plan.json').read_text())
plan['coordination_context']['source_support']=v['body']['read_only_source_support']
source=v['source']/v['method']['source_root'];cfg=v['cfg'];policy=v['front']['run_context']['retrieval_config']['public_execution_capabilities']['editable_policy'];results=[]
for cap in [24000,48000]:
 try:
  request=build_execution_request(plan,source,policy,v['count'],{**cfg,'max_execution_support_bytes':cap},requirement=v['method']['requirement_text'])
 except PlanRejected as exc:results.append(dict(support_cap=cap,status='REJECTED_BEFORE_API',reason=str(exc)));continue
 body=json.loads(request.messages[1]['content']);records=body['read_only_source_support'];owner=any('sdkapi *svcsdk.Client' in e['excerpt'] for e in records);size=sum(v['count'](m['content']) for m in request.messages)+request.max_output_tokens
 assert owner and size<=cfg['max_context_tokens'];assert compute_snapshot(source).snapshot_id==v['method']['initial_snapshot_id']
 results.append(dict(support_cap=cap,status='DELIVERED',request_plus_output_bound=size,owner_type_delivered=owner,non_target_source_bytes=sum(len(e['excerpt'].encode()) for e in records),target_source_exact=True,api_calls=0))
summary=dict(results=results,scope='Saved public .25 accepted plan is used solely as an existing executor-input carrier. Its repair strategy previously failed; no correctness or new .27 model plan is inferred. Source support comes from the actual current .26 failure replay, not a grader or successful patch.')
(out/'NATIVE_SOURCE_CAPACITY_v0627.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
