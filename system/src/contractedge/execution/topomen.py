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
import re
import tempfile
from typing import Mapping

from jsonschema import Draft202012Validator

from contractedge.contracts.validation import canonical_digest
from contractedge.contracts.delivery import (source_span_matches, target_supplies_source,
                                             target_supplies_support, reread_source_locator)
from contractedge.contracts.execution_capabilities import SOURCE_CONSUMER_ID, resolve_execution_capabilities
from contractedge.model.provider import ModelRequest
from contractedge.model.config import model_name
from contractedge.model.admission import complete_quoted, require_context, quote_request
from contractedge.model.budget import BudgetExceeded
from contractedge.orchestration.feedback import feedback_prompt_view, observations_prompt_view
from contractedge.source.snapshot import compute_snapshot
from contractedge.contracts.permissions import target_permission_error
from contractedge.diagnosis.evidence import same_evidence_content, merge_evidence_records


CONSUMER_ID = SOURCE_CONSUMER_ID


def execution_attempt_for_snapshot(previous_attempt: Mapping | None, snapshot_id: str):
    """Keep the existing correction context only on the snapshot it read."""
    if previous_attempt and previous_attempt.get('source_snapshot') != snapshot_id:
        return None
    return previous_attempt


def _execution_correction_change_ids(previous_attempt: Mapping | None,
                                      operation_order: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Return change IDs named by a concrete validation error.

    A failed literal transaction is atomic, so a correction still has to
    return a complete batch for the consumer.  The model should nevertheless
    spend its correction budget on the rejected operation rather than
    re-reasoning about every accepted operation.  Keep this extraction
    deliberately narrow: only an explicit ``change_id=...`` (or a bounded
    ``change_ids=[...]`` list) from the runner-owned validation error is
    trusted; prose in historical feedback is not an execution permission.
    """
    if not isinstance(previous_attempt, Mapping):
        return ()
    error = previous_attempt.get('validation_error')
    if not isinstance(error, str) or not error.strip():
        return ()
    allowed = set(operation_order)
    found = []
    for match in re.finditer(r"\bchange_id=([A-Za-z0-9_.:-]+)", error):
        value = match.group(1)
        if value in allowed and value not in found:
            found.append(value)
    # Some validators report a missing set instead of one failing operation.
    # This remains bounded to the accepted operation IDs and preserves their
    # deterministic plan order.
    if not found:
        match = re.search(r"change_ids?=\[([^\]]*)\]", error)
        if match:
            named = set(re.findall(r"[A-Za-z0-9_.:-]+", match.group(1))) & allowed
            found = [cid for cid in operation_order if cid in named]
    return tuple(found)


def _build_failure_change_ids(feedbacks, operations: list[Mapping] | tuple[Mapping, ...]) -> tuple[str, ...]:
    """Map source-bound public build diagnostics to accepted operation IDs.

    On a restored candidate the previous model response is intentionally
    discarded because its snapshot is historical.  A compiler diagnostic is
    still a current, bounded correction signal: use its path and (when
    available) line to identify the affected accepted operation.  If a
    diagnostic has no line, retain all operations on that path so the model
    does not silently omit a dependent edit.
    """
    if not feedbacks:
        return ()
    found = []
    for item in feedbacks:
        if not isinstance(item, Mapping):
            continue
        for diagnostic in item.get('observation_diagnostics', ()):
            if not isinstance(diagnostic, Mapping) or diagnostic.get('kind') != 'CANDIDATE_BUILD_FAILURE':
                continue
            if diagnostic.get('status') not in {None, 'FAILED'}:
                continue
            locations = diagnostic.get('source_locations') or ()
            if not isinstance(locations, (list, tuple)):
                continue
            for location in locations:
                if not isinstance(location, Mapping) or not isinstance(location.get('path'), str):
                    continue
                path, line = location['path'], location.get('line')
                matched_line = False
                for operation in operations:
                    target = operation.get('target') or {}
                    op_path = target.get('path') if target.get('kind') == 'EXISTING' else target.get('proposed_path')
                    if op_path != path:
                        continue
                    locator = target.get('locator') or {}
                    start, end = locator.get('start_line'), locator.get('end_line')
                    if (type(line) is int and type(start) is int and type(end) is int
                            and not (start <= line <= end)):
                        continue
                    matched_line = True
                    cid = operation.get('change_id')
                    if isinstance(cid, str) and cid not in found:
                        found.append(cid)
                # A restored plan can carry a coarse or historical locator.
                # If no operation covered the reported line, fall back to the
                # same-path operations instead of silently dropping a build
                # correction signal.
                if not matched_line and type(line) is int:
                    for operation in operations:
                        target = operation.get('target') or {}
                        op_path = target.get('path') if target.get('kind') == 'EXISTING' else target.get('proposed_path')
                        cid = operation.get('change_id')
                        if op_path == path and isinstance(cid, str) and cid not in found:
                            found.append(cid)
    order = [op.get('change_id') for op in operations]
    return tuple(cid for cid in order if cid in found)


from contractedge.execution.literal import PlanRejected, safe_target, LiteralTarget, apply_literal_edits


def operation_path(operation: Mapping) -> str:
    target = operation['target']
    return target['path'] if target['kind'] == 'EXISTING' else target['proposed_path']


def _execution_window_segments(text: str, ranges: list[tuple[int, int]], *, context_lines: int = 20,
                               path: str | None = None) -> list[tuple[int, int]]:
    lines = text.splitlines(keepends=True)
    if not lines or not ranges:
        return [(1, max(1, len(lines)))]
    # Configuration carriers commonly have a public Config declaration near
    # the top of the file and a toInternal conversion much farther down.  A
    # field edit that stops at the declaration can compile only after the
    # conversion bridge is updated, so keep that bridge in the same bounded
    # execution view without reopening the whole repository.
    if path and path.endswith('config.go'):
        for index, line in enumerate(lines, start=1):
            if 'toInternal' in line and line.lstrip().startswith('func '):
                ranges = [*ranges, (index, min(len(lines), index + 80))]
                break
    start = max(1, min(begin for begin, _end in ranges) - context_lines)
    end = min(len(lines), max(finish for _begin, finish in ranges) + context_lines)
    if path and path.endswith('.go') and start > 1:
        prefix_end = min(len(lines), 120)
        if start <= prefix_end + 1:
            return [(1, end)]
        return [(1, prefix_end), (start, end)]
    return [(start, end)]


def _execution_source_window(text: str, ranges: list[tuple[int, int]], *, context_lines: int = 20,
                            path: str | None = None) -> tuple[str, int, int, bool]:
    """Return the bounded source window covered by accepted target locators.

    Planning already binds every existing target to a current source span.  A
    concrete edit consumer does not need to resend an unrelated 50K-line file
    when the accepted operations touch one bounded region.  Keep a little
    surrounding context for literal anchors and report the original line
    numbers so the model can distinguish a window from a complete file.  The
    executor still applies edits to the full on-disk file after exact-match
    validation; this is only a transport-size reduction.
    """
    lines = text.splitlines(keepends=True)
    if not lines or not ranges:
        return text, 1, max(1, len(lines)), False
    segments = _execution_window_segments(text, ranges, context_lines=context_lines, path=path)
    if segments == [(1, len(lines))]:
        return text, 1, len(lines), False
    # Go edits frequently target an Options/type declaration well below the
    # import block.  Supplying only locator±context makes a valid local edit
    # look self-contained while hiding required imports and the surrounding
    # type contract.  Keep a bounded file prefix for Go, then the target
    # window.  If there is a gap, use a Go comment so parser/model consumers
    # can distinguish omitted lines without pretending the excerpt is a full
    # contiguous file.  The executor still validates anchors against the full
    # on-disk file.
    start, end = segments[0][0], segments[-1][1]
    if len(segments) > 1:
        prefix_end = segments[0][1]
        target_start = segments[1][0]
        prefix = ''.join(lines[:prefix_end])
        omitted = (f'\n/* ContractEdge execution window omits original lines '
                   f'{prefix_end + 1}-{target_start - 1}; line numbers in the target '
                   'window remain those of the current file. */\n')
        return prefix + omitted + ''.join(lines[target_start - 1:end]), 1, end, True
    return ''.join(lines[start - 1:end]), start, end, True


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


def _validate_configured_options_timeout_edits(plan: Mapping, response: Mapping, *,
                                               requirement: str | None = None, source=None) -> None:
    """Check an explicit original timeout requirement at its accepted source.

    The caller supplies the original public requirement after plan acceptance.
    Generated intent, read-only support and historical text cannot activate
    this rule. Without a bound current OPTIONS target, ordinary plan/source
    validation and public checks remain authoritative.
    """
    from contractedge.requirement.normalize import normalize_requirement
    if not isinstance(requirement, str) or source is None:
        return
    clauses = normalize_requirement({'requirement_text': requirement}).get('clauses', [])
    applicable = [row['text'] for row in clauses if re.search(r'\bOPTIONS\b', row['text'])
               and re.search(r'\btimeout\b', row['text'], re.I)
               and re.search(r'\bconfigur(?:able|ed|ation)\b', row['text'], re.I)]
    if not applicable:
        return
    # Configurability alone does not entail preserving a parent context.
    # Apply the latter check only for a stated positive obligation in the
    # same original clause, not for generated plan prose or a negated phrase.
    context_required = False
    for clause in applicable:
        for obligation in re.finditer(
                r'\b(?:preserve|retain|respect|inherit|propagate|use)\s+(?:the\s+)?'
                r'(?:caller|parent|request)(?:[\'’]s)?\s+(?:context|cancellation|deadline)\b', clause, re.I):
            if not re.search(r'\b(?:not|never)\s+(?:\w+\s+){0,2}$', clause[:obligation.start()], re.I):
                context_required = True
    from contractedge.retrieval.gap_delivery import _go_code, _go_import_specs
    operations = {op['change_id']: op for op in plan.get('operations', [])}
    violations = []
    for edit in response.get('edits', ()) if isinstance(response, Mapping) else ():
        if not isinstance(edit, Mapping):
            continue
        operation = operations.get(edit.get('change_id'), {})
        target = operation.get('target', {})
        if (operation.get('operation') != 'MODIFY' or target.get('kind') != 'EXISTING'
                or edit.get('path') != target.get('path') or not target.get('path', '').endswith('.go')):
            continue
        new_text = edit.get('new_text') if isinstance(edit.get('new_text'), str) else ''
        old_text = edit.get('old_text') if isinstance(edit.get('old_text'), str) else ''
        locator = target.get('locator', {})
        start, end = locator.get('start_line'), locator.get('end_line')
        if not new_text or not old_text or type(start) is not int or type(end) is not int:
            continue
        raw = safe_target(Path(source), target['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != target.get('content_digest'):
            raise OSError('Source changed before checking the accepted timeout target')
        text = raw.decode('utf-8')
        lines = text.splitlines(keepends=True)
        if not 1 <= start <= end <= len(lines) or text.count(old_text) != 1:
            continue
        offset = text.index(old_text)
        if not (len(''.join(lines[:start-1])) <= offset
                and offset + len(old_text) <= len(''.join(lines[:end]))):
            continue
        code = _go_code(''.join(lines[start-1:end]))
        new_code = _go_code(new_text)
        imports = _go_import_specs(text)
        def bound_aliases(package):
            # A bare use/declaration may shadow an import inside this target.
            # Abstain from lexical semantic claims if binding is uncertain.
            return [alias for alias, path in imports if path == package and alias not in {'_', '.'}
                    and not re.search(r'(?<![\w.])' + re.escape(alias) + r'\b(?!\s*\.)', code + '\n' + new_code)]
        if not any(re.search(r'\b' + re.escape(alias) + r'\s*\.\s*MethodOptions\b', code)
                   for alias in bound_aliases('net/http')):
            continue
        context_aliases, time_aliases = bound_aliases('context'), bound_aliases('time')
        for alias in context_aliases:
            timeout = r'\b' + re.escape(alias) + r'\s*\.\s*WithTimeout\s*\('
            if context_required and any(re.search(timeout + r'\s*' + re.escape(parent) +
                    r'\s*\.\s*Background\s*\(', new_code) for parent in context_aliases):
                violations.append('background_timeout')
            for match in re.finditer(timeout + r'[^,\n]+,\s*([^,\n\)]+)', new_code):
                duration = match.group(1).strip()
                if (re.fullmatch(r'\d+(?:\.\d+)?', duration) or any(re.fullmatch(
                        r'(?:\d+(?:\.\d+)?\s*\*\s*)?' + re.escape(unit) +
                        r'\s*\.\s*[A-Za-z_][A-Za-z0-9_]*', duration) for unit in time_aliases)):
                    violations.append('fixed_timeout')
                    break
    if violations:
        raise PlanRejected(
            'TIMEOUT_CONTRACT_VIOLATION: the original requirement specifies a configurable OPTIONS timeout; '
            'this accepted OPTIONS target violates the stated contract: ' + ', '.join(sorted(set(violations))))


def apply_edits(plan: Mapping, source: str | Path, policy: Mapping, response: Mapping, *, require_behavior_change=False,
                trial_session=None, requirement: str | None = None) -> dict:
    """Validate the complete edit batch before writing, rollback on write failure."""
    root = Path(source).resolve(strict=True)
    acceptance = accept_plan(plan, root, policy)
    _validate_configured_options_timeout_edits(plan, response, requirement=requirement, source=root)
    by_id = {op['change_id']: op for op in plan['operations']}
    facts = apply_literal_edits(root, policy, response, snapshot_id=plan['snapshot_id'],
        targets=tuple(LiteralTarget(op['change_id'], operation_path(op), op['operation'],
                                   op['target'].get('content_digest')) for op in plan['operations']),
        operation_order=acceptance['operation_order'],
        prior_applied_operations=(plan.get('execution_continuation') or {}).get('prior_applied_operations'),
        supported_operations=acceptance['supported_operations'],
        require_behavior_change=require_behavior_change, preflight=lambda: accept_plan(plan,root,policy),
        trial_session=trial_session)
    current, changed, per_change, retained = (facts[k] for k in ('snapshot','changed','per_change','retained'))
    edits = response['edits']
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


from contractedge.execution.consumer import execution_review_mapping


def build_execution_request(plan: Mapping, source: Path, policy: Mapping, token_counter, config: Mapping, *,
                            requirement: str, declared_artifacts=(), observations=(), feedback=None,
                            previous_attempt=None, provider=None, source_context=None) -> ModelRequest:
    """Build the admitted request; an optional provider contributes wire overhead."""
    request, _ = _build_execution_request(plan, source, policy, token_counter, config,
        requirement=requirement, declared_artifacts=declared_artifacts, observations=observations,
        feedback=feedback, previous_attempt=previous_attempt, provider=provider, source_context=source_context)
    return request


def _build_execution_request(plan: Mapping, source: Path, policy: Mapping, token_counter, config: Mapping, *,
                             requirement: str, declared_artifacts=(), observations=(), feedback=None,
                             previous_attempt=None, provider=None, source_context=None):
    """Keep the final quote beside the request for the actual execution consumer."""
    acceptance = accept_plan(plan, source, policy)
    # Keep the original value long enough to recover source-bound diagnostics.
    # A continuation may intentionally run on the candidate snapshot after a
    # public build failure; in that case the prior edit response is stale, but
    # the compiler locations still identify the current source context that the
    # correction must see.
    raw_previous_attempt = previous_attempt
    diagnostic_feedbacks = []
    if isinstance(feedback, Mapping):
        diagnostic_feedbacks.append(feedback)
    if isinstance(raw_previous_attempt, Mapping):
        rejected = raw_previous_attempt.get('rejected_trial')
        if isinstance(rejected, Mapping) and isinstance(rejected.get('feedback'), Mapping):
            diagnostic_feedbacks.append(rejected['feedback'])

    # A prior edit response is a format-correction aid only on the exact
    # snapshot it read. Successful S0 edits must not compete with S1 source
    # when public feedback asks for a behavioral continuation.
    previous_attempt = execution_attempt_for_snapshot(previous_attempt, acceptance['snapshot_id'])
    sources = []
    full_target_texts = {}
    full_target_file_digests = {}
    target_ranges: dict[str, list[tuple[int, int]]] = {}
    for operation in plan['operations']:
        target = operation.get('target', {})
        if target.get('kind') != 'EXISTING':
            continue
        locator = target.get('locator', {})
        if (isinstance(locator, Mapping) and type(locator.get('start_line')) is int
                and type(locator.get('end_line')) is int
                and locator['start_line'] >= 1 and locator['end_line'] >= locator['start_line']):
            target_ranges.setdefault(operation_path(operation), []).append(
                (locator['start_line'], locator['end_line']))
    # Planning locators can cover a broad function or a stale declaration
    # range.  A frozen public compiler diagnostic is a fresher, source-bound
    # locator, so include a bounded window around each reported line in the
    # same target file.  This keeps the edit contract unchanged while exposing
    # the actual identifier/import context needed to repair the diagnostic.
    for diagnostic_feedback in diagnostic_feedbacks:
        for diagnostic in diagnostic_feedback.get('observation_diagnostics', ()):
            if not isinstance(diagnostic, Mapping):
                continue
            if diagnostic.get('kind') != 'CANDIDATE_BUILD_FAILURE':
                continue
            for location in diagnostic.get('source_locations', ()):
                if not isinstance(location, Mapping):
                    continue
                path = location.get('path')
                line = location.get('line')
                if (not isinstance(path, str) or type(line) is not int
                        or line < 1 or path not in target_ranges):
                    continue
                target_ranges[path].append((max(1, line - 24), line + 24))
    for path in sorted({operation_path(op) for op in plan['operations']}):
        target = safe_target(source, path)
        full_content = target.read_text() if target.is_file() else None
        if full_content and len(full_content.encode()) > 512 * 1024:
            raise BudgetExceeded(f'Target file exceeds explicit execution context budget: '
                f'required_bytes={len(full_content.encode())}, allowed_bytes={512 * 1024}')
        if full_content is None:
            sources.append({'path': path, 'content': full_content})
            continue
        full_target_texts[path] = full_content
        full_target_file_digests[path] = hashlib.sha256(target.read_bytes()).hexdigest()
        content, line_start, line_end, windowed = _execution_source_window(
            full_content, target_ranges.get(path, []), path=path)
        sources.append({'path': path, 'content': content,
                        **({'line_start': line_start, 'line_end': line_end,
                            'window_segments': [list(segment) for segment in _execution_window_segments(
                                full_content, target_ranges.get(path, []), path=path)],
                            'source_file_sha256': full_target_file_digests[path],
                            'windowed_from_current_target': True} if windowed else {})})
    context = dict(plan.get('coordination_context') or {})
    from contractedge.diagnosis.evidence import (source_context_records, source_context_offer,
        source_context_key, source_context_projection)
    adopted = source_context_records(source_context)
    if source_context is not None:
        if source_context['binding']['input_digest'] != plan['input_digest']:
            raise PlanRejected('Source context belongs to another method input')
    if 'source_support' in context:
        context['source_support'] = merge_evidence_records(context['source_support'])
    source_material = {source_context_key(r): r for r in context.get('source_support', [])}
    for record in adopted:
        if record.get('kind') != 'SOURCE_SPAN' or record.get('snapshot_id') != acceptance['snapshot_id']:
            continue
        key = source_context_key(record)
        source_material[key] = merge_evidence_records(
            [source_material[key], record])[0] if key in source_material else record
    support, support_audit, used = [], [], 0
    reread_records = []
    covered_support = []
    support_source_texts = {}
    targets = {s['path']: s for s in sources}
    target_ids = set()
    current_target_continuity_ids = set()
    precondition_refs={r['source_evidence_ref'] for r in plan.get('precondition_review', [])
                       if r.get('disposition') != 'NOT_APPLICABLE'}
    prerequisite_refs = set(precondition_refs)
    prerequisite_refs.update(r['id'] for r in source_material.values()
        if r.get('delivery_required') or r.get('snapshot_id') == acceptance['snapshot_id'])
    for evidence in source_material.values():
        path = evidence.get('locator', {}).get('path')
        reason = None
        historical = evidence.get('snapshot_id') != acceptance['snapshot_id']
        if historical and evidence.get('id') not in prerequisite_refs:
            reason = 'HISTORICAL_SNAPSHOT'
        else:
            try:
                actual = safe_target(source, path).read_text()
                excerpt = evidence.get('excerpt', '')
                if (not excerpt or hashlib.sha256(excerpt.encode()).hexdigest() != evidence.get('content_digest')):
                    reason = 'STALE_OR_INVALID_SOURCE'
                elif (historical and evidence.get('id') not in precondition_refs
                        and evidence.get('id') in prerequisite_refs
                        and path in targets and excerpt not in actual):
                    # An edited selected target is not an unchanged semantic
                    # prerequisite. Supply its full current file, while keeping
                    # the old evidence intact in the historical plan context.
                    targets[path].clear()
                    targets[path].update(path=path, content=actual)
                    if not target_supplies_source(targets[path], actual):
                        raise PlanRejected('Current target source was not completely delivered')
                    reason = 'HISTORICAL_SNAPSHOT'
                    current_target_continuity_ids.add(evidence['id'])
                elif (historical and evidence.get('id') not in precondition_refs
                        and target_supplies_source(targets.get(path), actual)):
                    # Selected source may change after an accepted edit. A
                    # complete current target preserves that continuation
                    # contract without claiming its historical excerpt was
                    # delivered. Explicit prerequisites still need a re-read.
                    reason = 'HISTORICAL_SNAPSHOT'
                    current_target_continuity_ids.add(evidence['id'])
                elif excerpt not in actual:
                    reason = 'STALE_OR_INVALID_SOURCE'
                elif not historical and not source_span_matches(evidence, actual):
                    reason = 'STALE_OR_INVALID_SOURCE'
                elif historical and actual.count(excerpt) != 1:
                    reason = 'AMBIGUOUS_PREREQUISITE_SOURCE'
                elif not historical and target_supplies_support(targets.get(path), evidence, actual):
                    reason = 'TARGET_ALREADY_SUPPLIED'
                    target_ids.add(evidence['id'])
                    if path.endswith('.go'):
                        covered_support.append(dict(evidence))
                else:
                    if historical:
                        # Preserve an explicit current identity for a re-read,
                        # even if its bytes also occur in a delivered target.
                        locator = reread_source_locator(evidence, actual)
                        if locator is None:
                            raise ValueError('Historical prerequisite has no exact current locator')
                        evidence = {**dict(evidence), 'snapshot_id':acceptance['snapshot_id'],
                            'locator': locator,
                            'origin':'Fresh re-read of an exact unchanged prerequisite span; prior semantic intent remains historical, not re-proven'}
                        reread_records.append(evidence)
                    previous = next((r for r in support if source_context_key(r) == source_context_key(evidence)), None)
                    if previous is None:
                        if used + len(excerpt.encode()) > int(config.get('max_execution_support_bytes', 24000)):
                            raise BudgetExceeded('Selected execution source exceeds the read-only support budget: '
                                f'required_bytes_at_least={used + len(excerpt.encode())}, '
                                f'allowed_bytes={int(config.get("max_execution_support_bytes", 24000))}')
                        support.append(dict(evidence)); used += len(excerpt.encode())
                    else:
                        merged = merge_evidence_records([previous, evidence])[0]
                        previous.clear(); previous.update(merged)
                    support_source_texts[path] = actual
            except (OSError, ValueError, TypeError):
                reason = 'UNAVAILABLE_SOURCE'
        support_audit.append({'id': evidence.get('id'), 'path': path, 'result': reason or 'INCLUDED_READ_ONLY'})
    execution_adopted = source_context_records(source_context, producer='execution_helper')
    adopted_api = [r for r in execution_adopted if r.get('kind') == 'PUBLIC_DEPENDENCY_DECLARATION']
    compiler_api = [r for r in adopted_api if r.get('current_source_snapshot') == acceptance['snapshot_id']]
    new_execution_records = []
    used += sum(len(r['excerpt'].encode()) for r in compiler_api)
    support_limit = int(config.get('max_execution_support_bytes', 24000))
    if used > support_limit:
        raise BudgetExceeded('Adopted execution support exceeds the explicit support budget')
    if config.get('execution_helper_support_enabled'):
        from contractedge.execution.helper_context import compiler_dependency_support, local_helper_support
        snapshot = compute_snapshot(source)
        if snapshot.snapshot_id != acceptance['snapshot_id']:
            # This is source-integrity failure before dispatch, not an invalid
            # model plan/edit eligible for a format-correction retry.
            raise OSError('Source snapshot changed during execution context construction')
        remaining = max(0, support_limit-used)
        def api_already_supplied(record):
            return any(same_evidence_content(record, prior) for prior in adopted_api)
        new_api, compiler_audit = compiler_dependency_support(snapshot, feedback, max_bytes=remaining,
            max_records=max(0, min(2-len(adopted_api), 8-len(execution_adopted))),
            already_supplied=api_already_supplied)
        if not compiler_api and not new_api and previous_attempt:
            historical = (previous_attempt.get('rejected_trial') or {}).get('feedback')
            new_api, historical_audit = compiler_dependency_support(snapshot, historical, max_bytes=remaining,
                max_records=max(0, min(2-len(adopted_api), 8-len(execution_adopted))),
                already_supplied=api_already_supplied)
            compiler_audit.extend(historical_audit)
        remaining -= sum(len(r['excerpt'].encode()) for r in new_api)
        compiler_api.extend(new_api)
        support_audit.extend(compiler_audit)
        # Accepted locators use original file coordinates. The bounded display
        # can concatenate distant windows and cannot be sliced by those lines.
        # Resolve against files already read; only bounded helper excerpts are
        # added to the request, without expanding target windows/edit scope.
        helper_targets = [dict(path=s['path'], content=full_target_texts.get(s['path'])) for s in sources]
        def helper_already_supplied(record):
            path=record['locator']['path']
            return (any(all(record.get(k) == prior.get(k) for k in
                        ('kind','snapshot_id','locator','content_digest','excerpt')) for prior in support)
                    or target_supplies_support(targets.get(path),record,full_target_texts.get(path,'')))
        helpers, audit = local_helper_support(snapshot, helper_targets, plan['operations'], max_bytes=remaining,
            max_records=max(0,8-len(execution_adopted)-len(new_api)),already_supplied=helper_already_supplied)
        new_execution_records = [*helpers, *new_api]
        support.extend(helpers)
        support_audit.extend(audit)
    # Share only exact current bodies delivered in this request. A fresh read
    # does not replace the identity or body of the historical diagnosis.
    if context:
        context['source_support'] = [
            {k: v for k, v in e.items() if k != 'excerpt'}
            if e['id'] in target_ids or any(same_evidence_content(e, supplied) for supplied in support)
            else dict(e) for e in context.get('source_support', [])]
    observations=list(observations)
    if 'public_observations' in context:
        context.update(observations_prompt_view(context.pop('public_observations'),
            supplied_observations=observations))
    prompt_plan = {**dict(plan), **({'coordination_context': context} if context else {})}
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
    # Runtime facts in the original validated feedback may be compacted into
    # shared error tasks for delivery. Decide policy from their bound records,
    # never infer provenance from the task's human-readable description.
    reasoning_previous = previous_attempt
    if previous_attempt and isinstance(previous_attempt.get('rejected_trial'),Mapping):
        historical=previous_attempt['rejected_trial'].get('feedback')
        if isinstance(historical,Mapping):
            from contractedge.orchestration.feedback import historical_trial_prompt_view
            rejected={**previous_attempt['rejected_trial'],'feedback':historical_trial_prompt_view(historical,
                canonical_text=json.dumps(prompt_plan,ensure_ascii=False)+json.dumps(feedback_view,ensure_ascii=False))}
            if ('public_check_results' in rejected
                    and rejected['public_check_results'] == rejected['feedback'].get('public_check_results')):
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
    correction_ids = _execution_correction_change_ids(previous_attempt, acceptance['operation_order'])
    if not correction_ids:
        correction_ids = _build_failure_change_ids(diagnostic_feedbacks, plan['operations'])
    if correction_ids:
        # A rejected literal batch leaves the source untouched.  The consumer
        # therefore still requires the complete accepted operation set, but
        # the model only needs to *repair* the explicitly rejected operation;
        # valid edits from the previous batch should be copied verbatim.  This
        # bounded instruction prevents a correction from re-inventing anchors
        # for unrelated files and makes the correction target auditable.
        continuation = (plan.get('execution_continuation') or {}).get('prior_applied_operations') or {}
        prior_response_available = bool(previous_attempt and previous_attempt.get('model_response'))
        payload['execution_correction'] = {
            'rejected_change_ids': list(correction_ids),
            'preserve_other_edits': prior_response_available,
            'eligible_prior_change_ids': sorted(continuation),
            'policy': ('The prior literal transaction was rejected atomically. '
                       'Repair only rejected_change_ids using a fresh exact anchor. '
                       + ('Copy every other edit from previous_execution_attempt unchanged '
                          'so the returned batch still covers all accepted operation IDs.'
                          if prior_response_available else
                          'This is a public-build continuation: previously applied IDs in '
                          'continuation_operation_coverage may be omitted; return edits only '
                          'for rejected IDs and any remaining unapplied IDs.'))}
        payload['edit_contract'] += (
            ' This is a bounded correction. Repair only the rejected change IDs in '
            'execution_correction.rejected_change_ids; '
            + ('copy all other edits from previous_execution_attempt unchanged so the '
               'complete accepted batch remains covered. '
               if prior_response_available else
               'previously applied IDs may be omitted according to continuation_operation_coverage. ')
            + 'Do not redesign unrelated operations.')
    if compiler_api:
        payload['compiler_api_context'] = {'declarations': compiler_api, 'policy': (
            'These declarations come from hash-pinned public dependencies selected by a recorded compiler failure. '
            'Use the actual field types and method signatures to correct API usage in current_target_files. '
            'A declaration is read-only interface evidence, not an editable target or proof of behavior. '
            'The failure belongs to its recorded trial snapshot; the imported dependency is re-resolved from current source.')}
    payload['edit_batch_rules'] = {
        'multiple_edits_per_change_id': True,
        'allowed_edit_fields': ['change_id','path','operation','old_text','new_text'],
        'numbered_fields_allowed': False,
        'application_order': 'Separate regions are separate edits array items, reusing the same accepted change_id and path. Items for one operation apply in array order; each old_text matches the current virtual file after prior items. Never append numbered fields such as old_text_2, even when empty. The complete batch validates before writing.'}
    if config.get('helper_preconditions_enabled'):
        from contractedge.execution.preconditions import validate_helper_preconditions
        payload['helper_preconditions']=validate_helper_preconditions(
            plan, full_target_texts, [*covered_support, *support])
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
            'assumptions must remain unproven and be tested through the public boundary. '
            'When implementing a completion signal, account for zero initial work, the first item, and the full backlog. '
            'Signal after the required work is complete, not on receipt of the first item; an empty workload must not wait for an item that cannot arrive before readiness.')
        from contractedge.diagnosis.evidence import reader_reference_contexts
        from contractedge.source.parsers import parse_source_units
        target_units=[];target_refs={}
        for item in sources:
            if not item['path'].endswith('.go') or not item['content']:continue
            parsed=parse_source_units(item['path'],item['content'],acceptance['snapshot_id'],
                                      max_lines=768,max_chars=32000)
            target_units.extend(parsed)
            target_refs.update({u['id']:dict(path=item['path'],snapshot_id=acceptance['snapshot_id'],
                content_digest=hashlib.sha256(item['content'].encode()).hexdigest()) for u in parsed})
        # EvidenceRecord does not preserve the parser's truncation flag. Never
        # reconstruct an implicitly complete declaration from its excerpt.
        # Reuse the full text already read for freshness, and accept only a
        # complete declaration contained in the delivered canonical span.
        support_units=[]; support_refs={}; parsed_support={}
        for record in support:
            path=record.get('locator',{}).get('path','')
            if (record.get('kind')!='SOURCE_SPAN' or not record.get('id','').startswith('SRC-')
                    or not path.endswith('.go') or path not in support_source_texts):
                continue
            if path not in parsed_support:
                parsed_support[path]=parse_source_units(path,support_source_texts[path],
                    acceptance['snapshot_id'],max_lines=768,max_chars=32000)
            for unit in parsed_support[path]:
                if (unit.get('truncated') or unit.get('kind')!='FUNCTION_OR_TYPE'
                        or unit['locator']['start_line']<record['locator']['start_line']
                        or unit['locator']['end_line']>record['locator']['end_line']
                        or unit['text'] not in record['excerpt']):
                    continue
                support_units.append(unit)
                support_refs[unit['id']]=record
        readers=reader_reference_contexts([*target_units,*support_units])
        for row in readers:
            if row['candidate_id'] in target_refs:
                row['source_target']=target_refs[row.pop('candidate_id')]
                # This is a current_target_files quote, not an invented
                # diagnosis EvidenceRecord or a new allowed evidence ID.
                row.pop('source_evidence_ref')
            elif row['candidate_id'] in support_refs:
                parent=support_refs[row['candidate_id']]
                row.update(candidate_id=parent['id'][4:],source_evidence_ref=parent['id'],
                           source_content_digest=parent['content_digest'])
        while len(json.dumps(readers,ensure_ascii=False).encode())>8000:readers.pop()
        payload['source_reader_reference_contexts']=readers
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
    adopted_targets = [{'path': item['path'], 'content': full_target_texts[item['path']],
        'snapshot_id': acceptance['snapshot_id'], 'selected_target': dict(item),
        'source_file_sha256': full_target_file_digests[item['path']]}
        for item in sources if item['content'] is not None]
    source_draft = None
    if source_context is not None:
        source_draft = source_context_offer(source_context, reread_records, producer='reread')
        source_draft = source_context_offer(source_draft, new_execution_records,
            producer='execution_helper', targets=adopted_targets)
        full_context_records = [r for r in context.get('source_support', []) if 'excerpt' in r]
        exact_target_records = [r for r in adopted if r.get('kind') == 'SOURCE_SPAN'
            and r.get('snapshot_id') == acceptance['snapshot_id']
            and target_supplies_support(targets.get(r['locator']['path']), r,
                full_target_texts.get(r['locator']['path'], ''))]
        payload['adopted_source_context'] = source_context_projection(source_draft,
            supplied_records=[*support, *compiler_api, *full_context_records],
            target_supplied_records=exact_target_records,
            supplied_targets=adopted_targets)
        # The finite support budget includes retained historical bodies. Target
        # views keep their existing separate limits and whole-request admission.
        budget_records = {source_context_key(r): r for r in [*support, *compiler_api,
            *full_context_records, *payload['adopted_source_context']['records']]
            if 'excerpt' in r and r.get('kind') != 'STATIC_SEARCH'}
        if sum(len(r['excerpt'].encode()) for r in budget_records.values()) > support_limit:
            raise BudgetExceeded('Complete adopted execution support exceeds the explicit support budget')
    thinking, metadata = config.get('execution_thinking'), {}
    if thinking is not None:
        if thinking not in {'enabled', 'disabled'} or config.get('provider') != 'openai_compatible':
            raise PlanRejected('Execution thinking override requires a valid compatible transport setting')
        metadata['execution_thinking_policy'] = 'EXPLICIT_STAGE_CONFIGURATION'
    reasoning_policy=config.get('execution_reasoning_policy')
    if reasoning_policy not in {None,'complex_plan','failed_trial_only'}:
        raise PlanRejected('Unknown execution reasoning policy')
    complex_plan=(len(plan['operations'])>1 or any(
        r.get('disposition')=='REQUIRED' for r in plan.get('precondition_review',[])))
    previous=reasoning_previous or {}
    trial=previous.get('rejected_trial') or {}
    decision=trial.get('decision') or trial.get('candidate_decision') or {}
    if (trial.get('decision') and trial.get('candidate_decision')
            and trial['decision']!=trial['candidate_decision']):decision={}
    history=trial.get('feedback') or {}
    failed_predicates=(history.get('failure_tasks') or {}).get('failed_predicates',[])
    bound_trial=(previous.get('source_snapshot')==acceptance['snapshot_id']
        and decision.get('from_snapshot')==acceptance['snapshot_id']
        and bool(trial.get('snapshot_id')) and trial['snapshot_id']!=acceptance['snapshot_id']
        and decision.get('trial_snapshot')==trial['snapshot_id']==history.get('to_snapshot'))
    failed_behavior=(bound_trial and decision.get('decision')=='REJECT_REGRESSION'
        and any(p.get('snapshot_id')==trial['snapshot_id'] and p.get('status')=='FAIL'
                and p.get('probe_sha256') and p.get('content_digest') for p in failed_predicates))
    failed_runtime=False
    if bound_trial and decision.get('decision')=='UNCOMPARABLE' and history.get('build_state')=='FAILED':
        from contractedge.execution.public import has_candidate_runtime_failure
        failed_runtime=has_candidate_runtime_failure(dict(status='EXECUTION_FAILED',snapshot_id=trial['snapshot_id'],
            candidate_runtime_failures=[f for f in history.get('observation_diagnostics',[])
                if isinstance(f,Mapping) and f.get('kind')=='CANDIDATE_RUNTIME_FAILURE']))
    failed_trial=bound_trial and (failed_behavior or failed_runtime)
    if (reasoning_policy in {'complex_plan','failed_trial_only'}
            and (failed_trial or reasoning_policy=='complex_plan' and complex_plan)):
        if config.get('provider')!='openai_compatible':
            raise PlanRejected('Complex execution reasoning policy requires compatible transport')
        thinking='enabled'
        metadata['execution_thinking_policy']='FAILED_TRIAL_REASONING' if failed_trial else 'COMPLEX_PLAN_REASONING'
        if failed_runtime:metadata['execution_reasoning_trigger']='VERIFIED_RUNTIME_FAILURE'
    previous = previous_attempt or {}
    previous_metadata = previous.get('response_metadata') or {}
    usage = previous_metadata.get('usage') or {}
    completion = usage.get('completion_tokens', 0)
    reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0)
    # A public build/startup failure is discovered after a successful model
    # response (finish_reason=stop).  The next execution request is still a
    # correction, but the old truncation fallback could only recognize a
    # length-terminated response and therefore re-enabled expensive reasoning
    # for this bounded repair.  Prefer a concise correction whenever the
    # runner supplied a validation error for the same source snapshot.
    public_build_correction = any(
        isinstance(d, Mapping)
        and d.get('kind') == 'CANDIDATE_BUILD_FAILURE'
        and d.get('status') == 'FAILED'
        for item in diagnostic_feedbacks
        for d in item.get('observation_diagnostics', ())
    )
    validation_correction = (
        (bool(previous.get('validation_error'))
         and previous.get('source_snapshot') == acceptance['snapshot_id']
         and bool(_execution_correction_change_ids(previous_attempt, acceptance['operation_order'])
                  or previous.get('rejected_trial')))
        or (config.get('execution_truncation_retry') == 'disable_thinking'
            and public_build_correction))
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
            and validation_correction):
        if config.get('provider') != 'openai_compatible':
            raise PlanRejected('Validation correction fallback requires the configured compatible transport')
        thinking = 'disabled'
        metadata = {'execution_retry_policy': 'VALIDATION_FEEDBACK_DISABLE_THINKING'}
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
    request = ModelRequest(model=model_name(config['model']), prompt_version='topomen-source-execution.v1',
        messages=({'role': 'system', 'content': 'Implement the accepted semantic operations in the current source. Return literal JSON file edits only. Preserve the attached obligation triggers, relation conditions, dependency order and preservation constraints. Treat declared_artifacts as exact external input contracts: copy annotation names, configuration keys, command-line flags, paths and literal values verbatim; never replace a declared spelling with a semantically similar name. Read-only source support explains interfaces; it grants no edit permission. Historical diagnosis context is not fresh evidence. The plan is binding; do not perform new repository-wide diagnosis or invent execution results. When adding a configuration field, trace the actual public-to-internal conversion and update that bridge within the accepted target operation when the source requires it; use identifiers that exist in the current function scope, never an undefined shorthand such as cfg.'},
                  {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}),
        max_output_tokens=int(config.get('execution_max_output_tokens', 8192)), output_schema=schema,
        thinking=thinking, metadata=metadata)
    supplied_ids={r['id'] for r in support}
    missing_refs = prerequisite_refs - supplied_ids - target_ids - current_target_continuity_ids
    if missing_refs:
        raise PlanRejected('Prerequisite source was not delivered to execution: '+', '.join(
            sorted(missing_refs)))
    request = replace(request, metadata={**request.metadata, 'read_only_support_audit': support_audit,
        **({'source_context': source_draft} if source_draft is not None else {}),
        **({'covered_source_support': covered_support} if config.get('helper_preconditions_enabled') else {})})
    # Register the builder's selection before transport preparation. Dynamic
    # helpers and pinned dependency declarations are absent from the upstream
    # diagnosis catalog, but need the same final delivery check. Exact target
    # sharing keeps one body on the wire; this internal metadata adds no prompt.
    from contractedge.orchestration.semantic_delivery import with_adopted_source_support
    request = with_adopted_source_support(request, [*support, *compiler_api, *covered_support],
        current_targets=adopted_targets)
    quote = quote_request(provider, request, token_counter)
    # Selection has finished. An oversized request is rejected intact; do not
    # change the plan's evidence by popping support to make a request fit.
    require_context(quote, int(config['max_context_tokens']), config.get('model_context_tokens'),
        label='Execution request')
    return request, quote


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
    current = compute_snapshot(root)
    source_texts, code_texts = {}, {}
    for target in payload['current_target_files']:
        try:
            source_texts[target['path']] = safe_target(root, target['path']).read_text()
        except (OSError, ValueError, TypeError):
            # A missing helper target must invalidate its contract below,
            # rather than aborting before the caller's UNKNOWN checks exist.
            continue
    # Analysis records include validated excerpts already present in targets;
    # transport dedup must not erase the helper contract catalog. Metadata is
    # internal only and is never a substitute for final wire delivery.
    records = [*request.metadata.get('covered_source_support', ()),
               *payload['read_only_source_support']]
    helpers, unverified = [], set()
    for record in records:
        path = (record.get('locator') or {}).get('path')
        excerpt = record.get('excerpt')
        try:
            if path not in source_texts:
                source_texts[path] = safe_target(root, path).read_text()
            actual = source_texts[path]
            if (not isinstance(excerpt, str) or not excerpt or actual.count(excerpt) != 1
                    or hashlib.sha256(excerpt.encode()).hexdigest() != record.get('content_digest')):
                raise ValueError('Helper source no longer has a unique exact binding')
            offset = actual.index(excerpt)
            if path.endswith('.go'):
                from contractedge.retrieval.gap_delivery import _go_code
                if path not in code_texts:
                    code_texts[path] = _go_code(actual)
                standalone = _go_code(excerpt)
                if (not re.search(r'\bfunc\s+\w+\s*\(', standalone)
                        or code_texts[path][offset:offset + len(excerpt)] != standalone):
                    raise ValueError('Historical helper bytes are not a live current declaration')
            locator = reread_source_locator(record, actual)
            if locator is None:
                raise ValueError('Helper source has no exact current locator')
            helpers.append({**record, 'snapshot_id': current.snapshot_id,
                'locator': locator})
        except (OSError, ValueError, TypeError):
            # Retain the historical name/key only to identify checks that can
            # no longer be proved. Do not relabel changed source as current.
            helpers.append(record)
            unverified.add(record.get('id'))
    report = validate_helper_preconditions(payload['accepted_plan'],
        {s['path']:source_texts[s['path']] for s in payload['current_target_files'] if s['path'] in source_texts},
        helpers, unverified_evidence_ids=unverified)
    report['source_snapshot'] = current.snapshot_id
    if unverified:
        report['unverified_helper_evidence_refs'] = sorted(str(ref) for ref in unverified)
    return report


class ModelPlanExecutor:
    """Explicit direct backend; does not invoke the native TopoMen Agent."""
    def __init__(self, provider, token_counter, config: Mapping):
        self.provider, self.token_counter, self.config = provider, token_counter, dict(config)

    def execute(self, plan: Mapping, source: Path, policy: Mapping, *, requirement: str,
                declared_artifacts=(), observations=(), feedback: Mapping | None = None, previous_attempt=None,
                source_context=None) -> dict:
        request, quote = _build_execution_request(plan, source, policy, self.token_counter, self.config,
            requirement=requirement, declared_artifacts=declared_artifacts, observations=observations,
            feedback=feedback, previous_attempt=previous_attempt, provider=self.provider,
            source_context=source_context)
        response = complete_quoted(self.provider, request, quote)
        trial = getattr(self, 'trial_session', None)
        response_id = trial.record_response(request, response) if trial is not None else None
        return self.consume_received_response(request, response, plan, source, policy,
            trial_session=trial, response_id=response_id, requirement=requirement)

    def consume_received_response(self, request, response, plan, source, policy, *, trial_session=None,
                                  response_id=None, requirement: str | None = None):
        """Deterministic continuation; this method never calls the provider."""
        body = validate_execution_response(response, request)
        if trial_session is not None:
            trial_session.consume_response(response_id)
        result = apply_edits(plan, source, policy, body,
            require_behavior_change=json.loads(request.messages[1]['content'])['require_behavior_change'],
            trial_session=trial_session, requirement=requirement)
        report=check_edited_helper_preconditions(request,source,self.config)
        if report is not None:result['helper_preconditions']=report
        result['model_response'] = {'content': response.content, 'metadata': dict(response.metadata)}
        if trial_session is not None:
            trial_session.record('RESPONSE_CONSUME_COMPLETED',response_id=response_id,
                snapshot_id=result['to_snapshot'])
        return result
