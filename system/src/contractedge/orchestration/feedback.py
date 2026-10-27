"""Snapshot-bound public feedback. Hidden scores never enter this module."""
from __future__ import annotations

from contractedge.execution.support import support_error

import hashlib
import json
from typing import Any, Mapping

from contractedge.contracts.models import ContractError
from contractedge.contracts.validation import _check_schema, canonical_digest


def feedback_prompt_view(value: Mapping | None, *, supplied_observations=()) -> dict | None:
    """Keep all obligation state, referencing raw outputs stored in full on disk.

    This is a prompt projection, never a replacement for validated feedback.
    Fresh bounded observation excerpts are supplied by each stage separately.
    """
    if value is None:
        return None
    result = {key: item for key, item in value.items() if key != 'public_observations'}
    result['public_observation_refs'] = [
        {key: record[key] for key in ('id', 'snapshot_id', 'content_digest', 'locator') if key in record}
        for record in value.get('public_observations', [])]
    # Share metadata only with a unique, byte-bound record in this same
    # request. IDs alone cannot resolve current and historical observations.
    supplied={}
    for index,record in enumerate(supplied_observations):
        supplied.setdefault(record.get('id'),[]).append((index,record))
    shared=False
    for index,reference in enumerate(result['public_observation_refs']):
        if not all(key in reference for key in ('id','snapshot_id','content_digest','locator')):continue
        matches=supplied.get(reference.get('id'),[])
        if len(matches)!=1:continue
        target_index,record=matches[0]
        excerpt=record.get('excerpt')
        if (not isinstance(excerpt,str) or hashlib.sha256(excerpt.encode()).hexdigest()!=record.get('content_digest')
                or not all(record.get(k)==v for k,v in reference.items())):continue
        result['public_observation_refs'][index]=dict(id=reference['id'],
            public_observation_record_ref='/public_observations/'+str(target_index))
        shared=True
    if shared:
        result['observation_reference_policy']=(
            'public_observation_record_ref resolves to the exact public_observations array record in this request, '
            'including its snapshot, content_digest and locator. Other entries retain their full identity and location. '
            'A shared reference is not a new observation or a behavior verdict.')
    result['full_feedback_digest'] = canonical_digest(value)
    tasks=summarize_failure_tasks(value)
    if tasks:result['failure_tasks']=tasks
    return result



def _failed_predicate_tasks(value, *, max_records=8, max_bytes=8192):
    """Carry complete registered public failures as facts, not model verdicts.

    Historical projections retain the original trial snapshot. Never infer a
    failed predicate from an error string, partial JSON, or an absent field.
    """
    tasks=[];used=0;omitted=0
    for record in value.get('public_observations', []):
        check=record.get('behavior_check', {})
        if (record.get('snapshot_id')!=value.get('to_snapshot')
                or record.get('evidence_kind')!='PUBLIC_BEHAVIOR'
                or check.get('status')!='FAIL' or not check.get('probe_sha256')):continue
        locator=record.get('locator', {})
        excerpt=record.get('excerpt')
        if (locator.get('truncated') is not False or locator.get('whole_file_read') is not True
                or not isinstance(excerpt,str)
                or hashlib.sha256(excerpt.encode()).hexdigest()!=record.get('content_digest')):continue
        try:body=json.loads(excerpt)
        except (TypeError,ValueError):continue
        if (not isinstance(body,dict) or body.get('status')!='FAIL'
                or not isinstance(body.get('observed'),dict)
                or not isinstance(body.get('statement'),str)
                or check.get('id')!=str(body.get('probe_id'))+':'+str(body.get('id'))):continue
        task=dict(check_id=check['id'],status='FAIL',snapshot_id=record['snapshot_id'],
            scenario=record.get('scenario'),probe_sha256=check['probe_sha256'],
            observation_id=record['id'],content_digest=record['content_digest'],
            locator=dict(locator),expected_statement=body['statement'],observed=body['observed'],
            interpretation='Recorded finite public predicate failure at this snapshot; not a complete obligation verdict or proven root cause')
        cost=len(json.dumps(task,ensure_ascii=False).encode())
        if len(tasks)>=max_records or used+cost>max_bytes:
            omitted+=1;continue
        tasks.append(task);used+=cost
    return tasks,omitted


