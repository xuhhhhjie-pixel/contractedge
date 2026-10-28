"""Parent-only admission of one explicit pre-edit HTTP402 READY continuation.

This is an administrative protocol amendment, not reinterpretation of the old
INFRASTRUCTURE_STOP or a fresh semantic attempt. All outcomes, including failure,
are imported. Active time is accumulated; the external outage remains declared.
"""
from pathlib import Path

POLICY = 'ce.explicit_http402_ready_prefix.v1'
SCHEMA = 'ce.fixed_panel_provider_stop_amendment.v1'


def checked(ref):
    from fixed_panel import sha, read
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'} or sha(ref['path']) != ref['sha256']:
        raise ValueError('Financial continuation proof missing or changed')
    return read(ref['path'])


def validate_prefix(calls, config):
    """Exact replay, one existing correction, no unknown or edited prefix."""
    from contractedge.contracts.validation import canonical_digest
    if (config.get('base_url') != 'https://api.deepseek.com' or config.get('max_model_calls') != 12
            or config.get('max_rounds') != 2 or not isinstance(calls, list) or len(calls) != 4):
        raise ValueError('Only the recorded official pre-edit correction is eligible')
    if (any(not c['prompt_version'].startswith(('diagnosis.', 'changeset.')) for c in calls)
            or calls[0]['prompt_version'].startswith('changeset.')
            or any(not c['prompt_version'].startswith('changeset.') for c in calls[1:])):
        raise ValueError('Prefix must stop before any execution or review')
    failed = calls[2]
    if (failed.get('error') != 'HTTPError: HTTP Error 402: Payment Required'
            or 'response_content' in failed or calls[3]['request'] != failed['request']
            or calls[3]['messages'] != failed['messages']):
        raise ValueError('Only exact explicitly rejected HTTP402 correction may continue')
    for i,c in enumerate(calls):
        if (c.get('request_digest') != canonical_digest(c['request'])
                or c['request'].get('model') != config['model']
                or c['request'].get('prompt_version') != c['prompt_version']):
            raise ValueError('Prefix request identity changed')
        for k in ['prompt_tokens_reserved','output_tokens_reserved']:
            if type(c.get(k)) is not int or c[k] < 0:
                raise ValueError('Missing actual inherited token reservation')
        if type(c.get('elapsed_seconds')) not in (float,int) or not 0 <= c['elapsed_seconds'] <= config['max_elapsed_seconds']:
            raise ValueError('Missing or exhausted inherited active time')
        if i != 2:
            m = c.get('response_metadata',{})
            if (not isinstance(c.get('response_content'),str) or c.get('error')
                    or m.get('model_identity_verified') is not True or m.get('reported_model') != config['model']):
                raise ValueError('Successful prefix response identity is unverified')
    reserved = sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in calls)
    elapsed = max(c['elapsed_seconds'] for c in calls)
    if reserved >= config['max_total_tokens'] or elapsed >= config['max_elapsed_seconds']:
        raise ValueError('Prefix exhausted original case budget')
    return reserved, elapsed


def validate_accumulation(calls, new_calls, config, child, parent):
    """Never discard the 402 reservation or refill calls/tokens/active time."""
    reserved, elapsed = validate_prefix(calls,config)
    if not isinstance(new_calls,list):raise ValueError('Missing continuation ledger')
    count = len(calls)+len(new_calls)
    total = reserved + sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in new_calls)
    if count > config['max_model_calls'] or total > config['max_total_tokens']:
        raise ValueError('Continuation exceeds the original total budget')
    for b in [child,parent]:
        if (b.get('max_calls') != config['max_model_calls'] or b.get('max_tokens') != config['max_total_tokens']
                or b.get('max_seconds') != config['max_elapsed_seconds']
                or b.get('model_calls') != count or b.get('resumed_model_calls') != 4
                or b.get('reserved_tokens') != total
                or type(b.get('elapsed_seconds')) not in (float,int)
                or not elapsed <= b['elapsed_seconds'] <= config['max_elapsed_seconds']):
            raise ValueError('Continuation refilled or changed inherited budget')
    return count,total


