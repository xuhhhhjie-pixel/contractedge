"""One original ACK correction, with the complete stopped budget carried forward.

Controlled planning continuation only. It cannot overwrite the closed original
receipt, replace a fixed46 score, or silently restart its elapsed wall deadline.
"""
import argparse,hashlib,json,os,sys,time
from pathlib import Path
S=Path('/home/xhj/contractedge-alert-closure-20261028/system');sys.path[:0]=[str(S/'src'),str(S/'scripts')]
from contractedge.cli import _build_provider
from contractedge.contracts.validation import parse_method_input
from contractedge.orchestration.runner import run_front_half
from contractedge.model.provider import ModelRequest,ModelResponse
from contractedge.model.budget import BudgetedProvider
from run_fixed_panel import exclusive
from verify_development_snapshot import verify_snapshot

ROOT=Path('/home/xhj/contractedge-ack402-continuation-v068-20261028')
D=Path('/home/xhj/contractedge-fixed46-official-v068-20261028/methods/fixed46-official-v068-20261028-bcd9a8d56a4e00f2f902f406');ST=D/'.isolated-8a5d7540cf044247972edd5c2d29833f';F=D/'R4-ACK-CLOUDWATCH-2985/rounds/0'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');a=ap.parse_args()
    assert verify_snapshot(S,read(S/'docs/development/quote-binding-v068-20261028.snapshot.json'),require_current_bindings=True)['status']=='PASS'
    assert read(S/'outputs/alert-repeat-closure-20261028/V068_ACK_402_EXACT_REPLAY.json')['status']=='PASS_EXACT_PREFIX_AND_CORRECTION_REPLAY'
    calls=read(ST/'parent-model-calls.json');assert len(calls)==3 and calls[2].get('error')=='HTTPError: HTTP Error 402: Payment Required' and 'response_content' not in calls[2]
    assert all('response_content' in c and not c.get('error') for c in calls[:2])
    cfg=read(ST/'original-config.json');frontcfg=read(F/'front.config.json');assert cfg['base_url']=='https://api.deepseek.com'
    inherited=read(ST/'receipt.json')['parent_budget'];reserved=sum(c['prompt_tokens_reserved']+c['output_tokens_reserved']+c.get('transport_schema_tokens_reserved',0) for c in calls)
    assert reserved==inherited['reserved_tokens']
    wire,counter=_build_provider(cfg)
    # Keep original failed request and financial reservation. Cached successful
    # answers are replayed without HTTP; no diagnosis or first draft is resampled.
    budget=BudgetedProvider(wire,counter,max_tokens=cfg['max_total_tokens'],max_seconds=cfg['max_elapsed_seconds'],max_calls=cfg['max_model_calls'],on_update=lambda c: exclusive(ROOT/'dispatch-intent.json',c) if not (ROOT/'dispatch-intent.json').exists() else (ROOT/'new-model-calls.json').write_text(json.dumps(c,indent=2)+'\n'))
    budget.prior_calls=3;budget.reserved_tokens=reserved;budget.started-=inherited['elapsed_seconds']
    class Replay:
        def __init__(self):self.i=0
        def request_overhead(self,r):return wire.request_overhead(r)
        def request_metadata(self,r):return wire.request_metadata(r)
        def complete_json(self,r):
            i=self.i;self.i+=1
            from dataclasses import asdict
            assert i<3 and json.loads(json.dumps(asdict(r)))==calls[i]['request'],'Original request identity changed; no dispatch'
            if i<2:return ModelResponse(calls[i]['response_content'],calls[i]['response_metadata'])
            return budget.complete_json(r)
    wrapped=Replay()
    local=BudgetedProvider(wrapped,counter,max_tokens=cfg['max_total_tokens'],max_seconds=cfg['max_elapsed_seconds'],max_calls=cfg['max_model_calls'])
    mi=parse_method_input(read(F/'front.method_input.json'));source=Path(read(ST/'producer/workspace/workspace.json')['source_root'])
    proof=dict(status='PREFLIGHT_PASS',source_sha256='c73764051dd244529be00a5cfe8969f547d421b82caa404705f9f43cd699676d',driver_sha256=sha(__file__),original_calls_sha256=sha(ST/'parent-model-calls.json'),original_receipt_sha256=sha(ST/'receipt.json'),inherited_budget=inherited,counts_in_original46=False,new_calls_limit=1,replay_response_count=2,automatic_retry=False,formal_ready=False,scope='Controlled saved-prefix correction after account top-up. Account waiting excluded from this separate continuation active-time budget, not silently removed from the expired original attempt.')
    if not a.execute:print(json.dumps(proof));return
    ROOT.mkdir(exist_ok=False);exclusive(ROOT/'registration.json',proof)
    os.environ[cfg['api_key_env']]=Path('/home/xhj/.config/contractedge/deepseek-official-20261028.key').read_text().strip()
    started=time.monotonic()
    try:
        front=run_front_half(mi,source if mi.source_root=='.' else source.parent,local,frontcfg,counter,public_observations=read(F/'front.public_observations.json'),execution_feedback=read(F/'front.execution_feedback.json')).to_wire()
        (ROOT/'front.trace.json').write_text(json.dumps(front,indent=2)+'\n')
        full=[*calls,*budget.calls];exclusive(ROOT/'combined-model-calls.json',full)
        if front['status'] in {'READY','READY_PARTIAL'}:
            # Carry the rejected HTTP attempt into the existing READY budget;
            # its tokens, money and call count are not refunded.
            ready={**front,'model_calls':full};exclusive(ROOT/'ready.trace.json',ready)
        result={**proof,'status':'PASS_CORRECTED_PLAN_READY' if front['status'] in {'READY','READY_PARTIAL'} else 'CORRECTION_NOT_READY','front_status':front['status'],'errors':front.get('errors',[]),'new_model_calls':len(budget.calls),'combined_budget':budget.summary(),'prefix_unchanged':sha(ST/'parent-model-calls.json')==proof['original_calls_sha256'],'correction_elapsed_seconds':time.monotonic()-started}
        exclusive(ROOT/'result.json',result);print(json.dumps({k:result[k] for k in ['status','front_status','new_model_calls','combined_budget','counts_in_original46']}))
    finally:
        if budget.calls:(ROOT/'new-model-calls.json').write_text(json.dumps(budget.calls,indent=2)+'\n')
        os.environ.pop(cfg['api_key_env'],None)

if __name__=='__main__':main()
