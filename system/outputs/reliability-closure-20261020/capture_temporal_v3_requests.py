"""Capture real fresh-S0/S1 diagnosis builders under the calibrated v3 public observations."""
import hashlib
import json
from pathlib import Path

from contractedge.cli import _token_counter
from contractedge.execution.probe_specs import select_probes
from contractedge.orchestration.repair import select_observations
from contractedge.orchestration.runner import run_front_half


class Captured(BaseException):
    pass


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
    read=lambda p:json.loads(p.read_text())
    original=out/'runs/delivery-0.6.15-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    method=read(original/'method_input.json');old=read(original/'rounds/0/front.trace.json')
    config=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');counter=_token_counter(config['tokenizer'])
    feedback=read(out/'temporal-v3-feedback.json');rows=[]
    for name in ('broken','saved_candidate'):
        saved=out/'temporal-v3-calibration'/name
        source=Path(read(saved/'workspace/workspace.json')['source_root'])
        observed=read(saved/'public/observation.json')
        specs=select_probes(method['requirement_text'],source,config['method_probe_profiles'])
        capabilities={**old['run_context']['retrieval_config']['public_execution_capabilities'],
            'method_behavior_probes':[{k:v for k,v in spec.items() if k!='program'} for spec in specs]}
        cfg={**config,'run_id':feedback['run_id'],'model':old['run_context']['model_config'],
             'public_execution_capabilities':capabilities}
        class NoNetwork:
            request=None
            def complete_json(self,request):self.request=request;raise Captured()
        provider=NoNetwork()
        try:
            trace=run_front_half(method,source if method['source_root']=='.' else source.parent,provider,cfg,counter,
                execution_feedback=feedback if name=='saved_candidate' else None,
                public_observations=tuple(select_observations(observed['observations'],method['requirement_text'],counter,config['max_public_evidence_tokens'])))
        except Captured:pass
        else:raise AssertionError((trace.status,trace.errors))
        request=provider.request;body=json.loads(request.messages[1]['content'])
        public=[r for r in body['evidence_catalog'] if r.get('behavior_check')]
        assert len(public)==4 and all(r['behavior_check']['id'].startswith('alertmanager-reload-v3:') for r in public)
        assert all(r['snapshot_id']==observed['snapshot_id'] for r in public)
        source_records=[r for r in body['evidence_catalog'] if r.get('kind')=='SOURCE_SPAN']
        assert any('func (d *Dispatcher) Run(' in r['excerpt'] for r in source_records)
        assert all(hashlib.sha256(r['excerpt'].encode()).hexdigest()==r['content_digest'] for r in source_records)
        count=sum(counter(m['content']) for m in request.messages);assert count<=config['initial_diagnosis_context_tokens']
        rows.append(dict(stage=name,snapshot_id=observed['snapshot_id'],input_bound=count,
            public_predicates=len(public),gap_delivery=request.metadata.get('gap_delivery'),
            current_route=feedback['route'] if name=='saved_candidate' else None))
    result=dict(api_calls=0,scope='Actual request builders with calibrated current observations; no model result',records=rows)
    (out/'temporal-v3-request-replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
