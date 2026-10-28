"""Read-only recorded official prefix: compare the exact correction, never HTTP."""
import json,sys,hashlib
from dataclasses import asdict
from pathlib import Path
S=Path('/home/xhj/contractedge-alert-closure-20261028/system');sys.path[:0]=[str(S/'src'),str(S/'scripts')]
from contractedge.cli import _build_provider
from contractedge.contracts.validation import parse_method_input
from contractedge.orchestration.runner import run_front_half
from contractedge.model.provider import ModelResponse
from contractedge.model.budget import BudgetedProvider
R=Path('/home/xhj/contractedge-fixed46-official-v068-20261028');D=R/'methods/fixed46-official-v068-20261028-bcd9a8d56a4e00f2f902f406';ST=D/'.isolated-8a5d7540cf044247972edd5c2d29833f';F=D/'R4-ACK-CLOUDWATCH-2985/rounds/0'
read=lambda p:json.loads(Path(p).read_text())
calls=read(ST/'parent-model-calls.json');cfg=read(F/'front.config.json');wire,counter=_build_provider({**read(ST/'original-config.json'),'money_budget':None})
class Replayed:
    def __init__(self):self.i=0;self.comparisons=[]
    def request_overhead(self,r):return wire.request_overhead(r)
    def request_metadata(self,r):return wire.request_metadata(r)
    def complete_json(self,r):
        i=self.i;self.i+=1;actual=asdict(r);expected=calls[i]['request'];actual=json.loads(json.dumps(actual))
        different=[k for k in set(actual)|set(expected) if actual.get(k)!=expected.get(k)]
        self.comparisons.append({'index':i,'identical':not different,'different_fields':different})
        if different:
            Path('/tmp/ce-ack-replay-diff.json').write_text(json.dumps({'actual':actual,'expected':expected},indent=2));raise RuntimeError('EXACT_REPLAY_MISMATCH')
        if i==2:raise RuntimeError('ZERO_CALL_CAPTURE_BOUNDARY')
        return ModelResponse(calls[i]['response_content'],calls[i]['response_metadata'])
p=Replayed();budget=BudgetedProvider(p,counter,max_tokens=2000000,max_seconds=9000,max_calls=12)
mi=parse_method_input(read(F/'front.method_input.json'));source=Path(read(ST/'producer/workspace/workspace.json')['source_root'])
before=hashlib.sha256((ST/'parent-model-calls.json').read_bytes()).hexdigest()
front=run_front_half(mi,source if mi.source_root=='.' else source.parent,budget,cfg,counter,public_observations=read(F/'front.public_observations.json'),execution_feedback=read(F/'front.execution_feedback.json')).to_wire()
assert before==hashlib.sha256((ST/'parent-model-calls.json').read_bytes()).hexdigest()
result={'status':'PASS_EXACT_PREFIX_AND_CORRECTION_REPLAY' if p.i==3 and all(c['identical'] for c in p.comparisons) else 'FAIL','comparisons':p.comparisons,'front_status_after_capture':front['status'],'new_model_calls':0,'source_sha256':'c73764051dd244529be00a5cfe8969f547d421b82caa404705f9f43cd699676d','original_prefix_unchanged':True}
out=S/'outputs/alert-repeat-closure-20261028/V068_ACK_402_EXACT_REPLAY.json';out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
