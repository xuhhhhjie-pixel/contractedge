"""Isolated single-case worker; provider/public execution are finite proxies.

Seeded continuation v5 helpers are imported from unchanged worker_v9. The copied
recover orchestration differs only in runner injection and original start time.
There is no credential, real Docker runner or unisolated fallback in this entry.
"""
import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from run_reliability_worker_v9 import (write,source_files,bind_seed_retry,execute_seed_edit,
    seeded_review_envelope,recoverable_execution_failure,public_feedback_kwargs,continue_seeded_repair)
from isolated_protocol import RpcClient
from run_isolated_method import ProxyProvider,ProxyPublicRunner


def completed_edit_rejection(error,budget,position,source,before):
    """Recognize a known model edit rejection, never an uncertain dispatch."""
    from contractedge.execution.topomen import PlanRejected
    from contractedge.source.snapshot import compute_snapshot
    if not isinstance(error,PlanRejected) or len(budget.calls)<=position:return None
    last=budget.calls[-1]
    if (last.get('prompt_version')!='topomen-source-execution.v1'
            or not isinstance(last.get('response_content'),str) or last.get('error')
            or compute_snapshot(source).snapshot_id!=before):return None
    return dict(error_type='PlanRejected',message=str(error)[:1000],snapshot_id=before,
        response_sha256=hashlib.sha256(last['response_content'].encode()).hexdigest(),
        dispatch_outcome='COMPLETED_RESPONSE',source_unchanged=True)


def classify_recovery_continuation(result,rejection,source):
    """Keep model failure distinct after the legacy helper verifies rollback."""
    from contractedge.source.snapshot import compute_snapshot
    if (result.get('status')=='RECOVERY_FAILED' and rejection
            and result.get('source_integrity_verified') is True
            and compute_snapshot(source).snapshot_id==rejection['snapshot_id']):
        return {**result,'status':'CONCRETE_EDIT_REJECTED','method_rejection':rejection}
    return result


def reassess_seed_plan(front,method,source,feedback,previous,provider,tokenizer,config,paths):
    """Reuse current-source planning revision after a verified prerequisite failure.

    The enclosing seed transaction checkpoints only its declared paths. A
    revised plan may not escape that rollback boundary. Unknown plans stop
    before another edit; no old plan is executed as a fallback.
    """
    from contractedge.orchestration import repair as repair_api
    plan_reassessment_required=getattr(repair_api,'plan_reassessment_required',None)
    if plan_reassessment_required is None:
        return front,None  # Frozen older methods keep their existing protocol.
    revise_plan=repair_api.revise_plan
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.changeset.topomen import translate_for_topomen
    from contractedge.execution.topomen import operation_path,PlanRejected
    if not plan_reassessment_required(front['changeset'],compute_snapshot(source).snapshot_id,previous):
        return front,None
    revised=revise_plan(front,method,source,feedback,provider,tokenizer,config,
        rejected_trial=previous['rejected_trial'])
    if revised['status'] not in {'READY','READY_PARTIAL'}:
        raise PlanRejected('Runtime prerequisite reassessment remains unresolved; do not execute the old plan')
    translated=translate_for_topomen(revised['changeset'],'contractedge.topomen_plan.v1',
        diagnosis=revised['diagnosis'],public_capabilities=config['public_execution_capabilities'],
        **({'source_support':revised['run_context']['retrieval_config']['necessary_source_support']}
           if revised['run_context']['retrieval_config'].get('necessary_source_support') else {}))
    if not {operation_path(op) for op in translated['operations']}<=set(paths):
        raise PlanRejected('Revised seed plan exceeds the existing checkpoint path boundary')
    return revised,translated


