"""One paid, explicitly labelled execution-only counterfactual; no hidden input."""
import hashlib
import json
import os
import time
from pathlib import Path

from contractedge.cli import _build_provider
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.topomen import apply_edits, validate_execution_response
from contractedge.model.provider import ModelRequest
from contractedge.source.snapshot import compute_snapshot


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
    origin=out/'runs/delivery-0.6.11-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    dest=out/'execution-reasoning-single-factor-v0611'
    dest.mkdir(exist_ok=False)
    def save(name,value):
        (dest/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    calls=json.loads((origin/'model_calls.json').read_text())
    old=next(c for c in calls if c['prompt_version']=='topomen-source-execution.v1')
    request=ModelRequest(model=old['model'],prompt_version=old['prompt_version'],
        messages=tuple(old['messages']),max_output_tokens=old['output_tokens_reserved'],
        output_schema=old['output_schema'],thinking='enabled',temperature=0.0,
        metadata={**old.get('request_metadata',{}),'execution_thinking_policy':'SAVED_REQUEST_SINGLE_FACTOR_PROBE'})
    cfg=json.loads((root/'configs/deepseek-reliability-closure-v4-budget150.json').read_text())
    os.environ[cfg['api_key_env']]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider,counter=_build_provider(cfg)
    save('provenance.json',dict(scope='EXECUTION_ONLY_COUNTERFACTUAL_NOT_END_TO_END_NOT_PANEL_SCORE',
        source=str(origin/'model_calls.json'),change='execution thinking disabled -> enabled; same messages, model, schema, output cap and temperature',
        messages_sha256=hashlib.sha256(json.dumps(old['messages'],sort_keys=True).encode()).hexdigest(),
        max_api_calls=1,automatic_retry=False,hidden_feedback_used=False))
    save('state.json',dict(status='REQUEST_PENDING'))
    started=time.monotonic()
    try:
        response=provider.complete_json(request)
    except Exception as exc:
        save('state.json',dict(status='MODEL_ERROR_NO_RETRY',error=str(exc)));raise
    save('response.json',dict(content=response.content,metadata=response.metadata,elapsed_seconds=time.monotonic()-started))
    try:
        edits=validate_execution_response(response,request)
    except Exception as exc:
        save('state.json',dict(status='EDIT_RESPONSE_REJECTED',error=str(exc)));raise
    plan=json.loads((origin/'rounds/0/plan.json').read_text())
    policy=json.loads((origin/'projection.json').read_text())['editable_policy']
    benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    method=json.loads((origin/'method_input.json').read_text())
    runner=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',
        lock_root=cfg.get('lock_root','/tmp/contractedge-system-native-locks'),
        timeout_seconds=cfg['public_timeout_seconds'],query=method['requirement_text'],
        method_probe_profiles=cfg.get('method_probe_profiles',[]))
    prepared=runner.prepare(method['case_id'],dest/'workspace',dest/'prepare')
    source=Path(prepared['source_root'])
    assert compute_snapshot(source).snapshot_id==plan['snapshot_id']
    result=apply_edits(plan,source,policy,edits,require_behavior_change=True)
    save('execution.json',result)
    patch=runner.export(dest/'workspace',dest/'candidate.patch',dest/'export')
    save('patch.json',patch);save('state.json',dict(status='PUBLIC_CHECK'))
    observation=runner.observe(dest/'workspace',dest/'public')
    summary=dict(api_calls=1,public_status=observation['status'],independent_score='NOT_RUN',
        checks=[dict(id=r['behavior_check']['id'],status=r['behavior_check']['status'])
                for r in observation.get('observations',[]) if r.get('behavior_check')],
        scope='Execution-only mechanism probe; no end-to-end success claim')
    save('summary.json',summary);save('state.json',dict(status='COMPLETE'))
    print(json.dumps(summary))


if __name__=='__main__':main()
