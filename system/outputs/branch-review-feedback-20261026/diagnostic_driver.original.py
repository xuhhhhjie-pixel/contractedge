"""Two predeclared targets after qualification, once each, never fixed46 or formal150."""
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
SYSTEM=Path('/home/xhj/contractedge-branch-review-20261026/system')
WORK=Path(__file__).parent
ROOT=Path('/home/xhj/contractedge-diagnostic-source048-v2-20261026')
STATE=WORK/'diagnostic-v048-v2-state.json'
QUAL=WORK/'qualification-v048-v2-state.json'
FREEZE=SYSTEM/'docs/development/unified-branch-feedback-v048-20261026-v2.snapshot.json'
CONFIG=SYSTEM/'configs/fixed46-candidate-v048-budget150.json'
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
DEPS='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies'
BENCH='/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019'
sys.dont_write_bytecode=True;sys.path.insert(0,str(SYSTEM/'scripts'))
from run_isolated_method import execution_identity
from score_isolated_method import run as score
from run_fixed_panel import retire_workspace
from verify_development_snapshot import verify_snapshot
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())
state=dict(status='WAITING_ZERO_CALL_QUALIFICATION',rows=[],formal_ready=False,counts_in_46_slot_panel=False,automatic_retry=False,B_opened=False)
def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
def exclusive(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
def verify():assert verify_snapshot(SYSTEM,read(FREEZE),require_current_bindings=True)['status']=='PASS'
assert not STATE.exists() and not ROOT.exists();save()
try:
 deadline=time.monotonic()+7200
 while True:
  q=read(QUAL) if QUAL.exists() else {}
  if q.get('status')=='ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION':break
  if q.get('status')=='STOP_QUALIFICATION_NOT_PASSED' or time.monotonic()>deadline:raise RuntimeError('Qualification not passed; no paid calls allowed')
  time.sleep(10)
 verify()
 old=read('/home/xhj/contractedge-diagnostic-source-v046-v6-20261026/continuation-state.json')
 assert old['status']=='DIAGNOSTIC_FINISHED_REQUIRES_PUBLIC_ROOT_CAUSE_AUDIT'
 assert len(old['rows'])==3 and old['rows'][2]['status']=='NOT_RUN_TARGET_GATE_FAILED'
 attempts=[]
 for case in ['RW-ALERTMANAGER-4705','RW-CERTMANAGER-9116']:
  attempt_id='diagnostic-source048-v2-20261026-'+case.lower();output=ROOT/'methods'/case/case;reg=ROOT/'registrations'/(case+'.json')
  args=argparse.Namespace(method_root=str(SYSTEM),benchmark_root=BENCH,config=str(CONFIG),output=str(output),case=case,python=PYTHON,dependency_root=DEPS,seed=None,runtime_admission=None,panel_registration=None,panel_slot=None,attempt_id=attempt_id)
  identity=execution_identity(args)
  exclusive(reg,dict(identity,role='ISOLATED_DEVELOPMENT_VALIDATION',allow_paid=True,counts_in_46_slot_panel=False))
  attempts.append(dict(case_id=case,attempt_id=attempt_id,output=str(output),registration=str(reg),registration_sha256=sha(reg)))
 exclusive(ROOT/'registration.json',dict(schema_version='ce.minimal_root_cause_diagnostic.v1',source_sha256=read(FREEZE)['source_sha256'],freeze_sha256=sha(FREEZE),qualification_sha256=sha(QUAL),driver_sha256=sha(__file__),attempts=attempts,once_per_case=True,counts_in_46_slot_panel=False,formal_ready=False,cumulative_api_limit_cny=150,original_v046_outcomes='BOTH_SEMANTIC_FAIL_PRESERVED',hypotheses=['Prior supported source remains available through evidence extension and planning','Accurate branch conflict feedback allows correction while preserving unverified obligations'],success_gate='Both independent PASS and neither public FAIL; partial improvement does not open the fixed panel',transient_workspace_policy='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE'))
 state.update(status='REGISTERED_TWO_TARGETS',registration=str(ROOT/'registration.json'));save()
 for a in attempts:
  verify();assert sha(a['registration'])==a['registration_sha256'];output=Path(a['output']);assert not output.parent.exists();output.parent.mkdir(parents=True)
  state.update(status='RUNNING_PREDECLARED_TARGET_ONCE',active_case=a['case_id']);save()
  argv=[PYTHON,str(SYSTEM/'scripts/run_isolated_method.py'),'--execute','--method-root',str(SYSTEM),'--benchmark-root',BENCH,'--config',str(CONFIG),'--output',str(output),'--case',a['case_id'],'--python',PYTHON,'--dependency-root',DEPS,'--registration',a['registration'],'--attempt-id',a['attempt_id'],'--api-key-file','/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key']
  with (output.parent/'method.log').open('x') as stream:code=subprocess.call(argv,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
  if code:raise RuntimeError('Admission/infrastructure stop; never automatic retry: '+a['case_id'])
  receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1
  receipt=read(receipts[0]);assert receipt['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING' and receipt['method_source_unchanged'] and receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
  trace=read(output/'repair.trace.json')
  state.update(status='INDEPENDENT_SCORING',active_case=a['case_id']);save()
  result=(score(receipts[0],BENCH,ROOT/'independent'/a['case_id']) if trace.get('candidate')
          else dict(status='NO_CANDIDATE',model_calls_added=0))
  assert result['status'] in {'PASS','SEMANTIC_FAIL','BUILD_FAIL','PATCH_APPLY_FAIL','METHOD_FAIL','FAIL','NO_CANDIDATE'}
  public={'PUBLIC_REVIEW_PASS':'PASS','PUBLIC_REVIEW_FAIL':'FAIL'}.get((trace.get('last_feedback') or {}).get('semantic_outcome'),'UNKNOWN')
  refs=dict(receipt=dict(path=str(receipts[0]),sha256=sha(receipts[0])),trace=dict(path=str(output/'repair.trace.json'),sha256=sha(output/'repair.trace.json')))
  state['rows'].append(dict(case_id=a['case_id'],attempt_id=a['attempt_id'],method_status=trace['status'],independent_status=result['status'],public_decision=public,patch_bytes=(trace.get('candidate') or {}).get('patch_bytes',0),patch_sha256=(trace.get('candidate') or {}).get('patch_sha256'),evidence_files=refs,counts_in_46_slot_panel=False,formal_ready=False));save();verify()
  if trace.get('candidate'):retire_workspace(dict(transient_workspace_policy='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE',output_root=str(ROOT)),dict(status=result['status'],slot_id=a['attempt_id'],evidence_files=refs))
 passed=all(r['independent_status']=='PASS' and r['public_decision']!='FAIL' for r in state['rows'])
 state.update(status='DIAGNOSTIC_CLOSED_REQUIRES_EFFECT_AND_PUBLIC_TRACE_AUDIT',target_gate_passed=passed,active_case=None)
except BaseException as exc:
 state.update(status='STOP_NO_AUTOMATIC_RETRY',error=type(exc).__name__+': '+str(exc));save();raise
save()
