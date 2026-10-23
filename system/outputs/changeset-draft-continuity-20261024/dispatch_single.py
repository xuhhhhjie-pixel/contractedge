"""One predeclared original-Broken development gate; no scoring in this process."""
import json,hashlib,subprocess,sys,os
from pathlib import Path
r=Path('/home/xhj/contractedge-system');out=r/'outputs/reliability-closure-20261020'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):
 p=Path(p);temporary=p.with_suffix('.tmp');temporary.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');temporary.replace(p)
fp=out/'frozen-method-v0.6.37.json';freeze=read(fp);root=Path(freeze['candidate_root']);config=Path(freeze['configuration_path'])
actual={str(p.relative_to(root/'src')):sha(p) for p in (root/'src').rglob('*') if p.is_file() and p.suffix in {'.py','.json'}}
assert actual==freeze['files'] and sha(config)==freeze['configuration_hashes']['candidate']
assert all(sha(r/p)==h for p,h in freeze['harness_files'].items())
assert sha(freeze['probe_qualification'])==freeze['probe_qualification_sha256']
statepath=out/'delivery-0.6.37-A-state.json';state=read(statepath)
slot=next(x for x in state['schedule'] if x['case_id']=='R4-ACK-CLOUDWATCH-2985' and x['variant']=='candidate' and x['repeat']==0)
directory=Path(slot['output']);assert not directory.exists()
state.update(status='RUNNING_SINGLE_GATE',active=slot);write(statepath,state)
status=read(r/'CURRENT_STATUS.json');status['current_experiment']['status']='RUNNING';write(r/'CURRENT_STATUS.json',status)
command=[sys.executable,freeze['worker_path'],'--method-root',str(root),'--config',str(config),'--output',str(directory),'--case',slot['case_id']]
with (out/(slot['name']+'.log')).open('x') as stream:
 result=subprocess.run(command,cwd=r,env={**os.environ,'PYTHONPATH':str(r/'.topomen-deps')},stdout=stream,stderr=subprocess.STDOUT)
state=read(statepath)
if result.returncode:
 state.update(status='WORKER_ERROR_REQUIRES_AUDIT',returncode=result.returncode)
else:
 batch=read(directory/'batch.json');record=next(x for x in batch['records'] if x['case_id']==slot['case_id']);assert record['status']!='RUNNING'
 state.update(status='HELD_FOR_SINGLE_GATE_SCORING',records=[{**slot,'method_status':record['status'],'batch_sha256':sha(directory/'batch.json')}])
 status=read(r/'CURRENT_STATUS.json');status['current_experiment'].update(status=record['status'],output=str(directory));write(r/'CURRENT_STATUS.json',status)
write(statepath,state);print(json.dumps(dict(status=state['status'],returncode=result.returncode)))
