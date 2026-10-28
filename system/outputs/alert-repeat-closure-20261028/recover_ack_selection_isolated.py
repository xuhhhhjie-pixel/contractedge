"""Recover the exact received ACK patch in isolation, with zero new HTTP calls.

Only an explicit official pre-generation HTTP402 is removed from the semantic
READY ledger. It stays in the unmodified outer financial/resource ledger. Inner
caps are reduced by that reservation. Neither an empty response nor a PASS is
invented. Original trusted observations may only replay on their exact snapshot.
"""
import argparse,fcntl,hashlib,json,os,shutil,signal,sys
from dataclasses import asdict
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
S=Path('/home/xhj/contractedge-alert-closure-20261028/system');H=Path(__file__).resolve().parent;sys.path[:0]=[str(S/'src'),str(S/'scripts')]
import run_isolated_method as harness
from run_fixed_panel import exclusive
from score_isolated_method import run as score
from verify_development_snapshot import verify_snapshot
from isolated_paths import SafeRoot
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
R=Path('/home/xhj/contractedge-ack402-continuation-v068-20261028');OLD=R/'repair/R4-ACK-CLOUDWATCH-2985';OLDSTATE=next((R/'repair').glob('.isolated-*'));OUT=R/'selection-replay';PANEL=Path('/home/xhj/contractedge-fixed46-official-v068-20261028/panel.json');CONFIG=S/'configs/development-deepseek-official-budget200.json'
STAMP=lambda:datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()

