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
old=r/'outputs/reliability-closure-20261020/runs/delivery-0.6.36-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
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
  if index==5:raise Captured()
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
job=out/('normal-'+label);captured=False
try:result=repair_case(benchmark/'public',benchmark/'tools',case,job,p,count,cfg,public_runner=runner)
except Captured:captured=True
source=Path(read(job/'workspace/workspace.json')['source_root']);now=compute_snapshot(source).snapshot_id
record=dict(label=label,real_model_http_calls=0,saved_model_responses_consumed=min(5,len(p.requests)),
 request_count=len(p.requests),captured_plan_correction=captured,selected_source_snapshot=now,
 initial_snapshot=mi['initial_snapshot_id'],source_restored=now==mi['initial_snapshot_id'])
if captured:
 request=p.requests[-1];body=json.loads(request.messages[1]['content']);messages=request.messages
 prior_present=len(messages)==4 and messages[2]['role']=='assistant'
 record.update(next_prompt=request.prompt_version,input_bound=sum(count(x['content']) for x in messages),output_bound=request.max_output_tokens,
  total_context_limit=cfg['max_context_tokens'],rejected_draft_present=prior_present,
  unchanged_original_request_messages=list(messages[:2])==calls[5]['messages'],
  output_schema_unchanged=request.output_schema==calls[5]['output_schema'])
 if prior_present:
  proof=json.loads(messages[3]['content']);assert messages[2]['content']==calls[4]['response_content']
  assert proof['binding']['snapshot_id']==now==mi['initial_snapshot_id']
  assert proof['validation_feedback']==body['validation_feedback']
  record.update(draft_sha256=hashlib.sha256(messages[2]['content'].encode()).hexdigest(),draft_state=proof['previous_attempt_state'],
   draft_status=json.loads(messages[2]['content'])['status'],validation_codes=[x['code'] for x in proof['validation_feedback']])
else:record.update(method_status=result['status'],errors=result['errors'])
record['first_five_messages_match_saved']=[list(request.messages)==calls[index]['messages'] for index,request in enumerate(p.requests[:5])]
assert all(record['first_five_messages_match_saved'])
record['scope']='Five actual .36 public responses replayed, then capture real sixth corrective request. No new model output or patch. Previous draft remains untrusted planning state.'
(out/(label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
