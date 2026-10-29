"""Public request transport audit; no new model calls and no score interpretation."""
import json,hashlib
from pathlib import Path
r=Path('/home/xhj/contractedge-system');a=Path(__file__).resolve().parent
case=r/'outputs/reliability-closure-20261020/runs/delivery-0.6.37-A-R4-ACK-CLOUDWATCH-2985-0-candidate/cases/R4-ACK-CLOUDWATCH-2985'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda s:hashlib.sha256(s.encode()).hexdigest()
def walk(value):
 if isinstance(value,dict):
  yield value
  for child in value.values():yield from walk(child)
 elif isinstance(value,list):
  for child in value:yield from walk(child)
rows=[]
for i,call in enumerate(read(case/'model_calls.json')):
 messages=call['messages'];body=json.loads(messages[1]['content'])
 if 'evidence_catalog' in body:records=body['evidence_catalog'];origin='evidence_catalog'
 elif 'diagnosis_packet' in body:records=body['diagnosis_packet'].get('evidence_refs',[]);origin='diagnosis_packet.evidence_refs'
 elif 'public_observations' in body:records=body['public_observations'];origin='public_observations'
 else:records=[];origin='OTHER_STAGE'
 checks={str(x.get('id') or x.get('check_id')) for value in records for x in [value.get('behavior_check',{})] if x}
 assistants=[m['content'] for m in messages[2:] if m['role']=='assistant']
 response=call.get('response_content');status=None
 if response:
  try:status=json.loads(response).get('status')
  except (ValueError,AttributeError):pass
 rows.append(dict(index=i,prompt_version=call['prompt_version'],messages_sha256=sha(json.dumps(messages,sort_keys=True,ensure_ascii=False)),
  public_source=origin,check_ids=sorted(checks),check_count=len(checks),evidence_snapshots=sorted({str(x['snapshot_id']) for x in records if 'snapshot_id' in x}),
  prior_draft_digests=[sha(x) for x in assistants],response_status=status,response_present=bool(response),
  input_bound=sum(len(m['content'].encode()) for m in messages),output_bound=call['output_tokens_reserved']))
report=dict(scope='Actual serialized method requests only; checks in current stage input, prior plan text excluded from evidence count; no semantic sufficiency or scoring claim',version='0.6.37',rows=rows,new_model_calls=0)
(a/'actual-request-delivery.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(rows))
