"""Read-only live method/scoring audit; never invokes a model or evaluator."""
import hashlib
import json
from pathlib import Path
from datetime import datetime,timezone

root=Path.cwd();out=root/'outputs/reliability-closure-20261020';run=out/'runs/delivery-0.6.21-A-R3-KOPF-726-0-candidate';case=run/'cases/R3-KOPF-726'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
freeze=read(out/'frozen-method-v0.6.21.json');froot=Path(freeze['candidate_root'])
actual={str(p.relative_to(froot/'src')):sha(p) for p in sorted((froot/'src').rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
assert actual==freeze['files']
for rel,digest in freeze['harness_files'].items():assert sha(root/rel)==digest,rel
calls=read(case/'model_calls.json') if (case/'model_calls.json').exists() else []
rows=[]
for i,c in enumerate(calls):
 row=dict(index=i,prompt_version=c['prompt_version'],response_received=isinstance(c.get('response_content'),str) and not c.get('error'),finish_reason=c.get('response_metadata',{}).get('finish_reason'),error=c.get('error'),prompt_tokens_reserved=c.get('prompt_tokens_reserved'))
 try:json.loads(c.get('response_content') or '');row['json_parseable']=True
 except (ValueError,TypeError):row['json_parseable']=False
 meta=c.get('request_metadata',{});row['source_snapshot']=meta.get('snapshot_id');row['gap_delivery']=meta.get('gap_delivery');rows.append(row)
trace=read(case/'repair.trace.json') if (case/'repair.trace.json').exists() else None
score_records=read(out/'independent-v0.6.21/records.json') if (out/'independent-v0.6.21/records.json').exists() else []
scored=[r for r in score_records if r['case_id']=='R3-KOPF-726' and r['group']=='A' and r['variant']=='candidate' and r['repeat']==0]
report=dict(as_of=datetime.now(timezone.utc).isoformat(),version='0.6.21',case_id='R3-KOPF-726',group='A',repeat=0,method_digest=freeze['method_digest'],frozen_source_and_harness_match=True,calls=rows,method_status=trace['status'] if trace else 'RUNNING',patch=trace.get('candidate') if trace else None,errors=trace.get('errors') if trace else [],independent_status=scored[0]['status'] if scored else 'PENDING',rounds=[],scope='One fresh development G4, not complete same-version panel/repetition/recovery acceptance',formal_ready=False)
for d in sorted((case/'rounds').glob('*')) if (case/'rounds').exists() else []:
 if not d.is_dir():continue
 row={'round_id':d.name}
 for name in ['front.trace.json','feedback.json','candidate-decision.json','execution.json']:
  f=d/name
  if f.exists():
   value=read(f)
   row[name]=({k:value.get(k) for k in ['status','semantic_outcome','build_state','route','failure_kind','public_check_results','operation_assessments'] if k in value} if name!='front.trace.json' else dict(status=value['status'],violations=[dict(id=v['id'],causal_status=v.get('causal_status'),causal_explanation=v.get('causal_explanation')) for v in (value.get('diagnosis') or {}).get('violations',[])],prerequisites=(value.get('run_context') or {}).get('retrieval_config',{}).get('source_prerequisites',[]),precondition_review=(value.get('changeset') or {}).get('precondition_review',[])))
 report['rounds'].append(row)
if trace:
 impl=read(run/'implementation-manifest.json');harness=read(run/'harness-manifest.json');assert impl['unchanged'] and impl['before']==freeze['files'] and impl['after']==freeze['files'];assert harness['unchanged'];report['worker_before_after_identity_verified']=True
 if trace.get('candidate'):
  patch=Path(trace['candidate']['patch_path']);assert sha(patch)==trace['candidate']['patch_sha256'];assert patch.stat().st_size==trace['candidate']['patch_bytes']
ledger=read(Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json'));report['committed_cny_including_reservations']=sum(c.get('charged_micro_cny',c['reserved_micro_cny']) for c in ledger['calls'])/1e6
(out/'G4_LIVE_AUDIT_v0621.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['method_status','independent_status','errors','committed_cny_including_reservations']}));print('calls',[(r['prompt_version'],r['response_received']) for r in rows])
