"""Build a bounded existing-plan revision from the recorded public counterexample."""
import importlib.util,json,sys
from pathlib import Path
from dataclasses import replace,asdict
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
spec=importlib.util.spec_from_file_location('base_component',OUT/'plan_execution_component.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
from contractedge.execution.review import ModelPublicReviewer
from contractedge.orchestration.feedback import make_feedback,feedback_prompt_view
from contractedge.orchestration.repair import validate_ready_trace
from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import canonical_digest
from contractedge.orchestration.semantic_delivery import delivery_context,inspect_delivery,require_delivery

def main():
    trace,method,packet,request,payload=m.inputs();old=m.ROOT
    plan=m.read(old/'changeset.json');after=m.read(old/'after.json')
    class NeverCall:
        def complete_json(self,request):raise AssertionError('Negative review must not call a model')
    review_plan={**plan,'public_execution_capabilities':payload['public_execution_capabilities']}
    review=ModelPublicReviewer(NeverCall(),len,{}).review(method.requirement_text,review_plan,after)
    assert review['decision']=='FAIL'
    feedback=make_feedback(case_id=method.case_id,run_id=packet.run_id,input_digest=packet.input_digest,
        round_id=0,from_snapshot=packet.snapshot_id,to_snapshot=after['snapshot_id'],patch_digest=m.sha(old/'candidate.patch'),
        execution_status=after['status'],observations=after['observations'],plan=review_plan,review=review,diagnosis=packet.raw)
    # The complete prior plan already supplies all live obligations. Carry every
    # producer-validated failure once per feedback projection, not repeated full
    # review quotes and original positive observation excerpts.
    projection=dict(rejected_trial={k:feedback[k] for k in ('from_snapshot','to_snapshot','patch_digest')},
        current_source_snapshot=packet.snapshot_id,
        failure_tasks=feedback_prompt_view(feedback)['failure_tasks'],
        pending_obligation_refs=[{'id':p['id'],'reason_category':p.get('reason_category')} for p in feedback['pending_obligations']],
        interpretation='Rejected historical trial; source has been restored. Review the prior plan against these actual values. No root cause is inferred by the checker.')
    context=RunContext(**trace['run_context']);retrieval={**context.retrieval_config,
        'planning_helper_contracts':True,'planning_helper_max_bytes':24000,
        'necessary_source_support':payload['read_only_source_support'],'source_prerequisites':payload['source_prerequisites']}
    context=replace(context,retrieval_config=retrieval)
    trace={**trace,'status':plan['status'],'changeset':plan,'run_context':asdict(context),'model_calls':m.read(old/'model_calls.json')}
    source=Path(m.read(old/'workspace/workspace.json')['source_root'])
    validate_ready_trace(trace,method,source,capabilities=payload['public_execution_capabilities'])
    request=build_changeset_request(packet,context,[{'code':'PUBLIC_EXECUTION_PLAN_FEEDBACK','message':json.dumps(projection,ensure_ascii=False)}],
        previous_attempt=dict(run_id=context.run_id,snapshot_id=context.snapshot_id,input_digest=context.input_digest,
            diagnosis_digest=canonical_digest(packet.raw),response_content=json.dumps(plan,ensure_ascii=False,sort_keys=True),state='CURRENT_PLAN_FOR_REVISION'))
    request=replace(request,thinking='disabled',metadata={**request.metadata,
        'component_generation_mode':'Public failure plan revision with existing disabled mode; not fresh end-to-end'})
    real,counter=m._build_provider(m.read(m.CONFIG));before=real.money_budget.summary();quote=m.quote_request(real,request,counter)
    result=dict(status='PUBLIC_FAILURE_REVISION_ZERO_API_PASS' if quote.total_tokens<=220000 else 'STOP_CONTEXT_CAPACITY',
        total_token_bound=quote.total_tokens,request_digest=quote.request_digest,source_snapshot=context.snapshot_id,
        historical_failure_snapshot=after['snapshot_id'],prior_plan_in_actual_messages=True,
        failed_predicates=len(projection['failure_tasks']['failed_predicates']),formal_ready=False)
    (OUT/'REVISION_INPUT_PREFLIGHT.json').write_text(json.dumps(result,indent=2)+'\n')
    assert quote.total_tokens<=220000,result
    semantic=delivery_context(method,consumer='FEEDBACK_CHANGESET_REVISION',snapshot_id=context.snapshot_id,
        diagnosis=trace['diagnosis'],changeset=plan,feedback=feedback,necessary_support=retrieval['necessary_source_support'],
        prerequisites=retrieval['source_prerequisites'])
    delivery=inspect_delivery(request,quote,semantic);require_delivery(delivery)
    assert real.money_budget.summary()==before
    for name,value in [('revision-planning.request.json',asdict(request)),('revision-planning.delivery.json',delivery),
            ('revision-public-feedback.json',feedback),('revision-ready.trace.json',trace)]:
        (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':main()
