from pathlib import Path
from dataclasses import asdict,replace
from types import SimpleNamespace
import json,hashlib
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import parse_method_input
from contractedge.diagnosis.validate import validate_diagnosis
from contractedge.source.snapshot import compute_snapshot
from contractedge.retrieval.candidates import CandidateSet
from contractedge.diagnosis.prompt import build_diagnosis_request
from contractedge.diagnosis.evidence import reader_reference_contexts
from contractedge.changeset.prompt import build_changeset_request
from contractedge.orchestration.runner import required_source_prerequisites,required_planning_source_support
from contractedge.execution.topomen import build_execution_request
from contractedge.tokenizers import utf8_byte_token_bound
r=Path('/home/xhj/contractedge-diagnostic-source053-v1-20261026/methods/RW-ALERTMANAGER-4705/RW-ALERTMANAGER-4705');front=json.loads((r/'rounds/1/front.trace.json').read_text());method=parse_method_input(json.loads((r/'rounds/1/front.method_input.json').read_text()));method=replace(method,public_evidence=tuple(json.loads((r/'rounds/1/front.public_observations.json').read_text())));source=r/'workspace/source'/method.source_root;snapshot=compute_snapshot(source);assert snapshot.snapshot_id==front['snapshot_id'];cs=CandidateSet(**front['candidate_set']);context=RunContext(**front['run_context']);reader_ids={row['candidate_id'] for row in reader_reference_contexts(cs.candidates)};cs=replace(cs,candidates=tuple({**u,'gap_role_keys':[*u.get('gap_role_keys',[]),'SOURCE_READER:'+u['id']]} if u['id'] in reader_ids else u for u in cs.candidates),required_endpoint_ids=tuple(dict.fromkeys([*cs.required_endpoint_ids,*sorted(reader_ids)])));out=Path('outputs/publication-context-20261026/actual-current-builder-replay-v6');out.mkdir()
for u in cs.candidates:
 if u.get('origin_kind')=='DECLARED_ARTIFACT':continue
 loc=u['locator'];text=''.join((source/u['path']).read_text().splitlines(keepends=True)[loc['start_line']-1:loc['end_line']]);assert text==u['text'] and hashlib.sha256(text.encode()).hexdigest()==u['content_digest']
req=build_diagnosis_request(method,front['requirement_spec'],cs,context,scope_resolution=SimpleNamespace(**front['scope_resolution']));body=json.loads(req.messages[1]['content']);assert any(x['variable']=='disp' for x in body['source_reader_reference_contexts']);(out/'rediagnosis.request.json').write_text(json.dumps(asdict(req),indent=2)+'\n')
packet=validate_diagnosis(front['diagnosis'],method,context,cs);context=replace(context,retrieval_config={**context.retrieval_config,'source_prerequisites':required_source_prerequisites(cs),'necessary_source_support':required_planning_source_support(method,cs,context.snapshot_id)});req=build_changeset_request(packet,context);b=json.loads(req.messages[1]['content']);assert any('groupFn' in q['reason'] and 'publication' in q['reason'] for q in b['source_prerequisites']);(out/'planning.request.json').write_text(json.dumps(asdict(req),indent=2)+'\n')
plan=json.loads((r/'rounds/1/plan.json').read_text());config=json.loads(Path('configs/fixed46-candidate-v053-budget150.json').read_text());req=build_execution_request(plan,source,context.retrieval_config['public_execution_capabilities']['editable_policy'],utf8_byte_token_bound,config,requirement=method.requirement_text);b=json.loads(req.messages[1]['content']);assert any(x['variable']=='disp' for x in b['source_reader_reference_contexts']);(out/'execution.request.json').write_text(json.dumps(asdict(req),indent=2)+'\n');assert compute_snapshot(source).snapshot_id==snapshot.snapshot_id
summary=dict(status='CURRENT_SAVED_BUILDER_REQUESTS_CONTAIN_EXACT_READER_REFERENCE_WINDOWS',snapshot_id=snapshot.snapshot_id,rediagnosis=True,planning=True,execution=True,new_model_calls=0,new_grader_calls=0,formal_ready=False,scope='Current builders with new producer reader-role metadata projected on unchanged saved candidates/source/public observations, plus old accepted execution plan; not a new model plan or effect result');(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
