"""Same frozen public probe on an already fixed historical candidate; no scorer/API."""
import sys,json,shutil,os,subprocess,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from contractedge.execution.probes import run_method_probes
from contractedge.execution.probe_specs import select_probes
from contractedge.source.snapshot import compute_snapshot
out=Path(__file__).parent;base=out/'probe-broken-workspace';record=json.loads((base/'workspace.json').read_text())
w=out/'probe-historical-v0.4.7-workspace';w.mkdir();src=w/'source';shutil.copytree(record['source_root'],src,ignore=shutil.ignore_patterns('.git','.cache','.contractedge-deps'))
inputs=w/'inputs';shutil.copytree(record['inputs_root'],inputs,copy_function=os.link)
(w/'workspace.json').write_text(json.dumps({**record,'source_root':str(src.resolve()),'inputs_root':str(inputs.resolve())})+'\n')
subprocess.run(['git','init','-q',str(src)],check=True)
patch=ROOT/'outputs/deepseek-acceptance-recovery-v1-20261020/cases/R3-KPT-4673/candidate.patch'
subprocess.run(['git','-C',str(src),'apply','--check',str(patch)],check=True);subprocess.run(['git','-C',str(src),'apply',str(patch)],check=True)
m=json.loads((ROOT/'outputs/deepseek-acceptance-recovery-v1-20261020/cases/R3-KPT-4673/method_input.json').read_text())
specs=select_probes(m['requirement_text'],src,['kpt-configpath-v1']);r=run_method_probes(w,out/'probe-historical-v0.4.7',ROOT.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/tools',specs)
receipt={'historical_patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),'snapshot_id':compute_snapshot(src).snapshot_id,'probe_sha256':specs[0]['sha256'],'api_calls':0,'private_materials_loaded':False,'status':r['status'],'checks':[{'id':c['id'],'status':c['status']} for p in r['probes'] for c in p['checks']]}
(out/'historical-probe-comparison.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