def summarize_failure_tasks(value):
    """Group the same recorded execution error; never infer a unique cause."""
    groups={}
    for record in value.get('public_observations',[]):
        check=record.get('behavior_check',{})
        if (record.get('snapshot_id')!=value.get('to_snapshot') or record.get('evidence_kind')!='PUBLIC_BEHAVIOR'
                or check.get('status')!='FAIL' or not check.get('probe_sha256')):continue
        try:body=json.loads(record['excerpt'])
        except (TypeError,ValueError,KeyError):continue
        observed=body.get('observed',{}) if isinstance(body,dict) else {}
        if not isinstance(observed,dict):continue
        for name in ('render_error','runtime_error','process_error'):
            message=observed.get(name)
            if not isinstance(message,str) or message.strip() in {'','<nil>','null'}:continue
            key=(record.get('scenario'),check['probe_sha256'],name,message)
            group=groups.setdefault(key,dict(scenario=record.get('scenario'),error_field=name,
                actual_error=message[:1500],error_truncated=len(message)>1500,affected_checks=[],observation_refs=[],
                snapshot_id=value['to_snapshot'],interpretation='Shared recorded execution error; possible cascade, not proven unique root cause'))
            group['affected_checks'].append(check['id']);group['observation_refs'].append(record['id'])
    runtime=[]
    for failure in value.get('observation_diagnostics',[]):
        compile_failure = failure.get('kind') == 'CANDIDATE_BUILD_FAILURE'
        if compile_failure:
            from contractedge.execution.public import has_candidate_build_failure
            if not has_candidate_build_failure(dict(status='EXECUTION_FAILED',
                    snapshot_id=value.get('to_snapshot'), candidate_build_failures=[failure])):
                continue
        if (failure.get('kind') not in {'CANDIDATE_RUNTIME_FAILURE','CANDIDATE_BUILD_FAILURE'}
                or failure.get('snapshot_id')!=value.get('to_snapshot')
                or failure.get('operation')!=('PRODUCTION_COMPILE' if compile_failure else 'PRODUCTION_PROCESS_START')
                or failure.get('status')!='FAILED' or not failure.get('source_locations')
                or not isinstance(failure.get('stderr_excerpt'),str)):continue
        message=failure['stderr_excerpt']
        runtime.append(dict(scenario=failure.get('scenario'),error_field='production_compile' if compile_failure else 'production_startup',
            actual_error=message[:1500],error_truncated=len(message)>1500 or failure.get('stderr_truncated',False),
            source_locations=failure['source_locations'],stderr_sha256=failure.get('stderr_sha256'),
            affected_checks=[],observation_refs=[],snapshot_id=value['to_snapshot'],
            interpretation=('Registered source compilation failed' if compile_failure else 'Registered production startup failed')
                +' at this recorded snapshot; root cause remains unproven; compare its snapshot with current source before use'))
    errors=(runtime+list(groups.values()))[:4]
    conflicts=[c for c in (value.get('helper_preconditions') or {}).get('checks',[]) if c.get('status')=='CONFLICT'][:8]
    predicates,omitted=_failed_predicate_tasks(value)
    if not errors and not conflicts and not predicates and not omitted:return None
    return dict(shared_execution_errors=errors,helper_precondition_conflicts=conflicts,
        failed_predicates=predicates,omitted_failed_predicate_count=omitted,
        instruction=('Inspect the recorded actual values against each public predicate statement, even when execution raised no exception. '
            'For a rejected historical trial, use its own snapshot and failed edits as a counterexample; the restored current source is authoritative. '
            'Trace the concrete values passed through filtering, retention and writeback calls; a retention helper cannot restore items absent from its supplied collection. '
            'Inspect shared errors and conflicting helper preconditions before treating cascaded checks as separate causes. '
            'Do not repeat a known rejected implementation unchanged. Revalidate every protected condition after repair.'))


def historical_trial_prompt_view(value,*,canonical_text=''):
    """Compact a rejected trial; current plan/feedback retain all live obligations.

    Full trial feedback is immutable on disk. This projection changes neither
    candidate selection nor the retained-constraint ledger used by verification.
    """
    fields=('schema_version','case_id','run_id','input_digest','round_id','from_snapshot','to_snapshot',
            'patch_digest','build_state','semantic_outcome','failure_kind','route','route_confidence',
            'helper_preconditions','failure_tasks','operation_failure_comparison','predicate_progress_comparison')
    result={k:value[k] for k in fields if k in value}
    result['full_feedback_digest']=value.get('full_feedback_digest') or canonical_digest(value)
    result['projection']='REJECTED_HISTORICAL_TRIAL; current plan and current feedback are authoritative for live obligations'
    result['rationale']=str(value.get('rationale',''))[:2000]
    result['public_check_results']=[]
    for check in value.get('public_check_results',[]):
        row={k:check[k] for k in ('id','status','submitted_status','evidence_validation') if k in check}
        row['rationale_excerpt']=str(check.get('rationale',''))[:512]
        row['observation_refs']=[e.get('observation_id') for e in check.get('evidence',[]) if e.get('observation_id')]
        result['public_check_results'].append(row)
    result['pending_obligation_refs']=[]
    for obligation in value.get('pending_obligations',[]):
        row={k:obligation[k] for k in ('id','kind','stable_obligation_keys','obligation_refs','requirement_refs','verification_scope','reason_category','verification_gap') if k in obligation}
        statement=obligation.get('statement')
        if statement and statement not in canonical_text:row['statement']=statement
        result['pending_obligation_refs'].append(row)
    return result


