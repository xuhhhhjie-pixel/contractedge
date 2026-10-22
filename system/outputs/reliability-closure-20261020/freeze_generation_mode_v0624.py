"""Freeze unchanged experiment protocol plus run-local generation continuity."""
import hashlib,json,shutil
from pathlib import Path
from datetime import datetime,timezone
root=Path.cwd();out=root/'outputs/reliability-closure-20261020';base=root/'.superpowers/sdd/2026-10-21-cross-stage-repair-closure';version='0.6.24'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def files(p):return {str(x.relative_to(p/'src')):sha(x) for x in sorted((p/'src').rglob('*')) if x.is_file() and x.suffix in {'.py','.json'}}
old=read(out/'frozen-method-v0.6.23.json')
for log in ['v0.6.24-candidate-rootcwd-verification-final.log','v0.6.24-root-verification.log']:assert '648 passed, 22 subtests passed' in (out/log).read_text(),log
assert files(root)==files(base/'candidate')
changed=[k for k,v in files(root).items() if old['files'].get(k)!=v];assert set(changed)=={'contractedge/__init__.py','contractedge/orchestration/runner.py'}
for p,d in old['harness_files'].items():assert sha(root/p)==d,p
for v,k in [('candidate','configuration_path'),('baseline','baseline_configuration_path')]:assert sha(Path(old[k]))==old['configuration_hashes'][v]
assert read(out/'ack-generation-mode-v0623-before/summary.json')['thinking'] is None
replay=read(out/'ack-generation-mode-v0624-after/summary.json');assert replay['thinking']=='disabled' and replay['exact_original_extension_body'] and replay['current_source_unchanged']
assert not any(p.name.startswith('delivery-'+version+'-') for p in (out/'runs').iterdir())
frozen=base/('frozen-v'+version);assert not frozen.exists();shutil.copytree(base/'candidate',frozen,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
source=files(frozen);assert source==files(root);now=datetime.now(timezone.utc).isoformat()
reg=read(Path(old['registration_path']));reg.update(method_version=version,method_revision_reason='Keep completed reasoning-starved JSON generation mode for subsequent same-prompt diagnosis correction and bounded evidence extension within the same front half. No new call allowance, evidence, schema or scorer changes.',development_probe_order=['A/R4-ACK-CLOUDWATCH-2985/0/candidate'],B_opened_for_method_execution=False)
reg['development_changes'].append(dict(version=version,change='Run-local prompt-version scoped generation-mode continuity after completed truncation recovery. No case-id dispatch or helper/patch recipe. Unchanged 46 slots, limits and independent scorer.',api_calls_for_replays=0))
regpath=out/f'experiment-registration-v{version}.json';write(regpath,reg)
f={**old,'version':version,'method_digest':hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'files':source,'candidate_root':str(frozen),'registration_path':str(regpath),'full_suite':'648 passed,22 subtests passed','frozen_at':now,'source_delta':changed,'parent_version':'0.6.23','parent_result':'11 scored,35 NOT_RUN. Four A candidate PASS, ACK candidate NO_CANDIDATE vs baseline PASS, one registered KPT recovery candidate PASS. Closed without retries or pooling.','engineering_logs':{'root':str(out/'v0.6.24-root-verification.log'),'candidate':str(out/'v0.6.24-candidate-rootcwd-verification-final.log')},'scope':'Actual saved ACK stage sequence reaches exact fifth extension request with recovered generation mode; repair and same-version effects pending.','review':'Author review, positive/negative integration cases, actual saved trajectory before/after exact-body replay and full source-isolated suite. No new external code review.','setup_deviation':None}
f['offline_replays']=old['offline_replays']+['ACK_FAILURE_ALIGNMENT_v0623.json','ack-generation-mode-v0623-before/summary.json','ack-generation-mode-v0624-after/summary.json'];fp=out/f'frozen-method-v{version}.json';write(fp,f)
for group in ['A','B','repeat','recovery']:
 members=[r['case_id'] for r in reg[group]] if group in ['A','B'] else reg['key_repeat_cases'] if group=='repeat' else list(reg['recovery_seed_paths'])
 repeats=[0] if group in ['A','B'] else [1] if group=='repeat' else [0,1];schedule=[]
 for case in members:
  for repeat in repeats:
   order=['baseline','candidate']
   if int(hashlib.sha256((group+case+str(repeat)).encode()).hexdigest(),16)%2:order.reverse()
   for variant in order:
    name=f'delivery-{version}-{group}-{case}-{repeat}-{variant}';target=out/'runs'/name;assert not target.exists();schedule.append(dict(case_id=case,repeat=repeat,variant=variant,name=name,output=str(target)))
 write(out/f'delivery-{version}-{group}-state.json',dict(tag=f'delivery-{version}-{group}',schedule=schedule,status='HELD_FOR_G4_G5',records=[],freeze_sha256=sha(fp)))
assert sum(len(read(out/f'delivery-{version}-{g}-state.json')['schedule']) for g in ['A','B','repeat','recovery'])==46
write(out/'FREEZE_PRECALL_VERIFICATION_v0624.json',dict(verified_at=now,method_digest=f['method_digest'],all_source_config_harness_verified=True,registration_and_all_46_slots_written=True,no_worker_started_by_setup_script=True,no_results_observed_for_new_version=True,source_delta=changed))
print(json.dumps(dict(version=version,digest=f['method_digest'],preregistration_verified=True)))
