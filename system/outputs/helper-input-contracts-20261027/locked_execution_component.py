"""One native-executor component attempt on a frozen public historical plan."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SYSTEM=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
ROOT=Path('/home/xhj/contractedge-locked-execution-v059-20261027')
BASE_FREEZE=SYSTEM/'docs/development/helper-input-contracts-v059-20261027-v3.snapshot.json'
FREEZE=SYSTEM/'docs/development/helper-input-contracts-v059-20261027-v4.snapshot.json'
CONFIG=SYSTEM/'configs/fixed46-candidate-v059-budget150.json'
SAVED=Path('/home/xhj/contractedge-e2e-source058-20261027/methods/R3-KPT-4673/R3-KPT-4673')
BENCH=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
sys.path[:0]=[str(SYSTEM/'src'),str(SYSTEM/'scripts')]
from verify_development_snapshot import verify_snapshot
from contractedge.cli import _build_provider
from contractedge.model.budget import BudgetedProvider
from contractedge.model.admission import quote_request
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.topomen import build_execution_request
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.orchestration.candidate_guard import checkpoint_candidate,restore_candidate,compare_candidate_checks,bound_public_check_table
from contractedge.source.snapshot import compute_snapshot
from run_isolated_method import write_json


def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def exclusive(path,value):
    with Path(path).open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())


def runner(config,payload):
    return BenchmarkPublicRunner(BENCH/'public',BENCH/'tools',lock_root=ROOT/'locks',
        timeout_seconds=config['public_timeout_seconds'],query=payload['requirement'],
        method_probe_profiles=config['method_probe_profiles'])


def component_inputs():
    calls=read(SAVED/'model_calls.json');payload=json.loads(calls[4]['messages'][1]['content'])
    return read(SAVED/'rounds/0/plan.json'),payload


def request_for(source,config,provider,counter):
    plan,payload=component_inputs()
    request=build_execution_request(plan,source,payload['acceptance']['scope_policy'],counter,config,
        requirement=payload['requirement'],declared_artifacts=payload['declared_artifacts'],
        observations=payload['public_observations'],feedback=None,previous_attempt=None,provider=provider)
    expected=read(OUT/'execution.request.json')
    assert json.loads(request.messages[1]['content'])==json.loads(expected['messages'][1]['content'])
    assert request.thinking=='disabled' and request.max_output_tokens==config['execution_max_output_tokens']
    return request,quote_request(provider,request,counter)


def prepare():
    assert not ROOT.exists()
    frozen=read(BASE_FREEZE);assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
    assert read(OUT/'ZERO_CALL_QUALIFICATION_v2.json')['status']=='ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION'
    config=read(CONFIG);provider,counter=_build_provider(config);money_before=provider.money_budget.summary()
    plan,payload=component_inputs();ROOT.mkdir()
    exclusive(ROOT/'experiment.json',dict(role='LOCKED_NATIVE_EXECUTION_COMPONENT',source_sha256=frozen['source_sha256'],
        code_sha256=sha(__file__),config_sha256=sha(CONFIG),plan_sha256=sha(SAVED/'rounds/0/plan.json'),
        public_input_sha256=sha(SAVED/'model_calls.json'),max_real_model_calls=1,once_only=True,
        public_source_snapshot=plan['snapshot_id'],formal_ready=False,counts_in_46_slot_panel=False,
        prefix_stages='Historical validated public plan; no fresh diagnosis/planning claimed',
        execution_boundary='Trusted native Agent literal JSON transaction; generated source only runs in frozen public Docker runtime; credential removed before public/scorer execution',
        success='All finite public checks PASS, no known regression, fixed trial independentPASS; component only'))
    public=runner(config,payload)
    record=public.prepare('R3-KPT-4673',ROOT/'workspace',ROOT/'prepare')
    source=Path(record['source_root']);assert compute_snapshot(source).snapshot_id==plan['snapshot_id']
    before=public.observe(ROOT/'workspace',ROOT/'broken-public');write_json(ROOT/'before.json',before)
    assert before['status']=='EXECUTION_PASS' and before.get('method_probe_status')=='COMPLETE'
    request,quote=request_for(source,config,provider,counter)
    assert quote.total_tokens<=config['max_context_tokens']
    assert provider.money_budget.summary()==money_before
    exclusive(ROOT/'registration.json',dict(request_digest=quote.request_digest,
        source_snapshot=plan['snapshot_id'],before_sha256=sha(ROOT/'before.json'),
        config_sha256=sha(CONFIG),driver_sha256=sha(__file__),experiment_sha256=sha(ROOT/'experiment.json'),
        source_sha256=frozen['source_sha256'],money_before=money_before,max_real_model_calls=1))
    write_json(ROOT/'state.json',dict(status='PREPARED_ZERO_API',formal_ready=False))
    print(json.dumps(dict(status='PREPARED_ZERO_API',root=str(ROOT))))


def execute():
    frozen,base=read(FREEZE),read(BASE_FREEZE)
    assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
    assert verify_snapshot(SYSTEM,base,require_current_bindings=True)['status']=='PASS'
    assert frozen['source_sha256']==base['source_sha256'] and frozen['configuration']==base['configuration'] and frozen['fixture_files']==base['fixture_files']
    assert all(frozen['tooling_files'].get(k)==v for k,v in base['tooling_files'].items())
    registration=read(ROOT/'registration.json');config=read(CONFIG);plan,payload=component_inputs()
    assert registration['config_sha256']==sha(CONFIG) and registration['driver_sha256']==sha(__file__)
    assert registration['experiment_sha256']==sha(ROOT/'experiment.json') and registration['before_sha256']==sha(ROOT/'before.json')
    assert registration['source_sha256']==frozen['source_sha256']
    source=Path(read(ROOT/'workspace/workspace.json')['source_root'])
    assert compute_snapshot(source).snapshot_id==registration['source_snapshot']
    exclusive(ROOT/'claimed.json',dict(registration_sha256=sha(ROOT/'registration.json'),freeze_sha256=sha(FREEZE)))
    real,counter=_build_provider(config)
    def update(calls):
        assert len(calls)<=1
        if calls:assert calls[0]['request_digest']==registration['request_digest']
        write_json(ROOT/'model_calls.json',calls)
    budget=BudgetedProvider(real,counter,max_tokens=config['max_total_tokens'],max_seconds=config['max_elapsed_seconds'],max_calls=1,on_update=update)
    checkpoint=checkpoint_candidate(source,[op['target']['path'] for op in plan['operations']],ROOT/'checkpoint.json')
    public=runner(config,payload)
    from contractedge.execution.probe_specs import select_probes
    from contractedge.execution.observations import select_observation_adapters
    public._method_probe_specs[str((ROOT/'workspace').resolve())]=select_probes(payload['requirement'],source,config['method_probe_profiles'])
    public._observation_adapters[str((ROOT/'workspace').resolve())]=select_observation_adapters(BENCH/'public','R3-KPT-4673')
    exclusive(ROOT/'frozen-method-probes.json',public._method_probe_specs[str((ROOT/'workspace').resolve())])
    key_name=config['api_key_env'];assert key_name not in os.environ
    try:
        os.environ[key_name]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
        native=TopoMenAgentExecutor(budget,counter,config)
        result=native.execute(plan,source,payload['acceptance']['scope_policy'],requirement=payload['requirement'],
            declared_artifacts=payload['declared_artifacts'],observations=payload['public_observations'])
        write_json(ROOT/'execution.json',result)
    finally:
        os.environ.pop(key_name,None)
    assert len(budget.calls)==1 and native.last_trace['backend']['agent_class'].endswith('.DefaultAgent')
    after=public.observe(ROOT/'workspace',ROOT/'candidate-public');write_json(ROOT/'after.json',after)
    before=read(ROOT/'before.json');preserved=sorted({key[0] for key,state in bound_public_check_table(before).items() if state=='PASS'})
    decision=compare_candidate_checks(before,after,preserved);write_json(ROOT/'candidate-decision.json',decision)
    patch=public.export(ROOT/'workspace',ROOT/'candidate.patch',ROOT/'export');write_json(ROOT/'patch.json',patch)
    sealed=sha(ROOT/'candidate.patch')
    if decision['decision']!='PROMOTE':restore_candidate(source,checkpoint)
    exclusive(ROOT/'score-claimed.json',dict(patch_sha256=sealed))
    command=[sys.executable,str(BENCH/'tools/score_case.py'),'--evaluation-root',str(BENCH/'evaluation'),
        '--case-id','R3-KPT-4673','--patch',str(ROOT/'candidate.patch'),'--output',str(ROOT/'independent')]
    with (ROOT/'private-grader.log').open('x') as log:
        code=subprocess.call(command,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=log,stderr=subprocess.STDOUT)
    assert sha(ROOT/'candidate.patch')==sealed
    native_score=read(ROOT/'independent/score.json');status=native_score.get('status')
    checks=[r['behavior_check']['status'] for r in after.get('observations',[]) if r.get('behavior_check')]
    result=dict(status='LOCKED_EXECUTION_COMPONENT_CLOSED',independent_status=status,grader_exit=code,
        candidate_decision=decision['decision'],public_checks=len(checks),public_pass=checks.count('PASS'),
        public_fail=checks.count('FAIL'),patch_bytes=patch['patch_bytes'],patch_sha256=sealed,
        actual_api_calls=len(budget.calls),money_after=real.money_budget.summary(),
        source_restored=decision['decision']!='PROMOTE',formal_ready=False,counts_in_46_slot_panel=False,
        success=code==0 and status=='PASS' and decision['decision']=='PROMOTE' and bool(checks) and all(x=='PASS' for x in checks),
        limitation='One native execution on a historical validated plan; no end-to-end diagnosis/planning or formal-panel success claimed.')
    write_json(ROOT/'result.json',result);write_json(ROOT/'state.json',result)
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','execute']);args=parser.parse_args()
    prepare() if args.action=='prepare' else execute()
