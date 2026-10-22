"""Replay first real rejected edit through native execution, rollback and next request; zero HTTP."""
import json
from pathlib import Path
import urllib.request

from contractedge.cli import _token_counter
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot

class Captured(BaseException):pass
root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
origin=out/'runs/delivery-0.6.21-A-R3-KPT-4673-0-candidate/cases/R3-KPT-4673'
dest=out/'kpt-behavior-revision-replay-v0623';dest.mkdir(exist_ok=False)
read=lambda p:json.loads(p.read_text())
calls=read(origin/'model_calls.json');old=read(origin/'rounds/0/front.trace.json');cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');cfg['run_id']=old['run_id']
counter=_token_counter(cfg['tokenizer']);bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
class Public(BenchmarkPublicRunner):
    index=0
    def observe(self,workspace,output):
        assert self.index<2
        report=read(origin/('broken-public/observation.json' if self.index==0 else 'rounds/0/public/observation.json'))
        source=Path(read(Path(workspace)/'workspace.json')['source_root'])
        assert report['snapshot_id']==compute_snapshot(source).snapshot_id
        self.index+=1
        return report
public=Public(bench/'public',bench/'tools',lock_root='/tmp/contractedge-system-native-locks',timeout_seconds=2400,query=read(origin/'method_input.json')['requirement_text'],method_probe_profiles=cfg['method_probe_profiles'])
class Provider:
    index=0
    request=None
    def complete_json(self,request):
        if self.index==4:
            assert request.prompt_version=='topomen-source-execution.v1'
            self.request=request;raise Captured()
        saved=calls[self.index];self.index+=1
        assert saved['prompt_version']==request.prompt_version
        return ModelResponse(saved['response_content'],saved['response_metadata'])
def no_network(*args,**kwargs):raise AssertionError('Network forbidden in saved-response replay')
urllib.request.urlopen=no_network
provider=Provider()
try:
    result=repair_case(bench/'public',bench/'tools','R3-KPT-4673',dest/'case',provider,counter,cfg,public_runner=public)
except Captured:pass
else:raise AssertionError(('Did not reach second native request',result['status'],result['errors']))
request=provider.request;body=json.loads(request.messages[1]['content']);history=body['previous_execution_attempt']['rejected_trial'];tasks=history['feedback']['failure_tasks'];facts=tasks['failed_predicates'];assert len(facts)==4
assert request.thinking=='enabled' and request.metadata['execution_thinking_policy']=='FAILED_TRIAL_REASONING'
assert {f['check_id'] for f in facts}=={'kpt-configpath-v4:mutator.'+name for name in ['later_sees_config','config_transformed','resource_inventory','config_retained']}
assert next(f for f in facts if f['check_id'].endswith('later_sees_config'))['observed']['ordinary_inputs']==['demo','workload']
prepared=read(dest/'case/workspace/workspace.json');source=Path(prepared['source_root']);current=compute_snapshot(source).snapshot_id
assert current==read(origin/'method_input.json')['initial_snapshot_id']
assert all(f['snapshot_id']==history['snapshot_id'] and f['snapshot_id']!=current for f in facts)
assert body['execution_feedback'] is None
# Replay the common saved-failure worker's actual alternate decision field
# through the same native DefaultAgent without executing a second edit.
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.execution.topomen import build_execution_request
from contractedge.dataset.benchmark import editable_policy,execution_contract,public_task
from copy import deepcopy
previous=deepcopy(body['previous_execution_attempt']);trial=previous['rejected_trial'];trial['candidate_decision']=trial.pop('decision')
class SeedCapture:
    request=None
    def complete_json(self,request):self.request=request;raise Captured()
seed_provider=SeedCapture()
policy=read(origin/'projection.json')['editable_policy']
seed_executor=TopoMenAgentExecutor(seed_provider,counter,cfg)
try:
    seed_executor.execute(read(dest/'case/rounds/1/plan.json'),source,policy,requirement=body['requirement'],
        declared_artifacts=body['declared_artifacts'],observations=body['public_observations'],
        feedback=None,previous_attempt=previous)
except Captured:pass
else:raise AssertionError('Common-worker native request not captured')
assert seed_provider.request.thinking=='enabled'
assert seed_provider.request.metadata['execution_thinking_policy']=='FAILED_TRIAL_REASONING'
assert compute_snapshot(source).snapshot_id==current
assert all(t['content']==(source/t['path']).read_text() for t in body['current_target_files'])
reserved=sum(counter(m['content']) for m in request.messages);assert reserved+request.max_output_tokens<=cfg['max_context_tokens']
patch=public.export(dest/'case/workspace',dest/'unchanged.patch',dest/'export');assert patch['patch_bytes']==0
summary=dict(api_calls=0,saved_model_responses=provider.index,cached_public_observation_snapshots_verified=2,first_native_edit_replayed=True,common_saved_worker_native_retry_captured=True,actual_regression_rejected_and_source_restored=True,current_snapshot=current,trial_snapshot=history['snapshot_id'],failed_predicates=facts,reasoning_policy=request.metadata['execution_thinking_policy'],input_bound=reserved,reserved_output=request.max_output_tokens,context_limit=cfg['max_context_tokens'],patch_bytes=0,scope='Complete saved first-trial/rollback/second native request replay; not a new successful repair or independent score')
(dest/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');(dest/'second-request.json').write_text(json.dumps(dict(messages=request.messages,metadata=request.metadata,thinking=request.thinking),ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k!='failed_predicates'},ensure_ascii=False))
