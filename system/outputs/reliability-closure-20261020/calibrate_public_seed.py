import json,sys,subprocess,shutil,hashlib
from pathlib import Path
ROOT=Path('/home/xhj/contractedge-system');sys.path.insert(0,str(ROOT/'outputs/reliability-closure-20261020/baseline-v0.5.5/src'))
from contractedge.execution.public import BenchmarkPublicRunner
OUT=ROOT/'outputs/reliability-closure-20261020/seed-calibration-alertmanager';OUT.mkdir(exist_ok=False)
BENCH=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
runner=BenchmarkPublicRunner(BENCH/'public',BENCH/'tools',lock_root='/tmp/contractedge-system-native-locks',timeout_seconds=2400,query='configuration reload active groups',method_probe_profiles=['alertmanager-reload-v1'])
workspace=OUT/'workspace';patch=ROOT/'outputs/deepseek-empty-reasoning-live-regression-v1-20261020/candidate.patch'
try:
 record=runner.prepare('RW-ALERTMANAGER-4705',workspace,OUT/'prepare')
 subprocess.run(['git','apply',str(patch)],cwd=record['source_root'],check=True,capture_output=True)
 result=runner.observe(workspace,OUT/'public')
 checks=[o.get('behavior_check') for o in result.get('observations',[]) if o.get('behavior_check')]
 summary={'case_id':'RW-ALERTMANAGER-4705','seed_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),'status':result['status'],'checks':checks,'scope':'PUBLIC_ONLY_SEED_QUALIFICATION','hidden_materials_loaded':False,'api_calls':0}
 (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
finally:
 if workspace.exists():shutil.rmtree(workspace)
