"""Freeze current source and unchanged acceptance panel before paid validation."""
import hashlib,json,shutil
from pathlib import Path
from datetime import datetime,timezone

root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
base=root/'.superpowers/sdd/2026-10-21-cross-stage-repair-closure';version='0.6.27'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def files(p):return {str(x.relative_to(p/'src')):sha(x) for x in sorted((p/'src').rglob('*')) if x.is_file() and x.suffix in {'.py','.json'}}
old=read(out/'frozen-method-v0.6.26.json')
for log in ['v0.6.27-candidate-verification.log','v0.6.27-root-verification.log']:
    assert '662 passed, 22 subtests passed' in (out/log).read_text(),log
assert files(root)==files(base/'candidate')
changed=[k for k,v in files(root).items() if old['files'].get(k)!=v]
assert set(changed)=={'contractedge/__init__.py','contractedge/orchestration/runner.py','contractedge/changeset/prompt.py','contractedge/retrieval/gap_delivery.py'}
for p,d in old['harness_files'].items():assert sha(root/p)==d,p
assert sha(Path(old['baseline_configuration_path']))==old['configuration_hashes']['baseline']
assert sha(Path(old['configuration_path']))==old['configuration_hashes']['candidate']
config=root/'configs/deepseek-reliability-closure-v7-budget150.json'
prior_cfg=read(Path(old['configuration_path']));new_cfg=read(config)
assert {k:(prior_cfg.get(k),v) for k,v in new_cfg.items() if prior_cfg.get(k)!=v}=={'max_execution_support_bytes':(24000,48000)}
replay=read(out/'SELECTED_SOURCE_REPLAY_after-v0627.json')
assert replay['all_selected_source_delivered'] and replay['owner_type_delivered'] and replay['source_unchanged']
native=read(out/'NATIVE_SOURCE_CAPACITY_v0627.json');assert native['results'][0]['status']=='REJECTED_BEFORE_API'
assert native['results'][1]['status']=='DELIVERED' and native['results'][1]['request_plus_output_bound']<=220000
assert not any(p.name.startswith('delivery-'+version+'-') for p in (out/'runs').iterdir())
frozen=base/('frozen-v'+version);assert not frozen.exists()
shutil.copytree(base/'candidate',frozen,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
source=files(frozen);assert source==files(root);now=datetime.now(timezone.utc).isoformat()
reg=read(Path(old['registration_path']))
reg.update(method_version=version,method_revision_reason='Prevent selected-source loss at diagnosis-to-plan after the single acquisition; distinguish missing necessary contracts from a repair helper that is not yet implemented. Accurately label exact-query misses.',development_probe_order=['A/R4-ACK-CLOUDWATCH-2985/0/candidate'],B_opened_for_method_execution=False)
reg['development_changes'].append(dict(version=version,change='Preserve finally selected current source through planning and native execution. Exact fallback audit is lexical context, not FOUND. Clarify new-helper planning without weakening evidence/target/behavior gates. Local read-only cap 24000->48000 follows measured29506-byte delivery need; Top12,220000 full-context,12 calls,2 rounds,150CNY cap unchanged.',api_calls_for_replays=0))
regpath=out/f'experiment-registration-v{version}.json';write(regpath,reg)
f={**old,'version':version,'method_digest':hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
   'files':source,'candidate_root':str(frozen),'configuration_path':str(config),
   'configuration_hashes':{**old['configuration_hashes'],'candidate':sha(config)},'registration_path':str(regpath),
   'full_suite':'662 passed,22 subtests passed','frozen_at':now,'source_delta':changed,'parent_version':'0.6.26',
   'parent_result':'1 scored,45 NOT_RUN. ACK NEEDS_EVIDENCE / NO_CANDIDATE after6 calls; correct API contract diagnosis retained, selected SDK client type missing from final plan. Closed without reusing/repeating slots.',
   'engineering_logs':{'root':str(out/'v0.6.27-root-verification.log'),'candidate':str(out/'v0.6.27-candidate-verification.log')},
   'scope':'Actual saved public failure replay captures final plan with12/12 selected sources,118716 input+16384 output. Read-only native projection delivers29506 bytes at122408 total; no repair result inferred. Fresh same-version effect pending.',
   'review':'Author review,3 generic regressions including real front/Ready/execution input and budget rejection; saved actual public response before/after replay; native old-plan carrier input-only projection; source-isolated/root complete suites. No external reviewer or hidden grader source used.',
   'configuration_delta':{'max_execution_support_bytes':[24000,48000]},'setup_deviation':None}
f['offline_replays']=old['offline_replays']+['ACK_FAILURE_ALIGNMENT_v0626.json','SELECTED_SOURCE_REPLAY_before-v0626.json','SELECTED_SOURCE_REPLAY_after-v0627.json','NATIVE_SOURCE_CAPACITY_v0627.json','selected-source-v0627-focused.log']
fp=out/f'frozen-method-v{version}.json';write(fp,f)
for group in ['A','B','repeat','recovery']:
    members=[r['case_id'] for r in reg[group]] if group in ['A','B'] else reg['key_repeat_cases'] if group=='repeat' else list(reg['recovery_seed_paths'])
    repeats=[0] if group in ['A','B'] else [1] if group=='repeat' else [0,1];schedule=[]
    for case in members:
        for repeat in repeats:
            order=['baseline','candidate']
            if int(hashlib.sha256((group+case+str(repeat)).encode()).hexdigest(),16)%2:order.reverse()
            for variant in order:
                name=f'delivery-{version}-{group}-{case}-{repeat}-{variant}';target=out/'runs'/name;assert not target.exists()
                schedule.append(dict(case_id=case,repeat=repeat,variant=variant,name=name,output=str(target)))
    write(out/f'delivery-{version}-{group}-state.json',dict(tag=f'delivery-{version}-{group}',schedule=schedule,status='HELD_FOR_G4_G5',records=[],freeze_sha256=sha(fp)))
assert sum(len(read(out/f'delivery-{version}-{g}-state.json')['schedule']) for g in ['A','B','repeat','recovery'])==46
write(out/'FREEZE_PRECALL_VERIFICATION_v0627.json',dict(verified_at=now,method_digest=f['method_digest'],all_source_config_harness_verified=True,registration_and_all_46_slots_written=True,no_worker_started_by_setup_script=True,no_results_observed_for_new_version=True,source_delta=changed,config_delta=f['configuration_delta']))
print(json.dumps(dict(version=version,digest=f['method_digest'],preregistration_verified=True)))
