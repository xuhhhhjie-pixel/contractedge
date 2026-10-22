"""Use saved public responses to check unrelated real consumers; no new repair claim."""
import sys,json,urllib.request,hashlib
from pathlib import Path
root=Path('/home/xhj/contractedge-system');out=root/'outputs/reliability-closure-20261020';f=json.loads((out/'frozen-method-v0.6.25.json').read_text());sys.path[:0]=[str(Path(f['candidate_root'])/'src'),str(root/'.topomen-deps')]
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.repair import select_observations,validate_ready_trace
from contractedge.contracts.validation import parse_method_input,canonical_digest
from contractedge.dataset.archive import safe_extract
from contractedge.source.snapshot import compute_snapshot
from contractedge.model.provider import ModelResponse
from contractedge.tokenizers import utf8_byte_token_bound as count
read=lambda p:json.loads(p.read_text())
def no_http(*args,**kw):raise AssertionError('Network prohibited')
urllib.request.urlopen=no_http
dest=out/'unchanged-cross-case-replay-v0625';dest.mkdir(exist_ok=False);results=[]
for case in ['R3-KOPF-726','R3-KPT-4673']:
 origin=out/f'runs/delivery-0.6.23-A-{case}-0-candidate/cases/{case}'
 method=read(origin/'method_input.json');oldfront=read(origin/'rounds/0/front.trace.json');saved=read(origin/'model_calls.json');cap=oldfront['run_context']['retrieval_config']['public_execution_capabilities']
 here=dest/case;here.mkdir();public=root.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public';snapshot=safe_extract(public/method['archive_path'],here/'source');source=Path(snapshot.source_root);assert snapshot.snapshot_id==method['initial_snapshot_id']
 cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');cfg.update(run_id=oldfront['run_id'],model={'model':cfg['model'],'schema_in_prompt':True},public_execution_capabilities=cap)
 observations=select_observations(read(origin/'broken-public/observation.json')['observations'],method['requirement_text'],count,cfg['max_public_evidence_tokens'])
 class Provider:
  def __init__(self):self.requests=[]
  def complete_json(self,request):
   i=len(self.requests);self.requests.append(request);assert i<len(oldfront['model_calls'])
   old=saved[i];assert request.prompt_version==old['prompt_version']
   assert request.messages==tuple(old['messages']),f'{case}: request {i} changed'
   assert request.output_schema==old['output_schema']
   assert request.thinking is None
   return ModelResponse(old['response_content'],old['response_metadata'])
 provider=Provider();parsed=parse_method_input(method);trace=run_front_half(parsed,here/'source',provider,cfg,count,public_observations=tuple(observations)).to_wire();assert trace['status']=='READY',trace['errors'];assert trace['diagnosis']==oldfront['diagnosis'];assert trace['changeset']==oldfront['changeset']
 validate_ready_trace(trace,parsed,source)
 assert compute_snapshot(source).snapshot_id==method['initial_snapshot_id']
 results.append(dict(case_id=case,saved_responses=len(provider.requests),all_request_bodies_exact=True,diagnosis_exact=True,plan_exact=True,ready_consumer_accepted=True,source_unchanged=True,api_calls=0))
summary=dict(version='0.6.25',api_calls=0,cases=results,scope='Replayed old successful public responses through frozen new front half and real Ready consumer. Proves non-triggered context/contract preservation, not fresh model success or new independent scores.')
(dest/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
