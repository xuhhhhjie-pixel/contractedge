"""Latest actual common-worker failure reconstructed to native retry, zero HTTP."""
import importlib.util,json,shutil,subprocess,urllib.request
from pathlib import Path
from contractedge.cli import _token_counter
from contractedge.contracts.validation import parse_method_input
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.orchestration.repair import rebind_execution_plan,select_observations
from contractedge.orchestration.feedback import feedback_prompt_view
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.execution.topomen import execution_review_mapping
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.source.snapshot import compute_snapshot

class Captured(BaseException):pass
root=Path.cwd();out=root/'outputs/reliability-closure-20261020';origin=out/'runs/delivery-0.6.22-recovery-R3-KPT-4673-0-candidate/cases/R3-KPT-4673';dest=out/'current-seed-native-retry-v0623-closed';dest.mkdir(exist_ok=False)
read=lambda p:json.loads(p.read_text())
cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');counter=_token_counter(cfg['tokenizer']);front=read(origin/'front.trace.json');initial=read(origin/'seed-feedback.json');failed=read(origin/'rounds/0/feedback.json');decision=read(origin/'rounds/0/selection.json')['candidate_decision'];execution=read(origin/'rounds/0/execution.json')
method_value,projection=method_input_from_task('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public','R3-KPT-4673');method=parse_method_input(method_value)
source=dest/'source';shutil.copytree(next((root/'outputs/production-path-behavior-20261020/public-source-kpt').iterdir()),source,symlinks=True,ignore=shutil.ignore_patterns('.git'))
assert compute_snapshot(source).snapshot_id==method.initial_snapshot_id
subprocess.run(['patch','--batch','--no-backup-if-mismatch','-p1','-i',str((origin/'public-seed.patch').resolve())],cwd=source,check=True,capture_output=True)
assert compute_snapshot(source).snapshot_id==initial['to_snapshot']
capabilities=front['run_context']['retrieval_config']['public_execution_capabilities'];cfg['public_execution_capabilities']=capabilities
support=front['run_context']['retrieval_config'].get('necessary_source_support')
original=translate_for_topomen(front['changeset'],'contractedge.topomen_plan.v1',diagnosis=front['diagnosis'],public_capabilities=capabilities,**({'source_support':support} if support else {}))
spec=importlib.util.spec_from_file_location('common_worker',root/'scripts/run_reliability_worker_v8.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
plan=worker.bind_seed_retry(rebind_execution_plan,original,source,initial,read(origin/'rounds/0/continuation-plan.json'),execution)
previous=dict(source_snapshot=initial['to_snapshot'],error='Trial was rejected; current source was restored.',rejected_trial=dict(snapshot_id=failed['to_snapshot'],public_check_results=failed['public_check_results'],candidate_decision=decision,feedback=feedback_prompt_view(failed),actual_edits=execution_review_mapping(execution,max_characters=12000),patch_digest=failed['patch_digest'],interpretation='Historical rejected trial; current source was restored. These edits and errors are not the current snapshot.'))
class Provider:
 request=None
 def complete_json(self,request):self.request=request;raise Captured()
provider=Provider();executor=TopoMenAgentExecutor(provider,counter,cfg)
def no_http(*a,**k):raise AssertionError('Network prohibited')
urllib.request.urlopen=no_http
try:executor.execute(plan,source,projection['editable_policy'],requirement=method.requirement_text,feedback=initial,declared_artifacts=method.declared_artifacts,previous_attempt=previous,observations=select_observations(read(origin/'seed-public/observation.json')['observations'],method.requirement_text,counter,cfg['max_public_evidence_tokens']))
except Captured:pass
else:raise AssertionError('Native retry not captured')
request=provider.request;body=json.loads(request.messages[1]['content']);history=body['previous_execution_attempt']['rejected_trial'];facts=history['feedback']['failure_tasks']['failed_predicates'];assert facts and all(f['snapshot_id']==failed['to_snapshot'] for f in facts);assert request.thinking=='enabled';assert request.metadata['execution_thinking_policy']=='FAILED_TRIAL_REASONING';assert compute_snapshot(source).snapshot_id==initial['to_snapshot'];assert len(body['declared_artifacts'])==len(method.declared_artifacts)
inputs=sum(counter(m['content']) for m in request.messages);assert inputs+request.max_output_tokens<=cfg['max_context_tokens'];assert any('func MergeWithInput(' in r['excerpt'] for r in body['read_only_source_support'])
summary=dict(api_calls=0,scope='Saved actual .22 common worker regression-to-native-retry request; not new model repair or independent PASS',input_bound=inputs,reserved_output=request.max_output_tokens,context_limit=cfg['max_context_tokens'],historical_failure_facts=len(facts),actual_declared_artifacts=len(body['declared_artifacts']),current_source_restored=True,trial_snapshot_preserved=True,thinking=request.thinking,helper_count=len(body['read_only_source_support']),shared_current_observation_refs=sum('public_observation_record_ref' in r for r in body['execution_feedback']['public_observation_refs']))
(dest/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(dest/'request.json').write_text(json.dumps(dict(messages=request.messages,thinking=request.thinking,metadata=request.metadata),ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary))
