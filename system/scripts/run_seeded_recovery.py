"""Explicit seeded development continuation, never an original first-pass result.

Usage: NEW_OUTPUT ACCEPTED_CURRENT_BATCH CONFIG
Seed is a preserved PUBLIC model-generated KPT failure from 0.4.11. A current
S0 accepted intent is retained; current public observations of the seeded S1
must actually route a single concrete S1->S2 revision. No hidden feedback is read.
"""
import json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'src'),str(ROOT/'.topomen-deps'),str(ROOT/'scripts')]
from contractedge.cli import _build_provider
from contractedge.contracts.validation import parse_method_input
from contractedge.dataset.benchmark import method_input_from_task,file_sha
from contractedge.orchestration.batch import run_batch,_method_digest
from contractedge.orchestration.repair import rebind_execution_plan,write_json,select_observations
from contractedge.orchestration.feedback import make_feedback,review_plan_with_retained
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.review import ModelPublicReviewer
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.execution.probe_specs import select_probes
from contractedge.model.budget import BudgetedProvider
from contractedge.source.snapshot import compute_snapshot
from run_single_acceptance import implementation

BENCH=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
CASE='R3-KPT-4673'
SEED=ROOT/'outputs/deepseek-production-path-probe-v4-20261020/cases/R3-KPT-4673/rounds/0/candidate.patch'


def recover(directory, accepted, provider, tokenizer, config):
    directory.mkdir(parents=True,exist_ok=False)
    value,projection=method_input_from_task(BENCH/'public',CASE);method=parse_method_input(value)
    config={**config,'public_execution_capabilities':projection['public_execution_capabilities']}
    runner=BenchmarkPublicRunner(BENCH/'public',BENCH/'tools',lock_root=config['lock_root'],
        timeout_seconds=config['public_timeout_seconds'],query=method.requirement_text,method_probe_profiles=config['method_probe_profiles'])
    workspace=directory/'workspace';record=runner.prepare(CASE,workspace,directory/'prepare');source=Path(record['source_root'])
    s0=compute_snapshot(source).snapshot_id
    trace=json.load(open(accepted/'cases'/CASE/'rounds/0/front.trace.json'))
    native=json.load(open(accepted/'cases'/CASE/'rounds/0/plan.json'))
    if trace['status'] not in {'READY','READY_PARTIAL'} or trace['snapshot_id']!=s0:raise ValueError('Recovery requires current accepted S0 intent')
    specs=select_probes(method.requirement_text,source,config['method_probe_profiles'])
    config['public_execution_capabilities']={**config['public_execution_capabilities'],'method_behavior_probes':[{k:v for k,v in s.items() if k!='program'} for s in specs]}
    shutil.copy2(SEED,directory/'historical-public-seed.patch')
    subprocess.run(['git','apply',str(SEED)],cwd=source,check=True,capture_output=True)
    seed=runner.export(workspace,directory/'seed-candidate.patch',directory/'seed-export');s1=compute_snapshot(source).snapshot_id
    write_json(directory/'provenance.json',{'scope':'SEEDED_CONTINUATION_NOT_FIRST_PASS','seed_public_patch':str(SEED),
        'seed_sha256':file_sha(SEED),'accepted_current_batch':str(accepted),'accepted_plan_sha256':file_sha(accepted/'cases'/CASE/'rounds/0/plan.json'),
        'initial_snapshot':s0,'seed_snapshot':s1,'hidden_feedback_used':False})
    budget=BudgetedProvider(provider,tokenizer,max_tokens=config['max_total_tokens'],max_seconds=config['max_elapsed_seconds'],
        max_calls=config['max_model_calls'],on_update=lambda calls:write_json(directory/'model_calls.json',calls))
    reviewer=ModelPublicReviewer(budget,tokenizer,config);plan=trace['changeset'];review_plan=review_plan_with_retained(plan)
    first=runner.observe(workspace,directory/'seed-public')
    def review(observation,current_plan):
        selected=select_observations(observation['observations'],method.requirement_text,tokenizer,config['max_review_evidence_tokens'])
        return reviewer.review(method.requirement_text,current_plan,{**observation,'observations':list(selected),
            'declared_artifacts':list(method.declared_artifacts),'public_execution_capabilities':config['public_execution_capabilities']})
    first_review=review(first,review_plan)
    feedback=make_feedback(case_id=CASE,run_id=native['run_id'],input_digest=method.input_digest,round_id=0,from_snapshot=s0,to_snapshot=s1,
        patch_digest=seed['patch_sha256'],execution_status=first['status'],observations=first['observations'],plan=review_plan,review=first_review,diagnosis=trace['diagnosis'])
    write_json(directory/'seed-review.json',first_review);write_json(directory/'seed-feedback.json',feedback)
    result={'case_id':CASE,'run_id':native['run_id'],'scope':'SEEDED_CONTINUATION_NOT_FIRST_PASS','status':'STOPPED_UNCERTAIN','candidate':seed,
        'initial_snapshot_id':s0,'seed_snapshot_id':s1,'errors':[],'last_feedback':feedback}
    try:
        if feedback['route']=='EXECUTE':
            rebound=rebind_execution_plan(native,source,feedback);write_json(directory/'continuation-plan.json',rebound)
            execution=TopoMenAgentExecutor(budget,tokenizer,config,on_trace=lambda x:write_json(directory/'topomen-native-trace.json',x)).execute(
                rebound,source,projection['public_execution_capabilities']['editable_policy'],requirement=method.requirement_text,feedback=feedback,declared_artifacts=method.declared_artifacts,
                observations=select_observations(first['observations'],method.requirement_text,tokenizer,config['max_public_evidence_tokens']))
            write_json(directory/'execution.json',execution)
            final=runner.observe(workspace,directory/'final-public');final_patch=runner.export(workspace,directory/'candidate.patch',directory/'final-export')
            final_review_plan=review_plan_with_retained(plan,feedback);last_review=review(final,final_review_plan)
            last=make_feedback(case_id=CASE,run_id=native['run_id'],input_digest=method.input_digest,round_id=1,from_snapshot=s1,
                to_snapshot=compute_snapshot(source).snapshot_id,patch_digest=final_patch['patch_sha256'],execution_status=final['status'],
                observations=final['observations'],plan=final_review_plan,review=last_review,diagnosis=trace['diagnosis'])
            write_json(directory/'final-review.json',last_review);write_json(directory/'final-feedback.json',last)
            result.update(status='PUBLIC_REVIEW_COMPLETE' if last['semantic_outcome']=='PUBLIC_REVIEW_PASS' else 'PUBLIC_EXECUTION_COMPLETE',candidate=final_patch,last_feedback=last)
    except Exception as exc:
        result.update(status='RECOVERY_FAILED',errors=[type(exc).__name__+': '+str(exc)])
        result['candidate']=runner.export(workspace,directory/'candidate.patch',directory/'failed-export')
    result.update(final_snapshot_id=compute_snapshot(source).snapshot_id,budget=budget.summary())
    write_json(directory/'repair.trace.json',result);shutil.rmtree(workspace)
    return result


