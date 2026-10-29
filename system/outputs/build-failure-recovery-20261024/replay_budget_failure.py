"""Reconstruct saved public failure through the normal native-TopoMen loop. Zero HTTP."""
from pathlib import Path
import sys,json,hashlib,shutil,socket
r=Path('/home/xhj/contractedge-system');method=Path(sys.argv[1]);label=sys.argv[2];out=Path(__file__).resolve().parent
sys.path[:0]=[str(method/'src'),str(r/'.topomen-deps')]
from contractedge.orchestration.repair import repair_case
from contractedge.execution import public
from contractedge.model.provider import ModelResponse
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound as count
read=lambda p:json.loads(Path(p).read_text())
old=r/'outputs/reliability-closure-20261020/runs/delivery-0.6.35-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
case='R4-ACK-CLOUDWATCH-2985';benchmark=r.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019';calls=read(old/'model_calls.json');assert len(calls)==6
cfg=read(r/'configs/deepseek-reliability-closure-v10-budget150.json');cfg['run_id']=read(old/'rounds/0/front.trace.json')['run_id']
mi=read(old/'method_input.json')
def denied(*a,**k):raise AssertionError('No network in saved failure replay')
socket.create_connection=denied;socket.socket.connect=denied
class Captured(BaseException):pass
class Tape:
 def __init__(self):self.requests=[]
 def complete_json(self,request):
  index=len(self.requests);self.requests.append(request)
  if index==6:raise Captured()
  assert request.prompt_version==calls[index]['prompt_version'],(index,request.prompt_version)
  return ModelResponse(calls[index]['response_content'],calls[index]['response_metadata'])
class SavedPublic(public.BenchmarkPublicRunner):
 def observe(self,workspace,output):
  source=Path(read(Path(workspace)/'workspace.json')['source_root']);snapshot=compute_snapshot(source)
  origin=old/('broken-public' if snapshot.snapshot_id==mi['initial_snapshot_id'] else 'rounds/0/public')
  report=read(origin/'observation.json');assert report['snapshot_id']==snapshot.snapshot_id
  output.mkdir(parents=True,exist_ok=False);shutil.copytree(origin/'raw',output/'raw')
  report={**report}
  if hasattr(public,'classify_candidate_build_failures'):
   report['candidate_build_failures']=public.classify_candidate_build_failures(output/'raw',snapshot)
  (output/'observation.json').write_text(json.dumps(report,indent=2)+'\n')
  return report
p=Tape();runner=SavedPublic(benchmark/'public',benchmark/'tools',lock_root=out/'locks',timeout_seconds=cfg['public_timeout_seconds'],query=mi['requirement_text'],method_probe_profiles=cfg['method_probe_profiles'])
debug_rows=[]
def debug(frame,event,arg):
 if event=='return' and frame.f_code.co_filename.endswith('orchestration/runner.py'):
  values=frame.f_locals;name=frame.f_code.co_name
  if name=='fit_diagnosis' and isinstance(arg,tuple):
   fitted,request,bound=arg
   debug_rows.append(dict(function=name,request_present=request is not None,input_bound=bound,candidates=len(fitted.candidates),required_endpoint_ids=list(fitted.required_endpoint_ids),last_context_audit=list(fitted.context_audit[-3:])))
  elif name=='within_budget' and arg is False:
   debug_rows.append(dict(function=name,accepted=False,**{k:values.get(k) for k in ['prompt_tokens','max_elapsed_seconds','max_model_calls','max_total_tokens','reserved_tokens']},calls=len(values.get('calls',[]))))
 return debug
sys.settrace(debug)
job=out/('normal-'+label);captured=False
try:result=repair_case(benchmark/'public',benchmark/'tools',case,job,p,count,cfg,public_runner=runner)
except Captured:captured=True
source=Path(read(job/'workspace/workspace.json')['source_root']);now=compute_snapshot(source).snapshot_id
record=dict(label=label,real_model_http_calls=0,saved_model_responses_consumed=min(6,len(p.requests)),
 request_count=len(p.requests),captured_restored_retry=captured,selected_source_snapshot=now,
 initial_snapshot=mi['initial_snapshot_id'],source_restored=now==mi['initial_snapshot_id'])
if captured:
 request=p.requests[-1];record.update(next_prompt=request.prompt_version,input_bound=sum(count(x['content']) for x in request.messages))
else:record.update(method_status=result['status'],rounds=len(result['rounds']),errors=result['errors'])
record['first_six_messages_match_saved']=[list(request.messages)==calls[index]['messages'] for index,request in enumerate(p.requests[:6])]
assert all(record['first_six_messages_match_saved'])
record['debug_budget_boundaries']=debug_rows
record['scope']='Saved eight responses and public Broken/build-failure reports only; capture next request after coherent rollback. Not a new model correction or repair outcome.'
(out/(label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
