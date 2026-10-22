"""Second execution-only attempt using public compiler feedback; separate record."""
import json
import os
from pathlib import Path

from contractedge.cli import _build_provider
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.model.budget import BudgetedProvider
from contractedge.orchestration.feedback import make_feedback
from contractedge.orchestration.repair import rebind_execution_plan,select_observations
from contractedge.source.snapshot import compute_snapshot


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
    first=out/'execution-reasoning-single-factor-v0611'
    origin=out/'runs/delivery-0.6.11-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    dest=out/'execution-reasoning-continuation-v0611';dest.mkdir(exist_ok=False)
    def save(name,value):
        (dest/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    read=lambda p:json.loads(p.read_text())
    method=read(origin/'method_input.json');plan=read(origin/'rounds/0/plan.json')
    observation=read(first/'public/observation.json');assert observation['status']=='EXECUTION_FAILED'
    workspace=first/'workspace';source=Path(read(workspace/'workspace.json')['source_root'])
    patch=read(first/'patch.json')
    feedback=make_feedback(case_id=method['case_id'],run_id=plan['run_id'],input_digest=method['input_digest'],
        round_id=0,from_snapshot=plan['snapshot_id'],to_snapshot=compute_snapshot(source).snapshot_id,
        patch_digest=patch['patch_sha256'],execution_status=observation['status'],
        observations=observation['observations'],plan=read(origin/'rounds/0/front.trace.json')['changeset'])
    save('feedback.json',feedback)
    save('provenance.json',dict(scope='EXECUTION_ONLY_CONTINUATION_NOT_END_TO_END_NOT_PANEL_SCORE',
        first_attempt=str(first),max_additional_api_calls=1,combined_execution_attempts=2,
        same_accepted_plan=True,public_feedback_only=True,first_attempt_retained=True))
    cfg=read(root/'configs/deepseek-reliability-closure-v4-budget150.json');cfg['execution_thinking']='enabled'
    os.environ[cfg['api_key_env']]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider,counter=_build_provider(cfg)
    budget=BudgetedProvider(provider,counter,max_tokens=cfg['max_total_tokens'],max_seconds=cfg['max_elapsed_seconds'],
        max_calls=1,on_update=lambda calls:save('model_calls.json',calls))
    rebound=rebind_execution_plan(plan,source,feedback)
    executor=TopoMenAgentExecutor(budget,counter,cfg,on_trace=lambda trace:save('topomen-trace.json',trace))
    save('state.json',dict(status='EXECUTING'))
    result=executor.execute(rebound,source,read(origin/'projection.json')['editable_policy'],
        requirement=method['requirement_text'],feedback=feedback,
        observations=select_observations(observation['observations'],method['requirement_text'],counter,cfg['max_public_evidence_tokens']))
    save('execution.json',result)
    benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    runner=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',
        lock_root=cfg.get('lock_root','/tmp/contractedge-system-native-locks'),timeout_seconds=cfg['public_timeout_seconds'],
        query=method['requirement_text'],method_probe_profiles=cfg.get('method_probe_profiles',[]))
    runner._method_probe_specs[method['case_id']]=read(origin/'method-probe-specifications.json')
    runner.export(workspace,dest/'candidate.patch',dest/'export')
    save('state.json',dict(status='PUBLIC_CHECK'))
    observed=runner.observe(workspace,dest/'public')
    summary=dict(additional_api_calls=1,combined_execution_attempts=2,public_status=observed['status'],
        checks=[dict(id=r['behavior_check']['id'],status=r['behavior_check']['status']) for r in observed['observations'] if r.get('behavior_check')],
        independent_score='NOT_RUN',scope='Execution-only recovery mechanism; not end-to-end acceptance')
    save('summary.json',summary);save('state.json',dict(status='COMPLETE'));print(json.dumps(summary))


if __name__=='__main__':main()
