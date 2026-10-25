"""One predeclared paid correction of a saved public draft; never a repair score."""
import argparse,json,hashlib,sys,shutil,os
from pathlib import Path
r=Path('/home/xhj/contractedge-system');a=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
write=lambda p,x:Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
fp=r/'outputs/reliability-closure-20261020/frozen-method-v0.6.37.json';freeze=read(fp);method=Path(freeze['candidate_root']);sys.path[:0]=[str(method/'src'),str(r/'.topomen-deps')]
from contractedge.orchestration.repair import repair_case
from contractedge.execution import public
from contractedge.model.provider import ModelResponse
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound as count
old=r/'outputs/reliability-closure-20261020/runs/delivery-0.6.36-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
calls=read(old/'model_calls.json');assert len(calls)==6
mi=read(old/'method_input.json');cfg=read(freeze['configuration_path']);cfg['run_id']=read(old/'rounds/0/front.trace.json')['run_id']
assert {p.relative_to(method/'src').as_posix():sha(p) for p in (method/'src').rglob('*') if p.is_file() and p.suffix in {'.py','.json'}}==freeze['files']
assert sha(freeze['configuration_path'])==freeze['configuration_hashes']['candidate']
assert all(sha(r/p)==h for p,h in freeze['harness_files'].items())
registration=dict(schema_version='ce.saved_draft_correction_validation.v1',role='SUPPLEMENTAL_PLANNING_CORRECTION_ONLY',method_version='0.6.37',method_digest=freeze['method_digest'],freeze_sha256=sha(fp),script_sha256=sha(__file__),saved_calls_sha256=sha(old/'model_calls.json'),saved_response_count=5,max_new_paid_calls=1,max_total_method_calls=cfg['max_model_calls'],source_snapshot=mi['initial_snapshot_id'],counts_in_46_slot_panel=False,counts_as_fresh_broken_attempt=False,counts_as_behavior_repair_success=False,retry_if_failed=False,scope='Replay exactly5publicresponses, one new corrected plan only. Stop before any further model call or source edit; no independent scoring.')
rp=a/'correction-validation-registration.json'
if not args.execute:
 assert not rp.exists();write(rp,registration)
else:
 assert read(rp)==registration
 proof=read(a/'correction-validation-dry.json');assert proof['first_five_exact'] and proof['draft_exact'] and proof['source_unchanged']
class Captured(BaseException):pass
class Tape:
 def __init__(self,live=None):self.live=live;self.requests=[];self.new=[]
 def complete_json(self,request):
  i=len(self.requests);self.requests.append(request)
  if i<5:
   assert list(request.messages)==calls[i]['messages'],i
   assert request.output_schema==calls[i]['output_schema'],i
   return ModelResponse(calls[i]['response_content'],calls[i]['response_metadata'])
  if i==5 and self.live is not None:
   response=self.live.complete_json(request);self.new.append(dict(prompt_version=request.prompt_version,response_content=response.content,response_metadata=response.metadata));return response
  raise Captured()
class SavedPublic(public.BenchmarkPublicRunner):
 def observe(self,workspace,output):
  source=Path(read(Path(workspace)/'workspace.json')['source_root']);assert compute_snapshot(source).snapshot_id==mi['initial_snapshot_id']
  origin=old/'broken-public';output.mkdir(parents=True,exist_ok=False);shutil.copytree(origin/'raw',output/'raw');report=read(origin/'observation.json');write(output/'observation.json',report);return report
live=None
if args.execute:
 from contractedge.cli import _build_provider
 os.environ[cfg['api_key_env']]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip();live,_=_build_provider(cfg)
else:
 import socket
 def denied(*a,**k):raise AssertionError('No network during dry run')
 socket.create_connection=denied;socket.socket.connect=denied
p=Tape(live);job=a/('correction-validation-live' if args.execute else 'correction-validation-dry');assert not job.exists()
bench=r.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019'
runner=SavedPublic(bench/'public',bench/'tools',lock_root=a/'correction-validation-locks',timeout_seconds=cfg['public_timeout_seconds'],query=mi['requirement_text'],method_probe_profiles=cfg['method_probe_profiles'])
captured=False;result=None
try:result=repair_case(bench/'public',bench/'tools',mi['case_id'],job,p,count,cfg,public_runner=runner)
except Captured:captured=True
source=Path(read(job/'workspace/workspace.json')['source_root']);assert compute_snapshot(source).snapshot_id==mi['initial_snapshot_id']
req=p.requests[5];assert req.messages[2]['content']==calls[4]['response_content']
record=dict(role=registration['role'],new_paid_calls=len(p.new),saved_responses=5,first_five_exact=True,draft_exact=True,source_unchanged=True,input_bound=sum(count(m['content']) for m in req.messages),output_bound=req.max_output_tokens,counts_in_46_slot_panel=False,counts_as_behavior_repair_success=False,formal_ready=False,captured_next_request=captured,next_prompt=p.requests[-1].prompt_version,method_status=result['status'] if result else None)
if args.execute:
 tracepath=job/'rounds/0/front.trace.json';trace=read(tracepath) if tracepath.exists() else {};record.update(validated_plan_status=trace.get('status'),validation_errors=trace.get('errors'),stopped_before_execution=captured and p.requests[-1].prompt_version.startswith('topomen-source-execution'))
 write(a/'correction-validation-new-response.json',p.new)
write(a/('correction-validation-result.json' if args.execute else 'correction-validation-dry.json'),record);print(json.dumps(record))
