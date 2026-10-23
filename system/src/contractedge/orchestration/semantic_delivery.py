"""Exact, reviewable semantic delivery at the existing admission boundary.

This is a delivery witness, not a semantic-equivalence or model-adoption judge.
Only previously required concrete evidence content can deny a dispatch here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Mapping

from contractedge.contracts.validation import canonical_digest


class SemanticDeliveryError(ValueError):
    pass


def _clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _pointer(key):
    return str(key).replace('~', '~0').replace('/', '~1')


def _legal_source_path(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(ord(char) < 32 for char in value)):
        return False
    path = PurePosixPath(value)
    return (not path.is_absolute() and path.as_posix() == value
            and all(part not in {'.', '..'} for part in path.parts))


def _source_identity(record):
    return {key: record.get(key) for key in ('id', 'kind', 'snapshot_id', 'locator', 'content_digest')}


def _walk(value, path='', depth=0):
    """Retain JSON pointers, including parsed JSON in an actual message string."""
    if depth > 40:
        return
    yield path or '/', value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk(child, path + '/' + _pointer(key), depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, path + '/' + str(index), depth + 1)
    elif isinstance(value, str) and value[:1] in {'{', '['}:
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return
        yield from _walk(parsed, path + '#json', depth + 1)


def safe_request_projection(request, quote):
    """Project content only; never serialize opaque prepared objects or headers."""
    payload = {'messages': _clone(list(request.messages)),
               'output_schema': _clone(request.output_schema)}
    authority = 'ACTUAL_MODEL_REQUEST'
    prepared = quote.prepared
    identity = {'request_digest': quote.request_digest,
                'prepared_digest': getattr(quote, '_prepared_digest', None)}
    if prepared is not None:
        body = prepared.body
        identity['body_sha256'] = hashlib.sha256(body).hexdigest()
        try:
            wire = json.loads(body)
        except (ValueError, TypeError, UnicodeError):
            wire = None
        if isinstance(wire, Mapping) and isinstance(wire.get('messages'), list):
            payload = {'messages': _clone(wire['messages'])}
            if 'tools' in wire:
                payload['tools'] = _clone(wire['tools'])
            response_format = wire.get('response_format')
            if isinstance(response_format, Mapping):
                schema = response_format.get('json_schema')
                if isinstance(schema, Mapping) and 'schema' in schema:
                    payload['output_schema'] = _clone(schema['schema'])
            authority = 'PREPARED_MESSAGES_SCHEMA_TOOLS'
        else:
            # Logical content does not certify a differently shaped opaque wire body.
            payload, authority = {}, 'UNKNOWN_PREPARED_CONTENT'
    return {'authority': authority, 'identity': identity, 'payload': payload}


def _subject(kind, record, *, identifier=None, required_basis=(), origin=None, projections=()):
    return {'kind': kind, 'id': identifier if identifier is not None else record.get('id'),
            'original': _clone(record), 'original_digest': canonical_digest(record),
            'required_basis': _clone(list(required_basis)), 'origin': _clone(origin),
            'explicit_projections': _clone(list(projections))}


def delivery_context(method_input, *, consumer, snapshot_id=None, diagnosis=None,
                     changeset=None, plan=None, feedback=None, necessary_support=(),
                     prerequisites=(), evidence=(), mandatory_refs=(), layer_absence=None,
                     source_requirements=True, source_root=None):
    """Use actual upstream objects; absent E/C layers are never reconstructed."""
    method = asdict(method_input) if is_dataclass(method_input) else dict(method_input or {})
    diagnosis = diagnosis if isinstance(diagnosis, Mapping) else None
    changeset = changeset if isinstance(changeset, Mapping) else None
    plan = plan if isinstance(plan, Mapping) else None
    subjects = []
    raw = method.get('requirement_text')
    if isinstance(raw, str) and raw:
        subjects.append(_subject('ORIGINAL_REQUIREMENT', {'text': raw}, identifier='requirement_text',
                                 origin={'sha256': _sha(raw), 'span': [0, len(raw)]}))
    for atom in (diagnosis or {}).get('obligation_atoms', []):
        spans = []
        for ref in atom.get('source_refs', []):
            pieces = ref.split(':') if isinstance(ref, str) else []
            if len(pieces) == 3 and pieces[0] == 'requirement_text' and all(p.isdigit() for p in pieces[1:]):
                start, end = map(int, pieces[1:])
                if isinstance(raw, str) and 0 <= start < end <= len(raw):
                    quote = raw[start:end]
                    spans.append({'reference': ref, 'span': [start, end], 'quote': quote, 'sha256': _sha(quote)})
        subjects.append(_subject('OBLIGATION', atom, origin={'status': 'BOUND' if spans else 'UNKNOWN', 'spans': spans}))
    for kind, key in [('EXPECTED_RELATION', 'expected_relations'), ('VIOLATION', 'violations')]:
        subjects.extend(_subject(kind, row) for row in (diagnosis or {}).get(key, []))
    operations = (plan or {}).get('operations', [])
    for change in (changeset or {}).get('changes', []):
        # This is an explicit adapter field projection, never an inferred synonym.
        projected = {'change_id': change['id']}
        projected.update({k: change[k] for k in ('operation', 'semantic_action', 'intent',
            'violation_refs', 'postcondition_refs', 'depends_on') if k in change})
        projections = []
        if any(op.get('change_id') == change['id'] for op in operations):
            projections.append({'kind': 'SUMMARY', 'recipe': 'TOPOMEN_OPERATION_FIELD_PROJECTION_V1',
                'source_digest': canonical_digest(change), 'value': projected})
        subjects.append(_subject('CHANGE', change, projections=projections))
    for key in ('postconditions', 'preserve_constraints', 'open_obligations'):
        subjects.extend(_subject('POSTCONDITION' if key == 'postconditions' else key.upper(), row)
                        for row in (changeset or plan or {}).get(key, []))
    support, source_conflicts, all_records, current_targets = {}, [], {}, {}
    required_ids = {r['id'] for r in necessary_support if r.get('delivery_required') is True}
    for row in [*(diagnosis or {}).get('evidence_refs', []),
                *((plan or {}).get('coordination_context') or {}).get('source_support', []),
                *evidence, *necessary_support]:
        if isinstance(row, Mapping) and isinstance(row.get('id'), str):
            identifier = row['id']
            if row.get('delivery_required') is True:
                required_ids.add(identifier)
            previous = support.get(identifier)
            all_records.setdefault(identifier, []).append(_clone(row))
            if previous is not None and (_source_identity(previous) != _source_identity(row)
                    or previous.get('excerpt') and row.get('excerpt') and previous['excerpt'] != row['excerpt']):
                # Preserve both originals. An ID cannot silently select a new version.
                source_conflicts.append({'id': identifier, 'records': _clone(all_records[identifier])})
            if previous is None or not previous.get('excerpt') and _source_identity(previous) == _source_identity(row):
                support[row['id']] = dict(row)
    prerequisites = {r.get('source_evidence_ref'): r for r in prerequisites
                     if r.get('disposition') != 'NOT_APPLICABLE'}
    for identifier in required_ids | set(prerequisites):
        if isinstance(identifier, str) and identifier not in support:
            support[identifier] = {'id': identifier, 'actual_original_record': 'MISSING'}
    for ref in mandatory_refs:
        if isinstance(ref.get('id'), str) and ref['id'] not in support:
            support[ref['id']] = {**dict(ref), 'actual_original_record': 'MISSING'}
    target_paths = {(op.get('target') or {}).get('path') for op in operations
                    if isinstance(op.get('target'), Mapping)}
    for identifier, row in support.items():
        basis = []
        if source_requirements and identifier in required_ids:
            basis.append({'kind': 'EXISTING_DELIVERY_REQUIRED', 'component': 'excerpt'})
        if source_requirements and identifier in prerequisites:
            basis.append({'kind': 'EXISTING_SOURCE_PREREQUISITE', 'component': 'excerpt',
                          'record': prerequisites[identifier]})
        if any(all(row.get(k) == ref.get(k) for k in ('id', 'snapshot_id', 'content_digest'))
               for ref in mandatory_refs):
            basis.append({'kind': 'EXISTING_MANDATORY_EVIDENCE', 'component': 'excerpt'})
        subject = _subject('EVIDENCE', row, required_basis=basis)
        path = (row.get('locator') or {}).get('path') if isinstance(row.get('locator'), Mapping) else None
        if source_root is not None and row.get('kind') == 'SOURCE_SPAN' and _legal_source_path(path):
            root = Path(source_root).resolve()
            target = root / path
            try:
                if (target.resolve().is_relative_to(root) and target.is_file()
                        and not any(p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(root))):
                    content = target.read_text(encoding='utf-8')
                    excerpt = row.get('excerpt')
                    if path in target_paths:
                        if (source_requirements and identifier in required_ids and isinstance(excerpt, str)
                                and excerpt and row.get('content_digest') == _sha(excerpt)
                                and isinstance(row.get('snapshot_id'), str) and row['snapshot_id']):
                            key = (path, snapshot_id)
                            if key not in current_targets:
                                record = {'id': 'CURRENT_TARGET:' + path, 'snapshot_id': snapshot_id,
                                    'path': path, 'content': content, 'content_digest': _sha(content),
                                    'source_file_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
                                current_targets[key] = _subject('CURRENT_TARGET_SOURCE', record)
                            current_targets[key]['required_basis'].append({
                                'kind': 'EXISTING_SELECTED_TARGET_CURRENT_SOURCE', 'component': 'current_file_bytes',
                                'source_evidence_id': identifier,
                                'producer_protocol': 'execution.topomen._build_execution_request:target_ids'})
                            subject['historical_required_basis'] = [basis for basis in subject['required_basis']
                                if basis['kind'] == 'EXISTING_DELIVERY_REQUIRED']
                            subject['required_basis'] = [basis for basis in subject['required_basis']
                                if basis['kind'] != 'EXISTING_DELIVERY_REQUIRED']
                            subject['current_target_delivery_ref'] = current_targets[key]['id']
                    if (row.get('snapshot_id') != snapshot_id and isinstance(excerpt, str)
                            and excerpt and content.count(excerpt) == 1):
                        start = content.count('\n', 0, content.index(excerpt)) + 1
                        subject['reread_identity'] = {**_source_identity(row), 'snapshot_id': snapshot_id,
                            'locator': {'path': path, 'start_line': start,
                                        'end_line': start + len(excerpt.splitlines()) - 1}}
            except (OSError, ValueError, UnicodeError):
                pass
        subjects.append(subject)
    subjects.extend(current_targets.values())
    if isinstance(feedback, Mapping):
        subjects.append(_subject('FEEDBACK', feedback, identifier='execution_feedback'))
    absence = dict(layer_absence or {})
    return {'consumer': consumer, 'run_id': (changeset or diagnosis or {}).get('run_id'),
            'snapshot_id': snapshot_id, 'input_digest': method.get('input_digest'),
            'layers': {key: ('PRESENT' if value is not None else absence.get(key, 'UNKNOWN'))
                       for key, value in [('diagnosis', diagnosis), ('changeset', changeset)]},
            'subjects': subjects, 'source_conflicts': source_conflicts}


def _contains_record(expected, actual):
    return isinstance(actual, Mapping) and all(key in actual and actual[key] == value
                                              for key, value in expected.items())


def _classify(subject, nodes, authority):
    original, identifier = subject['original'], subject['id']
    locations, identities, drift = [], [], []
    candidates = [(path, value) for path, value in nodes if isinstance(value, Mapping)
                  and identifier is not None and any(value.get(k) == identifier
                  for k in ('id', 'change_id', 'source_evidence_ref'))]
    identities = [path for path, value in nodes if value == identifier]
    if authority == 'UNKNOWN_PREPARED_CONTENT':
        return 'UNKNOWN', [], identities
    if subject['kind'] == 'CURRENT_TARGET_SOURCE':
        for path, value in nodes:
            if not isinstance(value, Mapping) or not isinstance(value.get('current_target_files'), list):
                continue
            for index, entry in enumerate(value['current_target_files']):
                if not isinstance(entry, Mapping) or entry.get('path') != original['path']:
                    continue
                pointer = path + '/current_target_files/' + str(index) + '/content'
                if (value.get('acceptance') or {}).get('snapshot_id') != original['snapshot_id']:
                    return 'IDENTITY_DRIFT', [{'pointer': path + '/acceptance/snapshot_id'}], identities
                if entry.get('content') != original['content']:
                    return 'CONTENT_DRIFT', [{'pointer': pointer}], identities
                return 'FULL_CURRENT_TARGET_BYTES', [{'pointer': pointer,
                    'snapshot_id': original['snapshot_id'], 'path': original['path'],
                    'content_sha256': original['content_digest'],
                    'source_file_sha256': original['source_file_sha256']}], identities
        return 'NOT_DELIVERED', [], identities
    if subject['kind'] == 'ORIGINAL_REQUIREMENT':
        text = original['text']
        locations = [{'pointer': path, 'span': [0, len(text)], 'sha256': _sha(text)}
                     for path, value in nodes if isinstance(value, str) and value == text]
    elif subject['kind'] == 'FEEDBACK':
        locations = [{'pointer': path} for path, value in nodes if value == original]
    else:
        locations = [{'pointer': path} for path, value in candidates if _contains_record(original, value)]
    if subject['kind'] == 'EVIDENCE' and isinstance(original.get('excerpt'), str) and original['excerpt']:
        excerpt = original['excerpt']
        if original.get('content_digest') != _sha(excerpt):
            return 'UNKNOWN', [], identities
        # The existing execution consumer supplies a target file instead of duplicating
        # a source excerpt. Bind the exact excerpt to that exact declared source path.
        target_path = (original.get('locator') or {}).get('path') if isinstance(original.get('locator'), Mapping) else None
        bound = [(path, value) for path, value in candidates if _source_identity(value) == _source_identity(original)]
        reread = [(path, value) for path, value in candidates
                  if subject.get('reread_identity') is not None
                  and _source_identity(value) == subject['reread_identity']]
        identity_drift = []
        for path, value in candidates:
            if any(k in value and value[k] != original.get(k) for k in ('snapshot_id', 'locator', 'kind')):
                if not any(path == valid_path for valid_path, _ in reread):
                    identity_drift.append({'pointer': path, 'expected_identity': _source_identity(original),
                                           'actual_identity': _source_identity(value)})
        if identity_drift:
            return 'IDENTITY_DRIFT', identity_drift, identities
        for path, value in nodes:
            if (_legal_source_path(target_path) and isinstance(value, Mapping) and value.get('path') == target_path
                    and isinstance(value.get('content'), str) and bound):
                content = value['content']
                start = content.find(excerpt)
                if start >= 0:
                    locations.append({'pointer': path + '/content', 'span': [start, start + len(excerpt)],
                                      'sha256': _sha(excerpt), 'binding': 'EXACT_DECLARED_SOURCE_PATH'})
        for path, value in candidates:
            if value.get('excerpt') == excerpt:
                if any(path == bound_path for bound_path, _ in bound):
                    locations.append({'pointer': path + '/excerpt', 'span': [0, len(excerpt)],
                                      'sha256': _sha(excerpt), 'binding': 'EXACT_IDENTIFIED_EXCERPT'})
            if value.get('content_digest') not in (None, original['content_digest']):
                drift.append(path + '/content_digest')
            if 'excerpt' in value and value['excerpt'] != excerpt:
                drift.append(path + '/excerpt')
        if drift:
            return 'CONTENT_DRIFT', [{'pointer': path} for path in drift], identities
        if locations:
            return 'FULL_ORIGINAL', locations, identities
        for path, value in reread:
            if value.get('excerpt') == excerpt:
                return 'EXPLICIT_SOURCE_REREAD', [{'pointer': path + '/excerpt', 'sha256': _sha(excerpt),
                    'origin_identity': _source_identity(original), 'current_identity': subject['reread_identity'],
                    'recipe': 'INDEPENDENT_EXACT_UNCHANGED_SOURCE_REREAD'}], identities
    else:
        for path, value in candidates:
            if any(k in value and value[k] != original[k] for k in ('statement', 'semantic_action') if k in original):
                drift.append(path)
    if drift:
        return 'CONTENT_DRIFT', [{'pointer': path} for path in drift], identities
    if locations:
        return 'FULL_ORIGINAL', locations, identities
    for projection in subject['explicit_projections']:
        if projection.get('kind') != 'SUMMARY' or projection.get('source_digest') != subject['original_digest']:
            continue
        matches = [{'pointer': path, 'recipe': projection['recipe'],
                    'projection_digest': canonical_digest(projection['value'])}
                   for path, value in nodes if _contains_record(projection['value'], value)]
        if matches:
            return 'EXPLICIT_SUMMARY', matches, identities
    return ('IDENTITY_ONLY' if identities or candidates else 'NOT_DELIVERED'), [], identities


def inspect_delivery(request, quote, context):
    projection = safe_request_projection(request, quote)
    nodes = list(_walk(projection['payload']))
    rows, issues = [], []
    for subject in context.get('subjects', []):
        classification, locations, identity_locations = _classify(subject, nodes, projection['authority'])
        original = subject['original']
        concrete = (subject['kind'] == 'EVIDENCE' and isinstance(original.get('excerpt'), str) and bool(original['excerpt'])
                    and original.get('content_digest') == _sha(original['excerpt'])
                    and isinstance(original.get('snapshot_id'), str) and bool(original['snapshot_id'])
                    and (original.get('kind') != 'SOURCE_SPAN' or _legal_source_path(
                        original.get('locator', {}).get('path') if isinstance(original.get('locator'), Mapping) else None)))
        if subject['kind'] == 'CURRENT_TARGET_SOURCE':
            concrete = (isinstance(original.get('content'), str) and original.get('content_digest') == _sha(original['content'])
                and _legal_source_path(original.get('path')) and isinstance(original.get('snapshot_id'), str)
                and bool(original['snapshot_id']) and isinstance(original.get('source_file_sha256'), str)
                and len(original['source_file_sha256']) == 64)
        required = bool(subject['required_basis'])
        row = {**subject, 'classification': classification, 'locations': locations,
               'identity_locations': identity_locations, 'required_content_gate': required}
        rows.append(row)
        origin_ids = {basis.get('source_evidence_id') for basis in subject['required_basis']}
        conflict = next((row for row in context.get('source_conflicts', [])
                         if row['id'] == subject['id'] or row['id'] in origin_ids), None)
        if conflict:
            row['classification'] = classification = 'UNKNOWN_CONFLICTING_ORIGINAL_RECORDS'
        code = ('CONFLICTING_ORIGINAL_SOURCE' if required and conflict else
                'REQUIRED_SOURCE_UNVERIFIABLE' if required and not concrete else
                'SEMANTIC_DELIVERY_REQUIRED_CONTENT_MISSING')
        if required and (conflict or not concrete or classification not in {
                'FULL_ORIGINAL', 'EXPLICIT_SOURCE_REREAD', 'FULL_CURRENT_TARGET_BYTES'}):
            issues.append({'code': code, 'subject_id': subject['id'],
                'classification': classification, 'consumer': context['consumer'],
                'expected_component': 'current_file_bytes' if subject['kind'] == 'CURRENT_TARGET_SOURCE'
                                      else 'exact original excerpt', 'locations': locations,
                'identity_locations': identity_locations, 'required_basis': subject['required_basis']})
    required_count = sum(row['required_content_gate'] for row in rows)
    status = ('REQUIRED_CONTENT_GAP' if issues else 'REQUIRED_CONTENT_VERIFIED' if required_count
              else 'RECORDED_WITHOUT_REQUIRED_CONTENT_CLAIM')
    return {'schema_version': 'ce.semantic_delivery.v1', 'status': status,
            'context': {k: v for k, v in context.items() if k != 'subjects'},
            'request_projection': projection, 'subjects': rows, 'issues': issues,
            'required_content_count': required_count, 'adoption': 'UNVERIFIED',
            'interpretation': 'Exact delivery witnesses only; summaries and identity references are never original content or adoption proof.'}


def require_delivery(report):
    if report['issues']:
        issue = report['issues'][0]
        raise SemanticDeliveryError(issue['code'] + ': ' + issue['consumer'] + ':'
                                    + str(issue['subject_id']) + ':' + issue['classification'])


class SemanticDeliveryMonitor:
    """Observe the same BudgetedProvider/quote; no re-quoting or budget domain."""
    def __init__(self, provider, journal):
        self.provider, self.journal = provider, journal
        self.context, self.sequence, self.delivery_refs = None, 0, []
        original = provider.complete_quoted

        def complete(request, quote):
            if self.context is None:
                return original(request, quote)
            context = _clone(self.context)
            report = inspect_delivery(request, quote, context)
            start, error = journal.position(), None
            try:
                require_delivery(report)
                return original(request, quote)
            except Exception as exc:
                error = exc
                raise
            finally:
                from contractedge.orchestration.recorder import preserve_error
                def record():
                    refs = journal.refs(start)
                    report['call_refs'] = [asdict(ref) for ref in refs]
                    report['dispatch_status'] = 'RECORDED_CALL_FACT' if refs else 'NO_DISPATCH'
                    value = journal.recorder.capture_value(report, 'SEMANTIC_DELIVERY', snapshot_id=context.get('snapshot_id'))
                    receipt = journal.record('semantic_delivery', attempt_id='delivery-' + str(self.sequence),
                        snapshot_id=context.get('snapshot_id'), status=report['status'], call_start=start,
                        output_refs=(value,), issues=tuple(report['issues']))
                    self.sequence += 1
                    self.delivery_refs.append(journal.recorder.receipt_ref(receipt))
                preserve_error(error, record)
        provider.complete_quoted = complete
        provider._semantic_delivery_monitor = self

    @contextmanager
    def scope(self, context):
        previous, self.context = self.context, context
        try:
            yield self
        finally:
            self.context = previous

    def outcome(self, context, *, artifacts=(), execution=None, review=None, observations=None):
        """Attach actual edits/checks as evidence; never certify model adoption."""
        links = []
        execution_nodes = list(_walk(execution))
        observation_nodes = list(_walk(observations))
        for subject in context.get('subjects', []):
            if subject['kind'] == 'CHANGE':
                for path, value in execution_nodes:
                    if isinstance(value, Mapping) and value.get('change_id') == subject['id']:
                        links.append({'subject_id': subject['id'], 'kind': 'ACTUAL_EXECUTION_ID_LINK',
                                      'pointer': '/execution' + path, 'adoption': 'UNVERIFIED'})
            if subject['kind'] == 'POSTCONDITION':
                ids = set(subject['original'].get('behavior_check_refs', []))
                for path, value in observation_nodes:
                    if isinstance(value, Mapping) and (value.get('behavior_check') or {}).get('id') in ids:
                        links.append({'subject_id': subject['id'], 'kind': 'BOUNDED_PUBLIC_CHECK',
                            'pointer': '/public_observations' + path,
                            'check_id': value['behavior_check']['id'],
                            'status': value['behavior_check'].get('status'), 'adoption': 'UNVERIFIED'})
        result = {'schema_version': 'ce.semantic_delivery_outcome.v1', 'context': context,
                  'delivery_receipt_refs': [asdict(ref) for ref in self.delivery_refs],
                  'result_artifact_refs': [asdict(ref) for ref in artifacts],
                  'execution': _clone(execution), 'review': _clone(review),
                  'public_observations': _clone(observations), 'subject_result_links': links, 'adoption': 'UNVERIFIED',
                  'interpretation': 'Actual consumer/patch/check evidence. ID links do not establish causal adoption or hidden correctness.'}
        value = self.journal.recorder.capture_value(result, 'SEMANTIC_DELIVERY_OUTCOME',
                                                   snapshot_id=context.get('snapshot_id'))
        return self.journal.record('semantic_outcome', attempt_id='outcome-' + str(self.sequence),
            snapshot_id=context.get('snapshot_id'), status='RESULT_EVIDENCE_RECORDED',
            call_start=self.journal.position(), input_refs=tuple(artifacts) + tuple(self.delivery_refs),
            output_refs=(value,))


def install_monitor(provider, journal):
    existing = getattr(provider, '_semantic_delivery_monitor', None)
    if existing is not None:
        if existing.journal is not journal:
            raise ValueError('Semantic delivery monitor belongs to another actual ledger')
        return existing
    return SemanticDeliveryMonitor(provider, journal)
