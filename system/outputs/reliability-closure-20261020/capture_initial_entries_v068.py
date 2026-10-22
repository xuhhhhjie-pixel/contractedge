import json
from pathlib import Path
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.contracts.validation import parse_method_input
from contractedge.source.snapshot import compute_snapshot
from contractedge.execution.probe_specs import select_probes
from contractedge.orchestration.repair import select_observations
from contractedge.orchestration.runner import run_front_half
from contractedge.cli import _token_counter
out=Path('outputs/reliability-closure-20261020');bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
class Captured(BaseException):pass
for case in ['RW-ALERTMANAGER-4705','R3-KPT-4673']:
 value,projection=method_input_from_task(bench/'public',case);method=parse_method_input(value)
 if case.startswith('RW-'):
  source=next((out/'public-source-alertmanager').glob('*/go.mod')).parent
  prior=out/'runs/delivery-0.6.7-A-RW-ALERTMANAGER-4705-0-candidate/cases'/case
 else:
  source=next(Path('outputs/production-path-behavior-20261020/public-source-kpt').iterdir())
  prior=out/'runs/delivery-0.6.7-recovery-R3-KPT-4673-0-candidate/cases'/case
 assert compute_snapshot(source).snapshot_id==method.initial_snapshot_id
 cfg=json.loads(Path('configs/deepseek-reliability-closure-v4-budget80.json').read_text());counter=_token_counter(cfg['tokenizer'])
 obs=json.loads((prior/'broken-public/observation.json').read_text());observations=select_observations(obs['observations'],method.requirement_text,counter,cfg['max_public_evidence_tokens'])
 cfg['public_execution_capabilities']={**projection['public_execution_capabilities'],'method_behavior_probes':[{k:v for k,v in s.items() if k!='program'} for s in select_probes(method.requirement_text,source,cfg['method_probe_profiles'])]}
 cfg['model']={'model':cfg['model'],'provider':cfg['provider'],'schema_in_prompt':True}
 class Provider:
  def complete_json(self,request):
   body=json.loads(request.messages[1]['content']);sources=[r for r in body['evidence_catalog'] if r.get('kind')=='SOURCE_SPAN']
   if case.startswith('RW-'):
    required={'reload_callback':any('configCoordinator.Subscribe' in r.get('excerpt','') for r in sources),'groups_read_callback':any('return disp.Groups' in r.get('excerpt','') for r in sources)}
   else:
    required={name:any('func (pn *pkgNode) '+name+'(' in r.get('excerpt','') for r in sources) for name in ['runMutators','runValidators']}
   summary=dict(api_calls=0,case_id=case,scope='FULL_INITIAL_REQUEST_NO_RESPONSES_NO_HTTP',request_bytes=sum(counter(m['content']) for m in request.messages),input_limit=cfg['initial_diagnosis_context_tokens'],selected_count=len(request.metadata['selected_ids']),required_bodies=required,source_spans=[dict(id=r['id'],path=r['locator'].get('path'),start=r['locator'].get('start_line'),end=r['locator'].get('end_line'),characters=len(r['excerpt'])) for r in sources])
   (out/('initial-entry-request-v068-'+case+'.json')).write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
   assert all(required.values()) and summary['request_bytes']<=summary['input_limit']
   raise Captured()
 try:
  trace=run_front_half(method,source if method.source_root=='.' else source.parent,Provider(),cfg,counter,public_observations=observations)
  raise AssertionError(str((trace.status,trace.errors)))
 except Captured:pass
