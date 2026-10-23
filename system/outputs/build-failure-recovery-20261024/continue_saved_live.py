"""Supplemental saved-response continuation; never a fresh formal/panel trial."""
from pathlib import Path
import sys,json,hashlib,shutil,os,argparse
r=Path('/home/xhj/contractedge-system');out=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
write=lambda p,x:Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
freeze_path=r/'outputs/reliability-closure-20261020/frozen-method-v0.6.35.json';freeze=read(freeze_path);method=Path(freeze['candidate_root']);sys.path[:0]=[str(method/'src'),str(r/'.topomen-deps')]
from contractedge.orchestration.repair import repair_case
from contractedge.execution import public
from contractedge.model.provider import ModelResponse
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound as count
old=r/'outputs/reliability-closure-20261020/runs/delivery-0.6.34-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
calls=read(old/'model_calls.json');assert len(calls)==8
cfg=read(freeze['configuration_path']);cfg['run_id']=read(old/'rounds/0/front.trace.json')['run_id']
benchmark=r.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019';case='R4-ACK-CLOUDWATCH-2985';mi=read(old/'method_input.json')
actual={p.relative_to(method/'src').as_posix():sha(p) for p in (method/'src').rglob('*') if p.is_file() and p.suffix in {'.py','.json'}};assert actual==freeze['files']
assert all(sha(r/p)==h for p,h in freeze['harness_files'].items())
registration=dict(schema_version='ce.supplemental_saved_response_continuation.v1',status='PREDECLARED_SINGLE_ATTEMPT',
 role='SUPPLEMENTAL_CROSS_VERSION_SAVED_RESPONSE_CONTINUATION',method_version='0.6.35',method_digest=freeze['method_digest'],
 freeze_sha256=sha(freeze_path),harness_sha256=sha(__file__),source_run_version='0.6.34',case_id=case,
 saved_model_calls_sha256=sha(old/'model_calls.json'),saved_response_count=8,max_new_paid_model_calls=4,
 config_sha256=sha(freeze['configuration_path']),initial_snapshot_id=mi['initial_snapshot_id'],failed_trial_snapshot='9896145816cafd2fa9cd98a2178157b41c8d07ae062b6e7d96b926357b8faec6',
 max_total_model_calls=cfg['max_model_calls'],max_rounds=cfg['max_rounds'],formal150=False,counts_as_fresh_broken_attempt=False,
 counts_in_46_slot_panel=False,retry_if_failed=False,private_materials_loaded=False,
 scope='Exactly replay first eight public responses/observations, then actual model and public runner within existing remaining budget; retain all failures. No historical response is charged as a new call or counted as new output.')
regpath=out/'supplemental-registration.json'
if not args.execute:
 assert not regpath.exists();write(regpath,registration)
else:
 assert read(regpath)==registration
 assert read(out/'supplemental-dry-run.json')['first_eight_messages_match_saved']==[True]*8
 assert read(out/'supplemental-dry-run.json')['captured_restored_retry']
class Captured(BaseException):pass
class Tape:
 def __init__(self,live=None):self.live=live;self.requests=[];self.live_calls=0
 def complete_json(self,request):
  index=len(self.requests);self.requests.append(request)
  if index<8:
   assert list(request.messages)==calls[index]['messages'],index
   assert request.prompt_version==calls[index]['prompt_version']
   return ModelResponse(calls[index]['response_content'],calls[index]['response_metadata'])
  if self.live is None:raise Captured()
  assert self.live_calls<4;self.live_calls+=1
  return self.live.complete_json(request)
class ReplayThenPublic(public.BenchmarkPublicRunner):
 def __init__(self,*a,**kw):super().__init__(*a,**kw);self.replayed=0
 def observe(self,workspace,output):
  if self.replayed>=2:return super().observe(workspace,output)
  source=Path(read(Path(workspace)/'workspace.json')['source_root']);snapshot=compute_snapshot(source)
  origin=old/('broken-public' if self.replayed==0 else 'rounds/0/public');self.replayed+=1
  report=read(origin/'observation.json');assert report['snapshot_id']==snapshot.snapshot_id
  output.mkdir(parents=True,exist_ok=False);shutil.copytree(origin/'raw',output/'raw')
  report={**report,'candidate_build_failures':public.classify_candidate_build_failures(output/'raw',snapshot)}
  write(output/'observation.json',report);return report
provider=None;money_before=None
if args.execute:
 from contractedge.cli import _build_provider
 os.environ[cfg['api_key_env']]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
 provider,_=_build_provider(cfg);money_before=provider.money_budget.summary()
else:
 import socket
 def denied(*a,**kw):raise AssertionError('No network in dry run')
 socket.create_connection=denied;socket.socket.connect=denied
p=Tape(provider);job=out/('supplemental-live' if args.execute else 'supplemental-dry');assert not job.exists()
runner=ReplayThenPublic(benchmark/'public',benchmark/'tools',lock_root=out/'supplemental-locks',timeout_seconds=cfg['public_timeout_seconds'],query=mi['requirement_text'],method_probe_profiles=cfg['method_probe_profiles'])
try:
 result=repair_case(benchmark/'public',benchmark/'tools',case,job,p,count,cfg,public_runner=runner)
except Captured:
 body=json.loads(p.requests[-1].messages[1]['content']);history=body['previous_execution_attempt']['rejected_trial'];snapshot=compute_snapshot(read(job/'workspace/workspace.json')['source_root']).snapshot_id
 assert history['snapshot_id']==registration['failed_trial_snapshot'] and snapshot==mi['initial_snapshot_id']
 errors=history['feedback']['failure_tasks']['shared_execution_errors'];assert errors and all(e['snapshot_id']==history['snapshot_id'] for e in errors)
 assert body['accepted_plan']['snapshot_id']==snapshot
 write(out/'supplemental-dry-run.json',dict(captured_restored_retry=True,first_eight_messages_match_saved=[True]*8,
  requests=len(p.requests),input_bound=sum(count(m['content']) for m in p.requests[-1].messages),output_bound=p.requests[-1].max_output_tokens,
  current_snapshot=snapshot,historical_failure_snapshot=history['snapshot_id'],new_paid_calls=0))
 print('Dry replay qualified: first eight exact; restored ninth request is source-bound')
else:
 assert args.execute
 write(out/'supplemental-result.json',dict(role=registration['role'],method_status=result['status'],new_paid_model_calls=p.live_calls,
  saved_responses=8,total_requests=len(p.requests),initial_snapshot=mi['initial_snapshot_id'],final_snapshot=result.get('final_snapshot_id'),
  candidate=result.get('candidate'),errors=result.get('errors'),rounds=len(result.get('rounds',[])),
  counts_as_fresh_broken_attempt=False,counts_in_46_slot_panel=False,formal_ready=False))
 print(json.dumps(dict(method_status=result['status'],new_paid_calls=p.live_calls)))
finally:
 if provider:
  write(out/'supplemental-money-accounting.json',dict(before=money_before,after=provider.money_budget.summary()))