def reassess_unchanged_operations(feedback,before,after):
    """Recheck accepted cause when the same supported operation error survives.

    This selects a work stage, not a new claim that the diagnosis is proven
    wrong. Expected exceptions and incomparable invocations cannot trigger it.
    """
    from contractedge.diagnosis.operation_consistency import operation_facts
    if (not before or not after or feedback.get('route')!='EXECUTE'
            or feedback.get('semantic_outcome')!='PUBLIC_REVIEW_FAIL'
            or feedback.get('from_snapshot')!=before.get('snapshot_id')
            or feedback.get('to_snapshot')!=after.get('snapshot_id')):return feedback
    if any(not before.get(k) or before[k]!=after.get(k) for k in ('input_digest','environment_digest')):return feedback
    cited={e['observation_id'] for c in feedback.get('public_check_results',[]) if c.get('status')=='FAIL'
           for e in c.get('evidence',[]) if e.get('observation_id')}
    def table(report):
        records={r['id']:r for r in report.get('observations',[]) if r.get('snapshot_id')==report['snapshot_id']}
        result={}
        for fact in operation_facts(list(records.values())):
            if fact['status']!='FAILED':continue
            record=records[fact['observation_id']]
            key=json.dumps([record.get(k) for k in ('adapter_id','producer_manifest_sha256','source_pointer','scenario','resource_identity')],sort_keys=True)
            result.setdefault(key,[]).append(fact)
        return result
    old,new=table(before),table(after);rows=[]
    for key,values in new.items():
        prior=old.get(key,[])
        if len(values)!=1 or len(prior)!=1:continue
        a,b=prior[0],values[0]
        if b['observation_id'] in cited and a['exception']==b['exception']:
            rows.append(dict(before_observation=a['observation_id'],after_observation=b['observation_id'],
                scenario=b['scenario'],resource_identity=b['resource_identity'],exception=b['exception']))
    if not rows:return feedback
    return {**feedback,'route':'DIAGNOSIS','failure_kind':'DIAGNOSIS','route_confidence':'LOW',
        'routing_policy':'UNCHANGED_OPERATION_REASSESSMENT','next_action':'DIAGNOSIS',
        'operation_failure_comparison':dict(from_snapshot=before['snapshot_id'],to_snapshot=after['snapshot_id'],rows=rows,
            previous_route=feedback['route'],previous_failure_kind=feedback['failure_kind'],
            previous_rationale_sha256=canonical_digest(feedback.get('rationale',''))),
        'rationale':('The same registered operation exception persists after this patch on comparable input/environment. '
            'Recheck the accepted causal path and plan against the actual exception and current source before repeating '
            'the narrow edit. This routing choice is not proof of a unique root cause. '
            'This current routing decision supersedes the previous execution-first instruction; '
            'the original rationale remains in the saved review and its digest is recorded.')}


def reassess_failed_target_predicates(feedback, before, after, plan):
    """Revisit an ineffective accepted intent, without asserting a new cause.

    Only fully comparable, registered target predicates participate. A partial
    improvement or an explicit implementation precondition conflict keeps the
    implementation route. No extra round or model call is allocated here.
    """
    from contractedge.orchestration.candidate_guard import bound_public_check_table
    if (not before or not after or feedback.get('route')!='EXECUTE'
            or feedback.get('semantic_outcome')!='PUBLIC_REVIEW_FAIL'
            or feedback.get('build_state')!='PASSED'
            or before.get('status')!='EXECUTION_PASS' or after.get('status')!='EXECUTION_PASS'
            or feedback.get('from_snapshot')!=before.get('snapshot_id')
            or feedback.get('to_snapshot')!=after.get('snapshot_id')
            or before.get('snapshot_id')==after.get('snapshot_id')):return feedback
    if any(not before.get(k) or before[k]!=after.get(k) for k in ('input_digest','environment_digest')):return feedback
    if any(c.get('status')=='CONFLICT' for c in
           (feedback.get('helper_preconditions') or {}).get('checks',[])):return feedback
    bindings={(ref,p.get('verification_scope',{}).get('scenario'))
              for p in plan.get('postconditions',[]) if p.get('verification_kind')=='PUBLIC_BEHAVIOR'
              for ref in p.get('behavior_check_refs',[])}
    if not bindings or any(not scenario for _,scenario in bindings):return feedback
    def table(report):
        clean={**report,'observations':[r for r in report.get('observations',[])
                                       if r.get('evidence_kind')=='PUBLIC_BEHAVIOR']}
        return {key:state for key,state in bound_public_check_table(clean).items()
                if key[2] and (key[0],key[1]) in bindings}
    old,new=table(before),table(after)
    # Every target binding needs a complete comparison; absence is not no progress.
    if {(k[0],k[1]) for k in old}!=bindings or set(old)!=set(new):return feedback
    if any(s not in {'PASS','FAIL'} for s in [*old.values(),*new.values()]):return feedback
    failed=[key for key in old if old[key]=='FAIL']
    if not failed or any(new[key]=='PASS' for key in failed):return feedback
    cited={e.get('observation_id') for c in feedback.get('public_check_results',[]) if c.get('status')=='FAIL'
           for e in c.get('evidence',[])}
    if not any(r.get('id') in cited and r.get('snapshot_id')==after['snapshot_id']
               and r.get('evidence_kind')=='PUBLIC_BEHAVIOR'
               and r.get('behavior_check',{}).get('status')=='FAIL'
               and (r['behavior_check']['id'],r.get('scenario')) in bindings
               for r in after.get('observations',[])):return feedback
    return {**feedback,'route':'DIAGNOSIS','failure_kind':'DIAGNOSIS','route_confidence':'LOW',
        'routing_policy':'NO_TARGET_PROGRESS_REASSESSMENT','next_action':'DIAGNOSIS',
        'predicate_progress_comparison':dict(from_snapshot=before['snapshot_id'],to_snapshot=after['snapshot_id'],
            previous_route=feedback['route'],previous_failure_kind=feedback.get('failure_kind'),
            previous_rationale_sha256=canonical_digest(feedback.get('rationale','')),
            unchanged_failures=[dict(check_id=k[0],scenario=k[1],probe_sha256=k[2],before=old[k],after=new[k]) for k in failed]),
        'rationale':('No bound target predicate improved on comparable public executions after this patch. '
            'Recheck the accepted cause, production path and plan on the current source before repeating the edit. '
            'This stage selection is not proof of a unique cause; all original failures remain. '
            'This current routing decision supersedes the previous execution-first instruction; '
            'the original rationale remains in the saved review and its digest is recorded.')}


