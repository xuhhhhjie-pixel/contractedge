"""Capture public reasoning-to-edit links; no inference of final behavior success."""
from pathlib import Path
import json,hashlib
r=Path('/home/xhj/contractedge-system');a=Path(__file__).resolve().parent
case=r/'outputs/reliability-closure-20261020/runs/delivery-0.6.37-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
rows=[]
for folder in sorted((case/'rounds').iterdir()):
 if not folder.is_dir() or not (folder/'front.trace.json').exists():continue
 trace=read(folder/'front.trace.json');plan=trace.get('changeset') or {};execution=read(folder/'execution.json') if (folder/'execution.json').exists() else {};feedback=read(folder/'feedback.json') if (folder/'feedback.json').exists() else None
 assert plan['snapshot_id']==trace['snapshot_id']==execution.get('from_snapshot',trace['snapshot_id'])
 rows.append(dict(round=int(folder.name),trace_sha256=sha(folder/'front.trace.json'),source_snapshot=trace['snapshot_id'],diagnosis_status=trace['diagnosis']['status'],
  diagnosed_violations=trace['diagnosis']['violations'],plan_status=plan['status'],plan_changes=[dict(id=x['id'],semantic_action=x['semantic_action'],intent=x.get('intent'),violation_refs=x['violation_refs']) for x in plan['changes']],
  preconditions=plan.get('precondition_review',[]),preservation_impact=plan.get('preservation_impact',[]),preserve_count=len(plan['preserve_constraints']),
  execution_status=execution.get('status'),candidate_snapshot=execution.get('to_snapshot'),changed_paths=execution.get('changed_paths'),
  candidate_patch_sha256=sha(folder/'candidate.patch') if (folder/'candidate.patch').exists() else None,
  feedback=({k:feedback[k] for k in ['route','semantic_outcome','build_state']} if feedback else None)))
report=dict(version='0.6.37',scope='Public method trace only; full violation objects retained for audit, not independent behavior proof or generic causality',private_materials_loaded=False,rows=rows)
(a/'repair-chain-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps([dict(round=x['round'],plan=x['plan_status'],preserve=x['preserve_count'],source=x['source_snapshot'],candidate=x['candidate_snapshot']) for x in rows]))
