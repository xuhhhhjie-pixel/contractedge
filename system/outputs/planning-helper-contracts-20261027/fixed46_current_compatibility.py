"""Original fixed46 compatibility only: zero HTTP, exact current source, no effect claim."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
SYSTEM=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
ROOT=Path('/home/xhj/contractedge-fixed46-source060-20261027')
PYTHON='/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
sys.path[:0]=[str(SYSTEM/'scripts')]
from fixed_panel import source_files,digest
from verify_development_snapshot import verify_snapshot
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    assert not ROOT.exists()
    frozen=read(SYSTEM/'docs/development/ordering-source-v060-20261027-v1.snapshot.json')
    qualification=read(OUT/'ORDERING_SOURCE_QUALIFICATION.json')
    assert qualification['status']=='FINAL_SOURCE_ENGINEERING_QUALIFIED'
    assert qualification['source_sha256']==frozen['source_sha256']
    assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
    old=read('/home/xhj/contractedge-fixed46-20261025/panel-v045-terminal-r3.json')
    baseline=Path(old['variants']['baseline']['root'])
    assert source_files(baseline)==read(SYSTEM/'baselines/v0.5.5/SOURCE_REFERENCE.json')['baseline_source_files']
    assert source_files(SYSTEM)==frozen['files']
    bench=Path(read(SYSTEM/'CURRENT_STATUS.json')['dataset']['benchmark_root']).resolve(strict=True)
    ledger=Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json');before=read(ledger)['calls']
    variants={'baseline':(baseline,SYSTEM/'configs/fixed46-baseline-v045-budget150.json'),'candidate':(SYSTEM,SYSTEM/'configs/fixed46-candidate-v060-budget150.json')}
    configs=[read(c) for _,c in variants.values()]
    for key in ['model','max_model_calls','max_rounds','max_context_tokens','max_output_tokens','execution_max_output_tokens','review_max_output_tokens','max_total_tokens','max_elapsed_seconds','seed_planning_snapshot']:
        assert configs[0].get(key)==configs[1].get(key),(key,'resource mismatch')
    ROOT.mkdir();state={'status':'RUNNING_ZERO_HTTP_COMPATIBILITY','formal_ready':False,'new_model_http_calls':0}
    def save(): (ROOT/'state.json').write_text(json.dumps(state,indent=2)+'\n')
    def run(label,command):
        state['active_stage']=label;save()
        with (ROOT/(label+'.log')).open('x') as stream:
            code=subprocess.call(command,cwd=SYSTEM,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=stream,stderr=subprocess.STDOUT)
        if code:raise RuntimeError(label+' failed; no restart')
    save()
    try:
        captures=[];proof={};sandboxes=[]
        for variant,(method,config) in variants.items():
            for case,seed in old['seeds'].items():
                assert sha(seed['path'])==seed['sha256']
                path=ROOT/'captures'/(variant+'-'+case)
                run(variant+'-'+case,[PYTHON,str(OUT/'capture_fixed46_current_front.py'),'--variant',variant,'--case',case,'--seed',seed['path'],'--output',str(path),'--method',str(method),'--config',str(config),'--benchmark',str(bench)])
                result=read(path/'compatibility.json');provenance=read(path/'provenance.json');request=read(path/'captured-request.json');feedback=read(path/'seed-handoff-feedback.json')
                assert result['status']=='FIRST_REQUEST_CAPTURED_ZERO_HTTP' and result['source_unchanged']
                assert request['metadata']['snapshot_id']==provenance['seed_snapshot']==provenance['planning_snapshot']==feedback['to_snapshot']
                assert feedback['from_snapshot']==provenance['initial_snapshot']
                assert provenance['seed_sha256']==seed['sha256'] and provenance['hidden_feedback_used'] is False
                captures.append(dict(variant=variant,case_id=case,source_unchanged=True,current_snapshot_bound=True,initial_identity_preserved=True,request_sha256=sha(path/'captured-request.json')))
                for name in ['compatibility.json','captured-request.json','provenance.json','seed-handoff-feedback.json']:proof[str(path/name)]=sha(path/name)
            output=ROOT/'sandbox'/variant/'RW-CAND-130'
            run(variant+'-public-sandbox',[PYTHON,str(SYSTEM/'scripts/run_isolated_method.py'),'--dry-public','--method-root',str(method),'--benchmark-root',str(bench),'--config',str(config),'--output',str(output),'--case','RW-CAND-130','--python',PYTHON,'--dependency-root',old['runtime']['dependency_root']])
            receipts=list(output.parent.glob('.isolated-*/receipt.json'));assert len(receipts)==1
            receipt=read(receipts[0]);assert receipt['status']=='PUBLIC_ONLY_SMOKE_COMPLETE' and receipt['method_source_unchanged'] and not receipt['paid_enabled'] and receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
            proof[str(receipts[0])]=sha(receipts[0]);sandboxes.append(variant)
        assert read(ledger)['calls']==before
        assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
        report=dict(schema_version='ce.fixed46_compatibility.v1',status='PASS_INTERFACE_COMPATIBILITY',new_model_http_calls=0,source_sha256={k:digest(source_files(v[0])) for k,v in variants.items()},sandbox_verified_variants=sandboxes,seeded_request_captures=captures,proof_files=proof,scope='Original fixed46 methods/seeds/current-candidate protocol; actual sandbox + synthetic EXECUTION_ONLY front captures, no repair effectiveness')
        (ROOT/'COMPATIBILITY.json').write_text(json.dumps(report,indent=2)+'\n')
        (OUT/'FIXED46_CURRENT_COMPATIBILITY.json').write_text(json.dumps(report,indent=2)+'\n')
        state.update(status='CLOSED_ZERO_HTTP_COMPATIBILITY_PASS',active_stage=None)
    except BaseException as error:
        state.update(status='STOP_NO_AUTOMATIC_RETRY',error_type=type(error).__name__,error=str(error));save();raise
    save();print(json.dumps(state))

if __name__=='__main__':main()
