"""Zero-call qualification needs a source-bound successful process receipt."""
import hashlib,json,os,re,subprocess,time
from pathlib import Path
SYSTEM=Path('/home/xhj/contractedge-preservation-feedback-20261026/system')
WORK=Path(__file__).parent
ROOT=Path('/home/xhj/contractedge-source053-v1-qualification-20261026')
STATE=WORK/'qualification-v053-v1-state.json'
FREEZE=SYSTEM/'docs/development/preservation-feedback-v053-20261026-v1.snapshot.json'
OUT=SYSTEM/'outputs/preservation-feedback-20261026'
RECEIPT=OUT/'FULL_REGRESSION_COMPLETION_v053_v1.json'
LOG=OUT/'full-v053-v1.log'
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
LEDGER=Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json')
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def full_result(freeze,receipt,log):
    result=read(receipt)
    if result.get('status')!='PASS' or type(result.get('exit_code')) is not int or result['exit_code']!=0:
        raise RuntimeError('Full regression process did not exit successfully')
    if (result.get('log_sha256')!=sha(log) or result.get('freeze_sha256')!=sha(freeze)
            or result.get('source_sha256')!=read(freeze)['source_sha256']):
        raise RuntimeError('Full regression receipt identity mismatch')
    match=re.fullmatch(r'(\d+) passed, (\d+) subtests passed in [^\n]+',Path(log).read_text().strip().splitlines()[-1])
    if not match or tuple(map(int,match.groups()))!=(1982,91):
        raise RuntimeError('Incomplete or failed full regression summary')
    if result.get('tests')!=1982 or result.get('subtests')!=91:
        raise RuntimeError('Receipt count mismatch')
    return result

def main():
    assert sha(__file__)==sha(OUT/'qualification_driver_v053_v1.py')
    assert not STATE.exists() and not ROOT.exists()
    before=read(LEDGER)['calls']
    state=dict(status='WAITING_FULL_PROCESS_RECEIPT',stages=[],model_calls_added=0,formal_ready=False,B_opened=False,qualification_driver_sha256=sha(__file__))
    def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
    def run(stage,argv):
        state.update(status='RUNNING_ZERO_CALL_QUALIFICATION',active_stage=stage);save()
        with (WORK/('v053-v1-'+stage+'.log')).open('x') as stream:
            code=subprocess.call(argv,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
        state['stages'].append(dict(stage=stage,exit=code));save()
        if code:raise RuntimeError(stage+' failed; no automatic retry')
    save()
    try:
        deadline=time.monotonic()+2400
        while not RECEIPT.exists():
            if time.monotonic()>deadline:raise RuntimeError('Full completion receipt missing')
            time.sleep(10)
        result=full_result(FREEZE,RECEIPT,LOG)
        state.update(full_tests=result['tests'],subtests=result['subtests'],full_process_receipt_sha256=sha(RECEIPT));save()
        run('verify-after-full',[PYTHON,'scripts/verify_development_snapshot.py','--require-current-bindings'])
        run('actual-eight',[PYTHON,'scripts/run_trial_isolation_panel.py','--system',str(SYSTEM),'--binding','configs/trial-isolation-acceptance-v053.example.json','--output',str(ROOT/'actual-eight'),'--image','golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d','--docker-host','unix:///run/user/1008/docker.sock','--docker-api-version','1.54'])
        summary=read(ROOT/'actual-eight/summary.json');assert summary['status']=='PASS' and len(summary['cells'])==8 and all(c['status']=='PASS' for c in summary['cells'])
        output=ROOT/'candidate/RW-CAND-130'
        run('public-sandbox',[PYTHON,'scripts/run_isolated_method.py','--dry-public','--method-root',str(SYSTEM),'--benchmark-root','/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019','--config',str(SYSTEM/'configs/fixed46-candidate-v053-budget150.json'),'--output',str(output),'--case','RW-CAND-130','--python',PYTHON,'--dependency-root','/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies'])
        receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1
        receipt=read(receipts[0]);assert receipt['status']=='PUBLIC_ONLY_SMOKE_COMPLETE' and receipt['method_source_unchanged'] and not receipt['paid_enabled'] and receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
        run('verify-after-sandbox',[PYTHON,'scripts/verify_development_snapshot.py','--require-current-bindings'])
        full_result(FREEZE,RECEIPT,LOG)
        assert read(LEDGER)['calls']==before
        state.update(status='ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION',active_stage=None,actual8=str(ROOT/'actual-eight/summary.json'),sandbox_receipt=str(receipts[0]))
    except BaseException as exc:
        state.update(status='STOP_QUALIFICATION_NOT_PASSED',error=type(exc).__name__+': '+str(exc));save();raise
    save()

if __name__=='__main__':main()
