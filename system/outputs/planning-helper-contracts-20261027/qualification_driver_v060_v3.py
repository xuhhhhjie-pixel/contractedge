"""Qualify one integrated source: cheap preflight, public smoke, isolation, full suite."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
SYSTEM=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
ROOT=Path('/home/xhj/contractedge-source060-v3-qualification-20261027')
STATE=OUT/'qualification-v060-v3-state.json'
FREEZE=SYSTEM/'docs/development/planning-helper-contracts-v060-20261027-v3.snapshot.json'
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
LEDGER=Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json')
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
sys.path[:0]=[str(SYSTEM/'src'),str(SYSTEM/'scripts')]
from verify_development_snapshot import verify_snapshot
from contractedge.dataset.admission import check_case_admission

def main():
    assert not STATE.exists() and not ROOT.exists()
    frozen=read(FREEZE)
    before=read(LEDGER)['calls']
    state=dict(status='RUNNING',stages=[],source_sha256=frozen['source_sha256'],
        freeze_sha256=sha(FREEZE),model_calls_added=0,formal_ready=False,B_opened=False)
    def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
    def run(stage,command):
        state['active_stage']=stage;save()
        with (OUT/('v060-v3-'+stage+'.log')).open('x') as stream:
            code=subprocess.call(command,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
        state['stages'].append(dict(stage=stage,exit=code));save()
        if code:raise RuntimeError(stage+' failed; no automatic retry')
    save()
    try:
        # All paths come from the current formal binding, never a guessed release name.
        bench=Path(read(SYSTEM/'CURRENT_STATUS.json')['dataset']['benchmark_root']).resolve(strict=True)
        config=read(SYSTEM/'configs/fixed46-candidate-v060-budget150.json')
        assert sha(bench/'public/manifest.jsonl')==config['expected_public_manifest_sha256']
        assert (bench/'tools/score_case.py').is_file() and (bench/'public').is_dir()
        check_case_admission(bench/'public','RW-CAND-130',config)
        check_case_admission(bench/'public','R3-KPT-4673',config)
        assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
        state['preflight']=dict(status='PASS',benchmark_root=str(bench),configuration_sha256=sha(SYSTEM/'configs/fixed46-candidate-v060-budget150.json'))
        ROOT.mkdir();save()
        run('public-sandbox',[PYTHON,'scripts/run_isolated_method.py','--dry-public','--method-root',str(SYSTEM),
            '--benchmark-root',str(bench),'--config',str(SYSTEM/'configs/fixed46-candidate-v060-budget150.json'),
            '--output',str(ROOT/'candidate/RW-CAND-130'),'--case','RW-CAND-130','--python',PYTHON,
            '--dependency-root','/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies'])
        receipts=list((ROOT/'candidate').glob('.isolated-*/receipt.json'));assert len(receipts)==1
        receipt=read(receipts[0]);assert receipt['status']=='PUBLIC_ONLY_SMOKE_COMPLETE' and receipt['method_source_unchanged'] and not receipt['paid_enabled'] and receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
        run('actual-eight',[PYTHON,'scripts/run_trial_isolation_panel.py','--system',str(SYSTEM),
            '--binding','configs/trial-isolation-acceptance-v060-integrated.example.json','--output',str(ROOT/'actual-eight'),
            '--image','golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d',
            '--docker-host','unix:///run/user/1008/docker.sock','--docker-api-version','1.54'])
        summary=read(ROOT/'actual-eight/summary.json');assert summary['status']=='PASS' and len(summary['cells'])==8 and all(c['status']=='PASS' for c in summary['cells'])
        run('full-regression',[sys.executable,str(OUT/'full_runner_v060_v3.py')])
        full=read(OUT/'FULL_REGRESSION_COMPLETION_v060_v3.json')
        assert full['status']=='PASS' and full['exit_code']==0 and full['tests']==2172 and full['subtests']==91
        assert full['freeze_sha256']==sha(FREEZE) and full['source_sha256']==frozen['source_sha256']
        assert full['log_sha256']==sha(OUT/'full-v060-v3.log')
        assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
        assert read(LEDGER)['calls']==before
        state.update(status='ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION',active_stage=None,
            full_tests=2172,subtests=91,actual8=str(ROOT/'actual-eight/summary.json'),sandbox_receipt=str(receipts[0]))
    except BaseException as exc:
        state.update(status='STOP_QUALIFICATION_NOT_PASSED',error=type(exc).__name__+': '+str(exc));save();raise
    save();print(json.dumps(state))

if __name__=='__main__':main()
