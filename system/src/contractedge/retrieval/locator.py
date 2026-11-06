"""Read-only locator session: the model chooses one source action at a time.

FRONT_HALF_REDESIGN_IMPLEMENTATION_SPEC sections 3.2, 7.3 and 7.4. The locator
has its own conversation but no budget, ledger or dispatch of its own: every
model call goes through the ``complete`` callable supplied by the runner,
which quotes, admits, records and dispatches it like any other front request.

Tools are SourceNavigator.search/read/follow_dependency. The model ends a
session with ``finish`` and names the handles Diagnosis needs; the program
packages the exact bodies of those reads (adoption decision recorded
2026-11-06: delivery by handle selection). Unselected reads stay in the
session record for audit and are not passed downstream. A finish can neither
deliver a handle it never read nor replace a body with a summary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from contractedge.diagnosis.evidence import source_record_from_read
from contractedge.model.provider import ModelRequest
from contractedge.source.navigation import NavigationError, SourceNavigator

_TEXT = {'type': 'string'}
ACTION_SCHEMA: dict[str, Any] = {
    'type': 'object', 'required': ['action'], 'additionalProperties': False,
    'properties': {
        'action': {'type': 'string', 'enum': ['search', 'read', 'follow_dependency', 'finish']},
        'arguments': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'scope': _TEXT, 'symbol': _TEXT, 'receiver': _TEXT, 'text': _TEXT, 'kind': _TEXT,
                           'cursor': _TEXT, 'handle': _TEXT, 'path': _TEXT, 'selector': _TEXT,
                           'start_line': {'type': 'integer', 'minimum': 1},
                           'end_line': {'type': 'integer', 'minimum': 1}},
        },
        'deliver': {'type': 'array', 'items': _TEXT},
        'open_questions': {'type': 'array', 'items': _TEXT},
    },
}

SYSTEM_PROMPT = (
    'You are the read-only source locator for one software repair task. Find the source the diagnosis needs to '
    'explain the requirement: the code that implements, consumes or should enforce it, and any fixed dependency API '
    'it relies on. Reply with exactly one JSON object per turn matching action_schema; no markdown.'
    ' Actions: search (scope = directory or file; symbol/receiver = declaration names; text = an operation or literal'
    ' to check; use cursor to continue a page or an incomplete scan), read (a handle from a result, or path with'
    ' symbol/receiver, or path with start_line/end_line), follow_dependency (from a read Go declaration handle,'
    ' selector alias.Name, alias, Name or recv.Method), finish (deliver = handles of read bodies the diagnosis needs,'
    ' open_questions = what you could not establish).'
    ' Search results are names and positions only; read what you rely on. A scan that is not complete is not'
    ' evidence of absence. Ambiguous or unresolved bindings stay ambiguous; never guess one. Deliver only handles'
    ' you actually read; the runner passes their exact text to the diagnosis. Do not deliver unrelated code you'
    ' read while exploring. You cannot edit, run commands or read hidden tests or solutions.')


@dataclass(frozen=True)
class LocatorLimits:
    max_steps: int = 24
    no_progress_steps: int = 3
    max_output_tokens: int = 2048
    page_size: int = 20


@dataclass
class LocatorCorrections:
    """Format corrections shared by every locator session of one task."""
    limit: int = 2
    used: int = 0


@dataclass
class LocatorOutcome:
    status: str                      # FINISHED | STEP_LIMIT | NO_PROGRESS | INVALID_OUTPUT
    delivered_records: list[dict[str, Any]]
    delivered_handles: list[str]
    open_questions: list[str]
    record: dict[str, Any]           # front.locator.json projection
    reason: str = ''


@dataclass
class _Session:
    reads: dict[str, dict[str, Any]] = field(default_factory=dict)
    seen_hits: set[str] = field(default_factory=set)
    results: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)


def _compact_hit(hit: Mapping[str, Any]) -> dict[str, Any]:
    row = {k: hit[k] for k in ('handle', 'path', 'symbol', 'receiver', 'kind', 'start_line', 'end_line', 'bytes',
                               'matched_by', 'contains_text') if k in hit and hit[k] not in (None, '')}
    if hit.get('text_hit_lines'):
        row['text_hit_lines'] = list(hit['text_hit_lines'][:10])
        row['text_hit_count'] = hit.get('text_hit_count')
    if hit.get('read_hint'):
        row['read_hint'] = hit['read_hint']
    return row


def _key(action: str, arguments: Mapping[str, Any]) -> str:
    return json.dumps([action, {k: v for k, v in sorted(arguments.items()) if v not in (None, '')}], sort_keys=True)


def run_locator(navigator: SourceNavigator, *, questions: list[Mapping[str, Any]], task: Mapping[str, Any],
                complete: Callable[[ModelRequest], Any], truncated: Callable[[Any], bool], model: str,
                current_snapshot: str, limits: LocatorLimits | None = None,
                corrections: LocatorCorrections | None = None, prior_delivered: Mapping[str, Mapping] = (),
                session_id: str = 'locator-1') -> LocatorOutcome:
    """One bounded locator session for the given questions (initial or follow-up)."""
    limits = limits or LocatorLimits()
    corrections = corrections or LocatorCorrections()
    prior = dict(prior_delivered or {})
    session = _Session()
    validator = Draft202012Validator(ACTION_SCHEMA)
    opening = {
        'task': dict(task), 'questions': [dict(q) for q in questions],
        'already_delivered': [{'handle': h, **{k: r.get(k) for k in ('path', 'symbol', 'receiver',
                                                                     'start_line', 'end_line')}}
                              for h, r in prior.items()],
        'action_schema': ACTION_SCHEMA,
    }
    messages: list[dict[str, str]] = [{'role': 'system', 'content': SYSTEM_PROMPT},
                                      {'role': 'user', 'content': json.dumps(opening, ensure_ascii=False)}]
    no_progress = 0
    status, reason, delivered, open_questions = 'STEP_LIMIT', 'Locator step limit reached', [], []
    for index in range(1, limits.max_steps + 1):
        request = ModelRequest(model=model, prompt_version='locator.v1', messages=tuple(messages),
                               max_output_tokens=limits.max_output_tokens, temperature=0.0,
                               metadata={'locator_session': session_id, 'locator_step': index},
                               output_schema=ACTION_SCHEMA)
        response = complete(request)
        content = getattr(response, 'content', '')
        messages.append({'role': 'assistant', 'content': content})
        problem = None
        value = None
        if truncated(response):
            # A cut action is never executed or completed by the program.
            problem = 'Your reply was cut off. Send one short complete action object.'
        else:
            try:
                value = json.loads(content)
                errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
                if errors:
                    problem = 'Action does not match action_schema: ' + errors[0].message[:300]
            except (TypeError, ValueError):
                problem = 'Reply was not one JSON object.'
        if problem is not None:
            session.steps.append({'index': index, 'action': 'INVALID', 'problem': problem})
            if corrections.used >= corrections.limit:
                status, reason = 'INVALID_OUTPUT', problem
                break
            corrections.used += 1
            messages.append({'role': 'user', 'content': json.dumps({'correction': problem})})
            continue
        action, arguments = value['action'], dict(value.get('arguments') or {})
        step = {'index': index, 'action': action, 'arguments': arguments, 'result_handles': [], 'read_units': []}
        session.steps.append(step)
        if action == 'finish':
            wanted = list(dict.fromkeys(value.get('deliver', [])))
            unknown = [h for h in wanted if h not in session.reads and h not in prior]
            if unknown:
                problem = 'Deliver only handles you read in this task: ' + ', '.join(unknown[:8])
                if corrections.used >= corrections.limit:
                    wanted = [h for h in wanted if h not in unknown]
                    step['dropped_unread_handles'] = unknown
                else:
                    corrections.used += 1
                    messages.append({'role': 'user', 'content': json.dumps({'correction': problem})})
                    continue
            delivered = [h for h in wanted if h not in prior]
            open_questions = [q for q in value.get('open_questions', []) if isinstance(q, str) and q.strip()]
            status, reason = 'FINISHED', ''
            step['deliver'] = wanted
            break
        key = _key(action, arguments)
        repeated = key in session.results
        try:
            if repeated:
                result = {'repeated_query': True, 'result': session.results[key]}
            else:
                result = _execute(navigator, action, arguments, limits)
                session.results[key] = result
        except NavigationError as exc:
            result = {'error': exc.reason, 'message': str(exc)[:300],
                      **({'candidates': [_compact_hit(c) for c in exc.details.get('candidates', [])][:20]}
                         if exc.details.get('candidates') else {}),
                      **({k: exc.details[k] for k in ('size', 'limit', 'parse_status') if k in exc.details})}
            session.results.setdefault(key, result)
        progress = False
        if not repeated and 'error' not in result:
            if action == 'read':
                if result['handle'] not in session.reads:
                    progress = True
                session.reads[result['handle']] = result
                step['read_units'] = [{k: result[k] for k in ('path', 'start_line', 'end_line', 'bytes', 'handle')}]
            else:
                handles = {h['handle'] for h in result.get('hits', []) if h.get('handle')}
                fresh = handles - session.seen_hits
                session.seen_hits |= handles
                step['result_handles'] = sorted(handles)
                progress = bool(fresh) or bool(result.get('next_cursor') and arguments.get('cursor')
                                               != result.get('next_cursor'))
        no_progress = 0 if progress else no_progress + 1
        messages.append({'role': 'user', 'content': json.dumps({'tool_result': result}, ensure_ascii=False)})
        if no_progress >= limits.no_progress_steps:
            status, reason = 'NO_PROGRESS', f'{no_progress} consecutive actions found nothing new'
            break
    records = []
    for handle in delivered:
        records.append(source_record_from_read(session.reads[handle], current_snapshot=current_snapshot))
    unit = lambda r: {'path': r['path'], 'start_line': r['start_line'], 'end_line': r['end_line'],
                      'truncated': False, 'bytes': r['bytes'], 'handle': r['handle'],
                      'source_digest': r['file_digest']}
    record = {'schema_version': 'front_half.locator_record.v1', 'session_id': session_id, 'status': status,
              'reason': reason, 'questions': [dict(q) for q in questions], 'steps': session.steps,
              'reads': [unit(r) for r in session.reads.values()],
              'delivered': [unit(session.reads[h]) for h in delivered], 'open_questions': open_questions,
              'format_corrections_used': corrections.used}
    return LocatorOutcome(status, records, delivered, open_questions, record, reason)


def _execute(navigator: SourceNavigator, action: str, arguments: Mapping[str, Any],
             limits: LocatorLimits) -> dict[str, Any]:
    if action == 'search':
        query = {k: arguments[k] for k in ('scope', 'symbol', 'receiver', 'text', 'kind', 'cursor')
                 if arguments.get(k) not in (None, '')}
        page = navigator.search(page_size=limits.page_size, **query)
        return {'hits': [_compact_hit(h) for h in page['hits']], 'next_cursor': page['next_cursor'],
                'scan': {k: page['scan'][k] for k in ('files_in_scope', 'reached_end_of_scope', 'complete',
                                                      'stop_reason', 'parse_incomplete_files')},
                'skipped_files': len(page['scan']['skipped_files'])}
    if action == 'read':
        query = {k: arguments[k] for k in ('path', 'symbol', 'receiver', 'start_line', 'end_line')
                 if arguments.get(k) not in (None, '')}
        return navigator.read(arguments.get('handle') or None, **query)
    if not arguments.get('handle'):
        raise NavigationError('EMPTY_FOLLOW', 'follow_dependency needs the handle of a read Go declaration')
    result = navigator.follow_dependency(arguments['handle'], selector=arguments.get('selector'),
                                         page_size=limits.page_size, cursor=arguments.get('cursor') or None)
    return {'binding': {k: v for k, v in result['binding'].items() if k != 'dependency_scan'},
            'hits': [_compact_hit(h) for h in result['hits']], 'next_cursor': result['next_cursor']}
