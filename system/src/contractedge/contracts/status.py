"""One factual projection for repair, recovery and offline evaluation.

Execution describes verified execution of the selected repaired candidate.
Whole-attempt method failures stay independent and never grant scoring. Broken
observations and patch differences cannot prove that a repair was executed.
"""
from __future__ import annotations

from typing import Mapping


_METHOD_ERRORS = frozenset({
    'INVALID_OUTPUT', 'FORMAT_INVALID', 'INVALID_PLAN', 'PLAN_INVALID',
    'BUDGET_EXHAUSTED', 'BUDGET_EXCEEDED', 'INFRASTRUCTURE_ERROR', 'INFRA_ERROR',
    'ENVIRONMENT_ERROR', 'RESPONSE_UNKNOWN', 'UNKNOWN_RESPONSE', 'REJECTED',
    'INTERRUPTED', 'TIMEOUT', 'OUTPUT_LIMIT', 'EXECUTION_FAILED', 'INVALID_OBSERVATION',
    'EVIDENCE_DELIVERY_INCOMPLETE', 'DELIVERY_INCOMPLETE',
    'CANDIDATE_REGRESSION_REJECTED', 'CANDIDATE_COMPARISON_UNCERTAIN',
})
_EXECUTION_ERRORS = frozenset({'EXECUTION_FAILED', 'TIMEOUT', 'OUTPUT_LIMIT',
    'INFRA_ERROR', 'INFRASTRUCTURE_ERROR', 'ENVIRONMENT_ERROR', 'INVALID_OBSERVATION', 'OBSERVATION_ERROR'})
_FRONT_ONLY = frozenset({'READY', 'READY_PARTIAL', 'PARTIAL_READY', 'NO_SUPPORTED_VIOLATION',
                        'UNDER_SPECIFIED', 'INVALID_INPUT', 'EVIDENCE_GAP'})


def repair_execution_facts(chains, *, reference, observation_complete):
    """Project existing verified trial events; keep refs, never source/log bodies.

    Each row owns one attempt. Its selection ref permits an earlier observed
    incumbent to remain verifiable after a later trial is rolled back.
    """
    facts = []
    for events in chains:
        if not events:
            continue
        begin = events[0]
        if begin.get('kind') != 'BEGIN_COMMITTED':
            raise ValueError('Repair completion requires the existing committed trial beginning')
        row = {'trial_id': begin['trial_id'], 'begin_ref': reference(begin),
               'parent_snapshot': begin['parent_snapshot']}
        for event in events:
            kind = event['kind']
            if kind == 'APPLIED':
                result = event['execution']
                row['execution'] = {key: result[key] for key in ('status', 'from_snapshot', 'to_snapshot') if key in result}
                row['execution']['event_ref'] = reference(event)
            elif kind == 'OBSERVED':
                observation = event['observation']
                row['observation'] = {key: observation[key] for key in ('status', 'snapshot_id') if key in observation}
                row['observation'].update(complete=observation_complete(observation), event_ref=reference(event))
            elif kind == 'OBSERVATION_REJECTED':
                row['observation'] = {'status': event.get('observation_status', 'INVALID_OBSERVATION'),
                                      'snapshot_id': event['snapshot_id'],
                                      'complete': False, 'event_ref': reference(event)}
            elif kind == 'SELECTION_INTENT':
                row['selection'] = {'snapshot_id': event['selected_snapshot'], 'event_ref': reference(event)}
            elif kind in {'ROLLBACK_COMPLETED', 'ROLLBACK_FAILED'}:
                row['rollback'] = {'status': kind, 'snapshot_id': event['snapshot_id'], 'event_ref': reference(event)}
        facts.append(row)
    return facts