def main():
    output,accepted,config_path=[Path(p).resolve() for p in sys.argv[1:4]]
    config=json.load(open(config_path))
    if output.exists() or not config['development_only']:raise ValueError('Requires fresh output and development profile')
    state=json.load(open(accepted/'batch.json'))
    if any(r['status']=='RUNNING' for r in state['records']):raise ValueError('Accepted source batch still active')
    frozen=json.load(open(accepted/'implementation-manifest.json'))
    if not frozen['unchanged'] or implementation()!=frozen['after']:raise ValueError('Current method differs from accepted batch method')
    os.environ[config['api_key_env']]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider,tokenizer=_build_provider(config);before=implementation();money_before=provider.money_budget.summary()
    output.mkdir();write_json(output/'experiment-config.json',config)
    try:
        result=run_batch(BENCH/'public',output,config,lambda case,directory:recover(directory,accepted,provider,tokenizer,config),
            case_ids=[CASE],max_new_cases=1,tools_root=BENCH/'tools')
        print(json.dumps({'scope':'SEEDED_CONTINUATION','records':[r for r in result['records'] if r['case_id']==CASE]},ensure_ascii=False))
    finally:
        after=implementation();write_json(output/'implementation-manifest.json',{'before':before,'after':after,'unchanged':before==after})
        write_json(output/'money-accounting.json',{'before':money_before,'after':provider.money_budget.summary()})


if __name__=='__main__':main()
