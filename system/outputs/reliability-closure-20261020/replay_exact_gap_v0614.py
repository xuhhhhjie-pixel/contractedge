"""Reproduce saved initial diagnosis and plan; capture extension without HTTP."""
import json
from pathlib import Path
from unittest.mock import patch

from contractedge.cli import _token_counter
from contractedge.model.provider import ModelResponse
from contractedge.orchestration import runner
from contractedge.orchestration.repair import select_observations
from contractedge.retrieval import gap_delivery


class Captured(BaseException):
    pass


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
    case=out/'runs/delivery-0.6.14-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    read=lambda p:json.loads(p.read_text())
    old=read(case/'rounds/0/front.trace.json');calls=read(case/'model_calls.json')
    cfg=read(root/'configs/deepseek-reliability-closure-v5-budget150.json')
    cfg.update(run_id=old['run_id'],model=old['run_context']['model_config'],
        public_execution_capabilities=old['run_context']['retrieval_config']['public_execution_capabilities'])
    counter=_token_counter(cfg['tokenizer']);method=read(case/'method_input.json')
    observed=read(case/'broken-public/observation.json')
    observations=tuple(select_observations(observed['observations'],method['requirement_text'],counter,cfg['max_public_evidence_tokens']))
    source=next((out/'public-source-alertmanager').iterdir())
    requests=[];receipts=[]
    build=runner.build_diagnosis_request;audit=gap_delivery.audit_gap_delivery
    def capture_build(*args,**kw):
        request=build(*args,**kw);cs=args[2]
        captured['candidate_set']=cs
        requests.append(dict(extension=cs.extension_used,required_ids=list(cs.required_endpoint_ids),
            selected=[dict(id=u['id'],path=u['path'],symbol=u.get('symbol'),locator=u['locator'],roles=u.get('gap_role_keys'),digest=u['content_digest']) for u in cs.candidates],
            tokens=sum(counter(m['content']) for m in request.messages)))
        return request
    def capture_audit(*args,**kw):
        result=audit(*args,**kw);receipts.append(result);return result
    class Replay:
        index=0
        def complete_json(self,request):
            if self.index==2:
                captured['request']=request
                raise Captured()
            response=ModelResponse(calls[self.index]['response_content'],calls[self.index].get('response_metadata',{}))
            self.index+=1;return response
    captured={};provider=Replay();trace=None
    with patch.object(runner,'build_diagnosis_request',capture_build),patch.object(gap_delivery,'audit_gap_delivery',capture_audit):
        try:trace=runner.run_front_half(method,source if method['source_root']=='.' else source.parent,provider,cfg,counter,public_observations=observations)
        except Captured:pass
    result=dict(api_calls=0,saved_responses_replayed=provider.index,extension_request_reached='request' in captured,
        status=trace.status if trace else 'EXTENSION_CAPTURED',errors=list(trace.errors) if trace else [],requests=requests,delivery_receipts=receipts)
    if 'request' in captured:
        cs=captured['candidate_set']
        anchors={role:u for u in cs.candidates for role in u.get('gap_role_keys',[]) if role.endswith(':anchor')}
        assert anchors['EG1:anchor']['symbol']=='Dispatcher'
        assert anchors['EG2:anchor']['symbol']=='Run'
        assert 'd.alerts.Subscribe' in anchors['EG3:anchor']['text']
        assert 'groupFn := func' in anchors['EG4:anchor']['text']
        result['exact_anchor_checks']={role:dict(symbol=u.get('symbol'),path=u['path'],content_digest=u['content_digest']) for role,u in anchors.items()}
        assert any(u['path']=='provider/mem/mem.go' and u.get('symbol')=='Subscribe' for u in cs.candidates)
        from dataclasses import replace
        from contractedge.contracts.validation import parse_method_input
        from contractedge.orchestration.repair import validate_ready_trace
        from contractedge.changeset.prompt import build_changeset_request
        from contractedge.changeset.topomen import translate_for_topomen
        from contractedge.execution.topomen import build_execution_request
        prior=out/'runs/delivery-0.6.13-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
        ready=read(prior/'rounds/0/front.trace.json')
        packet,context=validate_ready_trace(ready,parse_method_input(read(prior/'method_input.json')),source)
        support=runner.required_planning_source_support(parse_method_input(method),cs,old['snapshot_id'])
        context=replace(context,retrieval_config={**context.retrieval_config,'necessary_source_support':support})
        planning=build_changeset_request(packet,context)
        assert json.loads(planning.messages[1]['content'])['read_only_source_support']==support
        plan=translate_for_topomen(ready['changeset'],'contractedge.topomen_plan.v1',diagnosis=packet.raw,
            source_support=support,public_capabilities=context.retrieval_config['public_execution_capabilities'])
        config=read(root/'configs/deepseek-reliability-closure-v5-budget150.json')
        execution=build_execution_request(plan,source,read(case/'projection.json')['editable_policy'],counter,config,requirement=method['requirement_text'])
        body=json.loads(execution.messages[1]['content']);targets={r['path'] for r in body['current_target_files']}
        delivered={r['id'] for r in body['read_only_source_support']}|{r['id'] for r in support if r['locator']['path'] in targets}
        assert {'SRC-'+i for i in cs.required_endpoint_ids}<=delivered
        result['downstream_transport']=dict(scope='Transport fixture with saved unchanged-S0 valid Ready plan, not a regenerated repair',
            saved_ready_consumer_passed=True,required_sources=len(support),provider_subscribe_delivered=True,
            planning_input=sum(counter(m['content']) for m in planning.messages),
            execution_input=sum(counter(m['content']) for m in execution.messages),all_required_execution_source_delivered=True)
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output-name',default='exact-gap-replay-v0615.json');args=parser.parse_args()
    (out/args.output_name).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
