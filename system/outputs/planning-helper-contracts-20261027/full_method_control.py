"""One default full-method development case; no locked diagnosis or inherited patch."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
SYSTEM=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
ROOT=Path('/home/xhj/contractedge-control-source060-v7-20261027')
FREEZE=SYSTEM/'docs/development/planning-helper-contracts-v060-20261027-v7.snapshot.json'
CONFIG=SYSTEM/'configs/fixed46-candidate-v060-budget150.json'
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
DEPS='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies'
sys.path[:0]=[str(SYSTEM/'scripts')]
from run_isolated_method import execution_identity,check_registration,write_json
from score_isolated_method import run as score
from verify_development_snapshot import verify_snapshot
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def exclusive(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:
        json.dump(value,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())

def main():
    prerequisite=read(OUT/'FINAL_SOURCE_QUALIFICATION.json')
    assert prerequisite['status']=='FINAL_SOURCE_ENGINEERING_QUALIFIED'
    assert read('/home/xhj/contractedge-e2e-source060-v7-20261027/state.json')['independent_status']=='PASS'
    assert not ROOT.exists()
    frozen=read(FREEZE);assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
    bench=Path(read(SYSTEM/'CURRENT_STATUS.json')['dataset']['benchmark_root']).resolve(strict=True)
    case='RW-CERTMANAGER-9116';attempt='source060-v7-default-cert-control-20261027'
    output=ROOT/'methods'/case/case
    exclusive(ROOT/'experiment.json',dict(role='SAME_SOURCE_PRESELECTED_CROSS_REPOSITORY_CONTROL',case_id=case,
        source_sha256=frozen['source_sha256'],freeze_sha256=sha(FREEZE),parent_driver_sha256=sha(Path(__file__)),
        method_config_sha256=sha(CONFIG),start='Original public Broken, fresh diagnosis; no READY/seed/locked prior plan',
        maximum_calls=12,maximum_semantic_rounds=2,once_only=True,formal_ready=False,counts_in_46_slot_panel=False,
        prior_component_results='Retained separately, never counted here',
        engineering_scope='Exact source full2174+91 and current-byte actual8 complete; one default KPT PASS; this is a preselected control, not original46',
        stop_policy='One default method attempt, retain every failure; no auto restart or next case'))
    state=dict(status='REGISTERED',formal_ready=False);write_json(ROOT/'state.json',state)
    try:
        args=argparse.Namespace(method_root=str(SYSTEM),benchmark_root=str(bench),config=str(CONFIG),
            output=str(output),case=case,python=PYTHON,dependency_root=DEPS,seed=None,
            runtime_admission=None,panel_registration=None,panel_slot=None,attempt_id=attempt)
        identity=execution_identity(args);registration=ROOT/'registration.json'
        exclusive(registration,dict(identity,role='ISOLATED_DEVELOPMENT_VALIDATION',allow_paid=True,counts_in_46_slot_panel=False))
        check_registration(registration,identity);output.parent.mkdir(parents=True)
        state['status']='RUNNING_DEFAULT_FULL_METHOD';write_json(ROOT/'state.json',state)
        command=[PYTHON,str(SYSTEM/'scripts/run_isolated_method.py'),'--execute','--method-root',str(SYSTEM),
            '--benchmark-root',str(bench),'--config',str(CONFIG),'--output',str(output),'--case',case,
            '--python',PYTHON,'--dependency-root',DEPS,'--registration',str(registration),'--attempt-id',attempt,
            '--api-key-file','/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key']
        with (ROOT/'method.log').open('x') as log:
            code=subprocess.call(command,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=log,stderr=subprocess.STDOUT)
        assert code==0,'Method infrastructure/admission stopped; no restart'
        receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1
        receipt=read(receipts[0]);assert receipt['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING'
        assert receipt['method_source_unchanged'] and receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
        trace=read(output/'repair.trace.json');state['status']='PARENT_SCORING';write_json(ROOT/'state.json',state)
        result=score(receipts[0],str(bench),ROOT/'independent') if trace.get('candidate') else dict(status='NO_CANDIDATE')
        state.update(status='CLOSED_DEFAULT_FULL_METHOD_SINGLE',method_status=trace['status'],independent_status=result['status'],
            public_outcome=(trace.get('last_feedback') or {}).get('semantic_outcome','UNKNOWN'),
            model_calls=trace.get('budget',{}).get('model_calls'),patch_bytes=(trace.get('candidate') or {}).get('patch_bytes',0),
            source_sha256=frozen['source_sha256'],receipt_sha256=sha(receipts[0]),trace_sha256=sha(output/'repair.trace.json'))
        assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
    except BaseException as error:
        state.update(status='STOP_NO_AUTOMATIC_RETRY',error_type=type(error).__name__);write_json(ROOT/'state.json',state);raise
    write_json(ROOT/'state.json',state);print(json.dumps(state))

if __name__=='__main__':main()
