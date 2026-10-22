"""Replay two saved model responses with corrected continuation accounting; zero API."""
import json
from pathlib import Path

from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.topomen import apply_edits
from contractedge.orchestration.repair import rebind_execution_plan


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
    first=out/'execution-reasoning-single-factor-v0611'
    second=out/'execution-reasoning-continuation-v0611'
    original=out/'runs/delivery-0.6.11-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    dest=out/'continuation-operation-coverage-replay';dest.mkdir(exist_ok=False)
    read=lambda p:json.loads(p.read_text())
    def save(name,value):
        (dest/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    cfg=read(root/'configs/deepseek-reliability-closure-v4-budget150.json')
    method=read(original/'method_input.json');plan=read(original/'rounds/0/plan.json')
    policy=read(original/'projection.json')['editable_policy']
    benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    runner=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',
        lock_root=cfg.get('lock_root','/tmp/contractedge-system-native-locks'),timeout_seconds=cfg['public_timeout_seconds'],
        query=method['requirement_text'],method_probe_profiles=cfg.get('method_probe_profiles',[]))
    prepared=runner.prepare(method['case_id'],dest/'workspace',dest/'prepare');source=Path(prepared['source_root'])
    one=apply_edits(plan,source,policy,json.loads(read(first/'response.json')['content']))
    rebound=rebind_execution_plan(plan,source,read(second/'feedback.json'),prior_execution=one)
    calls=read(second/'model_calls.json');assert len(calls)==1
    two=apply_edits(rebound,source,policy,json.loads(calls[0]['response_content']))
    assert two['change_ids']==['C2'] and two['unchanged_change_ids']==['C1']
    save('provenance.json',dict(scope='OFFLINE_SAVED_EXECUTION_REPLAY_NOT_END_TO_END_NOT_PANEL',api_calls=0,
        first_response=str(first/'response.json'),second_response=str(second/'model_calls.json'),
        source_consumer='candidate continuation accounting; fresh initial batch remains strict',
        old_consumer_result='Rejected missing C1 atomically',new_consumer_result='C2 edited; C1 accounted unchanged, semantics unverified'))
    save('first-execution.json',one);save('second-execution.json',two)
    save('patch.json',runner.export(dest/'workspace',dest/'candidate.patch',dest/'export'))
    save('state.json',dict(status='PUBLIC_CHECK'))
    observed=runner.observe(dest/'workspace',dest/'public')
    summary=dict(api_calls=0,public_status=observed['status'],independent_score='NOT_RUN',
        edited_change_ids=two['change_ids'],unchanged_change_ids=two['unchanged_change_ids'],
        checks=[dict(id=r['behavior_check']['id'],status=r['behavior_check']['status']) for r in observed['observations'] if r.get('behavior_check')])
    save('summary.json',summary);save('state.json',dict(status='COMPLETE'));print(json.dumps(summary))


if __name__=='__main__':main()
