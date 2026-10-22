import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from contractedge.execution.probe_specs import select_probes
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.repair import select_observations
from contractedge.orchestration.batch import _method_digest
out=Path(__file__).parent;source=next((out/'public-source-kpt').iterdir())
bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
m,p=method_input_from_task(bench/'public','R3-KPT-4673');cfg=json.loads((ROOT/'configs/deepseek-development-path-probe-v2-budget80.json').read_text())
specs=select_probes(m['requirement_text'],source,cfg['method_probe_profiles'])
cfg.update(public_execution_capabilities={**p['public_execution_capabilities'],'method_behavior_probes':[{k:v for k,v in s.items() if k!='program'} for s in specs]},model={'model':cfg['model'],'schema_in_prompt':True})
prior=json.loads((ROOT/'outputs/deepseek-production-path-probe-v2-20261020/cases/R3-KPT-4673/broken-public/observation.json').read_text())
class Capture:
 def complete_json(self,r):self.request=r;raise RuntimeError('ZERO_HTTP_FIRST_REQUEST_CAPTURE')
provider=Capture();counter=lambda s:len(s.encode())
t=run_front_half(m,source if m["source_root"]=="." else source.parent,provider,cfg,counter,public_observations=select_observations(prior['observations'],m['requirement_text'],counter,cfg['max_public_evidence_tokens'])).to_wire()
print('capture state',t['status'],t['errors']);payload=json.loads(provider.request.messages[1]['content']);candidates=t['candidate_set']['candidates'];names={u.get('symbol') for u in candidates}
assert {'runMutators','runValidators'}<=names,names
result={'method_code_digest':_method_digest(),'api_calls':0,'private_materials_loaded':False,'source_snapshot':t['snapshot_id'],
 'initial_candidates':[{'path':u['path'],'symbol':u.get('symbol'),'locator':u['locator']} for u in candidates],
 'frontier':payload['production_path_frontier'],'probe_entrypoint_resolution':__import__('contractedge.source.entrypoints',fromlist=['probe_entrypoint_hints']).probe_entrypoint_hints(__import__('contractedge.source.snapshot',fromlist=['compute_snapshot']).compute_snapshot(source),specs)[2],
 'request_messages':list(provider.request.messages),'interpretation':'Actual first diagnosis request capture; not a new model diagnosis or successful repair'}
(out/'initial-entry-context.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(result['initial_candidates'])
