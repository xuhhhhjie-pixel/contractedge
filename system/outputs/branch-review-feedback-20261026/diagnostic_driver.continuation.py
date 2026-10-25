"""Administrative continuation: original Alert closed/scored; untouched Cert once."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
SYSTEM=Path('/home/xhj/contractedge-branch-review-20261026/system')
ROOT=Path('/home/xhj/contractedge-diagnostic-source048-v2-20261026')
WORK=Path(__file__).parent
STATE=WORK/'diagnostic-v048-v2-cert-continuation-state.json'
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
DEPS='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies'
BENCH='/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019'
sys.dont_write_bytecode=True;sys.path.insert(0,str(SYSTEM/'scripts'))
from run_fixed_panel import retire_workspace
from score_isolated_method import run as score
from verify_development_snapshot import verify_snapshot
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert not STATE.exists()
old=read(WORK/'diagnostic-v048-v2-state.json');assert old['status']=='STOP_NO_AUTOMATIC_RETRY' and len(old['rows'])==1
assert old['error'].startswith('FileNotFoundError:') and 'workspace-retirement.json.tmp' in old['error']
registration=read(ROOT/'registration.json');attempts=registration['attempts'];assert len(attempts)==2
row=old['rows'][0];assert row['case_id']=='RW-ALERTMANAGER-4705' and row['independent_status']=='SEMANTIC_FAIL'
for k in ['receipt','trace']:assert sha(row['evidence_files'][k]['path'])==row['evidence_files'][k]['sha256']
score_result=read(ROOT/'independent/RW-ALERTMANAGER-4705/result.json');assert score_result['status']==row['independent_status'] and score_result['identity']['patch_sha256']==row['patch_sha256']
receipt=read(row['evidence_files']['receipt']['path']);assert receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED' and receipt['method_source_unchanged']
freeze=SYSTEM/'docs/development/unified-branch-feedback-v048-20261026-v2.snapshot.json'
assert verify_snapshot(SYSTEM,read(freeze),require_current_bindings=True)['status']=='PASS'
assert sha(freeze)==registration['freeze_sha256']
for a in attempts:assert sha(a['registration'])==a['registration_sha256']
queue=ROOT/'queue'/row['attempt_id'];queue.mkdir(parents=True,exist_ok=True)
retire_workspace(dict(transient_workspace_policy='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE',output_root=str(ROOT)),dict(status=row['independent_status'],slot_id=row['attempt_id'],evidence_files=row['evidence_files']))
with (ROOT/'administrative-continuation.json').open('x') as stream:
 json.dump(dict(original_registration_sha256=sha(ROOT/'registration.json'),original_supervisor_state_sha256=sha(WORK/'diagnostic-v048-v2-state.json'),reason='Missing caller-owned queue directory after method and score already closed; repair metadata only',original_alert_replayed=False,source_changed=False,driver_sha256=sha(__file__),formal_ready=False),stream,indent=2)
state=dict(status='CONTINUE_ORIGINAL_CERT_ONCE',rows=[row],formal_ready=False,counts_in_46_slot_panel=False,automatic_retry=False,B_opened=False)
def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
save()
try:
 a=attempts[1];assert a['case_id']=='RW-CERTMANAGER-9116';output=Path(a['output']);assert not output.parent.exists();output.parent.mkdir(parents=True)
 state['active_case']=a['case_id'];save()
 argv=[PYTHON,str(SYSTEM/'scripts/run_isolated_method.py'),'--execute','--method-root',str(SYSTEM),'--benchmark-root',BENCH,'--config',str(SYSTEM/'configs/fixed46-candidate-v048-budget150.json'),'--output',str(output),'--case',a['case_id'],'--python',PYTHON,'--dependency-root',DEPS,'--registration',a['registration'],'--attempt-id',a['attempt_id'],'--api-key-file','/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key']
 with (output.parent/'method.log').open('x') as stream:code=subprocess.call(argv,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
 if code:raise RuntimeError('Method admission/infrastructure failed; no automatic retry')
 receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1;receipt=read(receipts[0]);assert receipt['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING' and receipt['method_source_unchanged'] and receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
 trace=read(output/'repair.trace.json');state['status']='INDEPENDENT_SCORING';save()
 result=score(receipts[0],BENCH,ROOT/'independent'/a['case_id']) if trace.get('candidate') else dict(status='NO_CANDIDATE')
 assert result['status'] in {'PASS','SEMANTIC_FAIL','BUILD_FAIL','PATCH_APPLY_FAIL','METHOD_FAIL','FAIL','NO_CANDIDATE'}
 public={'PUBLIC_REVIEW_PASS':'PASS','PUBLIC_REVIEW_FAIL':'FAIL'}.get((trace.get('last_feedback') or {}).get('semantic_outcome'),'UNKNOWN')
 refs=dict(receipt=dict(path=str(receipts[0]),sha256=sha(receipts[0])),trace=dict(path=str(output/'repair.trace.json'),sha256=sha(output/'repair.trace.json')))
 state['rows'].append(dict(case_id=a['case_id'],attempt_id=a['attempt_id'],method_status=trace['status'],independent_status=result['status'],public_decision=public,patch_bytes=(trace.get('candidate') or {}).get('patch_bytes',0),patch_sha256=(trace.get('candidate') or {}).get('patch_sha256'),evidence_files=refs,counts_in_46_slot_panel=False,formal_ready=False));save()
 assert verify_snapshot(SYSTEM,read(freeze),require_current_bindings=True)['status']=='PASS'
 if trace.get('candidate'):
  (ROOT/'queue'/a['attempt_id']).mkdir(parents=True,exist_ok=True)
  retire_workspace(dict(transient_workspace_policy='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE',output_root=str(ROOT)),dict(status=result['status'],slot_id=a['attempt_id'],evidence_files=refs))
 state.update(status='DIAGNOSTIC_CLOSED_REQUIRES_PUBLIC_ROOT_CAUSE_AUDIT',target_gate_passed=False,active_case=None)
except BaseException as exc:
 state.update(status='STOP_NO_AUTOMATIC_RETRY',error=type(exc).__name__+': '+str(exc));save();raise
save()
