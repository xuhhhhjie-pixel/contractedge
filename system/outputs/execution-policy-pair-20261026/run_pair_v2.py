"""Once-only diagnostic pair through existing isolated seeded continuation."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
SYSTEM=Path('/home/xhj/contractedge-publication-context-20261026/system')
OUT=SYSTEM/'outputs/execution-policy-pair-20261026'
ROOT=Path('/home/xhj/contractedge-execution-policy-pair-v2-20261026')
FREEZE=SYSTEM/'docs/development/execution-policy-v055-20261026-v3.snapshot.json'
ORIGINAL=SYSTEM/'docs/development/probe-completeness-v055-20261026-v1.snapshot.json'
STATE=Path(__file__).parent/'state-v2.json'
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
DEPS='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies'
BENCH='/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019'
SEED=OUT/'public-failure-seed.patch'
CASE='RW-ALERTMANAGER-4705'
LEDGER=Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json')
sys.dont_write_bytecode=True;sys.path.insert(0,str(SYSTEM/'scripts'))
from run_isolated_method import execution_identity, check_registration
from score_isolated_method import run as score
from verify_development_snapshot import verify_snapshot
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())
def exclusive(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
def occupied():
 return sum(x.get('charged_micro_cny') if x.get('charged_micro_cny') is not None else x['reserved_micro_cny'] for x in read(LEDGER)['calls'])
def verify():
 assert verify_snapshot(SYSTEM,read(FREEZE),require_current_bindings=True)['status']=='PASS'
 assert verify_snapshot(SYSTEM,read(ORIGINAL),require_current_bindings=True)['status']=='PASS'
def run(argv,log):
 with log.open('x') as stream:result=subprocess.call(argv,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
 if result:raise RuntimeError('Completed command failed; no automatic repeat: '+log.name)
assert sha(__file__)==sha(OUT/'run_pair_v2.py')
assert not ROOT.exists() and not STATE.exists()
ROOT.mkdir()
state=dict(status='ZERO_CALL_QUALIFICATION',rows=[],formal_ready=False,counts_in_46_slot_panel=False,automatic_retry=False)
def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
save()
try:
 verify();old,new=read(ORIGINAL),read(FREEZE)
 assert old['source_sha256']==new['source_sha256'] and old['fixture_files']==new['fixture_files']
 assert all(new['tooling_files'].get(k)==v for k,v in old['tooling_files'].items())
 full=read(SYSTEM/'outputs/probe-completeness-20261026/FULL_REGRESSION_COMPLETION_v055_v1.json')
 assert full['status']=='PASS' and full['exit_code']==0 and (full['tests'],full['subtests'])==(2030,91)
 assert full['freeze_sha256']==sha(ORIGINAL) and full['source_sha256']==new['source_sha256']
 assert full['log_sha256']==sha(SYSTEM/'outputs/probe-completeness-20261026/full-v055-v1.log')
 assert read(SYSTEM/'outputs/probe-completeness-20261026/ACTUAL_EIGHT_SUMMARY_v1.json')['status']=='PASS'
 configs={label:SYSTEM/('configs/seed-execution-policy-v055-'+label+'.json') for label in ['control','reasoning']}
 a,b=map(read,configs.values());assert {k for k in set(a)|set(b) if a.get(k)!=b.get(k)}=={'execution_reasoning_policy'}
 assert a['execution_reasoning_policy']=='failed_trial_only' and b['execution_reasoning_policy']=='complex_plan'
 assert sha(SEED)=='63c23173318b7c40fad4dd33c387c3121b27cb2cdc122efb074edb5ff1e6954a'
 before=read(LEDGER)['calls']
 previous_root=Path('/home/xhj/contractedge-execution-policy-pair-20261026')
 for label,config in configs.items():
  receipts=list((previous_root/'qualification'/label).glob('.isolated-*/receipt.json'));assert len(receipts)==1
  r=read(receipts[0]);assert r['status']=='PUBLIC_ONLY_SMOKE_COMPLETE' and not r['paid_enabled'] and r['method_source_unchanged'] and r['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
  assert r['identity']['config_sha256']==sha(config)
  original=read(previous_root/'registrations'/(label+'.json'))
  probe_args=argparse.Namespace(method_root=str(SYSTEM),benchmark_root=BENCH,config=str(config),output=original['output_path'],case=CASE,python=PYTHON,dependency_root=DEPS,seed=str(SEED),runtime_admission=None,panel_registration=None,panel_slot=None,attempt_id=original['attempt_id'])
  assert all(original.get(k)==v for k,v in execution_identity(probe_args).items())
 assert read(LEDGER)['calls']==before
 verify()
 qualification=dict(status='CONFIGURATION_PAIR_QUALIFIED',source_sha256=new['source_sha256'],full_source_fixture_receipt_sha256=sha(SYSTEM/'outputs/probe-completeness-20261026/FULL_REGRESSION_COMPLETION_v055_v1.json'),source_and_all_existing_tests_tools_unchanged=True,sandbox_arms=['control','reasoning'],new_model_calls=0,formal_ready=False)
 exclusive(ROOT/'qualification.json',qualification)
 attempts=[]
 for label,config in configs.items():
  output=ROOT/'methods'/label/CASE;reg=ROOT/'registrations'/(label+'.json');attempt_id='execution-policy-v055-v2-'+label+'-20261026'
  args=argparse.Namespace(method_root=str(SYSTEM),benchmark_root=BENCH,config=str(config),output=str(output),case=CASE,python=PYTHON,dependency_root=DEPS,seed=str(SEED),runtime_admission=None,panel_registration=None,panel_slot=None,attempt_id=attempt_id)
  identity=execution_identity(args);exclusive(reg,dict(identity,role='ISOLATED_DEVELOPMENT_VALIDATION',diagnostic_scope='SEEDED_EXECUTION_POLICY_PAIR',allow_paid=True,counts_in_46_slot_panel=False));check_registration(reg,identity)
  attempts.append(dict(arm=label,output=str(output),registration=str(reg),registration_sha256=sha(reg),config=str(config),attempt_id=attempt_id))
 assert 150000000-occupied()>=11145728
 exclusive(ROOT/'registration.json',dict(schema_version='ce.seeded_execution_policy_pair.v1',source_sha256=new['source_sha256'],freeze_sha256=sha(FREEZE),plan_sha256=sha(OUT/'EXPERIMENT_PLAN.md'),driver_sha256=sha(__file__),qualification_sha256=sha(ROOT/'qualification.json'),seed_sha256=sha(SEED),seed_snapshot='630df027ac083203889f3ef602b2851b3fb95504879f48c7b59c863641032905',attempts=attempts,order=['control','reasoning'],once_per_arm=True,front_plans_regenerated=True,locked_plan_comparison=False,max_calls_per_arm=12,max_rounds=2,cumulative_cny_cap=150,budget_before_micro_cny=occupied(),counts_in_46_slot_panel=False,formal_ready=False))
 for attempt in attempts:
  verify();assert sha(attempt['registration'])==attempt['registration_sha256']
  output=Path(attempt['output']);output.parent.mkdir(parents=True,exist_ok=False)
  state.update(status='RUNNING_ONCE',active_arm=attempt['arm']);save()
  run([PYTHON,str(SYSTEM/'scripts/run_isolated_method.py'),'--execute','--method-root',str(SYSTEM),'--benchmark-root',BENCH,'--config',attempt['config'],'--output',str(output),'--case',CASE,'--python',PYTHON,'--dependency-root',DEPS,'--seed',str(SEED),'--registration',attempt['registration'],'--attempt-id',attempt['attempt_id'],'--api-key-file','/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key'],output.parent/'method.log')
  receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1
  receipt=read(receipts[0]);assert receipt['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING' and receipt['method_source_unchanged'] and receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
  trace=read(output/'repair.trace.json');state.update(status='PARENT_SCORING');save()
  result=score(receipts[0],BENCH,ROOT/'independent'/attempt['arm']) if trace.get('candidate') else dict(status='NO_CANDIDATE')
  row=dict(arm=attempt['arm'],method_status=trace['status'],independent_status=result['status'],public_outcome=(trace.get('last_feedback') or {}).get('semantic_outcome','UNKNOWN'),patch_bytes=(trace.get('candidate') or {}).get('patch_bytes'),patch_sha256=(trace.get('candidate') or {}).get('patch_sha256'),budget=trace.get('budget'),output=str(output),receipt_sha256=sha(receipts[0]),trace_sha256=sha(output/'repair.trace.json'))
  state['rows'].append(row);save()
  if result['status'] not in {'PASS','SEMANTIC_FAIL','EXECUTION_FAIL','BUILD_FAIL','PATCH_APPLY_FAIL','METHOD_FAIL','FAIL','NO_CANDIDATE'}:raise RuntimeError('Independent scoring did not finish a recognized terminal outcome')
  verify()
 state.update(status='PAIR_CLOSED_REQUIRES_TRACE_AUDIT',active_arm=None,budget_after_micro_cny=occupied());save()
except BaseException as error:
 state.update(status='STOP_NO_AUTOMATIC_REPEAT',error=type(error).__name__+': '+str(error));save();raise
