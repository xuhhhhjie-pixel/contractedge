"""Counterfactual guard evaluation from saved public data; not a real trial."""
import sys,json,copy
from pathlib import Path
r=Path('/home/xhj/contractedge-system');sys.path.insert(0,str(Path(sys.argv[1])/'src'));label=sys.argv[2]
from contractedge.orchestration.candidate_guard import compare_candidate_checks,preservation_check_ids
case=r/'outputs/reliability-closure-20261020/runs/delivery-0.6.37-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985';read=lambda p:json.loads(Path(p).read_text())
before=read(case/'rounds/0/public/observation.json');feedback=read(case/'rounds/0/feedback.json');plan=read(case/'rounds/1/public-review-plan.json');trial=copy.deepcopy(before);trial['snapshot_id']='f'*64
for o in trial['observations']:
 o['snapshot_id']=trial['snapshot_id']
 if o.get('behavior_check',{}).get('id')=='cloudwatch-tags-v2:put_error':o['behavior_check']['status']='FAIL'
ids=preservation_check_ids(plan,feedback);decision=compare_candidate_checks({**before,'feedback':feedback},trial,ids)
record=dict(scope='Counterfactual only: old actual public PASS put_error changed to FAIL in-memory, current real candidate has not been observed regressing this check. No source edit/model call/behavior score.',label=label,protected_check_ids=ids,decision=decision['decision'],regressions=decision['regressions'],old_whole_claim_retained_count=len(feedback['retained_constraints']),new_model_preserve_constraints=len(plan['preserve_constraints']),private_materials_loaded=False)
(Path(__file__).resolve().parent/(label+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record))
