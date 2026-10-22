import json,tempfile,shutil,subprocess
from pathlib import Path
from contractedge.orchestration.repair import rebind_execution_plan,select_observations
from contractedge.orchestration.feedback import feedback_prompt_view
from contractedge.execution.topomen import build_execution_request,execution_review_mapping
from contractedge.cli import _token_counter
from contractedge.dataset.benchmark import method_input_from_task
out=Path('outputs/reliability-closure-20261020');c=out/'runs/delivery-0.6.6-recovery-R3-KPT-4673-0-candidate/cases/R3-KPT-4673'
cfg=json.loads(Path('configs/deepseek-reliability-closure-v4-budget80.json').read_text());counter=_token_counter(cfg['tokenizer'])
seed=json.loads((c/'seed-feedback.json').read_text());bad=json.loads((c/'rounds/0/feedback.json').read_text());execution=json.loads((c/'rounds/0/execution.json').read_text());plan=json.loads((c/'rounds/0/continuation-plan.json').read_text());obs=json.loads((c/'seed-public/observation.json').read_text())
value,projection=method_input_from_task('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public','R3-KPT-4673');policy=projection['public_execution_capabilities']['editable_policy']
source=next(Path('outputs/production-path-behavior-20261020/public-source-kpt').iterdir());seedpatch=Path('outputs/deepseek-production-path-probe-v4-20261020/cases/R3-KPT-4673/rounds/0/candidate.patch').resolve()
with tempfile.TemporaryDirectory(prefix='ce-second-request-') as td:
 root=Path(td)/'source';shutil.copytree(source,root,symlinks=True,ignore=shutil.ignore_patterns('.git'));subprocess.run(['git','apply',str(seedpatch)],cwd=root,check=True,capture_output=True);current=rebind_execution_plan(plan,root,seed)
 previous=dict(source_snapshot=seed['to_snapshot'],validation_error='Protected behavior regressed; revise from restored current source.',rejected_trial=dict(snapshot_id=bad['to_snapshot'],public_check_results=bad['public_check_results'],candidate_decision=json.loads((c/'repair.trace.json').read_text())['rounds'][0]['candidate_decision'],interpretation='Historical rejected trial; current source was restored. These edits and errors are not the current snapshot.',feedback=feedback_prompt_view(bad),actual_edits=execution_review_mapping(execution,max_characters=12000),patch_digest=bad['patch_digest']))
 request=build_execution_request(current,root,policy,counter,cfg,requirement=value['requirement_text'],feedback=seed,previous_attempt=previous,observations=select_observations(obs['observations'],value['requirement_text'],counter,cfg['max_public_evidence_tokens']))
 payload=json.loads(request.messages[1]['content']);support=payload['read_only_source_support'];history=payload['previous_execution_attempt']['rejected_trial']['feedback']
 summary=dict(api_calls=0,input_upper_bound=sum(counter(m['content']) for m in request.messages),reserved_output=request.max_output_tokens,limit=cfg['max_context_tokens'],historical_helper_conflicts=len(history['failure_tasks']['helper_precondition_conflicts']),read_only_helper_count=len(support),merge_helper_delivered=any('func MergeWithInput(' in s.get('excerpt','') for s in support),full_historical_feedback_digest=history['full_feedback_digest'],history_characters=len(json.dumps(history)))
 (out/'kpt-restored-second-request-capture-v0610.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
 assert summary['input_upper_bound']+summary['reserved_output']<=summary['limit'] and summary['merge_helper_delivered']