def project_statuses(run_trace: Mapping) -> dict[str, str]:
    """Pure projection: no filesystem, model calls, patch or status-name heuristics."""
    status = str(run_trace.get('status') or 'NOT_RUN')
    failures = [run_trace.get('primary_failure') or {}, *(run_trace.get('failure_records') or [])]
    unknown = (status in {'RESPONSE_UNKNOWN', 'UNKNOWN_RESPONSE'} or any(
        isinstance(f, Mapping) and f.get('dispatch_state') == 'response_unknown' for f in failures))
    infrastructure = any(isinstance(f, Mapping) and f.get('failure_class') == 'infrastructure' for f in failures)
    facts = run_trace.get('repair_execution_facts')
    selected = run_trace.get('final_snapshot_id')
    model_error = any(isinstance(f, Mapping) and f.get('failure_class') == 'model_format'
                      and f.get('snapshot_id') in {None, selected} for f in failures)
    rows = facts if isinstance(facts, list) else []
    completed = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        execution, observation, selection = (row.get(key) or {} for key in ('execution', 'observation', 'selection'))
        if (all(isinstance(item, Mapping) for item in (execution, observation, selection))
                and row.get('begin_ref') and isinstance(selected, str) and selected and not row.get('rollback')
                and execution.get('status') == 'APPLIED' and execution.get('to_snapshot') == selected
                and execution.get('from_snapshot') == row.get('parent_snapshot')
                and observation.get('snapshot_id') == selection.get('snapshot_id') == selected
                and observation.get('status') == 'EXECUTION_PASS' and observation.get('complete') is True
                and all(item.get('event_ref') for item in (execution, observation, selection))):
            completed.append(row)
    if facts == []:
        execution_status = 'NOT_RUN'
    elif completed:
        execution_status = 'COMPLETE'
    elif unknown:
        execution_status = 'UNKNOWN'
    elif rows:
        failed = any(isinstance(row, Mapping) and (
            isinstance(row.get('observation'), Mapping) and row['observation'].get('status') in _EXECUTION_ERRORS
            or isinstance(row.get('rollback'), Mapping) and row['rollback'].get('status') == 'ROLLBACK_FAILED') for row in rows)
        execution_status = 'ERROR' if failed else 'INCOMPLETE'
    elif status == 'NOT_RUN' or status in _FRONT_ONLY:
        execution_status = 'NOT_RUN'
    else:
        execution_status = 'UNKNOWN'

    feedback = run_trace.get('last_feedback') or run_trace.get('feedback') or {}
    semantic = run_trace.get('public_semantic_status', feedback.get('semantic_outcome'))
    bound_feedback = (completed and feedback.get('to_snapshot') == selected
                      and feedback.get('build_state') == 'PASSED'
                      and semantic == feedback.get('semantic_outcome'))
    if bound_feedback and semantic in {'PUBLIC_REVIEW_PASS', 'PUBLIC_REVIEW_FAIL'}:
        public = 'PASS' if semantic == 'PUBLIC_REVIEW_PASS' else 'FAIL'
    elif facts is not None or status not in _FRONT_ONLY | {'NOT_RUN'}:
        public = 'UNKNOWN'
    else:
        public = 'NOT_APPLICABLE'

    method = {'FORMAT_INVALID': 'INVALID_OUTPUT', 'PLAN_INVALID': 'INVALID_PLAN'}.get(status, status)
    if status not in _METHOD_ERRORS:
        if unknown:
            method = 'RESPONSE_UNKNOWN'
        elif infrastructure:
            method = 'INFRASTRUCTURE_ERROR'
        elif model_error:
            method = 'INVALID_OUTPUT'
        elif public == 'UNKNOWN' and status in {'PUBLIC_EXECUTION_COMPLETE', 'PUBLIC_REVIEW_COMPLETE', 'COMPLETE'}:
            method = 'SEMANTIC_UNKNOWN'
    return {'method_status': method, 'execution_status': execution_status, 'public_status': public,
            'independent_score_status': str(run_trace.get('independent_score_status') or 'NOT_RUN')}