def financial_predecessor_rows(panel):
    from fixed_panel import (read, sha, digest, verify_panel, verify_record,
        verify_environment, ENVIRONMENT_KEYS, TERMINAL_OUTCOMES)
    from score_isolated_method import run as score
    a=panel['predecessor']
    if a.get('schema_version')!=SCHEMA or a.get('interpretation_policy')!=POLICY:
        raise ValueError('Unknown financial continuation policy')
    old=checked(a['panel']);old_path=a['panel']['path']
    if old.get('predecessor') is not None:raise ValueError('Financial amendments cannot be chained')
    old=verify_panel(old_path)
    unchanged=['panel_id','slots','variants','original_registration','benchmark_root','data_files','seeds','runtime',
        'model_call_cap','fresh_edit_cap','formal_ready','compatibility_report','continuation_protocol','transient_workspace_policy']
    if any(panel.get(k)!=old.get(k) for k in unchanged):
        raise ValueError('Financial amendment changed method, model, cases, budgets or thresholds')
    oldroot=Path(old['output_root']).resolve();newroot=Path(panel['output_root']).resolve()
    if oldroot.is_relative_to(newroot) or newroot.is_relative_to(oldroot):
        raise ValueError('Original panel must remain separate and immutable')
    if any(panel['execution_environment'][k]!=old['execution_environment'][k] for k in ENVIRONMENT_KEYS if k!='harness_files'):
        raise ValueError('Financial amendment changed common execution environment')
    before,after=a['worker_files_before'],a['worker_files_after']
    if (set(before)!=set(after) or digest(before)!=old['execution_environment']['harness_files']
            or digest(after)!=panel['execution_environment']['harness_files']
            or any(before[k]!=after[k] and k!='fixed_panel.py' for k in before)):
        raise ValueError('Only the parent panel authority may change; repair workers must remain exact')
    for mapping,owner in [(before,old),(after,panel)]:
        for name,expected in mapping.items():
            matches=[p for p in owner['harness_files'] if Path(p).name==name]
            if name=='runtime_preflight.py':matches=[p for p in owner['harness_files'] if p.endswith('/execution/runtime_preflight.py')]
            if len(matches)!=1 or owner['harness_files'][matches[0]]!=expected:
                raise ValueError('Worker is outside its frozen bundle')
    # The authoritative original ledger is used, never a selected best prefix.
    if Path(a['records']['path']).resolve()!=oldroot/'records.json':raise ValueError('Original records were substituted')
    rows=checked(a['records'])
    if not rows or len({r['slot_id'] for r in rows})!=len(rows):raise ValueError('Malformed original records')
    imported=[]
    for row in rows:
        verify_record(old_path,old,row)
        imported.append({**row,'recorded_status':row['status'],'predecessor_panel_sha256':a['panel']['sha256'],
            'predecessor_record_sha256':digest(row),'interpretation_policy':POLICY})
    policy=checked(a['policy_registration'])
    if (policy.get('schema_version')!='ce.http402_resume_policy.v1' or policy.get('policy')!=POLICY
            or policy.get('prospectively_registered_original_protocol') is not False
            or policy.get('outcome_selection_allowed') is not False or policy.get('semantic_threshold_changes_allowed') is not False
            or policy.get('eligible_for')!=['baseline','candidate'] or policy.get('all_original_rows_required') is not True):
        raise ValueError('Administrative amendment timing/scope must be disclosed without outcome selection')
    if policy['original_panel']!=a['panel']:raise ValueError('Policy belongs to another original panel')
    original=checked(a['original_receipt']);identity=original['identity'];slot=next((s for s in old['slots'] if s==original.get('panel_slot')),None)
    if slot is None or any(r['slot_id']==slot['slot_id'] for r in rows):raise ValueError('Continuation duplicates or replaces a completed result')
    case=oldroot/'methods'/slot['slot_id']/slot['case_id']
    if (identity.get('case_id')!=slot['case_id'] or identity.get('attempt_id')!=slot['slot_id']
            or identity.get('output_path')!=str(case) or identity.get('panel_slot_id')!=slot['slot_id']
            or identity.get('panel_registration_sha256')!=sha(old_path)
            or Path(a['original_trace']['path']).resolve()!=case/'repair.trace.json'):
        raise ValueError('Original stopped attempt identity or trace belongs to another slot')
    verify_environment(old,identity)
    v=old['variants'][slot['variant']];cfg=read(v['config'])
    if (original.get('status')!='INFRASTRUCTURE_STOP' or original.get('cleanup',{}).get('status')!='OWNED_CONTAINERS_CLOSED'
            or original.get('panel_registration_sha256')!=sha(old_path) or original.get('counts_in_46_slot_panel') is not True
            or identity.get('method_files')!=v['source_files'] or identity.get('config_sha256')!=v['config_sha256']):
        raise ValueError('Original financial-stop receipt is not bound to this slot')
    original_trace=checked(a['original_trace']);oldcalls=checked(a['original_calls']);calls=checked(a['combined_calls']);ready=checked(a['ready'])
    if (calls[:3]!=oldcalls or ready.get('model_calls')!=calls or ready.get('status')!='READY'
            or original_trace.get('candidate') or original_trace.get('rounds')
            or original_trace['initial_snapshot_id']!=original_trace['final_snapshot_id']
            or ready['snapshot_id']!=original_trace['initial_snapshot_id'] or ready['case_id']!=slot['case_id']):
        raise ValueError('Old attempt edited source, changed snapshot or regenerated responses')
    validate_prefix(calls,cfg)
    if policy['ready']!=a['ready'] or policy['combined_calls']!=a['combined_calls']:
        raise ValueError('READY or prefix changed after administrative scope declaration')
    q=checked(a['queue_state'])
    if (Path(a['queue_state']['path']).resolve()!=oldroot/'queue'/slot['slot_id']/'state.json'
            or q.get('status')!='METHOD_RETURNED_ERROR' or q.get('identity_sha256')!=digest(identity)
            or q.get('panel_sha256')!=sha(old_path)):
        raise ValueError('Original queue/claim does not bind the interrupted slot')
    rp=Path(a['original_receipt']['path']).resolve()
    if (rp.name!='receipt.json' or rp.parent.parent!=case.parent or not rp.parent.name.startswith('.isolated-')
            or Path(original['state_root']).resolve()!=rp.parent
            or Path(a['original_calls']['path']).resolve()!=rp.parent/'parent-model-calls.json'):
        raise ValueError('Original financial proof is outside its assigned source session')
    from contractedge.source.snapshot import compute_snapshot
    workspace=read(rp.parent/'producer/workspace/workspace.json')
    source=Path(workspace['source_root']).resolve()
    if not source.is_relative_to(rp.parent/'producer/workspace') or compute_snapshot(source).snapshot_id!=ready['snapshot_id']:
        raise ValueError('Original pre-edit production source changed')
    pending=[]
    for f in (rp.parent/'method-journal').glob('*.json'):
        j=read(f)
        if j.get('status')!='DONE':pending.append(j)
    if original.get('terminal_validation',{}).get('unknown_requests')!=1:
        raise ValueError('Unknown original requests cannot be hidden')
    expected=digest({'op':'model.complete','payload':{'request':oldcalls[2]['request']}})
    if len(pending)!=1 or pending[0].get('op')!='model.complete' or pending[0].get('identity_sha256')!=expected:
        raise ValueError('Original unknown request is not the explicitly rejected correction')
    # A second raw receipt records the adapter's failed candidate selection.
    # It is retained, not reclassified as success. Its one received edit is
    # replayed once with a smaller inner semantic budget and zero model HTTP.
    failed=checked(a['failed_selection_receipt']);failed_trace=checked(a['failed_selection_trace'])
    received=checked(a['received_calls']);resources=checked(a['resource_calls'])
    if (failed.get('status')!='INFRASTRUCTURE_STOP'
            or failed.get('cleanup',{}).get('status')!='OWNED_CONTAINERS_CLOSED'
            or failed.get('terminal_validation',{}).get('unknown_requests')!=0
            or failed.get('terminal_validation',{}).get('coordinator_policy_failures')
            or failed_trace.get('errors')!=['ValueError: Unknown dispatch cannot create a new candidate selection']
            or len(received)!=1 or resources!=calls+received
            or not isinstance(received[0].get('response_content'),str) or received[0].get('error')
            or received[0].get('prompt_version')!='topomen-source-execution.v1'
            or received[0].get('response_metadata',{}).get('model_identity_verified') is not True
            or received[0].get('response_metadata',{}).get('reported_model')!=cfg['model']):
        raise ValueError('Selection recovery is not the exact known adapter failure with one received edit')
    validate_accumulation(calls,received,cfg,failed_trace['budget'],failed['parent_budget'])
    verify_environment(old,failed['identity'])
    prior_supplement=failed['identity'].get('controlled_continuation',{})
    if (prior_supplement.get('ready_sha256')!=a['ready']['sha256']
            or prior_supplement.get('combined_model_calls_sha256')!=a['combined_calls']['sha256']
            or prior_supplement.get('original_receipt_sha256')!=a['original_receipt']['sha256']):
        raise ValueError('Failed selection did not inherit this original financial stop')
    if (failed['identity'].get('method_files')!=v['source_files'] or failed['identity'].get('config_sha256')!=v['config_sha256']):
        raise ValueError('Failed selection came from another method or config')
    r=checked(a['continuation_receipt']);trace=checked(a['continuation_trace']);newcalls=checked(a['new_calls']);ci=r['identity']
    if (r.get('status')!='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING' or r.get('method_returncode')!=0
            or r.get('cleanup',{}).get('status')!='OWNED_CONTAINERS_CLOSED'
            or r.get('terminal_validation',{}).get('classification')!='METHOD_RESULT'
            or ci.get('method_files')!=v['source_files'] or ci.get('config_sha256')!=v['config_sha256']
            or ci.get('case_id')!=slot['case_id'] or r.get('counts_in_46_slot_panel') is not False):
        raise ValueError('Controlled continuation is not a closed same-source method result')
    cp=Path(a['continuation_receipt']['path']).resolve();cc=Path(ci.get('output_path','')).resolve()
    if (cp.name!='receipt.json' or cp.parent.parent!=cc.parent or not cp.parent.name.startswith('.isolated-')
            or Path(r['state_root']).resolve()!=cp.parent or Path(a['new_calls']['path']).resolve()!=cp.parent/'parent-model-calls.json'
            or Path(a['continuation_trace']['path']).resolve()!=cc/'repair.trace.json'
            or Path(a['imported_ready']['path']).resolve()!=cc/'initial-front.trace.json'
            or trace.get('case_id')!=slot['case_id'] or trace.get('initial_snapshot_id')!=ready['snapshot_id']
            or trace.get('source_integrity_verified') is False
            or r['terminal_validation'].get('candidate_verified') is not bool(trace.get('candidate'))):
        raise ValueError('Continuation session, actual READY or candidate identity changed')
    verify_environment(old,ci)
    supplement=ci.get('controlled_continuation',{})
    mapping=checked(a['projection'])
    if (supplement.get('protocol')!='ce.explicit_rejected_transport_projection.v1'
            or supplement.get('model_http_calls_allowed')!=0 or supplement.get('mapping')!=mapping
            or supplement.get('ready_source_sha256')!=a['ready']['sha256']
            or supplement.get('failed_selection_receipt_sha256')!=a['failed_selection_receipt']['sha256']):
        raise ValueError('Recovery did not declare the exact financial/semantic projection')
    projected=checked(a['imported_ready']);expected={**ready,'model_calls':[calls[i] for i in [0,1,3]]}
    if projected!=expected or newcalls!=[]:
        raise ValueError('Semantic projection dropped a real response or dispatched a new model')
    omitted=calls[2]['prompt_tokens_reserved']+calls[2]['output_tokens_reserved']
    total=sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in resources)
    inner=total-omitted
    active=failed['parent_budget']['elapsed_seconds']
    if (mapping.get('transport_rejected_index')!=2 or mapping.get('semantic_ready_indices')!=[0,1,3]
            or mapping.get('cached_execution_index')!=4 or mapping.get('outer_model_calls')!=5
            or mapping.get('outer_reserved_tokens')!=total or mapping.get('model_http_calls_allowed')!=0
            or mapping.get('inner_max_calls')!=cfg['max_model_calls']-1
            or mapping.get('inner_max_tokens')!=cfg['max_total_tokens']-omitted
            or mapping.get('inherited_active_seconds')!=active):
        raise ValueError('Financial rejection projection changes original remaining allowance')
    pb=r['parent_budget'];cb=trace['budget']
    if (pb.get('model_calls')!=5 or pb.get('resumed_model_calls')!=5 or pb.get('reserved_tokens')!=total
            or pb.get('max_calls')!=cfg['max_model_calls'] or pb.get('max_tokens')!=cfg['max_total_tokens']
            or cb.get('model_calls')!=4 or cb.get('resumed_model_calls')!=3 or cb.get('reserved_tokens')!=inner
            or cb.get('max_calls')!=cfg['max_model_calls']-1 or cb.get('max_tokens')!=cfg['max_total_tokens']-omitted):
        raise ValueError('Parent or semantic budgets were refilled')
    for budget in [pb,cb]:
        if budget.get('max_seconds')!=cfg['max_elapsed_seconds'] or not active<=budget.get('elapsed_seconds',-1)<=cfg['max_elapsed_seconds']:
            raise ValueError('Recovery lost previously used active time')
    effective=checked(a['effective_config'])
    required={**cfg,'money_budget':None,'runtime_preflight':None,'max_model_calls':cfg['max_model_calls']-1,'max_total_tokens':cfg['max_total_tokens']-omitted}
    if (effective!=required or r.get('effective_config_sha256')!=a['effective_config']['sha256']
            or set(r.get('delegated_config_keys',[]))!={'money_budget','runtime_preflight','max_model_calls','max_total_tokens'}
            or r.get('money_before')!=r.get('money_after')):
        raise ValueError('Recovery config changed beyond downward budget projection or charged money')
    code={Path(ref['path']).name:ref for ref in a['supplementary_code']}
    expected_code={'recover_ack_selection_isolated.py':supplement.get('driver_sha256'),'ack402_projection_child.py':supplement.get('child_sha256')}
    if set(code)!=set(expected_code):raise ValueError('Missing actual supplementary parent or child')
    for name,digest_expected in expected_code.items():
        if code[name]['sha256']!=digest_expected or sha(code[name]['path'])!=digest_expected:
            raise ValueError('Supplementary recovery code changed')
    model_records=[];public_records=[]
    for f in (cp.parent/'method-journal').glob('*.json'):
        j=read(f)
        if j.get('status')!='DONE':raise ValueError('Unresolved replay RPC cannot be promoted')
        if j.get('op')=='model.complete':model_records.append(j)
        if j.get('op')=='public.observe':public_records.append(j)
    if (len(model_records)!=1 or model_records[0]['result']!={'content':received[0]['response_content'],'metadata':received[0]['response_metadata']}
            or model_records[0]['identity_sha256']!=digest({'op':'model.complete','payload':{'request':received[0]['request']}})):
        raise ValueError('Actual replay did not consume exactly the received execution request and response')
    cache=supplement.get('cached_observation_journals',{})
    if set(cache)!={trace['initial_snapshot_id'],trace['final_snapshot_id']}:
        raise ValueError('Original public cache does not cover the exact recovered snapshots')
    original_public={}
    failed_state=Path(a['failed_selection_receipt']['path']).resolve().parent
    for sid,ref in cache.items():
        if Path(ref['path']).resolve().parent!=failed_state/'method-journal':
            raise ValueError('Public cache was not produced by the original trusted observer')
        old_observe=checked(ref)
        if (old_observe.get('status')!='DONE' or old_observe.get('op')!='public.observe'
                or old_observe['result'].get('case_id')!=slot['case_id'] or old_observe['result'].get('snapshot_id')!=sid):
            raise ValueError('Original public cache snapshot, case or status differs')
        original_public[sid]=old_observe['result']
    if (len(public_records)!=2 or {j['result'].get('snapshot_id') for j in public_records}!=set(cache)
            or any(j['result']!=original_public[j['result']['snapshot_id']] for j in public_records)):
        raise ValueError('Replayed observation is not the exact previously verified runtime fact')
    trial=checked(a['failed_trial_patch_info'])
    if (sha(a['failed_trial_patch']['path'])!=a['failed_trial_patch']['sha256']
            or trial['patch_sha256']!=a['failed_trial_patch']['sha256']
            or (trace.get('candidate') or {}).get('patch_sha256')!=trial['patch_sha256']):
        raise ValueError('Recovery selected a different or regenerated semantic patch')
    candidate=trace.get('candidate')
    if candidate:
        sp=Path(a['continuation_score']['path']);scored=score(a['continuation_receipt']['path'],old['benchmark_root'],sp.parent,phase='finalize')
        if scored!=checked(a['continuation_score']):raise ValueError('Scoring proof changed')
        status=scored['status']
    else:
        if a.get('continuation_score') is not None:raise ValueError('No candidate must not have a score')
        status='NO_CANDIDATE'
    if status not in TERMINAL_OUTCOMES:raise ValueError('Continuation result is still infrastructure/unknown')
    decision={'PUBLIC_REVIEW_PASS':'PASS','PUBLIC_REVIEW_FAIL':'FAIL'}.get((trace.get('last_feedback') or {}).get('semantic_outcome'),'UNKNOWN')
    imported.append({**slot,'status':status,'independent_status':status,'public_decision':decision,'method_status':trace['status'],
        'terminal_verified':True,'counts_in_46_slot_panel':True,'identity_sha256':digest(ci),'formal_ready':False,
        'recorded_status':'INFRASTRUCTURE_STOP','predecessor_panel_sha256':a['panel']['sha256'],
        'predecessor_record_sha256':digest(a),'interpretation_policy':POLICY,
        'scope':'Same original assigned slot via explicit administrative READY continuation; not a fresh uninterrupted end-to-end trial',
        'evidence_files':{'receipt':a['continuation_receipt'],'trace':a['continuation_trace'],**({'score':a['continuation_score']} if a.get('continuation_score') else {}),**{k:a[k] for k in ['original_receipt','original_trace','failed_selection_receipt','failed_selection_trace','combined_calls','resource_calls','new_calls','policy_registration','projection']}}})
    return imported
