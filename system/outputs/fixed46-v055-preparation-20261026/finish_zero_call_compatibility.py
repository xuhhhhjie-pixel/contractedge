"""Complete interface qualification after the active controller releases its lock."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time
SYSTEM=Path('/home/xhj/contractedge-publication-context-20261026/system');WORK=Path(__file__).parent
ROOT=Path('/home/xhj/contractedge-fixed46-v055-20261026');STATE=WORK/'compatibility-state.json'
CROSS=SYSTEM/'.superpowers/sdd/2026-10-26-cross-case-v055/state.json'
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
BASE=Path('/home/xhj/contractedge-latest-integration-20261024/.superpowers/fixed46-20261025/frozen-baseline')
BCONFIG=Path('/home/xhj/contractedge-latest-integration-20261024/system/configs/fixed46-baseline-v045-budget150.json')
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
sys.path.insert(0,str(SYSTEM/'scripts'));from fixed_panel import source_files,digest
assert not STATE.exists();state=dict(status='WAITING_ACTIVE_REGISTERED_CROSS_CASE_CLOSE',model_calls=0)
def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
save()
try:
 deadline=time.monotonic()+7200
 while True:
  current=read(CROSS)
  if current['status']=='CROSS_CASE_CLOSED_REQUIRES_TRACE_AUDIT':break
  if current['status'].startswith('STOP') or time.monotonic()>deadline:raise RuntimeError('Cross-case controller did not close cleanly; do not dispatch sandbox')
  time.sleep(10)
 output=ROOT/'compatibility-sandbox/baseline/RW-CAND-130';assert not output.parent.exists()
 state.update(status='RUNNING_BASELINE_DRY_PUBLIC');save()
 cmd=[PYTHON,str(SYSTEM/'scripts/run_isolated_method.py'),'--dry-public','--method-root',str(BASE),'--benchmark-root','/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019','--config',str(BCONFIG),'--output',str(output),'--case','RW-CAND-130','--python',PYTHON,'--dependency-root','/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies']
 with (WORK/'baseline-sandbox-r2.log').open('x') as stream:code=subprocess.call(cmd,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
 if code:raise RuntimeError('Baseline dry qualification stopped; no repeat')
 receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1
 baseline=read(receipts[0]);assert baseline['status']=='PUBLIC_ONLY_SMOKE_COMPLETE' and not baseline['paid_enabled'] and baseline['method_source_unchanged'] and baseline['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
 candidate_path=SYSTEM/'outputs/probe-completeness-20261026/PUBLIC_SANDBOX_RECEIPT_v1.json';candidate=read(candidate_path);assert candidate['status']=='PUBLIC_ONLY_SMOKE_COMPLETE' and candidate['method_source_unchanged']
 assert baseline['identity']['harness_files']==candidate['identity']['harness_files'] and baseline['identity']['coordinator_code_files']==candidate['identity']['coordinator_code_files']
 proof=read(SYSTEM/'outputs/fixed46-v055-preparation-20261026/SEEDED_INTERFACE_CAPTURES.json')
 report=dict(schema_version='ce.fixed46_compatibility.v1',status='PASS_INTERFACE_COMPATIBILITY',new_model_http_calls=0,source_sha256=dict(baseline=digest(source_files(BASE)),candidate=digest(source_files(SYSTEM))),sandbox_verified_variants=['baseline','candidate'],seeded_request_captures=proof['seeded_request_captures'],proof_files={**proof['proof_files'],str(receipts[0]):sha(receipts[0]),str(candidate_path):sha(candidate_path)},scope='Actual public-only sandbox under same current harness plus four exact current-seed first-request captures with synthetic EXECUTION_ONLY fixture; not method effectiveness')
 path=ROOT/'compatibility.json'
 with path.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
 (SYSTEM/'outputs/fixed46-v055-preparation-20261026/COMPATIBILITY.json').write_text(json.dumps(report,indent=2)+'\n')
 state.update(status='ZERO_CALL_INTERFACE_COMPATIBILITY_PASS',proof=str(path));save()
except BaseException as error:
 state.update(status='STOP_NO_AUTOMATIC_REPEAT',error=type(error).__name__+': '+str(error));save();raise