def scoring_exclusion(run_trace: Mapping, projection=None, *, independent_repair=False):
    """One pre-scoring boundary; excluded cases remain in the denominator.

    Front-only READY diagnosis is eligible. Independent repair scoring needs
    verified selected execution without an attempt error; public FAIL/UNKNOWN
    remain method signals, not private score gates.
    """
    projection = project_statuses(run_trace) if projection is None else projection
    unscored = {
        "INFRASTRUCTURE_ERROR": ("INFRASTRUCTURE", "NOT_SCORED_INFRASTRUCTURE"),
        "INFRA_ERROR": ("INFRASTRUCTURE", "NOT_SCORED_INFRASTRUCTURE"),
        "ENVIRONMENT_ERROR": ("INFRASTRUCTURE", "NOT_SCORED_INFRASTRUCTURE"),
        "REJECTED": ("INFRASTRUCTURE", "NOT_SCORED_INFRASTRUCTURE"),
        "INTERRUPTED": ("INTERRUPTED", "NOT_SCORED_INTERRUPTED"),
        "RESPONSE_UNKNOWN": ("RESPONSE_UNKNOWN", "NOT_SCORED_RESPONSE_UNKNOWN"),
        "UNKNOWN_RESPONSE": ("RESPONSE_UNKNOWN", "NOT_SCORED_RESPONSE_UNKNOWN"),
        "BUDGET_EXHAUSTED": ("BUDGET", "NOT_SCORED_BUDGET"),
        "BUDGET_EXCEEDED": ("BUDGET", "NOT_SCORED_BUDGET"),
        "EXECUTION_FAILED": ("EXECUTION", "NOT_SCORED_EXECUTION"),
        "TIMEOUT": ("EXECUTION", "NOT_SCORED_EXECUTION"),
        "OUTPUT_LIMIT": ("EXECUTION", "NOT_SCORED_EXECUTION"),
        "INVALID_OUTPUT": ("METHOD_OUTPUT", "METHOD_FAILURE"),
        "INVALID_PLAN": ("METHOD_OUTPUT", "METHOD_FAILURE"),
        "FORMAT_INVALID": ("METHOD_OUTPUT", "METHOD_FAILURE"),
        "PLAN_INVALID": ("METHOD_OUTPUT", "METHOD_FAILURE"),
        "EVIDENCE_DELIVERY_INCOMPLETE": ("EVIDENCE", "NOT_SCORED_EVIDENCE"),
        "DELIVERY_INCOMPLETE": ("EVIDENCE", "NOT_SCORED_EVIDENCE"),
        "INVALID_OBSERVATION": ("OBSERVATION", "NOT_SCORED_OBSERVATION"),
        "CANDIDATE_REGRESSION_REJECTED": ("CANDIDATE", "NOT_SCORED_CANDIDATE"),
        "CANDIDATE_COMPARISON_UNCERTAIN": ("CANDIDATE", "NOT_SCORED_CANDIDATE"),
        "RECOVERED_INCUMBENT": ("INTERRUPTED", "NOT_SCORED_INTERRUPTED"),
    }
    method_status = projection['method_status']
    if method_status in unscored:
        return unscored[method_status]
    if projection['execution_status'] == 'ERROR':
        return ('EXECUTION', 'NOT_SCORED_EXECUTION')
    if independent_repair:
        return None if projection['execution_status'] == 'COMPLETE' else ('EXECUTION', 'NOT_SCORED_EXECUTION')
    if (method_status in {'SEMANTIC_UNKNOWN', 'EVIDENCE_GAP', 'DELIVERY_INCOMPLETE',
                          'ROUND_LIMIT', 'OBSERVATION_INSUFFICIENT'}
            or projection['public_status'] == 'UNKNOWN'):
        return ('SEMANTIC_UNKNOWN', 'SEMANTIC_UNKNOWN')
    if method_status == 'NOT_RUN':
        return ('NOT_RUN', 'NOT_RUN')
    return None
