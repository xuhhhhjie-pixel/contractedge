"""Predeclared two cross-case controls; no case repeats or formal score pooling."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
SYSTEM=Path('/home/xhj/contractedge-publication-context-20261026/system');OUT=SYSTEM/'outputs/cross-case-v055-20261026'
ROOT=Path('/home/xhj/contractedge-cross-case-v055-20261026');STATE=Path(__file__).parent/'state.json'
FREEZE=SYSTEM/'docs/development/cross-case-v055-20261026-v4.snapshot.json'
ORIGINAL=SYSTEM/'docs/development/probe-completeness-v055-20261026-v1.snapshot.json'
CONFIG=SYSTEM/'configs/fixed46-candidate-v055-budget150.json'
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
assert sha(__file__)==sha(OUT/'run_controls.py')
assert not ROOT.exists() and not STATE.exists();ROOT.mkdir()
state=dict(status='VERIFYING',rows=[],formal_ready=False,counts_in_46_slot_panel=False,automatic_retry=False)
def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
save()
try:
 verify();previous,now=read(ORIGINAL),read(FREEZE)
 assert previous['source_sha256']==now['source_sha256'] and previous['fixture_files']==now['fixture_files']
 assert all(now['tooling_files'].get(k)==v for k,v in previous['tooling_files'].items())
 q=read(SYSTEM/'outputs/probe-completeness-20261026/ZERO_CALL_QUALIFICATION_v1.json');assert q['status']=='ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION'
 f=read(SYSTEM/'outputs/probe-completeness-20261026/FULL_REGRESSION_COMPLETION_v055_v1.json');assert f['status']=='PASS' and f['exit_code']==0 and (f['tests'],f['subtests'])==(2030,91) and f['freeze_sha256']==sha(ORIGINAL)
 sandbox=read(SYSTEM/'outputs/probe-completeness-20261026/PUBLIC_SANDBOX_RECEIPT_v1.json');assert sandbox['status']=='PUBLIC_ONLY_SMOKE_COMPLETE' and sandbox['identity']['config_sha256']==sha(CONFIG)
 pair=read(SYSTEM/'outputs/execution-policy-pair-20261026/PAIR_RESULTS.json');assert pair['status']=='PAIR_CLOSED_AUDITED_BOTH_FRESH_REPAIRS_PASS' and all(r['independent_status']=='PASS' and r['fresh_edit_rounds'] for r in pair['rows'])
 attempts=[]
 for case in ['R3-KPT-4673','RW-CERTMANAGER-9116']:
  output=ROOT/'methods'/case/case;attempt='cross-case-v055-'+case.lower()+'-20261026';reg=ROOT/'registrations'/(case+'.json')
  args=argparse.Namespace(method_root=str(SYSTEM),benchmark_root=BENCH,config=str(CONFIG),output=str(output),case=case,python=PYTHON,dependency_root=DEPS,seed=None,runtime_admission=None,panel_registration=None,panel_slot=None,attempt_id=attempt)
  identity=execution_identity(args);exclusive(reg,dict(identity,role='ISOLATED_DEVELOPMENT_VALIDATION',allow_paid=True,counts_in_46_slot_panel=False));check_registration(reg,identity)
  attempts.append(dict(case_id=case,attempt_id=attempt,output=str(output),registration=str(reg),registration_sha256=sha(reg)))
 exclusive(ROOT/'registration.json',dict(schema_version='ce.fixed_cross_case_controls.v1',source_sha256=now['source_sha256'],freeze_sha256=sha(FREEZE),driver_sha256=sha(__file__),attempts=attempts,once_per_case=True,selection='Previously successful KPT and CertManager cover resource-flow/branch helper preconditions and admission-controller source changes; keep case order even after semantic failure, stop only infrastructure uncertainty',default_execution_policy='failed_trial_only',new_code_delta_from_qualified_source=False,cumulative_budget_cny=150,counts_in_46_slot_panel=False,formal_ready=False))
 for a in attempts:
  verify();assert sha(a['registration'])==a['registration_sha256'];output=Path(a['output']);output.parent.mkdir(parents=True,exist_ok=False);state.update(status='RUNNING_ONCE',active_case=a['case_id']);save()
  cmd=[PYTHON,str(SYSTEM/'scripts/run_isolated_method.py'),'--execute','--method-root',str(SYSTEM),'--benchmark-root',BENCH,'--config',str(CONFIG),'--output',str(output),'--case',a['case_id'],'--python',PYTHON,'--dependency-root',DEPS,'--registration',a['registration'],'--attempt-id',a['attempt_id'],'--api-key-file','/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key']
  with (output.parent/'method.log').open('x') as stream:code=subprocess.call(cmd,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
  if code:raise RuntimeError('Method infrastructure/admission stop; no repeat')
  receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1;r=read(receipts[0]);assert r['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING' and r['method_source_unchanged'] and r['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
  trace=read(output/'repair.trace.json');state.update(status='PARENT_SCORING');save();result=score(receipts[0],BENCH,ROOT/'independent'/a['case_id']) if trace.get('candidate') else dict(status='NO_CANDIDATE')
  refs=dict(receipt=dict(path=str(receipts[0]),sha256=sha(receipts[0])),trace=dict(path=str(output/'repair.trace.json'),sha256=sha(output/'repair.trace.json')))
  state['rows'].append(dict(case_id=a['case_id'],method_status=trace['status'],independent_status=result['status'],public_outcome=(trace.get('last_feedback') or {}).get('semantic_outcome','UNKNOWN'),patch_bytes=(trace.get('candidate') or {}).get('patch_bytes'),output=str(output),evidence_files=refs));save()
  if result['status'] not in {'PASS','SEMANTIC_FAIL','EXECUTION_FAIL','BUILD_FAIL','PATCH_APPLY_FAIL','METHOD_FAIL','FAIL','NO_CANDIDATE'}:raise RuntimeError('Scoring incomplete, do not dispatch next case')
  if trace.get('candidate'):
   (ROOT/'queue'/a['attempt_id']).mkdir(parents=True,exist_ok=True);retire_workspace(dict(transient_workspace_policy='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE',output_root=str(ROOT)),dict(status=result['status'],slot_id=a['attempt_id'],evidence_files=refs))
  verify()
 state.update(status='CROSS_CASE_CLOSED_REQUIRES_TRACE_AUDIT',active_case=None);save()
except BaseException as error:
 state.update(status='STOP_NO_AUTOMATIC_REPEAT',error=type(error).__name__+': '+str(error));save();raise
