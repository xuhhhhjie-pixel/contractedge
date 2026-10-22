"""Create and verify all preregistration artifacts before any worker launch."""
import hashlib
import json
import shutil
from datetime import datetime,timezone
from pathlib import Path

root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
base=root/'.superpowers/sdd/2026-10-21-cross-stage-repair-closure'
version='0.6.21'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
def source_files(p):return {str(x.relative_to(p/'src')):sha(x) for x in sorted((p/'src').rglob('*')) if x.is_file() and x.suffix in {'.py','.json'}}
old=read(out/'frozen-method-v0.6.20.json')
for n in ['v0.6.21-candidate-verification.log','v0.6.21-root-verification.log']:
    assert '613 passed, 22 subtests passed' in (out/n).read_text(),n
assert source_files(root)==source_files(base/'candidate')
for path,digest in old['harness_files'].items():assert sha(root/path)==digest,path
for v,key in [('candidate','configuration_path'),('baseline','baseline_configuration_path')]:assert sha(Path(old[key]))==old['configuration_hashes'][v]
assert read(out/'ready-consumer-native-replay-v0621/summary.json')['ready_trace_consumer_passed']
assert len(read(out/'independent-v0.6.20/records.json'))==2
freeze_root=base/('frozen-v'+version);assert not freeze_root.exists()
shutil.copytree(base/'candidate',freeze_root,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
files=source_files(freeze_root);assert files==source_files(root)
changed=[k for k in files if files[k]!=old['files'].get(k)]
assert set(changed)=={'contractedge/__init__.py','contractedge/orchestration/runner.py','contractedge/orchestration/repair.py'}
now=datetime.now(timezone.utc).isoformat()
registration=read(out/'experiment-registration-v0.6.20.json')
registration.update(method_version=version,method_revision_reason='One shared source prerequisite projection for planner and Ready consumer; saved actual responses reach native execution request',development_probe_order=['A/R3-KOPF-726/0/candidate'],B_opened_for_method_execution=False)
registration['development_changes'].append(dict(version=version,change='Share existing source prerequisite projection; no source search, prompt, model allowance, or semantic gate changes',api_calls_for_replays=0))
reg_path=out/('experiment-registration-v'+version+'.json');write(reg_path,registration)
freeze={**old,'version':version,'method_digest':hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'files':files,'candidate_root':str(freeze_root),'registration_path':str(reg_path),'full_suite':'613 passed,22 subtests passed','frozen_at':now,'source_delta':changed,'parent_version':'0.6.20','parent_result':'Two registered KOPF slots retained NO_CANDIDATE: primary HTTP IncompleteRead, repeat Ready consumer projection mismatch. Other44 NOT_RUN.','engineering_logs':{'root':str(out/'v0.6.21-root-verification.log'),'candidate':str(out/'v0.6.21-candidate-verification.log')},'scope':'Source-prerequisite producer/consumer consistency and saved-response native replay verified; real G4 and full same-version effect gates pending','review':'Existing delta review CODE_REVIEW_v0620.md; new bounded shared-projection fix self-reviewed with actual Ready RED/GREEN, full native replay, and whole-suite regression; no fresh review of this fix','setup_deviation':None}
freeze['offline_replays']=old['offline_replays']+['ready-consumer-native-replay-v0621/summary.json']
freeze_path=out/('frozen-method-v'+version+'.json');write(freeze_path,freeze)
# All 46 slots materialize and validate before the separate worker command.
for group in ['A','B','repeat','recovery']:
    members=[r['case_id'] for r in registration[group]] if group in ['A','B'] else registration['key_repeat_cases'] if group=='repeat' else list(registration['recovery_seed_paths'])
    repeats=[0] if group in ['A','B'] else [1] if group=='repeat' else [0,1]
    schedule=[]
    for case in members:
        for repeat in repeats:
            order=['baseline','candidate']
            if int(hashlib.sha256((group+case+str(repeat)).encode()).hexdigest(),16)%2:order.reverse()
            for variant in order:
                name=f'delivery-{version}-{group}-{case}-{repeat}-{variant}'
                target=out/'runs'/name;assert not target.exists()
                schedule.append(dict(case_id=case,repeat=repeat,variant=variant,name=name,output=str(target)))
    write(out/f'delivery-{version}-{group}-state.json',dict(tag=f'delivery-{version}-{group}',schedule=schedule,status='HELD_FOR_G4_G5',records=[],freeze_sha256=sha(freeze_path)))
assert sum(len(read(out/f'delivery-{version}-{g}-state.json')['schedule']) for g in ['A','B','repeat','recovery'])==46
write(out/'FREEZE_PRECALL_VERIFICATION_v0621.json',dict(verified_at=now,method_digest=freeze['method_digest'],all_source_config_harness_verified=True,registration_and_all_46_slots_written=True,no_worker_started_by_setup_script=True,no_results_observed_for_new_version=True,source_delta=changed))
print(json.dumps(dict(version=version,digest=freeze['method_digest'],preregistration_verified=True)))
