"""Actual saved planner request replay, public inputs and no model calls."""
from dataclasses import asdict,replace
import json
from pathlib import Path
from contractedge.cli import _build_provider
from contractedge.changeset.prompt import build_changeset_request
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import parse_method_input,validate_diagnosis
from contractedge.execution.helper_context import planning_helper_source_support
from contractedge.model.provider import ModelRequest
from contractedge.model.admission import quote_request
from contractedge.orchestration.runner import required_planning_source_support,required_source_prerequisites
from contractedge.orchestration.semantic_delivery import delivery_context,inspect_delivery,require_delivery
from contractedge.retrieval.candidates import CandidateSet
from contractedge.source.snapshot import compute_snapshot

SAVED=Path('/home/xhj/contractedge-e2e-source058-20261027/methods/R3-KPT-4673/R3-KPT-4673')
SOURCE=Path('/home/xhj/contractedge-generation-replay-v058-20261027/source/kpt-6345d96d661eae1f6cdcb97b1f70080ce35f61a4')
OUT=Path(__file__).parent


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    trace=read(SAVED/'rounds/0/front.trace.json');calls=read(SAVED/'model_calls.json')
    old=json.loads(calls[1]['messages'][1]['content'])
    config=read('configs/fixed46-candidate-v060-budget150.json');provider,counter=_build_provider(config)
    money=provider.money_budget.summary();snapshot=compute_snapshot(SOURCE)
    assert snapshot.snapshot_id==trace['snapshot_id']
    method=parse_method_input(read(SAVED/'method_input.json'));candidates=CandidateSet(**trace['candidate_set'])
    packet=validate_diagnosis(trace['diagnosis'],method,None);context=RunContext(**trace['run_context'])
    helpers,audit=planning_helper_source_support(snapshot,candidates.candidates)
    support=required_planning_source_support(method,candidates,snapshot.snapshot_id,helper_support=helpers)
    prerequisites=required_source_prerequisites(candidates,helper_support=helpers)
    assert helpers and any('func MergeWithInput(' in r['excerpt'] for r in helpers)
    context=replace(context,retrieval_config={**context.retrieval_config,'planning_helper_contracts':True,
        'planning_helper_max_bytes':24000,'planning_helper_audit':audit,
        'necessary_source_support':support,'source_prerequisites':prerequisites})
    new=build_changeset_request(packet,context,old['validation_feedback'])
    body=json.loads(new.messages[1]['content'])
    excluded={'read_only_source_support','source_prerequisites','output_schema','run_context','public_behavior_check_catalog'}
    assert old['run_context']=={k:v for k,v in body['run_context'].items() if k!='planning_helper_contracts'}
    assert {k:v for k,v in old.items() if k not in excluded}=={k:v for k,v in body.items() if k not in excluded}
    assert len(prerequisites)<=12 and 'precondition_review' in new.output_schema['required']
    # Keep exact historical model/transport/output-budget settings; only producer-built payload/system/schema differ.
    raw={**calls[1]['request'],'messages':tuple(calls[1]['request']['messages'])}
    request=replace(ModelRequest(**raw),messages=new.messages,output_schema=new.output_schema,
        metadata={**raw.get("metadata",{}),"planning_helper_contracts":new.metadata["planning_helper_contracts"],
                  "system_prompt_sha256":new.metadata["system_prompt_sha256"]})
    quote=quote_request(provider,request,counter);wire=json.loads(quote.prepared.body)
    assert wire['messages'][1]['content']==request.messages[1]['content']
    assert quote.total_tokens<=config['max_context_tokens']
    semantic=delivery_context(method,consumer='SEMANTIC_CHANGESET',snapshot_id=snapshot.snapshot_id,
        diagnosis=trace['diagnosis'],necessary_support=support,prerequisites=prerequisites)
    delivery=inspect_delivery(request,quote,semantic);require_delivery(delivery)
    old_plan=validate_semantic_changeset(trace['changeset'],packet,snapshot,
        policy=config.get('editable_policy'),prerequisites=prerequisites,
        capabilities=context.retrieval_config['public_execution_capabilities'])
    assert any(e.code=='PRECONDITION_REVIEW_INCOMPLETE' for e in old_plan.errors)
    assert provider.money_budget.summary()==money and compute_snapshot(SOURCE).snapshot_id==snapshot.snapshot_id
    write(OUT/'integrated-planning.request.json',asdict(request));write(OUT/'integrated-planning.delivery.json',delivery)
    write(OUT/'INTEGRATED_PLANNING_REPLAY.json',dict(status='ACTUAL_SAVED_PLANNING_HELPER_REPLAY_PASS',model_calls=0,
        source_snapshot=snapshot.snapshot_id,source_unchanged=True,helper_records=len(helpers),
        helper_bytes=sum(len(r['excerpt'].encode()) for r in helpers),prerequisite_reviews=len(prerequisites),
        actual_adapter_body_verified=True,final_delivery_gate_pass=True,old_plan_missing_review_rejected=True,
        other_payload_fields_unchanged=True,model_and_output_budget_unchanged=True,
        total_token_bound=quote.total_tokens,context_cap=config['max_context_tokens'],formal_ready=False,
        limitation='Planning input and review accountability only; no model-generated new plan or repair-quality improvement established.'))
    print(json.dumps(read(OUT/'INTEGRATED_PLANNING_REPLAY.json')))

if __name__=='__main__':main()
