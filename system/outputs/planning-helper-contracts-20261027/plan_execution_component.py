"""Once-only planning/execution component with a historical validated public diagnosis."""
from dataclasses import asdict
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
SYSTEM=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
ROOT=Path('/home/xhj/contractedge-plan-execution-v060-20261027')
SAVED=Path('/home/xhj/contractedge-e2e-source058-20261027/methods/R3-KPT-4673/R3-KPT-4673')
BENCH=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
CONFIG=SYSTEM/'configs/fixed46-candidate-v060-budget150.json'
ENGINEERING=SYSTEM/'docs/development/planning-helper-contracts-v060-20261027-v5.snapshot.json'
FREEZE=SYSTEM/'docs/development/planning-helper-contracts-v060-20261027-v5.snapshot.json'
sys.path[:0]=[str(SYSTEM/'src'),str(SYSTEM/'scripts')]
from verify_development_snapshot import verify_snapshot
from contractedge.cli import _build_provider
from contractedge.model.budget import BudgetedProvider
from contractedge.model.admission import quote_request
from contractedge.model.provider import ModelRequest
from contractedge.contracts.validation import parse_method_input,validate_diagnosis
from contractedge.changeset.adapter import bind_changeset_envelope,adapt_model_changeset
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.probe_specs import select_probes
from contractedge.execution.observations import select_observation_adapters
from contractedge.orchestration.candidate_guard import checkpoint_candidate,restore_candidate,compare_candidate_checks,bound_public_check_table
from contractedge.source.snapshot import compute_snapshot
from run_isolated_method import write_json
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()

def exclusive(path,value):
    with Path(path).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())

def inputs():
    trace=read(SAVED/'rounds/0/front.trace.json');method=parse_method_input(read(SAVED/'method_input.json'))
    packet=validate_diagnosis(trace['diagnosis'],method,None)
    raw=read(OUT/'integrated-planning.request.json');raw['messages']=tuple(raw['messages'])
    # Isolate contract-aware planning from the separately known reasoning-starvation mechanism.
    request=ModelRequest(**{**raw,'thinking':'disabled','metadata':{**raw.get('metadata',{}),
        'component_generation_mode':'Explicit existing disabled fallback mode; component only, not fresh full-front generation policy'}})
    return trace,method,packet,request,json.loads(request.messages[1]['content'])

def public_runner(config,payload):
    return BenchmarkPublicRunner(BENCH/'public',BENCH/'tools',lock_root=ROOT/'locks',
        timeout_seconds=config['public_timeout_seconds'],query=payload['diagnosis_packet']['requirement_spec']['raw_text'],
        method_probe_profiles=config['method_probe_profiles'])

def prepare():
    assert not ROOT.exists()
    frozen=read(ENGINEERING);assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
    qualification=read(OUT/'qualification-v060-v5-state.json')
    assert qualification['status']=='ZERO_CALL_SOURCE_QUALIFICATION_WITH_TARGETED_FIXTURE_RESUMPTION'
    assert qualification['source_sha256']==frozen['source_sha256'] and qualification['single_full_suite_pass'] is False
    assert qualification['resumption_audit_sha256']==sha(OUT/'FIXTURE_RESUMPTION_AUDIT.json')
    config=read(CONFIG);trace,method,packet,request,payload=inputs();real,counter=_build_provider(config)
    money=real.money_budget.summary();quote=quote_request(real,request,counter)
    assert request.thinking=='disabled' and quote.total_tokens<=config['max_context_tokens']
    assert read(OUT/'INTEGRATED_PLANNING_REPLAY.json')['status']=='ACTUAL_SAVED_PLANNING_HELPER_REPLAY_PASS'
    ROOT.mkdir();public=public_runner(config,payload)
    record=public.prepare('R3-KPT-4673',ROOT/'workspace',ROOT/'prepare')
    source=Path(record['source_root']);assert compute_snapshot(source).snapshot_id==packet.snapshot_id
    before=public.observe(ROOT/'workspace',ROOT/'broken-public');write_json(ROOT/'before.json',before)
    assert before['status']=='EXECUTION_PASS' and before['method_probe_status']=='COMPLETE'
    exclusive(ROOT/'registration.json',dict(role='LOCKED_DIAGNOSIS_PLAN_EXECUTION_COMPONENT',
        source_sha256=frozen['source_sha256'],source_snapshot=packet.snapshot_id,driver_sha256=sha(__file__),
        config_sha256=sha(CONFIG),historical_diagnosis_trace_sha256=sha(SAVED/'rounds/0/front.trace.json'),
        planning_request_file_sha256=sha(OUT/'integrated-planning.request.json'),request_digest=quote.request_digest,
        before_sha256=sha(ROOT/'before.json'),money_before=money,max_real_model_calls=2,
        planning_calls=1,execution_calls=1,format_retries=0,once_only=True,
        generation_mode='disabled existing fallback, component only',formal_ready=False,counts_in_46_slot_panel=False,
        boundary='Trusted parent schema validation and native literal-only agent; generated source only runs in official public Docker; key removed before public/scorer',
        success='Valid newly generated plan, native applied patch, all finite public checks PASS, no regression, independentPASS'))
    write_json(ROOT/'expected-planning-request.json',asdict(request))
    assert real.money_budget.summary()==money
    print(json.dumps(dict(status='PREPARED_ZERO_API',root=str(ROOT))))

