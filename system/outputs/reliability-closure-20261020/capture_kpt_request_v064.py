import hashlib,json
from pathlib import Path
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.contracts.validation import parse_method_input
from contractedge.source.snapshot import compute_snapshot
from contractedge.execution.probe_specs import select_probes
from contractedge.orchestration.repair import select_observations
from contractedge.orchestration.runner import run_front_half
from contractedge.cli import _token_counter
root=Path.cwd();out=root/'outputs/reliability-closure-20261020';bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
value,projection=method_input_from_task(bench/'public','R3-KPT-4673');method=parse_method_input(value)
source=next((root/'outputs/production-path-behavior-20261020/public-source-kpt').iterdir());assert compute_snapshot(source).snapshot_id==method.initial_snapshot_id
cfg=json.loads((root/'configs/deepseek-reliability-closure-v2-budget80.json').read_text());counter=_token_counter(cfg['tokenizer'])
observation=json.loads((out/'runs/delivery-0.6.3-recovery-R3-KPT-4673-0-candidate/cases/R3-KPT-4673/broken-public/observation.json').read_text())
observations=select_observations(observation['observations'],method.requirement_text,counter,cfg['max_public_evidence_tokens'])
cfg['public_execution_capabilities']={**projection['public_execution_capabilities'],'method_behavior_probes':[{k:v for k,v in s.items() if k!='program'} for s in select_probes(method.requirement_text,source,cfg['method_probe_profiles'])]}
cfg['model']={'model':cfg['model'],'provider':cfg['provider'],'schema_in_prompt':True}
class Captured(BaseException):pass
class Provider:
 def complete_json(self,request):
  public=json.loads(request.messages[1]['content'])
  summary=dict(api_calls=0,scope='FULL_FIRST_REQUEST_CAPTURE_NO_HTTP',request_bytes=sum(counter(m['content']) for m in request.messages),input_limit=cfg['initial_diagnosis_context_tokens'],selected=request.metadata['selected_ids'],failed_public_predicates=public['failed_public_predicates'],public_evidence_records=len(public['method_input']['public_evidence']))
  (out/'kpt-final-first-request-capture-v064.json').write_text(json.dumps(summary,indent=2)+'\n');print(summary)
  assert summary['request_bytes']<=summary['input_limit']
  assert len(summary['failed_public_predicates'])==3 and len(summary['selected'])>=2
  raise Captured()
try:
 trace=run_front_half(method,source if method.source_root=='.' else source.parent,Provider(),cfg,counter,public_observations=observations)
 print(trace.status,trace.errors)
 raise AssertionError('No request reached capture provider')
except Captured:pass
