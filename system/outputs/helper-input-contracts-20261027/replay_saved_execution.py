"""Rebuild the saved public execution request against unchanged Broken source."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from contractedge.execution.topomen import build_execution_request
from contractedge.source.snapshot import compute_snapshot
from contractedge.cli import _build_provider
from contractedge.model.admission import quote_request

SAVED=Path('/home/xhj/contractedge-e2e-source058-20261027/methods/R3-KPT-4673/R3-KPT-4673')
SOURCE=Path('/home/xhj/contractedge-generation-replay-v058-20261027/source/kpt-6345d96d661eae1f6cdcb97b1f70080ce35f61a4')
OUT=Path(__file__).parent


def main():
    calls=json.loads((SAVED/'model_calls.json').read_text())
    old=json.loads(calls[4]['messages'][1]['content'])
    plan=json.loads((SAVED/'rounds/0/plan.json').read_text())
    snapshot=compute_snapshot(SOURCE).snapshot_id
    assert snapshot==plan['snapshot_id']==old['acceptance']['snapshot_id']
    config=json.loads(Path('configs/fixed46-candidate-v058-budget150.json').read_text())
    provider,counter=_build_provider(config)
    request=build_execution_request(plan,SOURCE,old['acceptance']['scope_policy'],counter,config,
        requirement=old['requirement'],declared_artifacts=old['declared_artifacts'],
        observations=old['public_observations'],feedback=old['execution_feedback'],
        previous_attempt=old['previous_execution_attempt'],provider=provider)
    new=json.loads(request.messages[1]['content'])
    assert {k:v for k,v in old.items() if k!='read_only_source_support'}=={k:v for k,v in new.items() if k!='read_only_source_support'}
    old_support=old['read_only_source_support'];new_support=new['read_only_source_support']
    assert new_support[:len(old_support)]==old_support
    joined='\n'.join(r['excerpt'] for r in new_support)
    assert 'type Selector struct' in joined and 'func (s Selector) IsEmpty()' in joined
    assert 'func IsMatch(' in joined
    assert len(new_support)<=8 and sum(len(r['excerpt'].encode()) for r in new_support)<=config['max_execution_support_bytes']
    quote=quote_request(provider,request,counter)
    wire=json.loads(quote.prepared.body)
    assert wire['messages'][1]['content']==request.messages[1]['content']
    assert quote.total_tokens<=config['max_context_tokens']
    assert compute_snapshot(SOURCE).snapshot_id==snapshot
    result=dict(status='SAVED_EXECUTION_INPUT_CONTRACT_REPLAY_PASS',snapshot_id=snapshot,
        original_plan_and_targets_unchanged=True,all_other_payload_fields_unchanged=True,
        original_support_records=len(old_support),final_support_records=len(new_support),
        selector_type_delivered=True,empty_predicate_delivered=True,matching_helper_delivered=True,
        final_support_bytes=sum(len(r['excerpt'].encode()) for r in new_support),
        request_total_bound=quote.total_tokens,context_cap=config['max_context_tokens'],
        adapter_body_verified=True,new_model_calls=0,new_grader_calls=0,source_unchanged=True,
        original_failure_unchanged=True,formal_ready=False,
        limitation='Input contract delivery only; no new edit or repair-quality claim.')
    (OUT/'SAVED_EXECUTION_REPLAY.json').write_text(json.dumps(result,indent=2)+'\n')
    (OUT/'execution.request.json').write_text(json.dumps(asdict(request),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
