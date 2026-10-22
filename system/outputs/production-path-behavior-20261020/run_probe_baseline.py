import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'src')]
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.probe_specs import select_probes
from contractedge.execution.probes import run_method_probes
from contractedge.dataset.benchmark import method_input_from_task
out=Path(__file__).parent;bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
runner=BenchmarkPublicRunner(bench/'public',bench/'tools',lock_root='/tmp/contractedge-system-native-locks',timeout_seconds=1200)
workspace=out/'probe-broken-workspace';record=runner.prepare('R3-KPT-4673',workspace,out/'probe-broken-prepare')
method,_=method_input_from_task(bench/'public','R3-KPT-4673')
specs=select_probes(method['requirement_text'],Path(record['source_root']),['kpt-configpath-v1'])
(out/'probe-specification.json').write_text(json.dumps([{k:v for k,v in s.items() if k!='program'} for s in specs],indent=2)+'\n')
result=run_method_probes(workspace,out/'probe-broken-run',bench/'tools',specs,timeout_seconds=1200)
print(json.dumps({'status':result['status'],'probes':[{k:r[k] for k in ['probe_id','status','returncode','checks']} for r in result['probes']]},ensure_ascii=False))
