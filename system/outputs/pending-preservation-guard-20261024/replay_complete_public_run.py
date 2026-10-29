"""Zero HTTP regression of all responses/observations from a completed public run."""
from pathlib import Path
import sys,json,hashlib,shutil,socket
r=Path('/home/xhj/contractedge-system');method=Path('/home/xhj/contractedge-github-delivery-20261022/system');a=Path(__file__).resolve().parent
sys.path[:0]=[str(method/'src'),str(r/'.topomen-deps')]
from contractedge.orchestration.repair import repair_case
from contractedge.execution import public
from contractedge.model.provider import ModelResponse
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound as count
read=lambda p:json.loads(Path(p).read_text())
old=r/'outputs/reliability-closure-20261020/runs/delivery-0.6.37-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985';calls=read(old/'model_calls.json');assert len(calls)==9 and all('response_content' in x for x in calls)
mi=read(old/'method_input.json');cfg=read(r/'configs/deepseek-reliability-closure-v10-budget150.json');cfg['run_id']=read(old/'rounds/0/front.trace.json')['run_id']
observations={mi['initial_snapshot_id']:old/'broken-public'}
for folder in (old/'rounds').iterdir():
 if folder.is_dir() and (folder/'execution.json').exists():observations[read(folder/'execution.json')['to_snapshot']]=folder/'public'
def denied(*a,**k):raise AssertionError('No HTTP in completed-trace replay')
socket.create_connection=denied;socket.socket.connect=denied
class Tape:
 def __init__(self):self.n=0
 def complete_json(self,request):
  index=self.n;self.n+=1;assert index<len(calls),'Unexpected new model request'
  prior=calls[index];assert list(request.messages)==prior['messages'],('Changed messages',index)
  assert request.output_schema==prior['output_schema'],('Changed schema',index)
  assert request.prompt_version==prior['prompt_version']
  return ModelResponse(prior['response_content'],prior['response_metadata'])
class SavedPublic(public.BenchmarkPublicRunner):
 def observe(self,workspace,output):
  source=Path(read(Path(workspace)/'workspace.json')['source_root']);snapshot=compute_snapshot(source);origin=observations[snapshot.snapshot_id]
  output.mkdir(parents=True,exist_ok=False)
  for name in ['raw','method-probes']:
   if (origin/name).is_dir():shutil.copytree(origin/name,output/name)
  report=read(origin/'observation.json');assert report['snapshot_id']==snapshot.snapshot_id
  (output/'observation.json').write_text(json.dumps(report,indent=2)+'\n');return report
bench=r.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019';p=Tape();job=a/'complete-public-replay';assert not job.exists()
runner=SavedPublic(bench/'public',bench/'tools',lock_root=a/'complete-replay-locks',timeout_seconds=cfg['public_timeout_seconds'],query=mi['requirement_text'],method_probe_profiles=cfg['method_probe_profiles'])
result=repair_case(bench/'public',bench/'tools',mi['case_id'],job,p,count,cfg,public_runner=runner)
assert p.n==9 and result['final_snapshot_id']==read(old/'repair.trace.json')['final_snapshot_id']
record=dict(scope='Exact9savedresponses plus saved public observations through new candidate guard; not9newcalls or independent .38 effectiveness',saved_responses=9,new_model_calls=0,all_messages_and_schemas_exact=True,method_status=result['status'],final_snapshot=result['final_snapshot_id'],candidate_patch_sha256=result['candidate']['patch_sha256'],same_as_saved_candidate=result['candidate']['patch_sha256']==read(old/'repair.trace.json')['candidate']['patch_sha256'],candidate_decisions=[read(job/f'rounds/{i}/candidate-decision.json') for i in [0,1]])
assert record['same_as_saved_candidate'];(a/'complete-public-replay.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
