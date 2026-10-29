"""Isolated existing READY repair after one exact financial-stop correction.

All original and new failures are retained. No original46 score substitution.
"""
import argparse,fcntl,hashlib,json,os,signal,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
S=Path('/home/xhj/contractedge-alert-closure-20261028/system');H=Path(__file__).resolve().parent;sys.path[:0]=[str(S/'src'),str(S/'scripts')]
import run_isolated_method as harness
from run_fixed_panel import exclusive,pair_budget
from score_isolated_method import run as score
from verify_development_snapshot import verify_snapshot
from contractedge.contracts.validation import parse_method_input
from contractedge.orchestration.repair import validate_ready_trace
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
R=Path('/home/xhj/contractedge-ack402-continuation-v068-20261028');READY=R/'ready.trace.json'
PANEL=Path('/home/xhj/contractedge-fixed46-official-v068-20261028/panel.json')
FREEZE=S/'docs/development/quote-binding-v068-20261028.snapshot.json';CONFIG=S/'configs/development-deepseek-official-budget200.json'
STAMP=lambda:datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()

def stop(*_):raise KeyboardInterrupt('Retain exact continuation; no retry')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');a=ap.parse_args()
    frozen=read(FREEZE);assert verify_snapshot(S,frozen,require_current_bindings=True)['status']=='PASS'
    proof=read(R/'registration.json');presult=read(R/'result.json');assert presult['status']=='PASS_CORRECTED_PLAN_READY'
    original=Path('/home/xhj/contractedge-fixed46-official-v068-20261028/methods/fixed46-official-v068-20261028-bcd9a8d56a4e00f2f902f406');st=original/'.isolated-8a5d7540cf044247972edd5c2d29833f'
    assert sha(st/'parent-model-calls.json')==proof['original_calls_sha256'] and sha(st/'receipt.json')==proof['original_receipt_sha256']
    ready=read(READY);cfg=read(CONFIG);calls=read(R/'combined-model-calls.json');assert ready['model_calls']==calls and len(calls)==4
    assert calls[:3]==read(st/'parent-model-calls.json') and calls[3]['request']==calls[2]['request']
    assert sum('response_content' in c for c in calls)==3 and 'response_content' not in calls[2]
    assert all(c['response_metadata'].get('model_identity_verified') is True and c['response_metadata'].get('reported_model')=='deepseek-flash' for c in calls if 'response_content' in c)
    method=parse_method_input(read(original/'R4-ACK-CLOUDWATCH-2985/rounds/0/front.method_input.json'))
    source=Path(read(st/'producer/workspace/workspace.json')['source_root'])
    validate_ready_trace(ready,method,source)
    reserved=sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in calls)
    elapsed=max(float(c['elapsed_seconds']) for c in calls)
    assert reserved==presult['combined_budget']['reserved_tokens'] and reserved<cfg['max_total_tokens'] and elapsed<cfg['max_elapsed_seconds']
    supplement=dict(protocol='ce.controlled_ack402_ready_continuation.v1',driver_sha256=sha(__file__),child_sha256=sha(H/'ack402_ready_child.py'),ready_sha256=sha(READY),original_receipt_sha256=sha(st/'receipt.json'),original_model_calls_sha256=sha(st/'parent-model-calls.json'),combined_model_calls_sha256=sha(R/'combined-model-calls.json'),inherited_calls=4,inherited_tokens=reserved,inherited_elapsed_seconds=elapsed,counts_in_original46=False,source_sha256=frozen['source_sha256'],waiting_for_topup_is_outside_this_controlled_active_time_budget=True)
    base_namespace=harness.namespace_command
    def namespace(**kw):
        command=list(kw['command'])
        if len(command)>1 and str(command[1])=='/run/ce-harness/run_reliability_worker_v10.py':
            command[1]='/run/ce-ack402-child.py';kw={**kw,'command':command,'readonly':{**kw['readonly'],H/'ack402_ready_child.py':Path('/run/ce-ack402-child.py'),READY:Path('/run/ce-ack-ready.json')}}
        return base_namespace(**kw)
    harness.namespace_command=namespace
    base_identity=harness.execution_identity
    harness.execution_identity=lambda args:{**base_identity(args),'controlled_continuation':supplement}
    base_broker=harness.ModelBroker
    class InheritedBroker(base_broker):
        def __init__(self,*args,**kw):
            super().__init__(*args,**kw);self.budget.prior_calls=4;self.budget.reserved_tokens=reserved;self.budget.started-=elapsed
    harness.ModelBroker=InheritedBroker
    panel=read(PANEL);runtime=panel['runtime']
    args=argparse.Namespace(method_root=str(S),benchmark_root=panel['benchmark_root'],config=str(CONFIG),output=str(R/'repair/R4-ACK-CLOUDWATCH-2985'),case='R4-ACK-CLOUDWATCH-2985',python=runtime['python'],dependency_root=runtime['dependency_root'],runtime_admission=runtime.get('runtime_admission'),seed=None,attempt_id='ack402-ready-official-v068-20261028',panel_registration=None,panel_slot=None,execute=True,api_key_file='/home/xhj/.config/contractedge/deepseek-official-20261028.key',registration=str(R/'repair-registration.json'))
    identity=harness.execution_identity(args)
    pre=dict(status='PREFLIGHT_PASS',supplement=supplement,identity_sha256=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),new_model_calls=0,remaining_calls=8,remaining_tokens=cfg['max_total_tokens']-reserved,remaining_active_seconds=cfg['max_elapsed_seconds']-elapsed,formal_ready=False)
    if not a.execute:print(json.dumps(pre));return
    # One actual method at a time; do not pressure the live A public runtime.
    with (PANEL.parent/'phase-controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert not (R/'repair-registration.json').exists()
        state=read(PANEL.parent/'acceptance-state.json');assert state['status']!='STOPPED_EFFECT_PREREQUISITE','Known effect failure must be analyzed before more repair calls'
        exclusive(R/'repair-registration.json',dict(identity,role='ISOLATED_DEVELOPMENT_VALIDATION',allow_paid=True,counts_in_46_slot_panel=False))
        exclusive(R/'repair-plan.json',pre);signal.signal(signal.SIGTERM,stop)
        harness.write_json(R/'repair-state.json',dict(status='RUNNING_CONTROLLED_READY_CONTINUATION',owner_pid=os.getpid(),at=STAMP(),formal_ready=False))
        try:
            receipt=harness.run_session(args,cfg,identity)
            assert receipt['status']=='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING'
            assert receipt['cleanup']['status']=='OWNED_CONTAINERS_CLOSED'
            trace=read(Path(args.output)/'repair.trace.json')
            result=score(Path(receipt['state_root'])/'receipt.json',args.benchmark_root,R/'independent') if trace.get('candidate') else {'status':'NO_CANDIDATE'}
            closed=dict(status='CONTROLLED_READY_CONTINUATION_CLOSED',source_sha256=frozen['source_sha256'],independent_status=result['status'],method_status=trace['status'],public_semantic_status=(trace.get('last_feedback') or {}).get('semantic_outcome'),candidate=trace.get('candidate'),budget=trace['budget'],parent_budget=receipt['parent_budget'],counts_in_original46=False,scope='Original prefix replay and corrected READY import; not a fresh run or replacement of the expired original slot',completed_at=STAMP(),formal_ready=False)
            exclusive(R/'repair-result.json',closed);harness.write_json(R/'repair-state.json',closed);print(json.dumps(closed),flush=True)
        except BaseException as e:
            harness.write_json(R/'repair-state.json',dict(status='AUDIT_REQUIRED',error_type=type(e).__name__,at=STAMP(),automatic_retry=False,formal_ready=False));raise

if __name__=='__main__':main()
