"""Concrete, constrained consumer of contractedge.topomen_plan.v1.

This is the ContractEdge source-repair consumer at the TopoMen interchange
boundary. It does not claim the separate deployment scheduler ran. Only file
edits linked to accepted operations are executed; the model cannot run shell.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Mapping

from jsonschema import Draft202012Validator

from contractedge.contracts.validation import canonical_digest
from contractedge.contracts.execution_capabilities import SOURCE_CONSUMER_ID, resolve_execution_capabilities
from contractedge.model.provider import ModelRequest
from contractedge.orchestration.feedback import feedback_prompt_view
from contractedge.source.snapshot import compute_snapshot
from contractedge.contracts.permissions import target_permission_error


CONSUMER_ID = SOURCE_CONSUMER_ID


class PlanRejected(ValueError):
    pass


def safe_target(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or '\\' in relative or '\x00' in relative:
        raise PlanRejected('Invalid target path')
    rel = PurePosixPath(relative)
    if (not relative or rel.is_absolute() or rel.as_posix() != relative
            or any(p in {'.', '..', '.git', '.contractedge-deps'} for p in rel.parts)):
        raise PlanRejected('Unsafe target path')
    cursor = root
    for part in rel.parts:
        cursor /= part
        if cursor.is_symlink():
            raise PlanRejected('Target traverses a symlink')
    cursor.resolve().relative_to(root.resolve())
    return cursor


def operation_path(operation: Mapping) -> str:
    target = operation['target']
    return target['path'] if target['kind'] == 'EXISTING' else target['proposed_path']


def accept_plan(plan: Mapping, source: str | Path, policy: Mapping) -> dict:
    root = Path(source).resolve(strict=True)
    if plan.get('schema_version') != 'contractedge.topomen_plan.v1' or plan.get('status') not in {'READY', 'READY_PARTIAL'}:
        raise PlanRejected('Only a versioned ready plan can be consumed')
    snapshot = compute_snapshot(root)
    if snapshot.snapshot_id != plan['snapshot_id']:
        raise PlanRejected('Plan snapshot is stale')
    try:
        capabilities = resolve_execution_capabilities(plan.get('public_execution_capabilities'))
    except ValueError as exc:
        raise PlanRejected(str(exc)) from exc
    operations = plan.get('operations', [])
    ids = [op['change_id'] for op in operations]
    if not operations or len(ids) != len(set(ids)):
        raise PlanRejected('Operations must have nonempty unique IDs')
    remaining = {op['change_id']: op for op in operations}
    ordered = []
    while remaining:
        ready = sorted(cid for cid, op in remaining.items() if set(op['depends_on']) <= set(ordered))
        if not ready:
            raise PlanRejected('Operation dependencies are missing or cyclic')
        for cid in ready:
            ordered.append(cid)
            del remaining[cid]
    for op in operations:
        relative = operation_path(op)
        path = safe_target(root, relative)
        permission_error = target_permission_error(relative, policy)
        if permission_error == 'TARGET_OUTSIDE_EDITABLE_SCOPE':
            raise PlanRejected('Target is outside public editable scope: ' + relative)
        if permission_error:
            raise PlanRejected('Target is forbidden: ' + relative)
        if op['operation'] == 'REMOVE':
            raise PlanRejected('Current benchmark patch protocol rejects whole-file deletion; revise the plan explicitly')
        if op['operation'] not in capabilities['supported_operations']:
            raise PlanRejected('Execution consumer rejects unsupported operation: ' + op['operation'])
        target = op['target']
        if target['kind'] == 'EXISTING':
            if (op['operation'] != 'MODIFY' or not path.is_file()
                    or target['snapshot_id'] != snapshot.snapshot_id
                    or snapshot.file_digests.get(relative) != target['content_digest']):
                raise PlanRejected('Existing target identity/operation mismatch: ' + relative)
        elif (target['kind'] != 'NEW' or op['operation'] != 'CREATE' or path.exists()
              or not path.parent.is_dir()):
            raise PlanRejected('New target must have an existing parent and no path conflict')
    return {'schema_version': 'ce.consumer_acceptance.v1', 'consumer_id': CONSUMER_ID,
            'decision': 'ACCEPT', 'plan_digest': canonical_digest(plan),
            'snapshot_id': snapshot.snapshot_id, 'input_digest': plan['input_digest'],
            'operation_order': ordered, 'scope_policy': dict(policy),
            'supported_operations': capabilities['supported_operations']}


def apply_edits(plan: Mapping, source: str | Path, policy: Mapping, response: Mapping, *, require_behavior_change=False) -> dict:
    """Validate the complete edit batch before writing, rollback on write failure."""
    root = Path(source).resolve(strict=True)
    acceptance = accept_plan(plan, root, policy)
    by_id = {op['change_id']: op for op in plan['operations']}
    if not isinstance(response, Mapping):
        raise PlanRejected('Concrete response must be an object')
    edits = response.get('edits')
    if not isinstance(edits, list) or not edits or set(response) != {'edits'}:
        raise PlanRejected('Concrete response must contain a nonempty edits array only')
    if any(not isinstance(edit, Mapping) or set(edit) != {'change_id', 'path', 'operation', 'old_text', 'new_text'} for edit in edits):
        raise PlanRejected('Unsupported concrete edit fields')
    if any(not isinstance(edit['change_id'], str) for edit in edits):
        raise PlanRejected('Concrete change_id must be a string')
    submitted={edit['change_id'] for edit in edits}
    eligible=(plan.get('execution_continuation') or {}).get('prior_applied_operations',{})
    eligible=eligible if isinstance(eligible,Mapping) else {}
    missing=set(by_id)-submitted
    retained={cid for cid in missing if cid in eligible and
              eligible[cid].get('path')==operation_path(by_id[cid]) and
              eligible[cid].get('snapshot_id')==acceptance['snapshot_id'] and
              eligible[cid].get('content_digest')==by_id[cid]['target'].get('content_digest') and
              eligible[cid].get('content_digest')==hashlib.sha256(safe_target(root,operation_path(by_id[cid])).read_bytes()).hexdigest()}
    if submitted-set(by_id) or missing-retained:
        raise PlanRejected('Concrete edits must cover exactly the accepted change IDs; '
            f'missing={sorted(set(by_id)-submitted)}, unexpected={sorted(submitted-set(by_id))}. '
            'No source was changed. Cover every accepted operation with its own change_id; '
            'do not silently omit an operation or relabel another operation as completed.')
    original, changed, per_change = {}, {}, Counter()
    for edit_index, edit in enumerate(sorted(edits, key=lambda e: acceptance['operation_order'].index(e['change_id']))):
        if set(edit) != {'change_id', 'path', 'operation', 'old_text', 'new_text'}:
            raise PlanRejected('Unsupported edit fields')
        op = by_id[edit['change_id']]
        relative = operation_path(op)
        if edit['path'] != relative or edit['operation'] != op['operation']:
            raise PlanRejected('Edit path/operation differs from accepted semantic target')
        path = safe_target(root, relative)
        if not isinstance(edit['old_text'], str) or not isinstance(edit['new_text'], str):
            raise PlanRejected('Edits must contain literal UTF-8 text')
        if '\x00' in edit['new_text'] or len(edit['new_text'].encode()) > 4 * 1024 * 1024:
            raise PlanRejected('Concrete output is binary or exceeds patch budget')
        if relative not in original:
            original[relative] = path.read_bytes() if path.exists() else None
            changed[relative] = original[relative].decode('utf-8') if original[relative] is not None else ''
        if op['operation'] == 'CREATE':
            if per_change[op['change_id']] or edit['old_text'] or not edit['new_text'] or original[relative] is not None:
                raise PlanRejected('CREATE must specify one complete new regular file')
            changed[relative] = edit['new_text']
        else:
            matches = changed[relative].count(edit['old_text']) if edit['old_text'] else 0
            if not edit['old_text'] or matches != 1:
                raise PlanRejected('MODIFY old_text must occur exactly once in the current file; '
                    f'change_id={edit["change_id"]}, path={relative}, edit_index={edit_index}, matches={matches}. '
                    'Copy a unique literal anchor from current_target_files; no source was changed.')
            changed[relative] = changed[relative].replace(edit['old_text'], edit['new_text'], 1)
        per_change[op['change_id']] += 1
    if sum(len(value.encode()) for value in changed.values()) > 8 * 1024 * 1024:
        raise PlanRejected('Edited file batch exceeds the consumer byte limit')
    if all(original[path] == text.encode() for path, text in changed.items()):
        raise PlanRejected('Concrete edits are a no-op')
    if require_behavior_change:
        from contractedge.execution.code_change import semantics_unchanged
        if semantics_unchanged(original,changed):
            raise PlanRejected('The current supported behavior failure requires a semantic change; '
                'this retry changes only a matching Python AST or blank Go source lines. No source was changed. '
                'Address the actual failing operation/path from fresh feedback, preserving already passing conditions. '
                'Do not repeat a cosmetic edit or claim the accepted intent was completed.')
    # Recheck all source bytes after model generation and before transaction.
    accept_plan(plan, root, policy)
    written, staged, backups = [], {}, {}
    try:
        # Stage all bytes before replacing any source file. Existing inode links
        # allow rollback even if writing another file exhausts disk space.
        for relative, text in changed.items():
            path = safe_target(root, relative)
            descriptor, name = tempfile.mkstemp(prefix='.ce-edit-', dir=path.parent)
            staged[relative] = Path(name)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(text.encode('utf-8'))
            staged[relative].chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
            if path.exists():
                descriptor, name = tempfile.mkstemp(prefix='.ce-before-', dir=path.parent)
                os.close(descriptor)
                Path(name).unlink()
                os.link(path, name)
                backups[relative] = Path(name)
        for relative in changed:
            path = safe_target(root, relative)
            os.replace(staged[relative], path)
            written.append(relative)
    except Exception:
        for relative in written:
            path = safe_target(root, relative)
            if original[relative] is None:
                path.unlink()
            else:
                os.replace(backups[relative], path)
        raise
    finally:
        for path in [*staged.values(), *backups.values()]:
            path.unlink(missing_ok=True)
    current = compute_snapshot(root)
    return {'schema_version': 'ce.concrete_execution.v1', 'consumer_id': CONSUMER_ID,
            'acceptance': acceptance, 'from_snapshot': plan['snapshot_id'], 'to_snapshot': current.snapshot_id,
            'changed_paths': sorted(changed), 'change_ids': sorted(per_change),
            'unchanged_change_ids':sorted(retained),
            'file_digests': {path: current.file_digests[path] for path in changed},
            'status': 'APPLIED',
            'operation_coverage': [{'change_id': cid, 'path': operation_path(by_id[cid]),
                                   'edit_count': per_change[cid],
                                   'disposition':'NO_ADDITIONAL_EDIT_SEMANTICS_UNVERIFIED' if cid in retained else 'EDITED_SEMANTICS_UNVERIFIED'} for cid in acceptance['operation_order']],
            'semantic_edit_trace': [{
                'change_id': cid, 'path': operation_path(by_id[cid]),
                'semantic_action': by_id[cid]['semantic_action'], 'intent': by_id[cid]['intent'],
                'violation_refs': list(by_id[cid].get('violation_refs', [])),
                'postcondition_refs': list(by_id[cid].get('postcondition_refs', [])),
                'depends_on': list(by_id[cid].get('depends_on', [])),
                'literal_edits': [{'old_text': e['old_text'], 'new_text': e['new_text']}
                                  for e in edits if e['change_id'] == cid],
                'semantic_status': 'NOT_PROVEN; inspect actual edits against intent and fresh public evidence'}
                for cid in acceptance['operation_order']],
            'semantic_faithfulness': 'NOT_PROVEN_BY_EDIT_COVERAGE; requires public observation',
            'hidden_evaluation': 'NOT_AVAILABLE_TO_METHOD'}


def execution_review_mapping(result, *, max_characters=24000):
    """Bounded actual edit context, never public runtime/behavior evidence."""
    remaining = max(0, max_characters)
    mapping = []
    for row in result.get('semantic_edit_trace', []):
        item = {k: v for k, v in row.items() if k != 'literal_edits'}
        spans = []
        for edit in row.get('literal_edits', []):
            span = {}
            for key in ('old_text', 'new_text'):
                raw = edit[key]
                span[key] = raw[:remaining]
                remaining -= len(span[key])
            span['truncated'] = any(len(span[k]) < len(edit[k]) for k in ('old_text', 'new_text'))
            spans.append(span)
        item['literal_edits'] = spans
        mapping.append(item)
    return mapping


def build_execution_request(plan: Mapping, source: Path, policy: Mapping, token_counter, config: Mapping, *,
                            requirement: str, declared_artifacts=(), observations=(), feedback=None,
                            previous_attempt=None) -> ModelRequest:
    acceptance = accept_plan(plan, source, policy)
    # A prior edit response is a format-correction aid only on the exact
    # snapshot it read. Successful S0 edits must not compete with S1 source
    # when public feedback asks for a behavioral continuation.
    if previous_attempt and previous_attempt.get('source_snapshot') != acceptance['snapshot_id']:
        previous_attempt = None
    sources = []
    for path in sorted({operation_path(op) for op in plan['operations']}):
        target = safe_target(source, path)
        content = target.read_text() if target.is_file() else None
        if content and len(content.encode()) > 512 * 1024:
            raise PlanRejected('Target file exceeds explicit execution context budget')
        sources.append({'path': path, 'content': content})
    context = dict(plan.get('coordination_context') or {})
    support, support_audit, used = [], [], 0
    targets = {s['path'] for s in sources}
    prerequisite_refs={r['source_evidence_ref'] for r in plan.get('precondition_review', [])
                       if r.get('disposition') != 'NOT_APPLICABLE'}
    prerequisite_refs.update(r['id'] for r in context.get('source_support',[]) if r.get('delivery_required'))
    for evidence in context.get('source_support', []):
        path = evidence.get('locator', {}).get('path')
        reason = None
        historical = evidence.get('snapshot_id') != acceptance['snapshot_id']
        if historical and evidence.get('id') not in prerequisite_refs:
            reason = 'HISTORICAL_SNAPSHOT'
        elif path in targets:
            reason = 'TARGET_ALREADY_SUPPLIED'
        else:
            try:
                actual = safe_target(source, path).read_text()
                excerpt = evidence.get('excerpt', '')
                if (not excerpt or excerpt not in actual
                        or hashlib.sha256(excerpt.encode()).hexdigest() != evidence.get('content_digest')):
                    reason = 'STALE_OR_INVALID_SOURCE'
                elif historical and actual.count(excerpt) != 1:
                    reason = 'AMBIGUOUS_PREREQUISITE_SOURCE'
                elif used + len(excerpt.encode()) > int(config.get('max_execution_support_bytes', 24000)):
                    reason = 'READ_ONLY_SUPPORT_BUDGET'
                else:
                    if historical:
                        start = actual.count('\n', 0, actual.index(excerpt)) + 1
                        evidence = {**dict(evidence), 'snapshot_id':acceptance['snapshot_id'],
                            'locator':{'path':path,'start_line':start,
                                'end_line':start+len(excerpt.splitlines())-1},
                            'origin':'Fresh re-read of an exact unchanged prerequisite span; prior semantic intent remains historical, not re-proven'}
                    support.append(dict(evidence)); used += len(excerpt.encode())
            except (OSError, ValueError, TypeError):
                reason = 'UNAVAILABLE_SOURCE'
        support_audit.append({'id': evidence.get('id'), 'path': path, 'result': reason or 'INCLUDED_READ_ONLY'})
    if config.get('execution_helper_support_enabled'):
        from contractedge.execution.helper_context import local_helper_support
        helpers, audit = local_helper_support(compute_snapshot(source), sources, plan['operations'],
            max_bytes=max(0, int(config.get('max_execution_support_bytes', 24000))-used))
        existing = {(r['locator']['path'], r['content_digest']) for r in support}
        support.extend(h for h in helpers if (h['locator']['path'], h['content_digest']) not in existing)
        support_audit.extend(audit)
    # Never duplicate full support excerpts inside both the plan and the bounded
    # support view. Stale diagnosis remains historical on EXECUTE continuation.
    if context:
        context['source_support'] = [{k: v for k, v in e.items() if k != 'excerpt'}
                                     for e in context.get('source_support', [])]
    prompt_plan = {**dict(plan), **({'coordination_context': context} if context else {})}
    observations=list(observations)
    feedback_view = feedback_prompt_view(feedback, supplied_observations=observations)
    # Keep one complete canonical copy of unchanged accepted context. Only
    # prompt metadata is shared; the validated plan and feedback stay intact.
    if feedback_view is not None:
        for key in ('obligation_lineage','public_execution_capabilities'):
            if (key in feedback_view and key in prompt_plan
                    and isinstance(feedback_view[key],Mapping) and isinstance(prompt_plan[key],Mapping)
                    and canonical_digest(feedback_view[key])==canonical_digest(prompt_plan[key])):
                feedback_view.pop(key)
                feedback_view[key+'_record_ref']='/accepted_plan/'+key
        if any(key.endswith('_record_ref') for key in feedback_view):
            feedback_view['shared_context_reference_policy']=(
                'Each *_record_ref resolves to the complete identical object at that JSON Pointer in this request. '
                'The accepted plan and original validated feedback remain unchanged; no obligation or capability was removed.')
    if previous_attempt and isinstance(previous_attempt.get('rejected_trial'),Mapping):
        historical=previous_attempt['rejected_trial'].get('feedback')
        if isinstance(historical,Mapping):
            from contractedge.orchestration.feedback import historical_trial_prompt_view
            rejected={**previous_attempt['rejected_trial'],'feedback':historical_trial_prompt_view(historical,
                canonical_text=json.dumps(prompt_plan,ensure_ascii=False)+json.dumps(feedback_view,ensure_ascii=False))}
            if 'public_check_results' in rejected:
                rejected.pop('public_check_results');rejected['public_check_results_ref']='feedback.public_check_results'
            previous_attempt={**previous_attempt,'rejected_trial':rejected}
    from contractedge.execution.code_change import behavior_change_required
    payload = {'requirement': requirement, 'accepted_plan': prompt_plan, 'acceptance': acceptance,
               'require_behavior_change':behavior_change_required(feedback,acceptance['snapshot_id']),
               'current_target_files': sources, 'declared_artifacts': list(declared_artifacts),
               'read_only_source_support': support,
               'public_observations': list(observations), 'execution_feedback': feedback_view,
               'execution_feedback_digest': canonical_digest(feedback) if feedback is not None else None,
               'previous_execution_attempt': previous_attempt,
               'edit_contract': 'Return one JSON object containing only edits, with each edit containing only change_id, path, operation, old_text and new_text. Transport settings such as type=json_object are not output fields. Encode source strings exactly once as JSON: after JSON decoding, old_text must equal a unique current substring, with actual line breaks and tabs rather than literal backslash-n or backslash-t substitutes. CREATE uses empty old_text and complete file content. Prefer short, unique literal anchors copied exactly from current_target_files, preserving whitespace. Split separate regions into separate edits; avoid whole-file rewrites. Correct previous_execution_attempt using the supplied validation error and the current file. Cover every accepted operation, preserve all conditions and unrelated behavior. Do not add tests, fabricate observations or edit task/runner/oracle.'}
    if config.get('helper_preconditions_enabled'):
        from contractedge.execution.preconditions import validate_helper_preconditions
        payload['helper_preconditions']=validate_helper_preconditions(plan,{s['path']:s['content'] for s in sources},support)
        payload['helper_precondition_policy']=(
            'Read the actual helper contracts before adding or moving calls. For annotation-identity merge APIs, '
            'input resource identities must be assigned on EVERY path that merges and cleaned on those same paths. '
            'Changing a merge guard requires checking initialization and cleanup guards together. '
            'Concrete syntax checks are bounded hints, not behavior proof; preserve resource identities, content, '
            'selection boundaries and downstream visibility. New helper calls need the same public precondition review.')
        payload['state_handoff_policy']=(
            'For an observed continuity failure, construction of a replacement or starting a goroutine does not '
            'establish data readiness. Trace initialization, reader selection, publication and old-instance shutdown '
            'against fresh source contracts. Preserve the required state throughout the transition, not just before '
            'and after; a fixed sleep or moving Stop alone is not evidence of readiness. Unsupported readiness '
            'assumptions must remain unproven and be tested through the public boundary.')
    if config.get('writeback_context_enabled'):
        from contractedge.execution.writeback_context import writeback_context
        payload['collection_writeback_context']=writeback_context(sources,plan['operations'],acceptance['snapshot_id'])
    eligible=(plan.get('execution_continuation') or {}).get('prior_applied_operations',{})
    if eligible:
        payload['continuation_operation_coverage']=eligible
        payload['edit_contract'] = payload['edit_contract'].replace('Cover every accepted operation,',
            'On this verified continuation, previously applied change IDs listed in continuation_operation_coverage '
            'may receive no additional edit; all remaining change IDs must be edited. '
            'The consumer records every omitted eligible ID as unchanged and semantically unverified. '
            'All original postconditions and preservation still apply. For all other cases cover every accepted operation,')
    schema = {'type': 'object', 'required': ['edits'], 'additionalProperties': False,
              'properties': {'edits': {'type': 'array', 'minItems': 1, 'maxItems': 64,
                  'items': {'type': 'object', 'required': ['change_id', 'path', 'operation', 'old_text', 'new_text'],
                            'additionalProperties': False, 'properties': {
                                'change_id': {'type': 'string', 'enum': acceptance['operation_order']},
                                'path': {'type': 'string', 'enum': [s['path'] for s in sources]},
                                'operation': {'type': 'string', 'enum': ['MODIFY', 'CREATE']},
                                'old_text': {'type': 'string'}, 'new_text': {'type': 'string'}}}}}}
    # Compatible JSON mode constrains only JSON syntax. Unlike a native
    # schema-aware transport, it does not transmit ModelRequest.output_schema.
    # Match diagnosis/planning: deliver the exact per-plan schema in messages
    # when requested, before measuring the complete request's context budget.
    if config.get('schema_in_prompt', config.get('provider') == 'openai_compatible'):
        payload['output_schema'] = schema
    thinking, metadata = config.get('execution_thinking'), {}
    if thinking is not None:
        if thinking not in {'enabled', 'disabled'} or config.get('provider') != 'openai_compatible':
            raise PlanRejected('Execution thinking override requires a valid compatible transport setting')
        metadata['execution_thinking_policy'] = 'EXPLICIT_STAGE_CONFIGURATION'
    reasoning_policy=config.get('execution_reasoning_policy')
    if reasoning_policy not in {None,'complex_plan'}:
        raise PlanRejected('Unknown execution reasoning policy')
    complex_plan=(len(plan['operations'])>1 or any(
        r.get('disposition')=='REQUIRED' for r in plan.get('precondition_review',[])))
    previous=previous_attempt or {}
    trial=previous.get('rejected_trial') or {}
    decision=trial.get('decision') or trial.get('candidate_decision') or {}
    if (trial.get('decision') and trial.get('candidate_decision')
            and trial['decision']!=trial['candidate_decision']):decision={}
    history=trial.get('feedback') or {}
    failed_predicates=(history.get('failure_tasks') or {}).get('failed_predicates',[])
    failed_trial=(previous.get('source_snapshot')==acceptance['snapshot_id']
        and decision.get('decision')=='REJECT_REGRESSION'
        and decision.get('from_snapshot')==acceptance['snapshot_id']
        and bool(trial.get('snapshot_id')) and trial['snapshot_id']!=acceptance['snapshot_id']
        and decision.get('trial_snapshot')==trial['snapshot_id']==history.get('to_snapshot')
        and any(p.get('snapshot_id')==trial['snapshot_id'] and p.get('status')=='FAIL'
                and p.get('probe_sha256') and p.get('content_digest') for p in failed_predicates))
    if reasoning_policy=='complex_plan' and (complex_plan or failed_trial):
        if config.get('provider')!='openai_compatible':
            raise PlanRejected('Complex execution reasoning policy requires compatible transport')
        thinking='enabled'
        metadata['execution_thinking_policy']='FAILED_TRIAL_REASONING' if failed_trial else 'COMPLEX_PLAN_REASONING'
    previous = previous_attempt or {}
    previous_metadata = previous.get('response_metadata') or {}
    usage = previous_metadata.get('usage') or {}
    completion = usage.get('completion_tokens', 0)
    reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0)
    if (config.get('execution_empty_reasoning_retry') == 'disable_thinking'
            and previous.get('source_snapshot') == acceptance['snapshot_id']
            and previous.get('model_response') == ''
            and previous_metadata.get('finish_reason') in {'length', 'max_tokens'}
            and type(completion) is int and completion > 0 and reasoning == completion):
        if config.get('provider') != 'openai_compatible':
            raise PlanRejected('Empty-reasoning fallback requires the configured compatible transport')
        thinking = 'disabled'
        metadata = {'execution_retry_policy': 'EMPTY_REASONING_BUDGET_EXHAUSTED_DISABLE_THINKING'}
    elif (config.get('execution_truncation_retry') == 'disable_thinking'
            and previous.get('source_snapshot') == acceptance['snapshot_id']
            and isinstance(previous.get('model_response'), str)
            and previous_metadata.get('finish_reason') in {'length', 'max_tokens'}
            and type(completion) is int and completion > 0
            and type(reasoning) is int and completion >= reasoning > 0
            and reasoning * 2 >= completion):
        if config.get('provider') != 'openai_compatible':
            raise PlanRejected('Truncation fallback requires the configured compatible transport')
        thinking = 'disabled'
        metadata = {'execution_retry_policy': 'REASONING_MAJORITY_TRUNCATION_DISABLE_THINKING'}
    request = ModelRequest(model=str(config['model']), prompt_version='topomen-source-execution.v1',
        messages=({'role': 'system', 'content': 'Implement the accepted semantic operations in the current source. Return literal JSON file edits only. Preserve the attached obligation triggers, relation conditions, dependency order and preservation constraints. Read-only source support explains interfaces; it grants no edit permission. Historical diagnosis context is not fresh evidence. The plan is binding; do not perform new repository-wide diagnosis or invent execution results.'},
                  {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}),
        max_output_tokens=int(config.get('execution_max_output_tokens', 8192)), output_schema=schema,
        thinking=thinking, metadata=metadata)
    history=((previous_attempt or {}).get('rejected_trial') or {}).get('feedback') or {}
    critical_helpers={c.get('helper_evidence_ref') for f in [feedback_view or {},history]
        for c in (f.get('helper_preconditions') or {}).get('checks',[]) if c.get('status')=='CONFLICT'}
    critical_helpers.update(prerequisite_refs)
    supplied_ids={r['id'] for r in support}
    target_ids={r['id'] for r in (plan.get('coordination_context') or {}).get('source_support', [])
                if r.get('locator', {}).get('path') in targets}
    if prerequisite_refs - supplied_ids - target_ids:
        raise PlanRejected('Prerequisite source was not delivered to execution: '+', '.join(
            sorted(prerequisite_refs - supplied_ids - target_ids)))
    while support and sum(token_counter(m['content']) for m in request.messages) + request.max_output_tokens > int(config['max_context_tokens']):
        removable=[i for i,r in enumerate(support) if r.get('id') not in critical_helpers]
        if not removable:break
        removed = support.pop(removable[-1])
        support_audit.append({'id': removed['id'], 'result': 'FULL_REQUEST_CONTEXT_BUDGET'})
        request = replace(request, messages=(request.messages[0],
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}))
    request = replace(request, metadata={**request.metadata, 'read_only_support_audit': support_audit})
    if sum(token_counter(m['content']) for m in request.messages) + request.max_output_tokens > int(config['max_context_tokens']):
        raise PlanRejected('Execution request exceeds the full context budget')
    return request


def validate_execution_response(response, request: ModelRequest) -> dict:
    if response.metadata.get('finish_reason') in {'length', 'max_tokens'}:
        raise PlanRejected('Concrete edit generation was truncated')
    body = json.loads(response.content)
    errors = list(Draft202012Validator(request.output_schema).iter_errors(body))
    if errors:
        raise PlanRejected('Concrete edit schema invalid: ' + errors[0].message)
    return body


def check_edited_helper_preconditions(request, source, config):
    if not config.get('helper_preconditions_enabled'):
        return None
    from contractedge.execution.preconditions import validate_helper_preconditions
    payload=json.loads(request.messages[1]['content']);root=Path(source)
    return validate_helper_preconditions(payload['accepted_plan'],
        {s['path']:safe_target(root,s['path']).read_text() for s in payload['current_target_files']},
        payload['read_only_source_support'])


class ModelPlanExecutor:
    """Explicit direct backend; does not invoke the native TopoMen Agent."""
    def __init__(self, provider, token_counter, config: Mapping):
        self.provider, self.token_counter, self.config = provider, token_counter, dict(config)

    def execute(self, plan: Mapping, source: Path, policy: Mapping, *, requirement: str,
                declared_artifacts=(), observations=(), feedback: Mapping | None = None, previous_attempt=None) -> dict:
        request = build_execution_request(plan, source, policy, self.token_counter, self.config,
            requirement=requirement, declared_artifacts=declared_artifacts, observations=observations,
            feedback=feedback, previous_attempt=previous_attempt)
        response = self.provider.complete_json(request)
        body = validate_execution_response(response, request)
        result = apply_edits(plan, source, policy, body,
            require_behavior_change=json.loads(request.messages[1]['content'])['require_behavior_change'])
        report=check_edited_helper_preconditions(request,source,self.config)
        if report is not None:result['helper_preconditions']=report
        result['model_response'] = {'content': response.content, 'metadata': dict(response.metadata)}
        return result
