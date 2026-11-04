"""One prespecified reliability run; source method root is explicit and frozen.

This common harness uses each version's own planning, review and execution.
Independent scoring is deliberately absent from this process and its prompts.
"""
import argparse
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HARNESS_ROOT=Path(__file__).resolve().parents[1]


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def source_files(root):
    return {str(p.relative_to(root/'src')):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((root/'src').rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}


def bind_seed_retry(rebind, original_plan, source, feedback, prior_plan=None, prior_execution=None):
    """Use transaction proof only on its exact current snapshot and supported API."""
    from contractedge.source.snapshot import compute_snapshot
    if (prior_plan is not None and prior_execution
            and prior_execution.get('status') == 'APPLIED'
            and prior_execution.get('to_snapshot') == compute_snapshot(source).snapshot_id
            and 'prior_execution' in inspect.signature(rebind).parameters):
        return rebind(prior_plan, source, feedback, prior_execution=prior_execution)
    return rebind(original_plan, source, feedback)


def execute_seed_edit(executor,budget,plan,source,policy,*,config,kwargs,output,corrections_used):
    """Same one-format-correction policy as the normal repair loop.

    Only a completed invalid response on unchanged source can be corrected.
    Network uncertainty, applied edits, and exhausted allowances never retry.
    """
    from contractedge.execution.topomen import PlanRejected
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.contracts.validation import canonical_digest
    before=compute_snapshot(source).snapshot_id;attempts=0;options=dict(kwargs)
    while True:
        calls_before=len(budget.calls);attempts+=1
        try:
            result=executor.execute(plan,source,policy,**options)
        except (PlanRejected,ValueError) as exc:
            last=budget.calls[-1] if len(budget.calls)>calls_before else {}
            previous=dict(model_response=last.get('response_content'),response_metadata=last.get('response_metadata',{}),
                validation_error=str(exc),plan_digest=canonical_digest(plan),source_snapshot=before)
            write(Path(output)/(str(attempts-1)+'.json'),dict(status='CONCRETE_EDIT_REJECTED',**previous))
            complete=(last.get('prompt_version')=='topomen-source-execution.v1'
                and isinstance(last.get('response_content'),str) and not last.get('error'))
            if not (config.get('execution_format_retry_in_round') and complete
                    and compute_snapshot(source).snapshot_id==before
                    and corrections_used<max(0,int(config.get('max_format_retries',1)))):raise
            corrections_used+=1;options['previous_attempt']=previous
            continue
        result={**result,'generation_attempts':attempts,'format_corrections_used':corrections_used}
        write(Path(output)/(str(attempts-1)+'.json'),result)
        return result,corrections_used


def seeded_review_envelope(semantic_plan,method,config,previous=None):
    from contractedge.orchestration.feedback import review_plan_with_retained
    return review_plan_with_retained({**semantic_plan,
        'declared_artifacts':list(method.declared_artifacts),
        'public_execution_capabilities':config['public_execution_capabilities']},previous)


def recoverable_execution_failure(report):
    from contractedge.execution import public
    check=getattr(public,'has_candidate_execution_failure',None) or getattr(public,'has_candidate_runtime_failure',None)
    return bool(check and check(report))


def public_feedback_kwargs(feedback_fn,observation):
    from contractedge.execution import public
    collect=getattr(public,'public_failure_diagnostics',None)
    if collect and 'observation_diagnostics' in inspect.signature(feedback_fn).parameters:
        return {'observation_diagnostics':collect(observation)}
    return {}


def continue_seeded_repair(source,paths,feedback,observation,*,max_rounds,guard_enabled,output,attempt,
                          preservation_ids=lambda feedback:[]):
    """Protocol v5: the saved seed is the starting candidate, not a new edit.

    Both versions receive at most max_rounds fresh edits and the same shared
    call/token/money limits. Only public, freshly reviewed feedback is used.
    """
    if not 1<=max_rounds<=16:raise ValueError('Invalid recovery round bound')
    from contractedge.orchestration.feedback import feedback_prompt_view
    rounds=[];previous=None;retry_restored=False;errors=[];integrity=True
    status='RECOVERY_NOT_ROUTED';decision=None
    for index in range(max_rounds):
        if feedback.get('route')!='EXECUTE' and not retry_restored:break
        checkpoint=None;retry_restored=False
        if guard_enabled:
            from contractedge.orchestration.candidate_guard import checkpoint_candidate,restore_candidate,compare_candidate_checks
            checkpoint=checkpoint_candidate(source,paths,Path(output)/str(index)/'checkpoint.json')
        try:
            trial=attempt(index,feedback,observation,previous)
            decision=(compare_candidate_checks({**observation,'feedback':feedback},
                {**trial['observation'],'feedback':trial['feedback']},preservation_ids(feedback))
                if guard_enabled else None)
            row=dict(round_id=index,trial_snapshot=trial['feedback'].get('to_snapshot'),candidate_decision=decision)
            if decision and decision['decision']!='PROMOTE':
                restore_candidate(source,checkpoint);checkpoint=None
                previous=dict(source_snapshot=feedback['to_snapshot'],error='Trial was rejected; current source was restored.',
                    rejected_trial=dict(snapshot_id=trial['feedback'].get('to_snapshot'),
                        public_check_results=trial['feedback'].get('public_check_results',[]),candidate_decision=decision,
                        feedback=feedback_prompt_view(trial['feedback']),
                        actual_edits=trial.get('execution_mapping',[]),patch_digest=trial['feedback'].get('patch_digest'),
                        interpretation='Historical rejected trial; current source was restored. These edits and errors are not the current snapshot.'))
                retry_restored=(decision['decision']=='REJECT_REGRESSION' or
                    decision['decision']=='UNCOMPARABLE' and recoverable_execution_failure(trial['observation']))
                status='RECOVERY_REGRESSION_REJECTED' if retry_restored else 'RECOVERY_COMPARISON_UNAVAILABLE'
            else:
                feedback,observation=trial['feedback'],trial['observation'];checkpoint=None;previous=None
                status='PUBLIC_REVIEW_COMPLETE' if feedback.get('semantic_outcome')=='PUBLIC_REVIEW_PASS' else 'PUBLIC_EXECUTION_COMPLETE'
            row.update(selected_snapshot=feedback.get('to_snapshot'),status=status)
            rounds.append(row);write(Path(output)/str(index)/'selection.json',row)
            if decision and decision['decision']=='UNCOMPARABLE' and not retry_restored:break
        except Exception as exc:
            status='RECOVERY_FAILED';errors.append(type(exc).__name__+': '+str(exc))
            if checkpoint:
                try:restore_candidate(source,checkpoint)
                except Exception as restore_error:
                    integrity=False;errors.append('Restore failed: '+str(restore_error))
            elif not guard_enabled:
                # An interrupted unguarded legacy attempt has no verified final
                # candidate; do not export an unchecked partial transaction.
                integrity=False
            break
    return dict(feedback=feedback,observation=observation,rounds=rounds,status=status,
                candidate_decision=decision,errors=errors,source_integrity_verified=integrity)


def recover(case,directory,provider,tokenizer,config,benchmark,seed):
    from contractedge.contracts.validation import parse_method_input
    from contractedge.dataset.benchmark import method_input_from_task,file_sha
    from contractedge.execution.public import BenchmarkPublicRunner
    from contractedge.execution.review import ModelPublicReviewer
    from contractedge.execution.topomen_agent import TopoMenAgentExecutor
    from contractedge.changeset.topomen import translate_for_topomen
    from contractedge.execution.probe_specs import select_probes
    from contractedge.execution.observations import select_observation_adapters
    from contractedge.model.budget import BudgetedProvider
    from contractedge.orchestration.runner import run_front_half
    from contractedge.orchestration.repair import select_observations,rebind_execution_plan
    from contractedge.orchestration.feedback import make_feedback,review_plan_with_retained
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.contracts.permissions import target_permission_error
    directory.mkdir(parents=True,exist_ok=False);workspace=directory/'workspace'
    method_value,projection=method_input_from_task(benchmark/'public',case);method=parse_method_input(method_value)
    config={**config,'public_execution_capabilities':projection['public_execution_capabilities']}
    runner=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',lock_root=config['lock_root'],
        timeout_seconds=config['public_timeout_seconds'],query=method.requirement_text,
        method_probe_profiles=config['method_probe_profiles'])
    budget=BudgetedProvider(provider,tokenizer,max_tokens=config['max_total_tokens'],max_seconds=config['max_elapsed_seconds'],
        max_calls=config['max_model_calls'],on_update=lambda calls:write(directory/'model_calls.json',calls))
    source=None;feedback=None;integrity=True;rounds=[]
    status='STARTING';errors=[];selected_patch=None;candidate_decision=None
    try:
        prepared=runner.prepare(case,workspace,directory/'prepare');source=Path(prepared['source_root'])
        if compute_snapshot(source).snapshot_id!=method.initial_snapshot_id:raise ValueError('Recovery Broken mismatch')
        specs=select_probes(method.requirement_text,source,config['method_probe_profiles'])
        adapters=select_observation_adapters(benchmark/'public',case)
        config['public_execution_capabilities']={**config['public_execution_capabilities'],
            'method_behavior_probes':[{k:v for k,v in s.items() if k!='program'} for s in specs],
            'registered_public_observation_adapters':adapters}
        initial=runner.observe(workspace,directory/'broken-public')
        observations=select_observations(initial['observations'],method.requirement_text,tokenizer,config['max_public_evidence_tokens'])
        front=run_front_half(method,source if method.source_root=='.' else source.parent,budget,
            {**config,'model':{'model':config['model'],'provider':config['provider'],'schema_in_prompt':config['schema_in_prompt']}},
            tokenizer,public_observations=observations).to_wire()
        write(directory/'front.trace.json',front)
        shutil.copy2(seed,directory/'public-seed.patch')
        subprocess.run(['git','apply','--check',str(seed)],cwd=source,check=True,capture_output=True)
        subprocess.run(['git','apply',str(seed)],cwd=source,check=True,capture_output=True)
        changed=subprocess.check_output(['git','diff','--name-only'],cwd=source,text=True).splitlines()
        policy=projection['public_execution_capabilities']['editable_policy']
        if any(target_permission_error(p,policy) for p in changed):raise ValueError('Public recovery seed violates edit policy')
        seed_snapshot=compute_snapshot(source).snapshot_id
        write(directory/'provenance.json',dict(scope='SEEDED_CONTINUATION_NOT_FIRST_PASS',case_id=case,
            seed_path=str(seed),seed_sha256=file_sha(seed),seed_snapshot=seed_snapshot,
            initial_snapshot=method.initial_snapshot_id,hidden_feedback_used=False,
            continuation_protocol='ce.seeded_continuation.v5',
            bootstrap_seed_counts_as_fresh_edit=False,max_fresh_edits=int(config.get('max_rounds',2)),
            plan_basis='This method independently constructs S0 intent; no other version plan is injected'))
        selected_patch=runner.export(workspace,directory/'seed.patch',directory/'seed-export')
        if front['status'] not in {'READY','READY_PARTIAL'}:
            status='RECOVERY_PLAN_UNSUPPORTED';errors.extend(front.get('errors',[]))
        else:
            observation=runner.observe(workspace,directory/'seed-public')
            reviewer=ModelPublicReviewer(budget,tokenizer,config)
            semantic_plan=front['changeset'];plan=translate_for_topomen(semantic_plan,'contractedge.topomen_plan.v1',
                diagnosis=front['diagnosis'],public_capabilities=config['public_execution_capabilities'],
                **({'source_support':front['run_context']['retrieval_config']['necessary_source_support']}
                   if front['run_context']['retrieval_config'].get('necessary_source_support') else {}))
            def review(obs,envelope,execution=None):
                from contractedge.execution.topomen import execution_review_mapping
                return reviewer.review(method.requirement_text,envelope,{**obs,
                    'observations':list(select_observations(obs['observations'],method.requirement_text,tokenizer,config['max_review_evidence_tokens'])),
                    'declared_artifacts':list(method.declared_artifacts),'public_execution_capabilities':config['public_execution_capabilities'],
                    'execution_mapping':execution_review_mapping(execution,max_characters=12000) if execution else []})
            envelope=seeded_review_envelope(semantic_plan,method,config)
            first_review=review(observation,envelope);write(directory/'seed-review.json',first_review)
            feedback=make_feedback(case_id=case,run_id=front['run_id'],input_digest=method.input_digest,round_id=0,
                from_snapshot=method.initial_snapshot_id,to_snapshot=seed_snapshot,patch_digest=selected_patch['patch_sha256'],
                execution_status=observation['status'],observations=observation['observations'],plan=envelope,review=first_review,diagnosis=front['diagnosis'],
                **public_feedback_kwargs(make_feedback,observation))
            write(directory/'seed-feedback.json',feedback)
            status='RECOVERY_NOT_ROUTED'
            if feedback['route']=='EXECUTE':
                from contractedge.execution.topomen import operation_path
                format_corrections_used=0
                last_bound_plan=None;last_applied_execution=None
                def attempt(index,current_feedback,current_observation,previous):
                    nonlocal format_corrections_used,last_bound_plan,last_applied_execution
                    rd=directory/'rounds'/str(index);rd.mkdir(parents=True,exist_ok=True)
                    current_plan=bind_seed_retry(rebind_execution_plan,plan,source,current_feedback,
                        last_bound_plan,last_applied_execution);write(rd/'continuation-plan.json',current_plan)
                    executor=TopoMenAgentExecutor(budget,tokenizer,config,on_trace=lambda x:write(rd/'native-trace.json',x))
                    execution,format_corrections_used=execute_seed_edit(executor,budget,current_plan,source,policy,
                        config=config,corrections_used=format_corrections_used,output=rd/'execution-attempts',
                        kwargs=dict(requirement=method.requirement_text,feedback=current_feedback,
                            declared_artifacts=method.declared_artifacts,previous_attempt=previous,
                            observations=select_observations(current_observation['observations'],method.requirement_text,tokenizer,config['max_public_evidence_tokens'])))
                    write(rd/'execution.json',execution)
                    if execution.get('status')=='APPLIED':
                        last_bound_plan=current_plan;last_applied_execution=execution
                    trial=runner.observe(workspace,rd/'public');patch=runner.export(workspace,rd/'candidate.patch',rd/'export')
                    current_envelope=seeded_review_envelope(semantic_plan,method,config,current_feedback);last_review=review(trial,current_envelope,execution)
                    last=make_feedback(case_id=case,run_id=front['run_id'],input_digest=method.input_digest,round_id=index,
                        from_snapshot=current_feedback['to_snapshot'],to_snapshot=compute_snapshot(source).snapshot_id,
                        patch_digest=patch['patch_sha256'],execution_status=trial['status'],observations=trial['observations'],
                        plan=current_envelope,review=last_review,diagnosis=front['diagnosis'],
                        **public_feedback_kwargs(make_feedback,trial))
                    if execution.get('helper_preconditions'):last['helper_preconditions']=execution['helper_preconditions']
                    write(rd/'review.json',last_review);write(rd/'feedback.json',last)
                    from contractedge.execution.topomen import execution_review_mapping
                    return dict(feedback=last,observation=trial,execution_mapping=execution_review_mapping(execution,max_characters=12000))
                def protected(current_feedback):
                    from contractedge.orchestration.candidate_guard import preservation_check_ids
                    return preservation_check_ids(seeded_review_envelope(semantic_plan,method,config,current_feedback),current_feedback)
                continuation=continue_seeded_repair(source,[operation_path(op) for op in plan['operations']],feedback,observation,
                    max_rounds=int(config.get('max_rounds',2)),guard_enabled=bool(config.get('candidate_guard_enabled')),
                    output=directory/'rounds',attempt=attempt,preservation_ids=protected)
                feedback=continuation['feedback'];status=continuation['status'];rounds=continuation['rounds']
                candidate_decision=continuation['candidate_decision'];integrity=continuation['source_integrity_verified']
                errors.extend(continuation['errors'])
    except Exception as exc:
        status='RECOVERY_FAILED';errors.append(type(exc).__name__+': '+str(exc))
    finally:
        if not integrity:selected_patch=None
        if source is not None and integrity:
            selected_patch=runner.export(workspace,directory/'candidate.patch',directory/'final-export')
        result=dict(schema_version='ce.repair_trace.v1',case_id=case,status=status,candidate=selected_patch,
            scope='SEEDED_CONTINUATION_NOT_FIRST_PASS',errors=errors,last_feedback=feedback,
            candidate_decision=candidate_decision,budget=budget.summary(),rounds=rounds,
            initial_snapshot_id=method.initial_snapshot_id,final_snapshot_id=compute_snapshot(source).snapshot_id if source else None,
            source_integrity_verified=integrity)
        write(directory/'repair.trace.json',result)
        if workspace.exists() and not config.get('keep_workspaces'):shutil.rmtree(workspace)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--method-root',required=True);parser.add_argument('--config',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--case',required=True);parser.add_argument('--seed')
    args=parser.parse_args();method_root=Path(args.method_root).resolve();out=Path(args.output).resolve()
    if out.exists():raise ValueError('Fresh output required')
    sys.path[:0]=[str(method_root/'src'),str(HARNESS_ROOT/'.topomen-deps')]
    from contractedge.cli import _build_provider
    from contractedge.orchestration.batch import run_batch
    from contractedge.orchestration.repair import repair_case
    config=json.loads(Path(args.config).read_text());benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    os.environ[config['api_key_env']]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider,tokenizer=_build_provider(config);before=source_files(method_root);money=provider.money_budget.summary()
    harness_before=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    out.mkdir(parents=True);write(out/'experiment-config.json',config)
    try:
        worker=(lambda case,directory:recover(case,directory,provider,tokenizer,config,benchmark,Path(args.seed).resolve())) if args.seed else (
            lambda case,directory:repair_case(benchmark/'public',benchmark/'tools',case,directory,provider,tokenizer,config))
        result=run_batch(benchmark/'public',out,config,worker,case_ids=[args.case],max_new_cases=1,tools_root=benchmark/'tools')
        selected=next(r for r in result['records'] if r['case_id']==args.case)
        print(json.dumps({'case_id':args.case,'status':selected['status'],'patch_bytes':(selected.get('candidate') or {}).get('patch_bytes',0)},ensure_ascii=False))
    finally:
        after=source_files(method_root);write(out/'implementation-manifest.json',{'before':before,'after':after,'unchanged':before==after})
        harness_after=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        write(out/'harness-manifest.json',dict(path=str(Path(__file__).resolve()),
            before=harness_before,after=harness_after,unchanged=harness_before==harness_after,
            seeded_continuation_protocol='ce.seeded_continuation.v5'))
        write(out/'money-accounting.json',{'before':money,'after':provider.money_budget.summary()})
        if before!=after or harness_before!=harness_after:raise RuntimeError('Frozen method or common harness changed during run')


if __name__=='__main__':main()