def recover(case,directory,provider,tokenizer,config,benchmark,seed,*,public_runner=None,runtime_started=None):
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
    planning_mode=config.get('seed_planning_snapshot','ORIGINAL_BROKEN')
    if planning_mode not in {'ORIGINAL_BROKEN','CURRENT_CANDIDATE'}:
        raise ValueError('Unknown seed planning snapshot protocol')
    directory.mkdir(parents=True,exist_ok=False);workspace=directory/'workspace'
    method_value,projection=method_input_from_task(benchmark/'public',case);method=parse_method_input(method_value)
    config={**config,'public_execution_capabilities':projection['public_execution_capabilities']}
    if planning_mode=='CURRENT_CANDIDATE':config={**config,'run_id':str(config.get('run_id') or uuid.uuid4())}
    runner=public_runner or BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',lock_root=config['lock_root'],
        timeout_seconds=config['public_timeout_seconds'],query=method.requirement_text,
        method_probe_profiles=config['method_probe_profiles'])
    budget=BudgetedProvider(provider,tokenizer,max_tokens=config['max_total_tokens'],max_seconds=config['max_elapsed_seconds'],
        max_calls=config['max_model_calls'],on_update=lambda calls:write(directory/'model_calls.json',calls))
    if runtime_started is not None:budget.started=runtime_started
    source=None;feedback=None;integrity=True;rounds=[]
    status='STARTING';errors=[];selected_patch=None;candidate_decision=None;recovery_failure=None;method_rejection=None
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
        front_config={**config,'model':{'model':config['model'],'provider':config['provider'],'schema_in_prompt':config['schema_in_prompt']}}
        if planning_mode=='ORIGINAL_BROKEN':
            front=run_front_half(method,source if method.source_root=='.' else source.parent,budget,
                front_config,tokenizer,public_observations=observations).to_wire()
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
            continuation_protocol='ce.seeded_continuation.v6' if planning_mode=='CURRENT_CANDIDATE' else 'ce.seeded_continuation.v5',
            planning_snapshot=seed_snapshot if planning_mode=='CURRENT_CANDIDATE' else method.initial_snapshot_id,
            bootstrap_seed_counts_as_fresh_edit=False,max_fresh_edits=int(config.get('max_rounds',2)),
            plan_basis='Fresh current-candidate intent; no S0 plan is rebound to claim a new repair' if planning_mode=='CURRENT_CANDIDATE'
                else 'This method independently constructs S0 intent; no other version plan is injected'))
        selected_patch=runner.export(workspace,directory/'seed.patch',directory/'seed-export')
        observation=None
        if planning_mode=='CURRENT_CANDIDATE':
            observation=runner.observe(workspace,directory/'seed-public')
            # This handoff records only an applied public seed and its fresh
            # execution. With no accepted semantic plan it proves no obligation.
            handoff=make_feedback(case_id=case,run_id=config['run_id'],input_digest=method.input_digest,
                round_id=0,from_snapshot=method.initial_snapshot_id,to_snapshot=seed_snapshot,
                patch_digest=selected_patch['patch_sha256'],execution_status=observation['status'],
                observations=observation['observations'],plan={},
                review={'decision':'UNKNOWN','checks':[],'failure_kind':'UNKNOWN'},
                **public_feedback_kwargs(make_feedback,observation))
            write(directory/'seed-handoff-feedback.json',handoff)
            observations=select_observations(observation['observations'],method.requirement_text,tokenizer,config['max_public_evidence_tokens'])
            front=run_front_half(method,source if method.source_root=='.' else source.parent,budget,
                front_config,tokenizer,public_observations=observations,execution_feedback=handoff).to_wire()
            write(directory/'front.trace.json',front)
        if front['status'] not in {'READY','READY_PARTIAL'}:
            status='RECOVERY_PLAN_UNSUPPORTED';errors.extend(front.get('errors',[]))
        else:
            if observation is None:observation=runner.observe(workspace,directory/'seed-public')
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
                last_bound_plan=None;last_applied_execution=None;method_rejection=None
                checkpoint_paths=[operation_path(op) for op in plan['operations']]
                def attempt(index,current_feedback,current_observation,previous):
                    nonlocal format_corrections_used,last_bound_plan,last_applied_execution,method_rejection,front,semantic_plan,plan
                    rd=directory/'rounds'/str(index);rd.mkdir(parents=True,exist_ok=True)
                    if planning_mode=='CURRENT_CANDIDATE' and previous:
                        revised,new_plan=reassess_seed_plan(front,method,source,current_feedback,previous,
                            budget,tokenizer,config,checkpoint_paths)
                        if new_plan is not None:
                            front,semantic_plan,plan=revised,revised['changeset'],new_plan
                            last_bound_plan=None;last_applied_execution=None
                            write(rd/'plan-reassessment.trace.json',front)
                    current_plan=bind_seed_retry(rebind_execution_plan,plan,source,current_feedback,
                        last_bound_plan,last_applied_execution);write(rd/'continuation-plan.json',current_plan)
                    executor=TopoMenAgentExecutor(budget,tokenizer,config,on_trace=lambda x:write(rd/'native-trace.json',x))
                    call_position=len(budget.calls);source_before=compute_snapshot(source).snapshot_id
                    try:
                        execution,format_corrections_used=execute_seed_edit(executor,budget,current_plan,source,policy,
                            config=config,corrections_used=format_corrections_used,output=rd/'execution-attempts',
                            kwargs=dict(requirement=method.requirement_text,feedback=current_feedback,
                                declared_artifacts=method.declared_artifacts,previous_attempt=previous,
                                observations=select_observations(current_observation['observations'],method.requirement_text,tokenizer,config['max_public_evidence_tokens'])))
                    except Exception as exc:
                        method_rejection=completed_edit_rejection(exc,budget,call_position,source,source_before)
                        raise
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
                continuation=continue_seeded_repair(source,checkpoint_paths,feedback,observation,
                    max_rounds=int(config.get('max_rounds',2)),guard_enabled=bool(config.get('candidate_guard_enabled')),
                    output=directory/'rounds',attempt=attempt,preservation_ids=protected)
                continuation=classify_recovery_continuation(continuation,method_rejection,source)
                feedback=continuation['feedback'];status=continuation['status'];rounds=continuation['rounds']
                candidate_decision=continuation['candidate_decision'];integrity=continuation['source_integrity_verified']
                errors.extend(continuation['errors'])
    except Exception as exc:
        from contractedge.model.budget import BudgetExceeded
        from isolated_protocol import ProtocolError
        if isinstance(exc,BudgetExceeded):category='BUDGET'
        elif getattr(exc,'response',None) is not None:category='MODEL_POLICY'
        elif isinstance(exc,ProtocolError):category='CHANNEL'
        else:category='WORKER_ERROR'
        recovery_failure={'category':category,'error_type':type(exc).__name__}
        status='RECOVERY_FAILED';errors.append(type(exc).__name__+': '+str(exc))
    finally:
        if not integrity:selected_patch=None
        if source is not None and integrity:
            selected_patch=runner.export(workspace,directory/'candidate.patch',directory/'final-export')
        result=dict(schema_version='ce.repair_trace.v1',case_id=case,status=status,candidate=selected_patch,
            scope='SEEDED_CONTINUATION_NOT_FIRST_PASS',errors=errors,last_feedback=feedback,
            candidate_decision=candidate_decision,budget=budget.summary(),rounds=rounds,
            initial_snapshot_id=method.initial_snapshot_id,final_snapshot_id=compute_snapshot(source).snapshot_id if source else None,
            source_integrity_verified=integrity,recovery_failure=recovery_failure,method_rejection=method_rejection)
        write(directory/'repair.trace.json',result)
        if workspace.exists() and not config.get('keep_workspaces'):shutil.rmtree(workspace)
    return result


