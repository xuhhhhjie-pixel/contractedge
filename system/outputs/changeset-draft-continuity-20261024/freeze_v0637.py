"""Freeze verified same-context ChangeSet draft continuity; no new retry or semantic waiver."""
import json,hashlib,shutil,datetime
from pathlib import Path
r=Path('/home/xhj/contractedge-system');e=Path('/home/xhj/contractedge-github-delivery-20261022/system');a=Path(__file__).resolve().parent;o=r/'outputs/reliability-closure-20261020';v='0.6.37'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def files(root):return {p.relative_to(root/'src').as_posix():sha(p) for p in sorted((root/'src').rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
assert '859 passed, 22 subtests passed' in (a/'full-tests.log').read_text()
assert '826 passed, 17 subtests passed' in (a/'portable-tests.log').read_text()
proof=read(a/'after.json');assert proof['captured_plan_correction'] and proof['rejected_draft_present'] and proof['source_restored']
assert proof['input_bound']+proof['output_bound']<=proof['total_context_limit']==220000
assert proof['first_five_messages_match_saved']==[True]*5
assert proof['unchanged_original_request_messages'] and proof['output_schema_unchanged']
assert read(a/'cross-case.json')==read(r/'outputs/build-failure-recovery-20261024/cross-case-v0636-final.json')
assert read(a/'fresh-review.json')['status']=='REVIEW_AND_FULL_REGRESSION_PASSED'
old=read(o/'frozen-method-v0.6.36.json')
assert files(r)==old['files']
for p,h in old['harness_files'].items():assert sha(r/p)==h
backup=a/'pre-v0637-promotion';assert not backup.exists();backup.mkdir()
for name in ['src/contractedge/__init__.py','src/contractedge/changeset/prompt.py','src/contractedge/orchestration/runner.py','src/contractedge/orchestration/repair.py','pyproject.toml','tests/test_changeset_draft_continuity.py']:
 if (r/name).exists():
  (backup/name).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(r/name,backup/name)
 (r/name).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(e/name,r/name)
assert files(r)==files(e)
frozen=r/'.superpowers/sdd/2026-10-22-formal-experiment-delivery/frozen-v0.6.37';assert not frozen.exists();frozen.mkdir()
shutil.copytree(r/'src',frozen/'src',ignore=shutil.ignore_patterns('__pycache__'));assert files(frozen)==files(r)
reg=read(old['registration_path']);reg.update(method_version=v,method_revision_reason='Preserve the exact rejected planning draft in same-context correction and current accepted plan in public-feedback revision. Bind run/input/snapshot/diagnosis; clear after new evidence. No extra calls or weaker semantics.',development_probe_order=['A/R4-ACK-CLOUDWATCH-2985/0/candidate'],B_opened_for_method_execution=False)
reg['development_changes'].append(dict(version=v,change='Actual .36 corrective input now includes exact rejected READY draft and validation error at150107+16384 under220000; first5requests unchanged. No programmatic field repair, extra retry or new probe.',api_calls_for_replays=0))
rp=o/f'experiment-registration-v{v}.json';assert not rp.exists();write(rp,reg)
manifest=files(frozen)
f={**old,'version':v,'files':manifest,'method_digest':hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'candidate_root':str(frozen),'registration_path':str(rp),'full_suite':'859 passed,22 subtests passed','portable_suite':'826 passed,17 subtests passed; six historical/native modules excluded','frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'parent_version':'0.6.36','parent_result':'OriginalBroken INVALID_PLAN/NO_CANDIDATE6calls before edits; correction omitted rejected draft then model omitted status;1/46scored45NOT_RUN.',
 'source_delta':[n for n,h in manifest.items() if old['files'].get(n)!=h],
 'offline_input_projection':str(a/'after.json'),'cross_case_projection':str(a/'cross-case.json'),'fresh_source_review':str(a/'fresh-review.json'),
 'engineering_logs':{'full':str(a/'full-tests.log'),'portable_subset':str(a/'portable-tests.log')},'configuration_delta':{},
 'scope':'Same-context rejected/current plans are untrusted prior planning state, never evidence. Complete unchanged schema validation and budgets still apply. New diagnosis clears prior draft. No new probe/agent/round/call or verdict relaxation.',
 'review':'Fresh read-only source review noImportant/Critical;12counterexamples, full859+22, portable826+17. Author malformed-state guard verified RED to GREEN.'}
fp=o/f'frozen-method-v{v}.json';assert not fp.exists();write(fp,f)
for group in ['A','B','repeat','recovery']:
 parent=read(o/f'delivery-0.6.36-{group}-state.json');schedule=[]
 for item in parent['schedule']:
  name=item['name'].replace('delivery-0.6.36-','delivery-0.6.37-');target=o/'runs'/name;assert not target.exists();schedule.append({**item,'name':name,'output':str(target)})
 target=o/f'delivery-{v}-{group}-state.json';assert not target.exists();write(target,dict(tag=f'delivery-{v}-{group}',status='HELD_FOR_G4_G5',schedule=schedule,records=[],freeze_sha256=sha(fp)))
assert sum(len(read(o/f'delivery-{v}-{g}-state.json')['schedule']) for g in ['A','B','repeat','recovery'])==46
status=read(r/'CURRENT_STATUS.json');status.update(version=v,implementation_version=v,status='V0637_FROZEN_SINGLE_ACK_REGISTERED',formal_ready=False,implementation_completed_claim=False,repair_reliability_accepted=False,registration=str(rp),freeze=str(fp),frozen_method=str(fp),engineering_tests=859,subtests=22,staged_revision=None,current_experiment=dict(version=v,case_id='R4-ACK-CLOUDWATCH-2985',status='REGISTERED_NOT_DISPATCHED'))
status['current_development_revision']=dict(version=v,source_sha256=f['method_digest'],upstream_commit='c892215',local_delta=f['source_delta'],is_original_v0627_freeze=False)
write(r/'CURRENT_STATUS.json',status)
print(json.dumps(dict(version=v,digest=f['method_digest'],slots=46,formal_ready=False)))
