"""Read-only actual plan replay and explicitly synthetic minimal correction."""
from copy import deepcopy
from dataclasses import replace
import hashlib,json,subprocess,types
from pathlib import Path
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import parse_method_input
from contractedge.diagnosis.validate import validate_diagnosis
from contractedge.retrieval.candidates import CandidateSet
from contractedge.source.snapshot import compute_snapshot
BASE=Path('/home/xhj/contractedge-diagnostic-source-v046-v6-20261026/methods/RW-CERTMANAGER-9116/RW-CERTMANAGER-9116')
OUT=Path(__file__).parent
read=lambda p:json.loads(Path(p).read_text());sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
f=read(BASE/'rounds/0/front.trace.json');method=parse_method_input(read(BASE/'rounds/0/front.method_input.json'))
public=tuple(e for e in f['diagnosis']['evidence_refs'] if e['kind']=='PUBLIC_OBSERVATION');method=replace(method,public_evidence=public)
ctx=RunContext(**f['run_context']);cand=CandidateSet(**f['candidate_set'])
packet=validate_diagnosis(f['diagnosis'],method,ctx,cand)
source=BASE/'workspace/source'/method.source_root;snapshot=compute_snapshot(source);assert snapshot.snapshot_id==f['snapshot_id']
kwargs=dict(policy=ctx.retrieval_config['public_execution_capabilities']['editable_policy'],frontier=ctx.retrieval_config['production_path_frontier'],capabilities=ctx.retrieval_config['public_execution_capabilities'])
old=types.ModuleType('old_branch_validate')
exec(compile(subprocess.check_output(['git','show','0673503:system/src/contractedge/changeset/validate.py'],text=True),'old_branch_validate.py','exec'),old.__dict__)
plan=deepcopy(f['changeset']);before=deepcopy(plan)
original=old.validate_semantic_changeset(plan,packet,snapshot,**kwargs)
current=validate_semantic_changeset(plan,packet,snapshot,**kwargs)
assert [e.code for e in original.errors]==['PRODUCTION_BRANCH_CHANGE_INVALID']
assert [e.code for e in current.errors]==['PRODUCTION_BRANCH_MODIFY_OPEN_REFS']
assert plan==before
fixture=deepcopy(plan)
for row in fixture['production_path_review']:
 if row['disposition']=='MODIFY':row['open_obligation_refs']=[]
assert fixture['open_obligations']==plan['open_obligations']
assert old.validate_semantic_changeset(fixture,packet,snapshot,**kwargs).valid
assert validate_semantic_changeset(fixture,packet,snapshot,**kwargs).valid
assert compute_snapshot(source).snapshot_id==snapshot.snapshot_id
summary=dict(status='ORIGINAL_PUBLIC_PLAN_ERROR_CLASSIFIED_AND_MINIMAL_FIXTURE_VALIDATED',
 case_id=method.case_id,front_trace_sha256=sha(BASE/'rounds/0/front.trace.json'),snapshot_id=snapshot.snapshot_id,
 original_errors=[dict(code=e.code,path=e.path,message=e.message) for e in original.errors],
 current_errors=[dict(code=e.code,path=e.path,message=e.message) for e in current.errors],
 original_plan_still_rejected=True,fixture_open_obligations_preserved=True,fixture_accepted_by_both_versions=True,
 fixture_is_new_model_result=False,new_model_calls=0,new_grader_calls=0,source_unchanged=True,formal_ready=False)
(OUT/'CERT_BRANCH_FEEDBACK_REPLAY.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False))
