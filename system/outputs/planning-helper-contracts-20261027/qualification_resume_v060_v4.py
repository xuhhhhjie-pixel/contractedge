"""Resume only unstarted full regression after a stale runner freeze-path error."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
SYSTEM=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
STATE=OUT/'qualification-v060-v4-state.json'
FREEZE=SYSTEM/'docs/development/planning-helper-contracts-v060-20261027-v4.snapshot.json'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
sys.path.insert(0,str(SYSTEM/'scripts'))
from verify_development_snapshot import verify_snapshot

def main():
    assert not STATE.exists()
    old=read(OUT/'qualification-v060-v3-state.json')
    base=read(SYSTEM/'docs/development/planning-helper-contracts-v060-20261027-v3.snapshot.json');now=read(FREEZE)
    assert old['status']=='STOP_QUALIFICATION_NOT_PASSED'
    assert old['stages']==[{'stage':'public-sandbox','exit':0},{'stage':'actual-eight','exit':0},{'stage':'full-regression','exit':1}]
    assert not (OUT/'full-v060-v3.log').exists()  # pytest never started
    assert all(base[k]==now[k] for k in ['files','configuration','fixture_files','repository_files'])
    assert old['source_sha256']==now['source_sha256']
    assert verify_snapshot(SYSTEM,now,require_current_bindings=True)['status']=='PASS'
    root=Path('/home/xhj/contractedge-source060-v3-qualification-20261027')
    eight=read(root/'actual-eight/summary.json');assert eight['status']=='PASS' and len(eight['cells'])==8
    receipts=list((root/'candidate').glob('.isolated-*/receipt.json'));assert len(receipts)==1
    smoke=read(receipts[0]);assert smoke['status']=='PUBLIC_ONLY_SMOKE_COMPLETE' and not smoke['paid_enabled'] and smoke['method_source_unchanged'] and smoke['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
    ledger=Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json');before=read(ledger)['calls']
    state=dict(status='RUNNING_FULL_ONLY',source_sha256=now['source_sha256'],formal_ready=False,model_calls_added=0,
        reused_exact_source_stages={'public_sandbox_receipt':str(receipts[0]),'receipt_sha256':sha(receipts[0]),
            'actual8_summary':str(root/'actual-eight/summary.json'),'actual8_sha256':sha(root/'actual-eight/summary.json')},
        prior_failure='v3 runner referenced v2 snapshot and stopped before pytest; no source/config/fixture changes')
    STATE.write_text(json.dumps(state,indent=2)+'\n')
    with (OUT/'v060-v4-full-regression.log').open('x') as stream:
        code=subprocess.call([sys.executable,str(OUT/'full_runner_v060_v4.py')],cwd=SYSTEM,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
    result=read(OUT/'FULL_REGRESSION_COMPLETION_v060_v4.json') if (OUT/'FULL_REGRESSION_COMPLETION_v060_v4.json').exists() else {}
    passed=(code==0 and result.get('status')=='PASS' and result.get('source_sha256')==now['source_sha256']
        and result.get('freeze_sha256')==sha(FREEZE) and result.get('log_sha256')==sha(OUT/'full-v060-v4.log')
        and verify_snapshot(SYSTEM,now,require_current_bindings=True)['status']=='PASS' and read(ledger)['calls']==before)
    state.update(status='ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION' if passed else 'STOP_QUALIFICATION_NOT_PASSED',
        full_exit=code,full_tests=result.get('tests'),subtests=result.get('subtests'),full_receipt=result)
    STATE.write_text(json.dumps(state,indent=2)+'\n');print(json.dumps({k:v for k,v in state.items() if k!='full_receipt'}))
    raise SystemExit(0 if passed else 1)

if __name__=='__main__':main()
