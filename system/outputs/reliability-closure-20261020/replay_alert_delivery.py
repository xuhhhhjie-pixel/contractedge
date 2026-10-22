import json
from pathlib import Path
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import parse_method_input
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.source.snapshot import compute_snapshot
from contractedge.retrieval.candidates import CandidateSet,extend_once,trim_context_bundle
from contractedge.retrieval.gap_delivery import locate_gap_roles,audit_gap_delivery
from contractedge.diagnosis.prompt import build_diagnosis_request
out=Path('outputs/reliability-closure-20261020');old=json.load(open('outputs/deepseek-final-transfer-v2-20261020/cases/RW-ALERTMANAGER-4705/rounds/0/front.trace.json'))
source=next((out/'alert-source').iterdir());snapshot=compute_snapshot(source)
method,_=method_input_from_task('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public','RW-ALERTMANAGER-4705');method=parse_method_input(method)
context=RunContext(**old['run_context']);requirement={**old['requirement_spec'],'lineage_required':True,'operation_consistency_required':True}
roles,audit=locate_gap_roles(snapshot,old['diagnosis']['evidence_gaps'],dict(seed_units=old['candidate_set']['candidates']))
kwargs={k:v for k,v in old['candidate_set'].items() if k in CandidateSet.__dataclass_fields__};kwargs['extension_used']=False
initial=CandidateSet(**kwargs);candidate=extend_once(initial,old['diagnosis']['evidence_gaps'],roles,max_context_tokens=220000,token_counter=lambda s:len(s.encode()))
required=list(candidate.required_endpoint_ids)
while True:
 request=build_diagnosis_request(method,requirement,candidate,context)
 size=sum(len(m['content'].encode()) for m in request.messages)
 if size<=120000 or not candidate.candidates:break
 candidate=trim_context_bundle(candidate,request_tokens=size,limit=120000)
receipt=audit_gap_delivery([{'required_unit_ids':required}],list(candidate.candidates),json.dumps(request.messages,ensure_ascii=False))
summary={'scope':'PUBLIC_ONLY_FINAL_REQUEST_REPLAY_NO_MODEL','api_calls':0,'snapshot_matches_baseline':snapshot.snapshot_id==old['snapshot_id'],'request_bytes':size,'budget_bytes':120000,'selection_count':candidate.selected_count,'selected':[{k:c.get(k) for k in ['id','path','symbol','locator','truncated','gap_role_keys']} for c in candidate.candidates],'delivery':receipt,'search':audit}
(out/'alert-final-request-replay.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({'request_bytes':size,'selected':[(r['path'],r['symbol']) for r in summary['selected']],'delivery':receipt['status']},ensure_ascii=False))
