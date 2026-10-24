"""Fail-closed recovery of an existing production repair output.

This first recovery policy finishes a verified selection or restores its
incumbent and exports that actual source. It is terminal: it cannot grant a new
model/time/policy allowance or automatically replay any dispatch.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
from contractedge.contracts.validation import canonical_digest, parse_method_input
from contractedge.dataset.benchmark import method_input_from_task, public_task, editable_policy, execution_contract
from contractedge.orchestration.candidate_guard import restore_candidate
from contractedge.orchestration.recorder import RunRecorder
from contractedge.orchestration.trial import TrialSession, atomic_json, ledger_facts, recovery_identity, sha
from contractedge.source.snapshot import compute_snapshot


def _validate_workspace(trial, source):
    begin=trial.begin_event
    snapshot=compute_snapshot(source)
    events=trial.events()
    parent=begin['parent_files']
    # A write-ahead edit intent permits partial completion of that exact edit
    # batch. Arbitrary changes, including changes to owned paths, fail closed.
    edit=next((event for event in reversed(events) if event['kind']=='EDIT_INTENT'),None)
    actual=next((event for event in reversed(events) if event['kind']=='APPLIED'),None)
    allowed=[parent]
    if actual: allowed.append(actual['file_digests'])
    if edit:
        expected=edit['expected_files']; before=edit['before_files']
        keys=set(snapshot.file_digests)|set(before)|set(expected)
        if all(snapshot.file_digests.get(key) in {before.get(key),expected.get(key)} for key in keys): return snapshot
    if dict(snapshot.file_digests) not in allowed: raise ValueError('External workspace source change; recovery refused without overwriting source')
    return snapshot


def _policy_state(recorder, config):
    from contractedge.orchestration.state import PolicyLedger
    paths=sorted(recorder._path('journal/transitions').glob('revision-*.json'))
    usage=recorder._read(paths[-1].relative_to(recorder.root).as_posix())['next_state']['policy_usage'] if paths else []
    projected=recorder._read('state.json').get('policy_usage',[]) if recorder._path('state.json').exists() else []
    latest=recorder._read('journal/policy-usage.json') if recorder._path('journal/policy-usage.json').exists() else []
    limits=PolicyLedger(config).limits
    merged={}
    for row in [*usage,*projected,*latest]:
        if row['policy'] not in limits or row['limit']!=limits[row['policy']] or not 0 <= row['used'] <= row['limit']:
            raise ValueError('Recovered policy allowance differs')
        key=(row['policy'],row['scope'])
        if key not in merged or merged[key]['used']<row['used']: merged[key]=row
    return [merged[key] for key in sorted(merged)]


def _finish_selection(recorder, saved, config, policies, result):
    """Commit the real recovered effect through the same engine, once."""
    from contractedge.orchestration.state import RepairEngine
    from contractedge.contracts.stages import StageResult
    report=result['report'];snapshot=report['final_snapshot_id']
    reference=recorder.capture_artifact('journal/recovery.result.json','RECOVERED_SELECTION',snapshot_id=snapshot)
    engine=RepairEngine.restore_committed(recorder,saved['initial_snapshot'],config,policy_usage=policies)
    if engine.state.selected!=reference:
        engine.step(StageResult('selection','COMPLETE',reference,snapshot_id=snapshot,terminal_status=report['status']),
            current_snapshot=snapshot)
    engine.project(recovery_terminal=True,continuation_allowed=False)
    recorder._write_pending('selected-candidate.json',{'snapshot_id':snapshot,
        'selection':report['candidate_selection']['selection'],'recovered':True})
    recorder._write_pending('selected-feedback.json',report['last_feedback'])
    recorder._write_pending('repair.trace.json',report)
    atomic_json(recorder._path('journal/recovery.completed.json'),result,immutable=True)
    return report


def resume_repair_case(public_root, tools_root, case_id, output, config, *, public_runner=None,
                       executor=None, consumer=None, consume_received_response=False):
    root=Path(public_root).resolve(strict=True); out=Path(output).resolve(strict=True)
    if out.is_relative_to(root) or root.is_relative_to(out): raise ValueError('Repair output must be independent of frozen public tasks')
    from contractedge.dataset.admission import check_case_admission
    check_case_admission(root,case_id,config)
    method_value,projection=method_input_from_task(root,case_id)
    method_input=parse_method_input(method_value)
    backend=config.get('execution_backend','direct')
    run=json.loads((out/'journal/run.identity.json').read_bytes())
    recorder=RunRecorder(out,run_id=run['run_id'],input_digest=method_input.input_digest)
    saved=recorder._read('journal/recovery.identity.json')
    from contractedge.execution.consumer_recovery import recovery_consumer,reaccept
    from contractedge.execution.consumer import validate_consumer,BudgetPort
    consumer=recovery_consumer(saved['identity']['repair_consumer_id'],backend,consumer=consumer,executor=executor)
    capabilities=validate_consumer(consumer)
    if consume_received_response and not capabilities.received_response_recovery:
        raise ValueError('Native TopoMen or selected consumer received-response replay is unsupported; original response/reservations are retained')
    configured=resolve_execution_capabilities(config.get('public_execution_capabilities'),backend=backend)
    configured['supported_operations']=[op for op in configured['supported_operations'] if op in capabilities.supported_operations]
    projected=resolve_execution_capabilities(projection.get('public_execution_capabilities'),backend=backend)
    projected['supported_operations']=[op for op in projected['supported_operations'] if op in configured['supported_operations']]
    config={**dict(config),'public_execution_capabilities':resolve_execution_capabilities(projected,backend=backend)}
    source=Path(saved['identity']['source_root']).resolve(strict=True)
    if not source.is_relative_to(out/'workspace'): raise ValueError('Recovery workspace is outside the owned run')
    expected=recovery_identity(recorder,source,method_value,config,consumer_id=capabilities.consumer_id)
    if expected!=saved['identity']: raise ValueError('Recovery input/source/prompt/config/mode/session/ledger identity differs')
    if canonical_digest(recorder._read('method_input.json'))!=expected['method_input_digest']:
        raise ValueError('Saved public input changed')
    ledger=ledger_facts(recorder)
    policies=_policy_state(recorder,config)
    trials=[TrialSession(recorder,path.name) for path in sorted(recorder._path('journal/trials').glob('round-*'),
        key=lambda path:int(path.name[6:])) if path.is_dir()]
    trial=trials[-1] if trials else None
    selected_feedback=None; selection='INCUMBENT'; semantic='UNKNOWN'; errors=[]
    transition_lock=recorder._path('journal/transitions/writer.lock').exists()
    if transition_lock:
        raise ValueError('Unresolved transition writer lock; recovery refused without removing ownership evidence')
    if trial:
        begin=trial.begin_event; events=trial.events()
        # A previous rollback failure closes every selection/recovery branch.
        if any(event['kind']=='ROLLBACK_FAILED' for event in events):
            raise ValueError('Prior rollback failed; export prohibited until independently repaired')
        if begin['identity']!=expected: raise ValueError('Trial identity differs')
        if canonical_digest(recorder._read(begin['plan_path']))!=begin['plan_digest']:
            raise ValueError('Recovered plan changed')
        trial.checkpoint()
        _validate_workspace(trial,source)
    elif dict(compute_snapshot(source).file_digests)!=saved['initial_files']:
        raise ValueError('External workspace source change before trial; recovery refused')
    existing=recorder._path('journal/recovery.completed.json')
    if existing.exists():
        completed=recorder._read('journal/recovery.completed.json')
        if (completed['ledger_digest']!=ledger['digest'] or completed['report']['final_snapshot_id']!=compute_snapshot(source).snapshot_id
                or completed['report']['candidate'] and sha(out/'candidate.patch')!=completed['report']['candidate']['patch_sha256']):
            raise ValueError('Recovered selected source/patch/ledger changed')
        return completed['report']
    pending_result=recorder._path('journal/recovery.result.json')
    if pending_result.exists():
        result=recorder._read('journal/recovery.result.json')
        if (result['ledger_digest']!=ledger['digest'] or result['report']['final_snapshot_id']!=compute_snapshot(source).snapshot_id
                or result['report']['candidate'] and sha(out/'candidate.patch')!=result['report']['candidate']['patch_sha256']):
            raise ValueError('Pending recovery effect identity differs')
        return _finish_selection(recorder,saved,config,policies,result)
    selection_intent=next((event for event in reversed(events) if event['kind']=='SELECTION_INTENT'),None) if trial else None
    observed=next((event for event in reversed(events) if event['kind']=='OBSERVED'),None) if trial else None
    if selection_intent:
        if compute_snapshot(source).snapshot_id!=selection_intent['selected_snapshot']:
            raise ValueError('Committed selected snapshot differs')
        count=selection_intent['ledger_call_count']
        if len(ledger['calls'])<count or canonical_digest(ledger['calls'][:count])!=selection_intent['ledger_call_digest']:
            raise ValueError('Committed selection dispatch ledger prefix differs')
        isolated=config.get('public_runtime')=='isolated' or getattr(public_runner,'requires_public_profile',False) is True
        if isolated:
            from contractedge.execution.public_observer import isolated_observation_complete
            if not observed or not isolated_observation_complete(observed['observation']):
                raise ValueError('Committed selection lacks complete isolated observation/cleanup')
        selection='TRIAL'; selected_feedback=selection_intent['selected_feedback']
        semantic=selection_intent['public_semantic_status']
        if not any(event['kind']=='SELECTION_COMPLETED' for event in events):
            recorder._write_pending('selected-candidate.json',{'snapshot_id':selection_intent['selected_snapshot'],
                'selection':'TRIAL','trial_id':trial.trial_id,'selection_effect_id':selection_intent['effect_id']})
            trial.record('SELECTION_COMPLETED',intent_effect_id=selection_intent['effect_id'],snapshot_id=selection_intent['selected_snapshot'])
    elif trial:
        responses=[event for event in events if event['kind']=='RESPONSE_DURABLE']
        consumed={event['response_id'] for event in events if event['kind']=='RESPONSE_CONSUME_INTENT'}
        available=[event for event in responses if event['effect_id'] not in consumed]
        if consume_received_response and available and not ledger['unknown_dispatch']:
            response_event=available[-1]
            request=response_event['request']; response=response_event['response']
            if not any(call.get('request')==request and call.get('response_content')==response['content']
                       and call.get('response_metadata',{})==response['metadata'] for call in ledger['calls']):
                raise ValueError('Durable response differs from original dispatch settlement')
            if not callable(getattr(consumer,'consume_received_response',None)):
                raise ValueError('Consumer does not support deterministic received-response continuation')
            from contractedge.model.provider import ModelRequest,ModelResponse
            _,task_root,task,manifest=public_task(root,case_id)
            policy=editable_policy(task,execution_contract(task_root,task,manifest))
            authority=next((event['authority'] for event in reversed(events) if event['kind']=='AUTHORITY_DURABLE'),None)
            if authority is None:raise ValueError('No durable original consumer authority; received-response consumption refused')
            accepted=reaccept(consumer,authority,source,policy,method_input,plan=begin['plan'])
            if request.get('prompt_version') not in capabilities.execution_prompt_versions:
                raise ValueError('Durable response prompt belongs to another consumer')
            result=consumer.consume_received_response(ModelRequest(**request),ModelResponse(**response),accepted,
                BudgetPort(None,None,accepted.execution_input.context.config,trial),response_id=response_event['effect_id'])
            trial.record_applied(result)
            # No model review is fabricated. The unfinished trial is restored
            # under the same first-version recovery policy as any other APPLIED.
        if not any(event['kind']=='ROLLBACK_COMPLETED' for event in trial.events()):
            trial.rollback_intent()
            try:
                restore_candidate(source,trial.checkpoint())
                trial.record_rollback()
            except BaseException as exc:
                trial.record_rollback(error=exc)
                raise ValueError('Recovery rollback failed; export prohibited') from exc
        if compute_snapshot(source).snapshot_id!=begin['parent_snapshot']: raise ValueError('Recovered incumbent source differs')
        selected_feedback=begin['incumbent_feedback']
        semantic=(selected_feedback or {}).get('semantic_outcome','UNKNOWN')
    snapshot=compute_snapshot(source).snapshot_id
    if selected_feedback and selected_feedback.get('to_snapshot')!=snapshot: raise ValueError('Recovered selected feedback differs')
    from contractedge.execution.public import BenchmarkPublicRunner
    runner=public_runner or BenchmarkPublicRunner(root,tools_root,
        lock_root=config.get('lock_root','/tmp/contractedge-system-native-locks'))
    try:
        patch=runner.export(out/'workspace',out/'candidate.patch',out/'recovery-export')
        if selection_intent and selection=='TRIAL' and patch['patch_sha256']!=selection_intent['patch_sha256']:
            raise ValueError('Recovered selected patch differs from committed decision')
    except Exception as exc:
        patch=None; errors.append('Recovery export failed: '+str(exc))
    calls=ledger['calls']
    report={'schema_version':'ce.repair_trace.v1','case_id':case_id,'release_id':method_input.release_id,
        'run_id':recorder.run_id,'input_digest':method_input.input_digest,'initial_snapshot_id':saved['initial_snapshot'],
        'final_snapshot_id':snapshot,'status':'RESPONSE_UNKNOWN' if ledger['unknown_dispatch'] else 'RECOVERED_'+selection,
        'candidate':patch,'last_feedback':selected_feedback,'rounds':[], 'errors':errors,
        'candidate_selection':{'selection':selection,'trial_id':trial.trial_id if trial else None},
        'public_semantic_status':semantic,'exportable':patch is not None,
        'preservation_comparison':selection_intent['preservation_comparison'] if selection=='TRIAL' else None,
        'budget':{'max_tokens':int(config.get('max_total_tokens',160000)),
            'reserved_tokens':sum(call.get('prompt_tokens_reserved',0)+call.get('output_tokens_reserved',0) for call in calls),
            'max_calls':int(config.get('max_model_calls',24)),'model_calls':len(calls),'resumed_model_calls':len(calls),
            'max_seconds':float(config.get('max_elapsed_seconds',2400)),'elapsed_seconds':max(0,time.time()-saved['started_unix'])},
        'policy_usage':policies,'recovery':{'terminal':True,'dispatches':0,'budget_reinitialized':False,
            'continuation_allowed':False,'unknown_reservations_retained':ledger['unknown_dispatch'],
            'transition_writer_lock_left_in_place':transition_lock,
            'scope':'Finish verified selection or restore incumbent. New trials require an explicit continuation protocol preserving this ledger, policy usage and original deadline.'},
        'hidden_evaluation':'NOT_RUN; recovery is not semantic PASS'}
    result={'ledger_digest':ledger['digest'],'report':report}
    atomic_json(pending_result,result,immutable=True)
    return _finish_selection(recorder,saved,config,policies,result)