def _constraint_identity(value: Mapping) -> str:
    refs = value.get('requirement_refs', [value['outcome_id']] if value.get('outcome_id') else [])
    keys = value.get('stable_obligation_keys')
    body = {'stable_obligation_keys':sorted(keys)} if keys else {'statement': value['statement'], 'requirement_refs': sorted(refs)}
    return 'retained-' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]


def _merge_verification_boundaries(claim: dict, prior: list[Mapping]) -> None:
    """Bindings are cumulative; conflicting observation boundaries stay unknown."""
    sources = [claim, *prior]
    statements = list(dict.fromkeys(text for p in sources
        for text in p.get('semantic_statements',[p['statement']]) if isinstance(text,str) and text))
    if len(statements)>1:
        claim['semantic_statements']=statements
        claim['statement']='\nAND PRESERVE: '.join(statements)
    coverages = {p.get('check_coverage') for p in sources if p.get('check_coverage')}
    if len(statements)>1 and any(not p.get('behavior_check_refs') or not p.get('check_coverage') for p in sources):
        claim['check_coverage']='SUPPORTING'
    elif coverages:
        claim['check_coverage']=('SUPPORTING' if 'SUPPORTING' in coverages else
            'NECESSARY_ONLY' if 'NECESSARY_ONLY' in coverages else 'ALL_OF')
    for field in ('behavior_check_refs', 'comparison_requirements'):
        unique = {canonical_digest(value): value for p in sources for value in p.get(field, [])}
        if unique:
            claim[field] = list(unique.values())
    kinds = {p.get('verification_kind') for p in sources} & {
        'PUBLIC_BEHAVIOR', 'DECLARED_INPUT_EQUALITY', 'NOT_PUBLICLY_OBSERVABLE'}
    scopes = {canonical_digest(p['verification_scope']): p['verification_scope']
              for p in sources if p.get('verification_scope')}
    if len(kinds) > 1 or len(scopes) > 1 or 'NOT_PUBLICLY_OBSERVABLE' in kinds:
        claim['verification_kind'] = 'NOT_PUBLICLY_OBSERVABLE'
    elif kinds:
        claim['verification_kind'] = next(iter(kinds))
    if len(scopes) == 1:
        claim['verification_scope'] = next(iter(scopes.values()))


def review_plan_with_retained(plan: Mapping, previous_feedback: Mapping | None = None) -> dict:
    """Public review envelope; historical atom IDs never become new Diagnosis refs."""
    result = {**plan, **{kind: [dict(p) for p in plan.get(kind, [])]
              for kind in ('postconditions', 'preserve_constraints', 'open_obligations')}}
    historical = [dict(p) for p in (previous_feedback or plan).get('retained_constraints', [])]
    from contractedge.requirement.lineage import disputed_obligation
    ledger=plan.get('obligation_lineage',{})
    disputed=[p for p in historical if disputed_obligation(p,ledger)]
    historical=[p for p in historical if not disputed_obligation(p,ledger)]
    for item in disputed:
        identifier='disputed-'+item['retention_id']
        if not any(p.get('id')==identifier for p in result['open_obligations']):
            result['open_obligations'].append(dict(id=identifier,statement=item['statement'],
                requirement_refs=item.get('requirement_refs',[]),stable_obligation_keys=item.get('stable_obligation_keys',[]),
                reason='Historical derived interpretation disputed; reassess original requirement, do not enforce as preservation',
                verification_kind='UNKNOWN',interpretation_disputed=True))
    covered = {_constraint_identity(p) for p in [*plan.get('postconditions', []), *result['preserve_constraints']]}
    for candidate in historical:
        key = _constraint_identity(candidate)
        if key not in covered:
            result['preserve_constraints'].append({'id': key, 'statement': candidate['statement'],
                'requirement_refs': list(candidate.get('requirement_refs', [])),
                'historical_obligation_refs': list(candidate.get('historical_obligation_refs', [])),
                'verification_kind': candidate.get('verification_kind', 'UNKNOWN'),
                'verification_scope': candidate.get('verification_scope'),
                'behavior_check_refs': candidate.get('behavior_check_refs', []),
                'semantic_statements':candidate.get('semantic_statements', [candidate['statement']]),
                'stable_obligation_keys':candidate.get('stable_obligation_keys', []),
                'check_coverage':candidate.get('check_coverage'), 'coverage_rationale':candidate.get('coverage_rationale'),
                'comparison_requirements': candidate.get('comparison_requirements', []),
                'planned_check': candidate.get('planned_check'), 'check_scope': candidate.get('check_scope'),
                'source_snapshot': candidate['source_snapshot'], 'support_basis': 'HISTORICAL_PUBLIC_MODEL_REVIEW',
                'interpretation': 'Requires fresh public review; historical evidence is not rebound.'})
            covered.add(key)
    result['retained_constraints'] = historical
    # Keep unresolved historical claims as review obligations, without turning
    # old atom IDs into references in the new Diagnosis.
    covered = {_constraint_identity(p) for p in [*result.get('postconditions', []),
               *result['preserve_constraints'], *result['open_obligations']]}
    for pending in (previous_feedback or {}).get('pending_obligations', []):
        key = _constraint_identity(pending)
        if key not in covered:
            result['open_obligations'].append({'id': 'historical-' + key,
                'statement': pending['statement'],
                'requirement_refs': list(pending.get('requirement_refs',
                    [pending['outcome_id']] if pending.get('outcome_id') else [])),
                'historical_obligation_refs': list(pending.get('obligation_refs', pending.get('historical_obligation_refs', []))),
                'verification_kind': pending.get('verification_kind', 'UNKNOWN'),
                'verification_scope': pending.get('verification_scope'),
                'behavior_check_refs': pending.get('behavior_check_refs', []),
                'semantic_statements':pending.get('semantic_statements', [pending['statement']]),
                'stable_obligation_keys':pending.get('stable_obligation_keys', []),
                'check_coverage':pending.get('check_coverage'), 'coverage_rationale':pending.get('coverage_rationale'),
                'comparison_requirements': pending.get('comparison_requirements', []),
                'planned_check': pending.get('planned_check'), 'check_scope': pending.get('check_scope'),
                'source_snapshot': previous_feedback['to_snapshot'], 'historical_check_id': pending['id'],
                'reason': 'Prior unresolved obligation; requires fresh public evidence, not a new causal claim.'})
            covered.add(key)
    # The same obligation cannot shed its frozen public observation boundary
    # merely by appearing under a new check ID after rediagnosis.
    prior_by_identity = {}
    for prior in [*historical, *(previous_feedback or {}).get('pending_obligations', [])]:
        prior_by_identity.setdefault(_constraint_identity(prior), []).append(prior)
    for kind in ('postconditions', 'preserve_constraints', 'open_obligations'):
        for claim in result[kind]:
            _merge_verification_boundaries(claim, prior_by_identity.get(_constraint_identity(claim), []))
    bounded = {_constraint_identity(p) for p in [*historical,
               *(previous_feedback or {}).get('pending_obligations', [])]
               if p.get('verification_kind') == 'NOT_PUBLICLY_OBSERVABLE'}
    for kind in ('postconditions', 'preserve_constraints', 'open_obligations'):
        for claim in result[kind]:
            if _constraint_identity(claim) in bounded:
                claim['verification_kind'] = 'NOT_PUBLICLY_OBSERVABLE'
    return result