def invoke_repair(api,benchmark,case,directory,provider,counter,config,runner,preflight,started):
    if not isinstance(preflight,dict) or preflight.get('status')!='PASS' or preflight.get('runtime_verified') is not True:
        raise ValueError('Verified parent preflight is required')
    function=getattr(api,'_repair_case_impl',None)
    options=dict(public_runner=runner)
    if function is not None:
        if not {'public_runner','_runtime_preflight','_runtime_started'} <= set(inspect.signature(function).parameters):
            raise ValueError('Unsupported current repair API; no fallback')
        options.update(_runtime_preflight=preflight,_runtime_started=started)
    else:
        function=api.repair_case
        if 'public_runner' not in inspect.signature(function).parameters:
            raise ValueError('Unsupported historical repair API; no fallback')
    return function(benchmark/'public',benchmark/'tools',case,directory,provider,counter,config,**options)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['method-root','benchmark-root','config','output','case','preflight','control']:
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--seed')
    args=parser.parse_args()
    method_root,benchmark,out=Path(args.method_root),Path(args.benchmark_root),Path(args.output)
    if out.exists():raise ValueError('Fresh isolated output required')
    sys.path.insert(0,str(method_root/'src'))
    from contractedge.cli import _token_counter
    from contractedge.orchestration import repair
    config=json.loads(Path(args.config).read_text())
    if config.get('money_budget') is not None or config.get('runtime_preflight') is not None:
        raise ValueError('Money and runtime admission must be held by parent')
    receipt=json.loads(Path(args.preflight).read_text());control=json.loads(Path(args.control).read_text())
    if receipt.get('status')!='PASS' or receipt.get('runtime_verified') is not True:
        raise ValueError('Verified parent preflight is required')
    socket,token=os.environ['CONTRACTEDGE_RPC_SOCKET'],os.environ['CONTRACTEDGE_RPC_TOKEN']
    provider=ProxyProvider(RpcClient(socket,token,timeout=config['timeout_seconds']+30),
                           timeout_seconds=config['timeout_seconds'])
    runner=ProxyPublicRunner(RpcClient(socket,token,timeout=config['public_timeout_seconds']+60),
                             timeout_seconds=config['public_timeout_seconds'])
    counter=_token_counter(config['tokenizer'])
    before=source_files(method_root)
    try:
        if args.seed:
            result=recover(args.case,out,provider,counter,config,benchmark,Path(args.seed),
                           public_runner=runner,runtime_started=control['started_monotonic'])
        else:
            result=invoke_repair(repair,benchmark,args.case,out,provider,counter,config,runner,
                                 receipt,control['started_monotonic'])
    finally:
        after=source_files(method_root)
        if out.exists():
            write(out/'isolated-method-integrity.json',dict(before=before,after=after,unchanged=before==after,
                preflight_delegated=True,real_docker_socket_mounted=False,api_key_mounted=False))
        if before!=after:raise RuntimeError('Frozen method source changed')


if __name__=='__main__':main()
