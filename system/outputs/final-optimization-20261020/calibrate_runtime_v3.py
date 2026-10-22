import sys,json,shutil
from pathlib import Path
ROOT=Path('/home/xhj/contractedge-system');sys.path.insert(0,str(ROOT/'src'))
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.probe_specs import select_probes
from contractedge.execution.probes import run_method_probes
from contractedge.dataset.benchmark import public_task
benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
output=ROOT/'outputs/final-optimization-20261020'
for case,profile in [('RW-ALERTMANAGER-4705','alertmanager-reload-v1')]:
    dest=output/('calibration-v3-'+case);dest.mkdir(exist_ok=True)
    runner=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',lock_root='/tmp/ce-final-probe-locks',timeout_seconds=2400)
    workspace=dest/'workspace'
    try:
        record=runner.prepare(case,workspace,dest/'prepare')
        row,directory,task,manifest=public_task(benchmark/'public',case)
        requirement=task.get('requirement') or (directory/task['requirement_file']).read_text()
        specs=select_probes(requirement,Path(record['source_root']),[profile])
        if not specs:raise ValueError('Public profile did not match')
        result=run_method_probes(workspace,dest/'probe',benchmark/'tools',specs,timeout_seconds=2400)
        print(case,result['status'],[(p['probe_id'],[(c['id'],c['status']) for c in p['checks']]) for p in result['probes']],flush=True)
    finally:
        if workspace.exists():shutil.rmtree(workspace)
