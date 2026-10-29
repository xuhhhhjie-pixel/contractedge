"""Replay only the lost public planning-to-diagnosis boundary; zero dispatch."""
from pathlib import Path
import json,hashlib
from contractedge.orchestration.runner import diagnosis_continuity
root=Path('/home/xhj/contractedge-cross-case-v055-20261026/methods/R3-KPT-4673/R3-KPT-4673')
output=Path(__file__).parent
calls=json.loads((root/'model_calls.json').read_text())
prior=json.loads(calls[1]['messages'][1]['content'])['diagnosis_packet']
plan=json.loads(calls[1]['response_content'])
request=json.loads(calls[2]['messages'][1]['content'])
old=request['diagnosis_continuity']
new=diagnosis_continuity(prior,request['run_context']['snapshot_id'],request['evidence_catalog'],old['extension_gaps'],input_digest=request['run_context']['input_digest'],planning_context=plan)
added={'planning_branch_assessments','omitted_planning_branch_count','planning_context_digest','planning_branch_interpretation'}
assert {k:v for k,v in new.items() if k not in added}==old
rows=new['planning_branch_assessments'];validator=next(r for r in rows if r['source_evidence_ref']=='SRC-CU-60d3496476d58093')
assert 'runValidators' in validator['rationale'] and validator['open_obligations']
assert any('validator.owner_excludes_config' in x for o in validator['open_obligations'] for x in o.get('behavior_check_refs',[]))
request['diagnosis_continuity']=new
(output/'rediagnosis.request.payload.json').write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n')
raw=json.dumps(calls[2]['response_content'],ensure_ascii=False).encode()
result=dict(status='PUBLIC_PLANNING_BRANCH_HANDOFF_REPLAY_PASS',snapshot_id=request['run_context']['snapshot_id'],initial_request_changed=False,existing_supported_claims_unchanged=True,selected_evidence_ids_unchanged=True,current_source_digests_unchanged=True,carried_branch_refs=[r['source_evidence_ref'] for r in rows],validator_concern_present=True,new_model_calls=0,new_grader_calls=0,old_diagnosis_response_preserved_sha256=hashlib.sha256(raw).hexdigest(),old_failed_result_changed=False,interpretation='Counterfactual current serialized input only. Does not show that a new model diagnoses or repairs the missing branch.',formal_ready=False)
(output/'SAVED_HANDOFF_REPLAY.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
