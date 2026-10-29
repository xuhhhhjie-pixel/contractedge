"""Actual saved-response replay through candidate selection, with no model HTTP.

Keep pre-generation rejection in the outer resource ledger; project only received
model responses into the semantic READY ledger. Lower inner caps by the omitted
reservation, so no call/token allowance is replenished. Cached runtime facts are
exact trusted observations for these same source snapshots, not new executions.
"""
from dataclasses import asdict
import hashlib,json,sys,time
from pathlib import Path
S=Path('/home/xhj/contractedge-alert-closure-20261028/system');sys.path[:0]=[str(S/'src'),str(S/'scripts')]
from contractedge.cli import _build_provider
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.orchestration.repair import _repair_case_impl
from contractedge.model.provider import ModelResponse
from contractedge.source.snapshot import compute_snapshot
from verify_development_snapshot import verify_snapshot
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
R=Path('/home/xhj/contractedge-ack402-continuation-v068-20261028');OLD=R/'repair/R4-ACK-CLOUDWATCH-2985';ST=next((R/'repair').glob('.isolated-*'))
OUT=S/'.superpowers/sdd/2026-10-28-behavior-plan-closure/ack-selection-no-http'
assert not OUT.exists(),'Retain old replay artifacts; inspect before rerunning'
assert verify_snapshot(S,read(S/'docs/development/quote-binding-v068-20261028.snapshot.json'),require_current_bindings=True)['status']=='PASS'
config=read(ST/'original-config.json');full=read(R/'combined-model-calls.json');paid=read(ST/'parent-model-calls.json');assert len(full)==4 and len(paid)==1 and 'response_content' in paid[0]
assert full[2].get('error')=='HTTPError: HTTP Error 402: Payment Required' and 'response_content' not in full[2]
failed=full[2]['prompt_tokens_reserved']+full[2]['output_tokens_reserved'];ready=read(R/'ready.trace.json');semantic=[full[0],full[1],full[3]];ready={**ready,'model_calls':semantic}
config={**config,'money_budget':None,'runtime_preflight':None,'max_model_calls':config['max_model_calls']-1,'max_total_tokens':config['max_total_tokens']-failed}
wire,counter=_build_provider(config)
class Replay:
 def __init__(self):self.calls=0
 def request_overhead(self,r):return wire.request_overhead(r)
 def request_metadata(self,r):return wire.request_metadata(r)
 def complete_json(self,r):
  assert self.calls==0,'No unseen model request is admitted in a recorded-response replay'
  self.calls+=1;actual=json.loads(json.dumps(asdict(r)));expected=paid[0]['request']
  if actual!=expected:
   OUT.mkdir(parents=True,exist_ok=True);(OUT/'request-diff.json').write_text(json.dumps({'actual':actual,'expected':expected},indent=2)+'\n');raise ValueError('Recorded execution request differs; replay refused')
  return ModelResponse(paid[0]['response_content'],paid[0]['response_metadata'])
provider=Replay();bench=Path(read(S/'CURRENT_STATUS.json')['dataset']['benchmark_root'])
base=BenchmarkPublicRunner(bench/'public',bench/'tools',lock_root=OUT.parent/'offline-replay-locks',timeout_seconds=config['public_timeout_seconds'])
# Bind cache to trusted original parent RPC results, not a model-authored claim.
cache={}
for p in (ST/'method-journal').glob('*.json'):
 d=read(p)
 if d.get('status')=='DONE' and d.get('op')=='public.observe':
  report=d['result'];cache[report['snapshot_id']]=report
assert set(cache)=={ready['snapshot_id'],read(OLD/'rounds/0/candidate-decision.json')['trial_snapshot']}
class CachedPublic:
 requires_public_profile=True
 def prepare(self,*a):return base.prepare(*a)
 def observe(self,workspace,output):
  sid=compute_snapshot(read(Path(workspace)/'workspace.json')['source_root']).snapshot_id
  if sid not in cache:raise ValueError('No trusted observation for current source snapshot')
  output=Path(output);output.mkdir(parents=True,exist_ok=False)
  (output/'observation.json').write_text(json.dumps(cache[sid],indent=2)+'\n')
  return cache[sid]
 def export(self,*a):return base.export(*a)
prior_elapsed=read(ST/'receipt.json')['parent_budget']['elapsed_seconds'];prefix_elapsed=max(c['elapsed_seconds'] for c in semantic)
result=_repair_case_impl(bench/'public',bench/'tools','R4-ACK-CLOUDWATCH-2985',OUT,provider,counter,config,initial_trace=ready,public_runner=CachedPublic(),_runtime_preflight=read(ST/'runtime-preflight.json'),_runtime_started=time.monotonic()-(prior_elapsed-prefix_elapsed))
expected=read(OLD/'rounds/0/patch.json');candidate=result.get('candidate') or {}
report={'status':'PASS_EXACT_REPLAY_AND_SELECTION' if result['status']=='PUBLIC_REVIEW_COMPLETE' and candidate.get('patch_sha256')==expected['patch_sha256'] else 'FAIL_REPLAY','method_status':result['status'],'errors':result.get('errors'),'model_http_calls_added':0,'recorded_response_replayed':provider.calls,'same_exact_execution_request':provider.calls==1 and not (OUT/'request-diff.json').exists(),'same_model_patch':candidate.get('patch_sha256')==expected['patch_sha256'],'patch_bytes':candidate.get('patch_bytes'),'semantic_call_count':result['budget']['model_calls'],'failed_transport_count_kept_outside':1,'outer_resource_calls':result['budget']['model_calls']+1,'semantic_reserved_tokens':result['budget']['reserved_tokens'],'failed_transport_tokens_kept_outside':failed,'outer_reserved_tokens':result['budget']['reserved_tokens']+failed,'all_prior_active_time_retained':result['budget']['elapsed_seconds']>=prior_elapsed,'public_semantic':(result.get('last_feedback') or {}).get('semantic_outcome'),'scope':'Offline actual source/consumer/selection replay of exactly received response and trusted saved public observations. Not a new runtime execution or independent repair score. No fabricated response for HTTP402.','formal_ready':False}
p=S/'outputs/alert-repeat-closure-20261028/V068_ACK_SELECTION_NO_HTTP_REPLAY.json';p.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