def execute():
    frozen=read(FREEZE);base=read(ENGINEERING)
    assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
    assert frozen['files']==base['files'] and frozen['configuration']==base['configuration'] and frozen['fixture_files']==base['fixture_files']
    assert all(frozen['tooling_files'][k]==v for k,v in base['tooling_files'].items())
    r=read(ROOT/'registration.json');config=read(CONFIG);trace,method,packet,request,payload=inputs()
    assert r['driver_sha256']==sha(__file__) and r['config_sha256']==sha(CONFIG)
    assert r['historical_diagnosis_trace_sha256']==sha(SAVED/'rounds/0/front.trace.json')
    assert r['planning_request_file_sha256']==sha(OUT/'integrated-planning.request.json') and r['before_sha256']==sha(ROOT/'before.json')
    assert r['source_sha256']==frozen['source_sha256']
    source=Path(read(ROOT/'workspace/workspace.json')['source_root']);assert compute_snapshot(source).snapshot_id==r['source_snapshot']
    exclusive(ROOT/'claimed.json',dict(registration_sha256=sha(ROOT/'registration.json'),freeze_sha256=sha(FREEZE)))
    real,counter=_build_provider(config)
    def update(calls):
        assert len(calls)<=2
        if calls:assert calls[0]['request_digest']==r['request_digest']
        write_json(ROOT/'model_calls.json',calls)
    budget=BudgetedProvider(real,counter,max_tokens=config['max_total_tokens'],max_seconds=config['max_elapsed_seconds'],max_calls=2,on_update=update)
    checkpoint=checkpoint_candidate(source,['pkg/lib/kptops/render_executor.go'],ROOT/'checkpoint.json')
    public=public_runner(config,payload);slot=str((ROOT/'workspace').resolve())
    public._method_probe_specs[slot]=select_probes(payload['diagnosis_packet']['requirement_spec']['raw_text'],source,config['method_probe_profiles'])
    public._observation_adapters[slot]=select_observation_adapters(BENCH/'public','R3-KPT-4673')
    exclusive(ROOT/'frozen-method-probes.json',public._method_probe_specs[slot])
    key=config['api_key_env'];assert key not in os.environ
    applied=False;plan_valid=False;error=None;validation=None
    try:
        os.environ[key]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
        response=budget.complete_json(request)
        draft=json.loads(response.content);draft,binding=bind_changeset_envelope(draft,packet,packet.run_id,packet.snapshot_id,packet.input_digest)
        draft=adapt_model_changeset(draft,packet,compute_snapshot(source));write_json(ROOT/'changeset.json',draft)
        validation=validate_semantic_changeset(draft,packet,compute_snapshot(source),
            policy=payload['public_execution_capabilities'].get('editable_policy'),
            frontier=payload['production_path_frontier'],prerequisites=payload['source_prerequisites'],
            capabilities=payload['public_execution_capabilities'])
        write_json(ROOT/'planning-validation.json',dict(valid=validation.valid,errors=[asdict(x) for x in validation.errors]))
        plan_valid=validation.valid and draft.get('status') in {'READY','READY_PARTIAL'}
        if plan_valid:
            plan=translate_for_topomen(draft,'contractedge.topomen_plan.v1',diagnosis=packet.raw,source_support=payload['read_only_source_support'])
            write_json(ROOT/'plan.json',plan)
            checkpoint=checkpoint_candidate(source,[op['target']['path'] if op['target']['kind']=='EXISTING' else op['target']['proposed_path'] for op in plan['operations']],ROOT/'execution-checkpoint.json')
            native=TopoMenAgentExecutor(budget,counter,config)
            execution=native.execute(plan,source,payload['public_execution_capabilities']['editable_policy'],
                requirement=packet.requirement_spec['raw_text'],declared_artifacts=method.declared_artifacts,
                observations=read(ROOT/'before.json')['observations'])
            write_json(ROOT/'execution.json',execution);applied=execution['status']=='APPLIED'
    except Exception as exc:
        error=type(exc).__name__+': '+str(exc);write_json(ROOT/'component-error.json',dict(error=error))
    finally:os.environ.pop(key,None)
    after=public.observe(ROOT/'workspace',ROOT/'candidate-public') if compute_snapshot(source).snapshot_id!=r['source_snapshot'] else read(ROOT/'before.json')
    write_json(ROOT/'after.json',after)
    before=read(ROOT/'before.json');preserved=sorted({k[0] for k,v in bound_public_check_table(before).items() if v=='PASS'})
    decision=compare_candidate_checks(before,after,preserved);write_json(ROOT/'candidate-decision.json',decision)
    patch=public.export(ROOT/'workspace',ROOT/'candidate.patch',ROOT/'export');write_json(ROOT/'patch.json',patch);sealed=sha(ROOT/'candidate.patch')
    if decision['decision']!='PROMOTE':restore_candidate(source,checkpoint)
    exclusive(ROOT/'score-claimed.json',dict(patch_sha256=sealed))
    command=[sys.executable,str(BENCH/'tools/score_case.py'),'--evaluation-root',str(BENCH/'evaluation'),
        '--case-id','R3-KPT-4673','--patch',str(ROOT/'candidate.patch'),'--output',str(ROOT/'independent')]
    with (ROOT/'private-grader.log').open('x') as log:code=subprocess.call(command,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=log,stderr=subprocess.STDOUT)
    assert sha(ROOT/'candidate.patch')==sealed
    independent=read(ROOT/'independent/score.json')['status']
    checks=[x['behavior_check']['status'] for x in after.get('observations',[]) if x.get('behavior_check')]
    result=dict(status='LOCKED_DIAGNOSIS_PLAN_EXECUTION_COMPONENT_CLOSED',plan_valid=plan_valid,native_applied=applied,
        independent_status=independent,grader_exit=code,candidate_decision=decision['decision'],
        public_checks=len(checks),public_pass=checks.count('PASS'),public_fail=checks.count('FAIL'),
        patch_bytes=patch['patch_bytes'],patch_sha256=sealed,actual_api_calls=len(budget.calls),money_after=real.money_budget.summary(),
        source_restored=decision['decision']!='PROMOTE',error=error,formal_ready=False,counts_in_46_slot_panel=False,
        success=plan_valid and applied and code==0 and independent=='PASS' and decision['decision']=='PROMOTE' and bool(checks) and all(v=='PASS' for v in checks),
        limitation='Historical validated diagnosis and explicit disabled fallback generation; no fresh diagnosis/end-to-end/original46/generalization result.')
    write_json(ROOT/'result.json',result);print(json.dumps(result))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','execute']);a=p.parse_args()
    prepare() if a.action=='prepare' else execute()
