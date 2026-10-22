import hashlib,json
from pathlib import Path
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.contracts.validation import parse_method_input
from contractedge.source.snapshot import compute_snapshot
from contractedge.execution.probe_specs import select_probes
from contractedge.orchestration.repair import select_observations
from contractedge.orchestration.runner import run_front_half
from contractedge.model.provider import ModelResponse
from contractedge.cli import _token_counter
out=Path('outputs/reliability-closure-20261020');bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019');case='RW-ALERTMANAGER-4705'
value,projection=method_input_from_task(bench/'public',case);method=parse_method_input(value);dest=out/'public-source-alertmanager';source=next(dest.glob('*/go.mod')).parent if not(dest/'go.mod').exists() else dest
assert compute_snapshot(source).snapshot_id==method.initial_snapshot_id
prior=out/'runs/delivery-0.6.6-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705';saved=json.loads((prior/'model_calls.json').read_text())
cfg=json.loads(Path('configs/deepseek-reliability-closure-v4-budget80.json').read_text());counter=_token_counter(cfg['tokenizer']);obs=json.loads((prior/'broken-public/observation.json').read_text());observations=select_observations(obs['observations'],method.requirement_text,counter,cfg['max_public_evidence_tokens'])
cfg['public_execution_capabilities']={**projection['public_execution_capabilities'],'method_behavior_probes':[{k:v for k,v in s.items() if k!='program'} for s in select_probes(method.requirement_text,source,cfg['method_probe_profiles'])]}
cfg['model']={'model':cfg['model'],'provider':cfg['provider'],'schema_in_prompt':True}
class Captured(BaseException):pass
class Provider:
 calls=0
 def complete_json(self,request):
  self.calls+=1
  if self.calls<5:return ModelResponse(saved[self.calls-1]['response_content'],saved[self.calls-1]['response_metadata'])
  public=json.loads(request.messages[1]['content']);catalog={r['id']:r for r in public['evidence_catalog']}
  (out/'alert-planning-gap-serialized-request-v067.json').write_text(json.dumps(public,indent=2)+'\n')
  required=json.loads((out/'actual-alert-gap-replay-v066.json').read_text())['roles'];rows=[]
  for u in required:
   record=catalog.get('SRC-'+u['id']);rows.append(dict(id=u['id'],path=u['path'],symbol=u['symbol'],required=not(u['path']=='dispatch/dispatch.go' and u['symbol']=='run'),delivered=bool(record),characters=len(record.get('excerpt','')) if record else 0))
  summary=dict(api_calls=0,scope='ACTUAL_CHANGESET_GAP_AND_FRESH_CORRECTION_REQUEST_REPLAY',saved_public_responses=4,request_bytes=sum(counter(m['content']) for m in request.messages),selected_count=len(request.metadata['selected_ids']),top_n=cfg['top_n'],delivery=rows)
  (out/'alert-planning-gap-request-capture-v067.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
  assert all(r['delivered'] for r in rows if r['required'])
  assert request.metadata.get('correction_allowance')=='FRESH_EVIDENCE_CONTEXT_ONCE'
  raise Captured()
try:
 trace=run_front_half(method,source if method.source_root=='.' else source.parent,Provider(),cfg,counter,public_observations=observations)
 raise AssertionError(str((trace.status,trace.errors)))
except Captured:pass
