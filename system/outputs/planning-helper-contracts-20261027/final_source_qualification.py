"""One final full suite and current-byte isolation panel after the real repair PASS."""
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
SYSTEM=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
FREEZE=SYSTEM/'docs/development/planning-helper-contracts-v060-20261027-v7.snapshot.json'
ROOT=Path('/home/xhj/contractedge-source060-final-qualification-20261027')
STATE=OUT/'FINAL_SOURCE_QUALIFICATION.json'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
sys.path[:0]=[str(SYSTEM/'src'),str(SYSTEM/'scripts'),str(OUT)]
from verify_development_snapshot import verify_snapshot
from external_fixture_inventory import verify_external_fixtures

def main():
    assert not STATE.exists() and not ROOT.exists()
    source=read(FREEZE);assert verify_snapshot(SYSTEM,source,require_current_bindings=True)['status']=='PASS'
    full_method=read('/home/xhj/contractedge-e2e-source060-v7-20261027/state.json')
    assert full_method['independent_status']=='PASS' and full_method['source_sha256']==source['source_sha256']
    ledger=Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json');calls_before=read(ledger)['calls']
    public_fixtures=verify_external_fixtures(SYSTEM,OUT/'EXTERNAL_PUBLIC_FIXTURES.json')
    state=dict(status='RUNNING_FULL_REGRESSION',source_sha256=source['source_sha256'],
        source_snapshot_sha256=sha(FREEZE),driver_sha256=sha(Path(__file__)),model_calls_added=0,formal_ready=False)
    def save():STATE.write_text(json.dumps(state,indent=2)+'\n')
    ROOT.mkdir();save()
    environment=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='src:.:/home/xhj/contractedge-system/.topomen-deps',
        CONTRACTEDGE_TOPOMEN_SOURCE=str(SYSTEM/'vendor/topomen'),CONTRACTEDGE_RUN_ISOLATED_DEMOS='1',
        DOCKER_HOST='unix:///run/user/1008/docker.sock',DOCKER_API_VERSION='1.54',
        CONTRACTEDGE_RUNTIME_IMAGE='golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d')
    log=OUT/'full-source-v7-final.log'
    with log.open('x') as stream:
        code=subprocess.call(['/home/xhj/contractedge-system/.venv-dev/bin/python','-m','pytest','-q','tests'],
            cwd=SYSTEM,env=environment,stdout=stream,stderr=subprocess.STDOUT)
    state.update(full_exit_code=code,full_log_sha256=sha(log));save()
    match=re.fullmatch(r'(\d+) passed, (\d+) subtests passed in [^\n]+',log.read_text().strip().splitlines()[-1])
    if code or not match or tuple(map(int,match.groups()))!=(2174,91):
        state['status']='STOP_FULL_REGRESSION_FAILED';save();raise SystemExit(1)
    assert verify_snapshot(SYSTEM,source,require_current_bindings=True)['status']=='PASS'
    assert verify_external_fixtures(SYSTEM,OUT/'EXTERNAL_PUBLIC_FIXTURES.json')==public_fixtures
    state.update(status='RUNNING_CURRENT_ACTUAL8',full_tests=2174,subtests=91);save()
    binding=SYSTEM/'configs/trial-isolation-acceptance-v060-delivery.example.json'
    command=[sys.executable,str(SYSTEM/'scripts/run_trial_isolation_panel.py'),'--system',str(SYSTEM),
        '--binding',str(binding),'--output',str(ROOT/'actual-eight'),'--image',environment['CONTRACTEDGE_RUNTIME_IMAGE'],
        '--docker-host',environment['DOCKER_HOST'],'--docker-api-version',environment['DOCKER_API_VERSION']]
    with (OUT/'final-source-actual-eight.log').open('x') as stream:
        code=subprocess.call(command,cwd=SYSTEM,env=environment,stdout=stream,stderr=subprocess.STDOUT)
    state.update(actual8_exit_code=code,actual8_binding_sha256=sha(binding));save()
    summary=read(ROOT/'actual-eight/summary.json') if (ROOT/'actual-eight/summary.json').exists() else {}
    passed=(code==0 and summary.get('status')=='PASS' and len(summary.get('cells',[]))==8
        and verify_snapshot(SYSTEM,source,require_current_bindings=True)['status']=='PASS'
        and read(ledger)['calls']==calls_before)
    state.update(status='FINAL_SOURCE_ENGINEERING_QUALIFIED' if passed else 'STOP_ACTUAL8_FAILED',
        current_byte_actual8_status=summary.get('status'),actual_runtime_sessions=summary.get('actual_runtime_sessions'),
        actual_containers=summary.get('actual_candidate_observer_containers'),
        full_method_independent_status=full_method['independent_status'],new_source_single_full_suite_pass=True)
    save();print(json.dumps(state));raise SystemExit(0 if passed else 1)

if __name__=='__main__':main()
