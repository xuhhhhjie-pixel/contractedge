"""One source056 original-Broken KPT after exact engineering qualification."""
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
SYSTEM=Path('/home/xhj/contractedge-planning-branch-continuity-20261026/system');OUT=SYSTEM/'outputs/planning-branch-continuity-20261026'
ROOT=Path('/home/xhj/contractedge-diagnostic-source056-v2-20261026');STATE=Path(__file__).parent/'diagnostic-v056-v2-state.json'
QUAL=Path(__file__).parent/'qualification-v056-v2-state.json'
ORIGINAL=SYSTEM/'docs/development/planning-branch-continuity-v056-20261026-v3.snapshot.json'
FREEZE=SYSTEM/'docs/development/planning-branch-continuity-v056-20261026-v4.snapshot.json'
CONFIG=SYSTEM/'configs/fixed46-candidate-v056-budget150.json'
CASE='R3-KPT-4673'
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
DEPS='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies'
BENCH='/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019'
sys.dont_write_bytecode=True;sys.path.insert(0,str(SYSTEM/'scripts'))
from run_isolated_method import execution_identity,check_registration
from score_isolated_method import run as score
from verify_development_snapshot import verify_snapshot
from run_fixed_panel import retire_workspace
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();read=lambda p:json.loads(Path(p).read_text())
def exclusive(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def verify():
 assert verify_snapshot(SYSTEM,read(FREEZE),require_current_bindings=True)['status']=='PASS'
 assert verify_snapshot(SYSTEM,read(ORIGINAL),require_current_bindings=True)['status']=='PASS'
assert sha(__file__)==sha(OUT/'diagnostic_driver_v056_v2.py')
assert not STATE.exists() and not ROOT.exists()
state=dict(status='WAITING_EXACT_SOURCE056_QUALIFICATION',rows=[],formal_ready=False,counts_in_46_slot_panel=False,automatic_retry=False)
def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
save()
try:
 deadline=time.monotonic()+7200
 while True:
  q=read(QUAL) if QUAL.exists() else {}
  if q.get('status')=='ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION':break
  if q.get('status','').startswith('STOP') or time.monotonic()>deadline:raise RuntimeError('Qualification not passed, no paid dispatch')
  time.sleep(10)
 verify();a,b=read(ORIGINAL),read(FREEZE);assert a['source_sha256']==b['source_sha256'] and a['configuration']==b['configuration'] and a['fixture_files']==b['fixture_files']
 assert all(b['tooling_files'].get(k)==v for k,v in a['tooling_files'].items())
 full=read(OUT/'FULL_REGRESSION_COMPLETION_v056_v2.json')
 assert full['status']=='PASS' and full['exit_code']==0 and (full['tests'],full['subtests'])==(2061,91) and full['freeze_sha256']==sha(ORIGINAL) and full['source_sha256']==b['source_sha256'] and full['log_sha256']==sha(OUT/'full-v056-v2.log')
 replay=read(OUT/'SAVED_HANDOFF_REPLAY.json');assert replay['status']=='PUBLIC_PLANNING_BRANCH_HANDOFF_REPLAY_PASS' and replay['validator_concern_present'] and not replay['new_model_calls']
 output=ROOT/'methods'/CASE/CASE;reg=ROOT/'registration.json';attempt='diagnostic-source056-v2-r3-kpt-4673-20261026'
 args=argparse.Namespace(method_root=str(SYSTEM),benchmark_root=BENCH,config=str(CONFIG),output=str(output),case=CASE,python=PYTHON,dependency_root=DEPS,seed=None,runtime_admission=None,panel_registration=None,panel_slot=None,attempt_id=attempt)
 identity=execution_identity(args);exclusive(reg,dict(identity,role='ISOLATED_DEVELOPMENT_VALIDATION',allow_paid=True,counts_in_46_slot_panel=False));check_registration(reg,identity)
 exclusive(ROOT/'experiment.json',dict(schema_version='ce.single_root_cause_validation.v1',source_sha256=b['source_sha256'],freeze_sha256=sha(FREEZE),registration_sha256=sha(reg),driver_sha256=sha(__file__),qualification_sha256=sha(QUAL),before_implementation='Source055 KPT SEMANTIC_FAIL/publicFAIL; first plan recognized validator concern but extension diagnostic lacked planning review. First trial regression restored in second edit; validator still failed.',hypothesis='Existing same-snapshot planning-evidence diagnosis receives previously recognized in-scope branch concerns and may form a complete supported plan; no forcing of causality or units.',success='Fresh patch independentPASS and publicnotFAIL; audit actual handoff use, diagnosis units and plan/validator behavior. If mechanism not triggered, success is only an originalBroken repair witness, not causal proof.',once_only=True,max_model_calls=12,max_rounds=2,cumulative_budget_cny=150,counts_in_46_slot_panel=False,formal_ready=False))
 current=read(SYSTEM/'CURRENT_STATUS.json');current['current_development'].update(status='V056_QUALIFIED_REGISTERED_SINGLE_KPT_RUNNING',snapshot=str(FREEZE.relative_to(SYSTEM)),paid_dispatch_allowed='THIS_REGISTERED_DEVELOPMENT_ATTEMPT_ONLY');(SYSTEM/'CURRENT_STATUS.json').write_text(json.dumps(current,ensure_ascii=False,indent=2)+'\n')
 output.parent.mkdir(parents=True,exist_ok=False);state.update(status='RUNNING_ORIGINAL_BROKEN_KPT_ONCE',registration=str(reg));save()
 argv=[PYTHON,str(SYSTEM/'scripts/run_isolated_method.py'),'--execute','--method-root',str(SYSTEM),'--benchmark-root',BENCH,'--config',str(CONFIG),'--output',str(output),'--case',CASE,'--python',PYTHON,'--dependency-root',DEPS,'--registration',str(reg),'--attempt-id',attempt,'--api-key-file','/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key']
 with (output.parent/'method.log').open('x') as log:code=subprocess.call(argv,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=log,stderr=subprocess.STDOUT)
 if code:raise RuntimeError('Admission/infrastructure stop; do not repeat')
 receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1;r=read(receipts[0]);assert r['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING' and r['method_source_unchanged'] and r['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
 trace=read(output/'repair.trace.json');state.update(status='PARENT_SCORING');save();result=score(receipts[0],BENCH,ROOT/'independent'/CASE) if trace.get('candidate') else dict(status='NO_CANDIDATE')
 refs=dict(receipt=dict(path=str(receipts[0]),sha256=sha(receipts[0])),trace=dict(path=str(output/'repair.trace.json'),sha256=sha(output/'repair.trace.json')))
 state['rows'].append(dict(case_id=CASE,method_status=trace['status'],independent_status=result['status'],public_outcome=(trace.get('last_feedback') or {}).get('semantic_outcome','UNKNOWN'),patch_bytes=(trace.get('candidate') or {}).get('patch_bytes'),output=str(output),evidence_files=refs));save()
 if result['status'] not in {'PASS','SEMANTIC_FAIL','EXECUTION_FAIL','BUILD_FAIL','PATCH_APPLY_FAIL','METHOD_FAIL','FAIL','NO_CANDIDATE'}:raise RuntimeError('Independent score not a completed recognized outcome')
 verify()
 if trace.get('candidate'):
  (ROOT/'queue'/attempt).mkdir(parents=True,exist_ok=True);retire_workspace(dict(transient_workspace_policy='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE',output_root=str(ROOT)),dict(status=result['status'],slot_id=attempt,evidence_files=refs))
 state.update(status='SINGLE_KPT_CLOSED_REQUIRES_ACTUAL_HANDOFF_AND_EFFECT_AUDIT');save()
except BaseException as error:
 state.update(status='STOP_NO_AUTOMATIC_REPEAT',error=type(error).__name__+': '+str(error));save();raise