def validate_feedback(value: Mapping, *, case_id: str, input_digest: str,
                      to_snapshot: str | None = None) -> dict:
    _check_schema(value, 'ce.execution_feedback.v1')
    if value['case_id'] != case_id or value['input_digest'] != input_digest:
        raise ContractError('FEEDBACK_IDENTITY_MISMATCH', '', 'Feedback belongs to another public input')
    if to_snapshot is not None and value['to_snapshot'] != to_snapshot:
        raise ContractError('FEEDBACK_SNAPSHOT_MISMATCH', 'to_snapshot', 'Feedback is stale for the current tree')
    ids = set()
    for record in value['public_observations']:
        if record['id'] in ids or record['snapshot_id'] != value['to_snapshot']:
            raise ContractError('FEEDBACK_EVIDENCE_MISMATCH', 'public_observations', 'Evidence IDs/snapshots must bind current feedback')
        ids.add(record['id'])
        if hashlib.sha256(record['excerpt'].encode()).hexdigest() != record['content_digest']:
            raise ContractError('FEEDBACK_EVIDENCE_MISMATCH', 'content_digest', 'Observation digest must match its exact copied text')
    if value['semantic_outcome'] == 'PUBLIC_REVIEW_PASS' and (value['build_state'] != 'PASSED' or value['pending_obligations']):
        raise ContractError('FEEDBACK_OUTCOME_CONFLICT', 'semantic_outcome', 'Pending/failed execution cannot claim public completion')
    bounded_retry = (value.get('routing_policy')=='BOUNDED_ACCEPTED_INTENT_RETRY'
        and value['failure_kind']=='UNKNOWN' and value['route']=='EXECUTE' and value['round_id']==0
        and value['from_snapshot']!=value['to_snapshot'] and value['build_state']=='PASSED'
        and value['semantic_outcome']=='PUBLIC_REVIEW_FAIL'
        and any(c.get('status')=='FAIL' and any(
            o['id']==e.get('observation_id') and o.get('evidence_kind')=='PUBLIC_BEHAVIOR'
            and o.get('behavior_check',{}).get('status')=='FAIL'
            for e in c.get('evidence',[]) for o in value['public_observations'])
            for c in value['public_check_results']))
    if value['failure_kind'] in {'ENVIRONMENT', 'UNKNOWN', 'NONE'} and value['route'] != 'STOP' and not bounded_retry:
        raise ContractError('FEEDBACK_ROUTE_UNSUPPORTED', 'route', 'Do not guess a repair route without supported failure attribution')
    link_ids = set()
    for link in value.get('coordination_links', []):
        if (link.get('check_id') in link_ids or link.get('observation_snapshot') != value['to_snapshot']
                or not set(link.get('observation_refs', [])) <= ids):
            raise ContractError('FEEDBACK_LINK_MISMATCH', 'coordination_links', 'Linked observations must belong to this feedback snapshot')
        link_ids.add(link.get('check_id'))
    return dict(value)


