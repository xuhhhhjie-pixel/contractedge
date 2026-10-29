"""Zero-dispatch replay; public selection and parent score interpretation stay separate."""
import hashlib,json,sys
from pathlib import Path
SYSTEM=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(SYSTEM/'src'),str(SYSTEM/'scripts')]
from contractedge.orchestration.candidate_guard import compare_candidate_checks,preservation_check_ids
from score_isolated_method import read_recorded_score
ROOT=Path('/home/xhj/contractedge-diagnostic-source054-v2-20261026')
method=ROOT/'methods/RW-ALERTMANAGER-4705/RW-ALERTMANAGER-4705'
read=lambda p:json.loads(p.read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
old=read(method/'rounds/0/public-observation.json');trial=read(method/'rounds/1/public-observation.json')
ids=preservation_check_ids(read(method/'rounds/1/public-review-plan.json'),read(method/'rounds/0/feedback.json'))
selection=compare_candidate_checks(old,trial,ids)
assert not ids and selection['decision']=='UNCOMPARABLE' and not selection['regressions']
# Current interpreter must equal the completed scorer identity. No grader call.
receipt=next((ROOT/'methods/RW-ALERTMANAGER-4705').glob('.isolated-*/receipt.json'))
program=SYSTEM/'tests/fixtures/isolated_score_v054.py'
score_root=ROOT/'independent/RW-ALERTMANAGER-4705'
before={p.name:sha(p) for p in [score_root/'result.json',score_root/'checkpoint.json']}
score=read_recorded_score(receipt,Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019'),score_root,recorded_program=dict(path=str(program),sha256=sha(program)))
assert score['raw']['status']=='SCORER_ERROR' and score['interpreted_status']=='EXECUTION_FAIL'
assert before=={p.name:sha(p) for p in [score_root/'result.json',score_root/'checkpoint.json']}
result=dict(status='SAVED_BOUNDARIES_REPLAY_PASS',selection=selection,original_selection='PROMOTE',protected_ids=ids,raw_score_status=score['raw']['status'],interpreted_independent_status=score['interpreted_status'],patch_sha256=score['raw']['identity']['patch_sha256'],score_files_unchanged=True,new_model_calls=0,new_grader_calls=0,formal_ready=False,scope='Counterfactual selection only: incumbent still publicly FAIL. Original v054 live result remains unchanged; not a new repair success.')
out=SYSTEM/'outputs/probe-completeness-20261026/SAVED_BOUNDARIES_REPLAY.json'
with out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
print(json.dumps(result))
