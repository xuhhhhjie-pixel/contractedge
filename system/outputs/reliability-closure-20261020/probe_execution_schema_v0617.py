"""One saved-plan execution probe, not a fresh end-to-end acceptance attempt."""
import hashlib
import json
import os
from pathlib import Path
from contractedge.cli import _build_provider
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.model.budget import BudgetedProvider
from contractedge.orchestration.repair import select_observations
from contractedge.source.snapshot import compute_snapshot


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
    read=lambda p:json.loads(p.read_text())
    origin=out/'runs/delivery-0.6.16-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    dest=out/'execution-schema-single-factor-v0617';dest.mkdir(exist_ok=False)
    def save(name,value):
        (dest/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    freeze=read(out/'frozen-method-v0.6.17.json')
    cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json')
    method=read(origin/'method_input.json');plan=read(origin/'rounds/0/plan.json')
    previous=read(origin/'rounds/0/execution-attempts/0.json')['execution_attempt']
    benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    runner=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',lock_root=cfg['lock_root'],
        timeout_seconds=cfg['public_timeout_seconds'],query=method['requirement_text'],
        method_probe_profiles=cfg['method_probe_profiles'])
    prepared=runner.prepare(method['case_id'],dest/'workspace',dest/'prepare')
    source=Path(prepared['source_root'])
    assert compute_snapshot(source).snapshot_id==plan['snapshot_id']
    save('provenance.json',dict(scope='SAVED_PLAN_EXECUTION_COMPONENT_NOT_FRESH_END_TO_END_OR_PANEL_SCORE',
        origin=str(origin),frozen_method=freeze['method_digest'],max_api_calls=1,automatic_retry=False,
        changed='Execution contract and in-prompt schema; same saved plan and unchanged source; existing empty-reasoning fallback',
        prior_rejection=previous['validation_error'],independent_score='NOT_RUN',hidden_materials_used=False))
    os.environ[cfg['api_key_env']]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider,counter=_build_provider(cfg)
    budget=BudgetedProvider(provider,counter,max_tokens=cfg['max_total_tokens'],
        max_seconds=cfg['max_elapsed_seconds'],max_calls=1,on_update=lambda calls:save('model_calls.json',calls))
    executor=TopoMenAgentExecutor(budget,counter,cfg,on_trace=lambda trace:save('topomen-trace.json',trace))
    obs=read(origin/'broken-public/observation.json')
    save('state.json',dict(status='EXECUTING'))
    try:
        result=executor.execute(plan,source,read(origin/'projection.json')['editable_policy'],
            requirement=method['requirement_text'],previous_attempt=previous,
            observations=select_observations(obs['observations'],method['requirement_text'],counter,cfg['max_public_evidence_tokens']))
        save('execution.json',result)
    except Exception as exc:
        patch=runner.export(dest/'workspace',dest/'candidate.patch',dest/'export')
        save('state.json',dict(status='EXECUTION_REJECTED_NO_RETRY',error=type(exc).__name__+': '+str(exc),patch=patch))
        raise
    save('patch.json',runner.export(dest/'workspace',dest/'candidate.patch',dest/'export'))
    save('state.json',dict(status='PUBLIC_CHECK'))
    observation=runner.observe(dest/'workspace',dest/'public')
    summary=dict(scope='Execution-only component; no end-to-end or independent success claim',
        api_calls=len(budget.calls),public_status=observation['status'],
        method_probe_status=observation.get('method_probe_status'),
        checks=[dict(id=r['behavior_check']['id'],status=r['behavior_check']['status'])
            for r in observation.get('observations',[]) if r.get('behavior_check')],independent_score='NOT_RUN')
    save('summary.json',summary);save('state.json',dict(status='COMPLETE'));print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