def make_feedback(*, case_id: str, run_id: str, input_digest: str, round_id: int,
                  from_snapshot: str, to_snapshot: str, patch_digest: str,
                  execution_status: str, observations: list[Mapping],
                  plan: Mapping, review: Mapping | None = None,
                  diagnosis: Mapping | None = None, observation_diagnostics=()) -> dict:
    review = dict(review or {})
    checks = [dict(c) for c in review.get('checks', [])]
    public_by_id = {o['id']: o for o in observations}
    expected_ids = {p['id'] for p in plan.get('postconditions', [])}
    expected_ids.update('preserve:' + p['id'] for p in plan.get('preserve_constraints', []))
    expected_ids.update('open:' + p['id'] for p in plan.get('open_obligations', []))
    obligations = {prefix + p['id']: p for kind, prefix in [
        ('postconditions', ''), ('preserve_constraints', 'preserve:'), ('open_obligations', 'open:')]
        for p in plan.get(kind, [])}
    unobservable_ids = {prefix + p['id'] for kind, prefix in [
        ('postconditions', ''), ('preserve_constraints', 'preserve:'), ('open_obligations', 'open:')]
        for p in plan.get(kind, []) if p.get('verification_kind') == 'NOT_PUBLICLY_OBSERVABLE'}
    # PASS and FAIL both require current evidence. Keep the submitted verdict,
    # but never retain/route on a stale, fabricated or unsupported review claim.
    for check in checks:
        if check.get('status') not in {'PASS', 'FAIL'}:
            continue
        evidence = check.get('evidence', [])
        verified = (execution_status == 'EXECUTION_PASS' and check.get('id') in expected_ids
                    and check.get('id') not in unobservable_ids and bool(evidence))
        for citation in evidence:
            record = public_by_id.get(citation.get('observation_id'))
            quote = citation.get('quote')
            verified = verified and bool(record and record.get('snapshot_id') == to_snapshot
                and isinstance(quote, str) and quote and quote in record.get('excerpt', ''))
        capability_error = support_error(obligations.get(check.get('id'), {}), check, observations,
                                         plan.get('declared_artifacts', []), snapshot_id=to_snapshot,
                                         capabilities=plan.get('public_execution_capabilities')) if verified else None
        if not verified or capability_error:
            check.update(submitted_status=check['status'], status='UNKNOWN',
                         evidence_validation=capability_error or 'REJECTED_NO_FRESH_SUPPORT')
    passed_ids = {c['id'] for c in checks if c.get('status') == 'PASS'}
    pending = [{'kind': 'POSTCONDITION', **dict(p)}
               for p in plan.get('postconditions', []) if p['id'] not in passed_ids]
    pending += [{'kind': 'PRESERVE', **dict(p), 'id': 'preserve:' + str(p['id'])}
                for p in plan.get('preserve_constraints', [])
                if 'preserve:' + str(p['id']) not in passed_ids]
    pending += [{'kind': 'OPEN', **dict(p), 'id': 'open:' + str(p['id'])}
                for p in plan.get('open_obligations', []) if 'open:' + str(p['id']) not in passed_ids]
    # A previously accepted candidate remains a preservation obligation even if
    # it is currently FAIL/UNKNOWN. Otherwise one failed round could erase it.
    retained_by_id = {_constraint_identity(p): dict(p) for p in plan.get('retained_constraints', [])}
    entries = [(p['id'], p) for p in plan.get('postconditions', [])]
    entries += [('preserve:' + p['id'], p) for p in plan.get('preserve_constraints', [])]
    entries += [('open:' + p['id'], p) for p in plan.get('open_obligations', [])]
    for check_id, p in entries:
        key = _constraint_identity(p)
        if key in retained_by_id:
            _merge_verification_boundaries(retained_by_id[key], [p])
        if execution_status == 'EXECUTION_PASS' and check_id in passed_ids and key not in retained_by_id:
            retained_by_id[key] = {'retention_id': key, 'postcondition_id': check_id,
                'statement': p['statement'],
                'requirement_refs': list(p.get('requirement_refs', [p['outcome_id']] if p.get('outcome_id') else [])),
                'historical_obligation_refs': list(p.get('obligation_refs', p.get('historical_obligation_refs', []))),
                'source_snapshot': to_snapshot, 'support_basis': 'PUBLIC_MODEL_REVIEW',
                'verification_kind': p.get('verification_kind', 'UNKNOWN'),
                'verification_scope': p.get('verification_scope'),
                'behavior_check_refs': p.get('behavior_check_refs', []),
                'semantic_statements':p.get('semantic_statements', [p['statement']]),
                'stable_obligation_keys':p.get('stable_obligation_keys', []),
                'check_coverage':p.get('check_coverage'), 'coverage_rationale':p.get('coverage_rationale'),
                'comparison_requirements': p.get('comparison_requirements', []),
                'planned_check': p.get('planned_check'), 'check_scope': p.get('check_scope'),
                'interpretation': 'Preserve candidate; requires fresh public review after later edits.'}
    statuses = {c['id']: ('UNKNOWN' if c.get('status') == 'PASS' and c['id'] not in passed_ids
                         else c.get('status', 'UNKNOWN')) for c in checks}
    for key, item in retained_by_id.items():
        matched = [statuses.get(cid, 'UNKNOWN') for cid, p in entries if _constraint_identity(p) == key]
        status = ('FAIL' if 'FAIL' in matched else 'PASS' if matched and all(s == 'PASS' for s in matched) else 'UNKNOWN')
        if execution_status != 'EXECUTION_PASS':
            status = 'UNKNOWN'
        item.update(latest_public_status=status, latest_review_snapshot=to_snapshot)
        if status == 'PASS':
            item['source_snapshot'] = to_snapshot
    retained = list(retained_by_id.values())
    build = 'PASSED' if execution_status == 'EXECUTION_PASS' else (
        'INFRA_ERROR' if execution_status in {'INFRA_ERROR', 'INVALID_OBSERVATION'} else
        'BUDGET_EXCEEDED' if execution_status == 'BUDGET_EXCEEDED' else 'FAILED')
    semantic, kind, route, confidence = 'UNKNOWN', 'UNKNOWN', 'STOP', 'LOW'
    rationale = 'Public raw execution does not establish task correctness; retain open checks.'
    if build == 'INFRA_ERROR':
        kind, confidence = 'ENVIRONMENT', 'HIGH'
        rationale = 'Public execution infrastructure failed; no diagnosis blame or automatic repair.'
    elif build == 'FAILED':
        kind, route, confidence = 'EXECUTION', 'EXECUTE', 'MEDIUM'
        rationale = 'Public candidate execution failed; revise concrete implementation before changing diagnosis.'
    elif build == 'PASSED' and review.get('decision') == 'PASS' and checks and not pending and all(c['status'] == 'PASS' for c in checks):
        semantic, kind, confidence = 'PUBLIC_REVIEW_PASS', 'NONE', 'MEDIUM'
        rationale = 'All planned public checks accepted by evidence-bound model review; hidden evaluation remains independent.'
    elif build == 'PASSED' and review.get('decision') == 'FAIL' and any(c['status'] == 'FAIL' for c in checks):
        semantic = 'PUBLIC_REVIEW_FAIL'
        # The reviewer is an uncertain routing signal, not a proof of causality.
        proposed = review.get('failure_kind', 'UNKNOWN')
        if proposed in {'PLAN', 'DIAGNOSIS', 'EXECUTION'} and any(c.get('status') == 'FAIL' for c in checks):
            kind = proposed
            route = {'PLAN': 'CHANGESET', 'DIAGNOSIS': 'DIAGNOSIS', 'EXECUTION': 'EXECUTE'}[kind]
            confidence = 'LOW'
        rationale = str(review.get('rationale') or rationale)
        # A failed declared behavior does not establish that accepted intent was
        # wrong. First try fulfilling that intent on the changed candidate,
        # within the existing semantic-round budget. Retain the reviewer's
        # uncertain attribution separately from this operational route choice.
        implemented_claims = {p['id'] for p in plan.get('postconditions', [])}
        implemented_claims.update('preserve:' + p['id'] for p in plan.get('preserve_constraints', []))
        bound_failure = any(c.get('status') == 'FAIL' and c['id'] in implemented_claims
            and obligations.get(c['id'], {}).get('behavior_check_refs') for c in checks)
        if ((kind == 'PLAN' or kind == 'UNKNOWN' and (review.get('method')=='BOUND_CHECK_AGGREGATION' or any(c.get('status')=='FAIL' and c.get('decision_method')=='BOUND_CHECK_AGGREGATION' for c in checks))) and round_id == 0 and from_snapshot != to_snapshot
                and plan.get('status') in {'READY', 'READY_PARTIAL'} and plan.get('changes') and bound_failure):
            route = 'EXECUTE'
            rationale = ('Bounded execution-first routing preference, not proof of causal attribution: '
                'fresh registered behavior checks show accepted postconditions/preservation remain unmet after edits. '
                'Use the existing next round to implement the same accepted intent; do not rediagnose solely from '
                'a failed postcondition. Original attribution remains '+kind+'/LOW. ' + rationale)
    payload = {'schema_version': 'ce.execution_feedback.v1', 'case_id': case_id, 'run_id': run_id,
               'input_digest': input_digest, 'round_id': round_id, 'from_snapshot': from_snapshot,
               'to_snapshot': to_snapshot, 'patch_digest': patch_digest, 'build_state': build,
               'semantic_outcome': semantic, 'public_observations': [dict(o) for o in observations],
               'public_check_results': checks, 'retained_constraints': retained,
               'pending_obligations': pending, 'failure_kind': kind, 'route': route,
               'route_confidence': confidence, 'rationale': rationale,
               'hidden_evaluation': 'NOT_AVAILABLE_TO_METHOD'}
    if route=='EXECUTE' and kind=='UNKNOWN':
        payload['routing_policy']='BOUNDED_ACCEPTED_INTENT_RETRY'
    changes = plan.get('changes', plan.get('operations', []))
    violations = {v['id']: v for v in (diagnosis or {}).get('violations', [])}
    payload['coordination_links'] = []
    for check_id, obligation in entries:
        change_ids = [c.get('id', c.get('change_id')) for c in changes if check_id in c.get('postcondition_refs', [])]
        if check_id.startswith('preserve:'):
            impacts = [r for r in plan.get('preservation_impact', []) if r['preserve_id'] == obligation['id']]
            for impact in impacts:
                change_ids.extend(impact['change_refs'] if impact['scope']=='CHANGES'
                                  else [c.get('id',c.get('change_id')) for c in changes])
            change_ids = sorted(set(change_ids))
        violation_ids = sorted({v for c in changes if c.get('id', c.get('change_id')) in change_ids
                                for v in c.get('violation_refs', [])})
        payload['coordination_links'].append({'check_id': check_id,
            'requirement_refs': list(obligation.get('requirement_refs', [obligation['outcome_id']] if obligation.get('outcome_id') else [])),
            'obligation_refs': list(obligation.get('obligation_refs', [])),
            'historical_obligation_refs': list(obligation.get('historical_obligation_refs', [])),
            'relation_refs': sorted(set(obligation.get('relation_refs', [])) |
                {violations[v]['relation_id'] for v in violation_ids if v in violations}),
            'violation_refs': violation_ids, 'change_refs': change_ids,
            'diagnosis_snapshot': (diagnosis or {}).get('snapshot_id'), 'observation_snapshot': to_snapshot,
            'observation_refs': [e['observation_id'] for c in checks if c['id'] == check_id for e in c.get('evidence', [])
                                 if e.get('observation_id') in public_by_id],
            'status': statuses.get(check_id, 'UNKNOWN'),
            'interpretation': 'Traceable association, not independent proof of causal attribution'})
    runtime_errors = [dict(r) for r in observation_diagnostics if r.get('kind')=='CANDIDATE_RUNTIME_FAILURE']
    build_errors = [dict(r) for r in observation_diagnostics if r.get('kind')=='CANDIDATE_BUILD_FAILURE']
    adapter_errors = [dict(r) for r in observation_diagnostics
        if r.get('status') not in {'COMPLETE','NOT_APPLICABLE'}
        and r.get('kind') not in {'CANDIDATE_RUNTIME_FAILURE','CANDIDATE_BUILD_FAILURE'}]
    for pending_item in pending:
        check = next((c for c in checks if c['id']==pending_item['id']), {})
        pending_item['reason_category'] = ('OBSERVED_FAILURE' if check.get('status')=='FAIL' else
            'EXECUTION_BLOCKED' if build_errors else
            'OBSERVATION_ADAPTER_ERROR' if adapter_errors else
            'NOT_PUBLICLY_OBSERVABLE' if pending_item.get('verification_kind') in {'NOT_PUBLICLY_OBSERVABLE','BUILD_ONLY'} else
            'CHECK_COVERAGE_INSUFFICIENT' if check.get('evidence_validation') else 'EVIDENCE_INSUFFICIENT')
        pending_item['verification_gap']={
            'predicate':pending_item.get('statement'),
            'scenario':(pending_item.get('verification_scope') or {}).get('scenario'),
            'required_check_refs':pending_item.get('behavior_check_refs',[]),
            'coverage':pending_item.get('check_coverage','UNDECLARED'),
            'remaining_scope':'Original requirement beyond these finite checks remains unverified' if pending_item.get('check_coverage')=='NECESSARY_ONLY' else None,
            'action':'REPAIR_IMPLEMENTATION' if check.get('status')=='FAIL' else
                'REPAIR_IMPLEMENTATION' if build_errors else
                'MAINTAIN_ADAPTER' if adapter_errors else
                'STOP_UNOBSERVABLE' if pending_item.get('verification_kind') in {'NOT_PUBLICLY_OBSERVABLE','BUILD_ONLY'} else 'OBTAIN_SUFFICIENT_PUBLIC_EVIDENCE'}
    payload['observation_diagnostics'] = adapter_errors+runtime_errors+build_errors
    payload['next_action'] = ('REPAIR_OBSERVATION_ADAPTER' if adapter_errors and not any(c.get('status')=='FAIL' for c in checks)
        else route if route!='STOP' else 'STOP_UNOBSERVABLE' if pending and all(p.get('reason_category')=='NOT_PUBLICLY_OBSERVABLE' for p in pending)
        else 'STOP_UNCERTAIN' if pending else 'COMPLETE')
    if plan.get('public_execution_capabilities') is not None:
        payload['public_execution_capabilities'] = dict(plan['public_execution_capabilities'])
    if plan.get('obligation_lineage') is not None:
        from copy import deepcopy
        from contractedge.requirement.lineage import project_obligation_coverage
        payload['obligation_lineage']=deepcopy(plan['obligation_lineage'])
        lineage_checks=[]
        for cid, obligation in entries:
            for key in obligation.get('stable_obligation_keys', []):
                lineage_checks.append(dict(obligation_id=key, status=statuses.get(cid,'UNKNOWN'),
                    snapshot_id=to_snapshot,input_digest=input_digest,evidence_validated=True,
                    covered_statements=obligation.get('semantic_statements',[obligation['statement']]),
                    check_coverage=obligation.get('check_coverage','SUPPORTING')))
        payload['obligation_coverage']=project_obligation_coverage(payload['obligation_lineage'],lineage_checks,
            dict(snapshot_id=to_snapshot,input_digest=input_digest))
    return validate_feedback(payload, case_id=case_id, input_digest=input_digest, to_snapshot=to_snapshot)
