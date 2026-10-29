"""Freeze tested necessary-check protection; all effect slots remain separate."""
import json,hashlib,shutil,datetime
from pathlib import Path
r=Path('/home/xhj/contractedge-system');e=Path('/home/xhj/contractedge-github-delivery-20261022/system');a=Path(__file__).resolve().parent;o=r/'outputs/reliability-closure-20261020';v='0.6.38'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def files(root):return {p.relative_to(root/'src').as_posix():sha(p) for p in sorted((root/'src').rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
assert '870 passed, 22 subtests passed' in (a/'full-tests.log').read_text()
assert '837 passed, 17 subtests passed' in (a/'portable-tests.log').read_text()
assert '47 passed' in (a/'final-green.log').read_text()
assert read(a/'fresh-review.json')['status']=='REVIEW_IMPORTANT_FIXED_FULL_REGRESSION_PASSED'
replay=read(a/'complete-public-replay.json');assert replay['all_messages_and_schemas_exact'] and replay['same_as_saved_candidate'] and replay['new_model_calls']==0
assert read(a/'before.json')['decision']=='PROMOTE' and read(a/'after.json')['decision']=='REJECT_REGRESSION'
assert read(r/'outputs/changeset-draft-continuity-20261024/single-gate.json')['accepted']
old=read(o/'frozen-method-v0.6.37.json');assert files(r)==old['files']
for p,h in old['harness_files'].items():assert sha(r/p)==h
candidate=files(e);delta=[n for n,h in candidate.items() if old['files'].get(n)!=h]
assert set(delta)=={'contractedge/__init__.py','contractedge/orchestration/candidate_guard.py'},delta
backup=a/'pre-v0638-promotion';assert not backup.exists();backup.mkdir()
for name in ['src/contractedge/__init__.py','src/contractedge/orchestration/candidate_guard.py','pyproject.toml','tests/test_pending_preservation_guard.py']:
 if (r/name).exists():
  (backup/name).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(r/name,backup/name)
 (r/name).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(e/name,r/name)
assert files(r)==candidate
frozen=r/'.superpowers/sdd/2026-10-22-formal-experiment-delivery/frozen-v0.6.38';assert not frozen.exists();frozen.mkdir();shutil.copytree(r/'src',frozen/'src',ignore=shutil.ignore_patterns('__pycache__'));assert files(frozen)==candidate
reg=read(old['registration_path']);reg.update(method_version=v,method_revision_reason='Protect already-PASS necessary public checks from previous pending PRESERVE obligations when re-planning omits them; whole UNKNOWN not promoted, SUPPORTING/target/disputed excluded.',development_probe_order=['A/R4-ACK-CLOUDWATCH-2985/0/candidate'],B_opened_for_method_execution=False)
reg['development_changes'].append(dict(version=v,change='Candidate promotion only: import ALL_OF/NECESSARY_ONLY previous pending PRESERVE checks, preserve current input/environment/probe identity; no model prompt, probe, configuration, edit/call or semantic PASS changes.',api_calls_for_replays=0))
rp=o/f'experiment-registration-v{v}.json';assert not rp.exists();write(rp,reg)
f={**old,'version':v,'files':candidate,'method_digest':hashlib.sha256(json.dumps(candidate,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'candidate_root':str(frozen),'registration_path':str(rp),'full_suite':'870passed22subtests','portable_suite':'837passed17subtests;six historical/native modules excluded','frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'parent_version':'0.6.37','parent_result':'OriginalBroken independentPASS after public3PASS5FAIL→8PASS,9calls/2edits;1/46scored45NOT_RUN. Supplemental planning-only1call excluded repair/panel counts. Never import old result into this version.',
'source_delta':delta,'offline_input_projection':str(a/'complete-public-replay.json'),'cross_case_projection':'Prompt/retrieval/execution source bytes identical to0.6.37; actual9-request replay exact plus11generic guard tests,47related,870full. No new across-repository model results.',
'fresh_source_review':str(a/'fresh-review.json'),'engineering_logs':{'full':str(a/'full-tests.log'),'portable_subset':str(a/'portable-tests.log')},'configuration_delta':{},
'scope':'Necessary local PASS protection survives pending broad UNKNOWN; missing comparable evidence still refuses promotion. No target or SUPPORTING lock, no old UNKNOWN→PASS. No new model/probe/agent/round/call.',
'review':'One Important over-locking finding fixed2RED→GREEN; author malformedcoverage2RED→GREEN;47related plus final full870+22/portable837+17. No rereview/deferred minors.'}
fp=o/f'frozen-method-v{v}.json';assert not fp.exists();write(fp,f)
for group in ['A','B','repeat','recovery']:
 parent=read(o/f'delivery-0.6.37-{group}-state.json');schedule=[]
 for item in parent['schedule']:
  name=item['name'].replace('delivery-0.6.37-','delivery-0.6.38-');target=o/'runs'/name;assert not target.exists();schedule.append({**item,'name':name,'output':str(target)})
 target=o/f'delivery-{v}-{group}-state.json';assert not target.exists();write(target,dict(tag=f'delivery-{v}-{group}',status='HELD_FOR_G4_G5',schedule=schedule,records=[],freeze_sha256=sha(fp)))
assert sum(len(read(o/f'delivery-{v}-{g}-state.json')['schedule']) for g in ['A','B','repeat','recovery'])==46
status=read(r/'CURRENT_STATUS.json');status.update(version=v,implementation_version=v,status='V0638_FROZEN_SINGLE_ACK_REGISTERED',formal_ready=False,implementation_completed_claim=False,repair_reliability_accepted=False,registration=str(rp),freeze=str(fp),frozen_method=str(fp),engineering_tests=870,subtests=22,staged_revision=None,current_experiment=dict(version=v,case_id='R4-ACK-CLOUDWATCH-2985',status='REGISTERED_NOT_DISPATCHED'),same_version_candidate_independent_pass_so_far=0)
status['current_development_revision']=dict(version=v,source_sha256=f['method_digest'],upstream_commit='c892215',local_delta=delta,is_original_v0627_freeze=False)
write(r/'CURRENT_STATUS.json',status);print(json.dumps(dict(version=v,digest=f['method_digest'],slots=46,formal_ready=False)))