def prepare():
 assert verify_snapshot(S,read(S/'docs/development/quote-binding-v068-20261028.snapshot.json'),require_current_bindings=True)['status']=='PASS'
 replay=read(S/'outputs/alert-repeat-closure-20261028/V068_ACK_SELECTION_NO_HTTP_REPLAY.json');assert replay['status']=='PASS_EXACT_REPLAY_AND_SELECTION' and replay['same_exact_execution_request'] and replay['same_model_patch']
 full=read(R/'combined-model-calls.json');new=read(OLDSTATE/'parent-model-calls.json');assert len(full)==4 and len(new)==1
 assert full[2]['error']=='HTTPError: HTTP Error 402: Payment Required' and 'response_content' not in full[2]
 assert full[3]['request']==full[2]['request'] and all('response_content' in c and not c.get('error') for c in [full[0],full[1],full[3],new[0]])
 assert read(OLD/'repair.trace.json')['errors']==['ValueError: Unknown dispatch cannot create a new candidate selection']
 assert read(OLDSTATE/'receipt.json')['terminal_validation']['unknown_requests']==0
 resources=full+new;semantic=[full[i] for i in [0,1,3]];ready={**read(R/'ready.trace.json'),'model_calls':semantic};cfg=read(CONFIG)
 omitted=full[2]['prompt_tokens_reserved']+full[2]['output_tokens_reserved'];total=sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in resources);elapsed=read(OLDSTATE/'receipt.json')['parent_budget']['elapsed_seconds'];prefixelapsed=max(c['elapsed_seconds'] for c in semantic)
 mapping=dict(schema_version='ce.received_response_projection.v1',transport_rejected_index=2,semantic_ready_indices=[0,1,3],cached_execution_index=4,outer_model_calls=5,outer_reserved_tokens=total,inherited_active_seconds=elapsed,extra_inherited_active_seconds=elapsed-prefixelapsed,inner_max_calls=cfg['max_model_calls']-1,inner_max_tokens=cfg['max_total_tokens']-omitted,outer_max_calls=cfg['max_model_calls'],outer_max_tokens=cfg['max_total_tokens'],omitted_from_semantics_not_from_resources=omitted,model_http_calls_allowed=0)
 cache={};journals={}
 for p in (OLDSTATE/'method-journal').glob('*.json'):
  v=read(p)
  if v.get('op')=='public.observe' and v.get('status')=='DONE':cache[v['result']['snapshot_id']]=v['result'];journals[v['result']['snapshot_id']]={'path':str(p),'sha256':sha(p)}
 dirs={}
 for d in (OLDSTATE/'producer').glob('observation-*'):
  p=d/'observation.json';v=read(p)
  if v['snapshot_id'] in cache:
   assert v==cache[v['snapshot_id']];dirs[v['snapshot_id']]=d
 assert set(cache)==set(dirs) and len(cache)==2
 assert read(OLD/'rounds/0/feedback.json')['semantic_outcome']=='PUBLIC_REVIEW_PASS'
 return cfg,ready,resources,mapping,cache,dirs,journals,new[0]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');a=ap.parse_args()
 cfg,ready,resources,mapping,cache,dirs,journals,cached=prepare();panel=read(PANEL)
 proof=dict(protocol='ce.explicit_rejected_transport_projection.v1',driver_sha256=sha(__file__),child_sha256=sha(H/'ack402_projection_child.py'),ready_source_sha256=sha(R/'ready.trace.json'),actual_resource_calls_sha256=hashlib.sha256(json.dumps(resources,sort_keys=True).encode()).hexdigest(),failed_selection_receipt_sha256=sha(OLDSTATE/'receipt.json'),mapping=mapping,cached_observation_journals=journals,model_http_calls_allowed=0,counts_in_original46=False)
 if not a.execute:print(json.dumps(dict(status='PREFLIGHT_PASS',supplement=proof,formal_ready=False)));return
 with (PANEL.parent/'phase-controller.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);OUT.mkdir(exist_ok=False)
  exclusive(OUT/'semantic-ready.json',ready);exclusive(OUT/'resource-calls.json',resources);exclusive(OUT/'projection.json',mapping)
  namespace=harness.namespace_command
  def projected_namespace(**kw):
   command=list(kw['command'])
   if len(command)>1 and str(command[1])=='/run/ce-harness/run_reliability_worker_v10.py':
    command[1]='/run/ce-projection-child.py';kw={**kw,'command':command,'readonly':{**kw['readonly'],H/'ack402_projection_child.py':Path('/run/ce-projection-child.py'),OUT/'semantic-ready.json':Path('/run/ce-semantic-ready.json'),OUT/'projection.json':Path('/run/ce-projection.json')}}
   return namespace(**kw)
  harness.namespace_command=projected_namespace
  writer=harness.write_json
  def projected_write(path,value):
   if Path(path).name=='effective-config.json':value.update(max_model_calls=mapping['inner_max_calls'],max_total_tokens=mapping['inner_max_tokens'])
   return writer(path,value)
  harness.write_json=projected_write
  broker=harness.ModelBroker
  class ReceivedOnlyBroker(broker):
   def __init__(self,*args,**kw):
    super().__init__(*args,**kw);self.budget.prior_calls=5;self.budget.reserved_tokens=mapping['outer_reserved_tokens'];self.budget.started-=mapping['inherited_active_seconds'];self.replayed=False;self.replays=Path(args[3]).with_name('recorded-response-replays.json');writer(args[3],[])
   def _complete(self,payload):
    request=self._request(payload);actual=json.loads(json.dumps(asdict(request)))
    if self.replayed or actual!=cached['request']:raise ValueError('No new or changed model request admitted by a received-only replay')
    if self.budget.remaining_seconds<=0:raise ValueError('Original active-time budget exhausted')
    self.replayed=True;writer(self.replays,[dict(request_sha256=hashlib.sha256(json.dumps(actual,sort_keys=True).encode()).hexdigest(),source_calls_sha256=sha(OLDSTATE/'parent-model-calls.json'),source_index=0,http_dispatch=False)])
    return {'content':cached['response_content'],'metadata':cached['response_metadata']}
  harness.ModelBroker=ReceivedOnlyBroker
  bridge=harness.PublicBridge
  class RecordedObservationBridge(bridge):
   def observe(self,payload):
    if set(payload)!={'workspace','snapshot_id','output','timeout_seconds'}:raise ValueError('Unexpected observation replay request')
    output=self._relative(payload['output'],output=True);stage=self._stage(payload);sid=stage['snapshot_id']
    if sid not in cache:raise ValueError('No previously verified observation for the exact current snapshot')
    d=dirs[sid];assert read(d/'observation.json')==cache[sid] and sha(journals[sid]['path'])==journals[sid]['sha256']
    with SafeRoot(self.child_root) as child:child.publish_dir(d,output)
    shutil.rmtree(self.transfer/stage['stage_id'])
    path=self.producer_root.parent/'public-observation-replays.json';replays=read(path) if path.exists() else [];replays.append(dict(snapshot_id=sid,original_journal=journals[sid],report_sha256=sha(d/'observation.json'),fresh_runtime_claim=False));writer(path,replays)
    return cache[sid]
  harness.PublicBridge=RecordedObservationBridge
  identity=harness.execution_identity
  harness.execution_identity=lambda args:{**identity(args),'controlled_continuation':proof}
  args=argparse.Namespace(method_root=str(S),benchmark_root=panel['benchmark_root'],config=str(CONFIG),output=str(OUT/'method/R4-ACK-CLOUDWATCH-2985'),case='R4-ACK-CLOUDWATCH-2985',python=panel['runtime']['python'],dependency_root=panel['runtime']['dependency_root'],runtime_admission=panel['runtime'].get('runtime_admission'),seed=None,attempt_id='ack402-selection-replay-v068-20261028',panel_registration=None,panel_slot=None,execute=True,api_key_file='/home/xhj/.config/contractedge/deepseek-official-20261028.key',registration=str(OUT/'registration.json'))
  ident=harness.execution_identity(args);exclusive(OUT/'registration.json',dict(ident,role='ISOLATED_DEVELOPMENT_VALIDATION',allow_paid=True,counts_in_46_slot_panel=False));exclusive(OUT/'plan.json',proof)
  ledger=Path(cfg['money_budget']['path']);before=sha(ledger)
  writer(OUT/'state.json',dict(status='ISOLATED_RECEIVED_RESPONSE_REPLAY',owner_pid=os.getpid(),at=STAMP(),formal_ready=False))
  signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt('Retain replay; no automatic retry')))
  try:
   receipt=harness.run_session(args,cfg,ident)
   assert receipt['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING',receipt['terminal_validation']
   trace=read(Path(args.output)/'repair.trace.json');expected=read(OLD/'rounds/0/patch.json')
   assert trace.get('candidate',{}).get('patch_sha256')==expected['patch_sha256'],'Recovered source must be exactly the already generated model patch'
   assert sha(ledger)==before,'No ledger reservation or paid model dispatch is permitted'
   writer(OUT/'state.json',dict(status='INDEPENDENT_SCORING_SAME_RECEIVED_PATCH',at=STAMP(),formal_ready=False))
   scored=score(Path(receipt['state_root'])/'receipt.json',args.benchmark_root,OUT/'independent')
   result=dict(status='CLOSED_DETERMINISTIC_RECOVERY',at=STAMP(),source_sha256=panel['variants']['candidate']['source_sha256'],independent_status=scored['status'],public_semantic_status=(trace.get('last_feedback') or {}).get('semantic_outcome'),patch=trace['candidate'],model_http_calls_added=0,same_received_patch=True,parent_resource_budget=receipt['parent_budget'],semantic_budget=trace['budget'],projection=mapping,ledger_unchanged=True,counts_in_original46=False,formal_ready=False,scope='Deterministic recovery of the same received response after semantic/financial ledger separation; cached public observations are explicit, independent scoring is fresh once')
   exclusive(OUT/'result.json',result);writer(OUT/'state.json',result);print(json.dumps(result),flush=True)
  except BaseException as e:
   writer(OUT/'state.json',dict(status='AUDIT_REQUIRED',error_type=type(e).__name__,at=STAMP(),automatic_retry=False,formal_ready=False));raise

if __name__=='__main__':main()
