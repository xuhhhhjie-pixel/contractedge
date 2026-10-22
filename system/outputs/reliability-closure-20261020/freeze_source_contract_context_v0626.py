"""Freeze unchanged experiment protocol plus run-local generation continuity."""
import hashlib,json,shutil
from pathlib import Path
from datetime import datetime,timezone
root=Path.cwd();out=root/'outputs/reliability-closure-20261020';base=root/'.superpowers/sdd/2026-10-21-cross-stage-repair-closure';version='0.6.26'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def files(p):return {str(x.relative_to(p/'src')):sha(x) for x in sorted((p/'src').rglob('*')) if x.is_file() and x.suffix in {'.py','.json'}}
old=read(out/'frozen-method-v0.6.25.json')
for log in ['v0.6.26-final-candidate-verification.log','v0.6.26-root-verification.log']:assert '659 passed, 22 subtests passed' in (out/log).read_text(),log
assert files(root)==files(base/'candidate')
changed=[k for k,v in files(root).items() if old['files'].get(k)!=v];assert set(changed)=={'contractedge/__init__.py','contractedge/orchestration/runner.py','contractedge/diagnosis/evidence.py','contractedge/diagnosis/prompt.py','contractedge/contracts/schema/semantic_changeset.v1_1.json'}
for p,d in old['harness_files'].items():assert sha(root/p)==d,p
for v,k in [('candidate','configuration_path'),('baseline','baseline_configuration_path')]:assert sha(Path(old[k]))==old['configuration_hashes'][v]
replay=read(out/'ACK_CONTRACT_CONTEXT_REPLAY_v0626.json');assert replay['producer_body_still_delivered'] and replay['field_contexts']==1 and replay['snapshot_unchanged']
assert not any(p.name.startswith('delivery-'+version+'-') for p in (out/'runs').iterdir())
frozen=base/('frozen-v'+version);assert not frozen.exists();shutil.copytree(base/'candidate',frozen,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
source=files(frozen);assert source==files(root);now=datetime.now(timezone.utc).isoformat()
reg=read(Path(old['registration_path']));reg.update(method_version=version,method_revision_reason='Highlight exact requirement-local Go JSON-field comments within selected current source; bind the source as a mandatory review/support record through diagnosis, plan and execution. No automatic semantic truth or behavior verdict.',development_probe_order=['A/R4-ACK-CLOUDWATCH-2985/0/candidate'],B_opened_for_method_execution=False)
reg['development_changes'].append(dict(version=version,change='Bounded 4-record/8000-byte field-document windows, precise locators and excerpt digests; retained within original Top12/full request budget. Shared required-source projection makes source contract review and native read-only delivery non-optional even if diagnosis omits citation. Existing review array capacity 6->12, covering bounded Top12 without changing its semantic checks or fields. No new module, API call allowance or verdict relaxation.',api_calls_for_replays=0))
regpath=out/f'experiment-registration-v{version}.json';write(regpath,reg)
f={**old,'version':version,'method_digest':hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'files':source,'candidate_root':str(frozen),'registration_path':str(regpath),'full_suite':'659 passed,22 subtests passed','frozen_at':now,'source_delta':changed,'parent_version':'0.6.25','parent_result':'1 scored,45 NOT_RUN. ACK candidate SEMANTIC_FAIL after producer-only nil/empty mapping patch, public build passed but semantic UNKNOWN. API contract was present in diagnosis/plan long source, absent from native input. Closed without retries or pooling.','engineering_logs':{'root':str(out/'v0.6.26-root-verification.log'),'candidate':str(out/'v0.6.26-final-candidate-verification.log')},'scope':'Actual current ACK initial request delivers explicit local API field contract plus producer source at117422+16384 within original budgets. Generic integration passes front half, Ready and execution builder with uncited source contract. Fresh behavior/independent effect pending.','review':'Author review, exact source positions/digest, bounded deduplication, no-query/no-doc/non-Go controls, rereading current comments, generic full front/Ready/execution builder including honest NOT_APPLICABLE, seven-review capacity RED->GREEN, actual ACK source/observation initial request capture and complete source-isolated/root suites. No external code review.','setup_deviation':None}
f['offline_replays']=old['offline_replays']+['ACK_FAILURE_ALIGNMENT_v0625.json','ACK_CONTRACT_CONTEXT_REPLAY_v0626.json','source-contract-v0626-pipeline.log','source-contract-capacity-v0626-green.log'];fp=out/f'frozen-method-v{version}.json';write(fp,f)
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
write(out/'FREEZE_PRECALL_VERIFICATION_v0626.json',dict(verified_at=now,method_digest=f['method_digest'],all_source_config_harness_verified=True,registration_and_all_46_slots_written=True,no_worker_started_by_setup_script=True,no_results_observed_for_new_version=True,source_delta=changed))
print(json.dumps(dict(version=version,digest=f['method_digest'],preregistration_verified=True)))
