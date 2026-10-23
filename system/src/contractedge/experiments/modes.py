"""Real method routes. Absent semantic mechanisms are never reconstructed.

Only presemantic public material enters DirectPlan/Direct. Relation-only reuses
the actual diagnosis generator and stops before a ChangeSet request is built.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from jsonschema import Draft202012Validator

from contractedge.contracts.repair_intent import (SemanticChangeSetIntent,
    DirectPlanIntent, RelationOnlyIntent, RequirementOnlyIntent)
from contractedge.contracts.validation import canonical_digest
from contractedge.contracts.permissions import target_permission_error
from contractedge.execution.literal import PlanRejected, safe_target
from contractedge.model.provider import ModelRequest
from contractedge.model.admission import quote_request, context_fits, complete_quoted
from contractedge.source.snapshot import compute_snapshot
from contractedge.orchestration.recorder import preserve_error

MODES = ('full', 'without_relation', 'without_changeset', 'direct')
INTENTS = dict(zip(MODES, ('SEMANTIC_CHANGESET', 'DIRECT_PLAN', 'RELATION_ONLY', 'REQUIREMENT_ONLY')))


def method_mode(config):
    value = config.get('method_mode', 'full')
    if value not in MODES:
        raise ValueError('method_mode must be one of ' + ', '.join(MODES))
    return value


def public_feedback(value):
    """Project raw public facts; never copy semantic links or model attribution."""
    if value is None:
        return None
    records = []
    for record in value.get('public_observations', []):
        import hashlib
        if (record.get('snapshot_id') != value.get('to_snapshot')
                or hashlib.sha256(record.get('excerpt', '').encode()).hexdigest() != record.get('content_digest')):
            raise PlanRejected('Mode feedback has stale or altered public observation')
        records.append(dict(record))
    return {'schema_version': 'ce.mode_public_feedback.v1',
        'from_snapshot': value['from_snapshot'], 'to_snapshot': value['to_snapshot'],
        'build_state': value['build_state'], 'public_observations': records,
        'semantic_attribution': 'UNKNOWN', 'operation_plan': 'NOT_APPLICABLE'}


def source_input(method_input, source, policy, observations=(), feedback=None):
    snapshot = compute_snapshot(source)
    files = []
    for path in sorted(snapshot.file_digests):
        if target_permission_error(path, policy) is None:
            text = safe_target(source, path).read_text(encoding='utf-8')
            files.append({'path': path, 'content_digest': snapshot.file_digests[path], 'text': text})
    if sum(len(row['text'].encode()) for row in files) > 4 * 1024 * 1024:
        raise PlanRejected('Presemantic source inventory exceeds byte limit')
    return {'requirement': method_input.requirement_text,
        'snapshot_id': snapshot.snapshot_id, 'input_digest': method_input.input_digest,
        'declared_artifacts': list(method_input.declared_artifacts),
        'permitted_source_inventory': files, 'public_observations': list(observations),
        'public_feedback': public_feedback(feedback),
        'source_origin': 'PRESEMANTIC_PUBLIC_REQUIREMENT_S0_AND_EDIT_PERMISSION'}


def direct_plan_request(inputs, config):
    step = {'type': 'object', 'additionalProperties': False,
        'properties': {'id': {'type': 'string', 'minLength': 1},
            'path': {'type': 'string', 'enum': [r['path'] for r in inputs['permitted_source_inventory']]},
            'operation': {'const': 'MODIFY'}, 'intent': {'type': 'string', 'minLength': 1},
            'depends_on': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['id', 'path', 'operation', 'intent', 'depends_on']}
    schema = {'type': 'object', 'additionalProperties': False,
        'properties': {'schema_version': {'const': 'ce.direct_plan.v1'},
            'snapshot_id': {'const': inputs['snapshot_id']}, 'input_digest': {'const': inputs['input_digest']},
            'goal': {'type': 'string', 'minLength': 1},
            'steps': {'type': 'array', 'minItems': 1, 'maxItems': 128, 'items': step}},
        'required': ['schema_version', 'snapshot_id', 'input_digest', 'goal', 'steps']}
    return ModelRequest(model=str(config['model']), prompt_version='direct-plan.v1',
        messages=({'role': 'system', 'content': 'Independently propose a direct edit plan from the raw public requirement and permitted source. Preserve its complete conditions and existing required behavior. Source inventory grants permission, not a proposed target. Author your own steps and dependencies. Return the strict JSON plan.'},
                  {'role': 'user', 'content': json.dumps(inputs, ensure_ascii=False)}),
        output_schema=schema, max_output_tokens=int(config.get('max_output_tokens', 4096)),
        metadata={'method_mode': 'without_relation', 'input_origin': inputs['source_origin'],
                  'semantic_input_stages': [], 'generated_stage': 'DIRECT_PLAN'})


def generate_nonrelation(mode, method_input, source, policy, provider, token_counter, config,
                         *, observations=(), feedback=None, journal=None, attempt_id='mode'):
    if mode not in {'without_relation', 'direct'}:
        raise ValueError('This generator accepts only modes without a relation stage')
    inputs = source_input(method_input, source, policy, observations, feedback)
    plan = None
    if mode == 'without_relation':
        request = direct_plan_request(inputs, config)
        allowance = max(0, int(config.get('max_format_retries', 0)))
        for attempt in range(allowance + 1):
            quote = quote_request(provider, request, token_counter)
            if not context_fits(quote, int(config['max_context_tokens']), config.get('model_context_tokens')):
                raise PlanRejected('DirectPlan request exceeds context budget')
            start = len(provider.calls)
            dispatch_error = None
            try:
                response = complete_quoted(provider, request, quote)
            except BaseException as exc:
                dispatch_error = exc
                raise
            finally:
                if journal is not None:
                    preserve_error(dispatch_error, lambda: journal.record_dispatch('DIRECT_PLAN',
                        attempt_id=attempt_id + '.direct-' + str(attempt),
                        snapshot_id=inputs['snapshot_id'], call_start=start, status='RECORDED'))
            try:
                plan = json.loads(response.content)
                errors = list(Draft202012Validator(request.output_schema).iter_errors(plan))
                if errors:
                    raise PlanRejected('DirectPlan output contract: ' + errors[0].message)
                if response.metadata.get('finish_reason') in {'length', 'max_tokens'}:
                    raise PlanRejected('DirectPlan response was truncated')
                break
            except (ValueError, PlanRejected) as exc:
                if attempt >= allowance:
                    raise
                if journal is not None and getattr(journal, 'engine', None) is not None:
                    journal.engine.policy.consume('format_correction', 'direct-plan:' + attempt_id)
                request = replace(request, messages=request.messages + (
                    {'role': 'assistant', 'content': response.content},
                    {'role': 'user', 'content': 'Correct this same independent plan contract: ' + str(exc) + '. Keep all original requirement conditions and preservation duties.'}))
    trace = {'schema_version': 'ce.method_mode_trace.v1', 'method_mode': mode,
        'run_id': str(config.get('run_id', 'mode')), 'case_id': method_input.case_id,
        'snapshot_id': inputs['snapshot_id'], 'input_digest': inputs['input_digest'],
        'status': 'READY', 'diagnosis': None, 'changeset': None, 'direct_plan': plan,
        'errors': [], 'model_calls': [], 'semantic_layers': {
            'relation': 'NOT_APPLICABLE', 'changeset': 'NOT_APPLICABLE',
            'direct_plan': 'AUTHORED' if plan else 'NOT_APPLICABLE'},
        'presemantic_input_digest': canonical_digest(inputs)}
    if journal is not None:
        ref = journal.recorder.capture_value(trace, 'MODE_FRONT_TRACE', snapshot_id=inputs['snapshot_id'])
        journal.record('front', attempt_id=attempt_id, snapshot_id=inputs['snapshot_id'],
            status='READY', call_start=len(provider.calls), output_refs=(ref,))
    return trace


def repair_intent(mode, trace, method_input, source):
    if trace.get('snapshot_id') != compute_snapshot(source).snapshot_id or trace.get('input_digest') != method_input.input_digest:
        raise PlanRejected('Mode trace belongs to a stale source or different input')
    if mode == 'full':
        return SemanticChangeSetIntent(trace['changeset'], trace.get('diagnosis'),
            tuple(trace['run_context']['retrieval_config'].get('necessary_source_support') or ()))
    if mode == 'without_relation':
        if trace.get('diagnosis') is not None or trace.get('changeset') is not None:
            raise PlanRejected('DirectPlan trace contains forbidden semantic-stage products')
        return DirectPlanIntent(trace['direct_plan'])
    if mode == 'without_changeset':
        if trace.get('changeset') is not None:
            raise PlanRejected('Relation-only trace contains a forbidden ChangeSet')
        return RelationOnlyIntent(trace['diagnosis'])
    if trace.get('diagnosis') is not None or trace.get('changeset') is not None or trace.get('direct_plan') is not None:
        raise PlanRejected('Direct trace contains forbidden semantic-stage products')
    return RequirementOnlyIntent(trace['snapshot_id'], trace['input_digest'])


def audit_mode_calls(mode, calls):
    counts = {'relation': 0, 'changeset': 0, 'direct_plan': 0, 'execution': 0, 'review': 0}
    requests = []
    for index, call in enumerate(calls):
        version = call['prompt_version']
        stage = ('relation' if version.startswith('diagnosis.') else 'direct_plan' if version == 'direct-plan.v1'
            else 'changeset' if version.startswith(('semantic-changeset.', 'changeset.')) else 'execution' if version == 'modify-only-literal-execution.v1'
            or version.startswith('topomen') else 'review')
        counts[stage] += 1
        request = call.get('request')
        requests.append({'call_index': index, 'stage': stage, 'request_digest': canonical_digest(request),
            'prompt_version': version, 'schema_digest': canonical_digest(call.get('output_schema')),
            'tools': 'NONE_MODEL_REQUEST_CONTRACT', 'final_messages_recorded': isinstance(request, dict)})
    forbidden = ({'without_relation': ('relation', 'changeset'), 'without_changeset': ('changeset', 'direct_plan'),
        'direct': ('relation', 'changeset', 'direct_plan')}).get(mode, ())
    return {'schema_version': 'ce.mode_call_audit.v1', 'method_mode': mode,
        'counts': counts, 'forbidden_stages': list(forbidden),
        'removed_stage_calls_zero': all(counts[key] == 0 for key in forbidden), 'requests': requests,
        'interpretation': 'Actual dispatch and final request audit; no method-effectiveness claim.'}
