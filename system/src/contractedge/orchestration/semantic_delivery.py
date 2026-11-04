"""Exact, reviewable semantic delivery at the existing admission boundary.

This is a delivery witness, not a semantic-equivalence or model-adoption judge.
Only previously required concrete evidence content can deny a dispatch here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass, replace
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Mapping

from contractedge.contracts.validation import canonical_digest
from contractedge.contracts.delivery import target_supplies_source, target_supplies_support, reread_source_locator
from contractedge.diagnosis.evidence import merge_evidence_records


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


def _evidence_key(record):
    snapshot = (record.get('current_source_snapshot') if record.get('kind') == 'PUBLIC_DEPENDENCY_DECLARATION'
                else record.get('snapshot_id'))
    return record.get('id'), snapshot if isinstance(snapshot, str) else None


def with_adopted_source_support(request, records, *, current_targets=()):
    """Freeze builder-selected read-only records independently of prepared bytes.

    Source sharing remains the existing exact target/excerpt contract. Pinned
    public dependency declarations retain their external provenance and never
    become editable source-root paths or additional model-required fields.
    """
    selected = [*request.metadata.get('adopted_source_support', ()), *records]
    targets = [*request.metadata.get('adopted_current_targets', ()), *current_targets]
    if not selected and not targets:
        return request
    by_key = {}
    try:
        for record in selected:
            if not isinstance(record, Mapping) or record.get('kind') not in {
                    'SOURCE_SPAN', 'PUBLIC_DEPENDENCY_DECLARATION'}:
                raise ValueError('Adopted source support must use an existing source/API record kind')
            key = _evidence_key(record)
            by_key[key] = merge_evidence_records([by_key[key], record] if key in by_key else [record])[0]
    except ValueError as exc:
        raise SemanticDeliveryError('Invalid adopted source support: ' + str(exc)) from exc
    return replace(request, metadata={**request.metadata,
        'adopted_source_support': _clone(list(by_key.values())),
        **({'adopted_current_targets': _clone(targets)} if targets else {})})


def _with_request_source_subjects(context, request):
    """Add selected builder facts to the same evidence namespace and gate."""
    context = {**context, 'subjects': list(context.get('subjects', [])),
               'source_conflicts': list(context.get('source_conflicts', [])),
               'current_target_sources': dict(context.get('current_target_sources', {}))}
    source_context = request.metadata.get('source_context')
    adopted_records = list(request.metadata.get('adopted_source_support', ()))
    if source_context is not None:
        from contractedge.diagnosis.evidence import source_context_records, source_context_target_view
        adopted_records.extend(source_context_records(source_context))
        # Current target gates still verify original file/window coordinates.
        # Earlier adopted views live in their own snapshot namespace and must
        # remain complete; they cannot be matched against a newer target path.
        for index, target in enumerate(source_context.get('targets', ())):
            if target in request.metadata.get('adopted_current_targets', ()):
                continue
            view = source_context_target_view(target)
            context['subjects'].append(_subject('HISTORICAL_CONTEXT', view,
                identifier='adopted_target_view:'+str(index), required_basis=[{
                    'kind':'ADOPTED_REQUEST_SOURCE_SUPPORT','component':'complete_context'}]))
    # Every selected target view is adopted material, including simple plans
    # without an upstream source catalog. Freeze both the real file and the
    # chosen view: a transport cannot shrink two valid windows into one.
    for target in request.metadata.get('adopted_current_targets', ()):
        path = target['path']
        original = {'id': 'CURRENT_TARGET:' + path, **target,
                    'content_digest': _sha(target['content'])}
        basis = {'kind': 'ADOPTED_EXECUTION_TARGET_SOURCE', 'component': 'selected_source_view'}
        found = next((index for index, subject in enumerate(context['subjects'])
            if subject['kind'] == 'CURRENT_TARGET_SOURCE' and subject['id'] == original['id']), None)
        if found is None:
            context['subjects'].append(_subject('CURRENT_TARGET_SOURCE', original, required_basis=[basis]))
        else:
            subject = _clone(context['subjects'][found])
            if not _contains_record(subject['original'], original):
                context['source_conflicts'].append({'id': original['id'], 'snapshot_id': original['snapshot_id'],
                    'records': [subject['original'], original]})
            subject['original'] = original
            subject['original_digest'] = canonical_digest(original)
            subject['required_basis'].append(basis)
            context['subjects'][found] = subject
        context['current_target_sources'][path] = {key: original[key]
            for key in ('content', 'snapshot_id', 'source_file_sha256')}
    for record in adopted_records:
        basis = {'kind': 'ADOPTED_REQUEST_SOURCE_SUPPORT', 'component': 'excerpt'}
        found = next((index for index, subject in enumerate(context['subjects'])
            if subject['kind'] == 'EVIDENCE' and _evidence_key(subject['original']) == _evidence_key(record)), None)
        if found is None:
            context['subjects'].append(_subject('EVIDENCE', record, required_basis=[basis]))
            continue
        subject = _clone(context['subjects'][found])
        if basis not in subject['required_basis']:
            subject['required_basis'].append(basis)
        try:
            subject['original'] = merge_evidence_records([subject['original'], record])[0]
            subject['original_digest'] = canonical_digest(subject['original'])
        except ValueError:
            context['source_conflicts'].append({'id': record.get('id'),
                'snapshot_id': _evidence_key(record)[1], 'records': [subject['original'], _clone(record)]})
        context['subjects'][found] = subject
    return context


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
                     source_requirements=True, source_root=None, declared_artifacts=(),
                     feedback_required=False, historical_contexts=(), execution_mapping=None):
    """Use actual upstream objects; absent E/C layers are never reconstructed."""
    method = asdict(method_input) if is_dataclass(method_input) else dict(method_input or {})
    diagnosis = diagnosis if isinstance(diagnosis, Mapping) else None
    changeset = changeset if isinstance(changeset, Mapping) else None
    plan = plan if isinstance(plan, Mapping) else None
    subjects = []
    raw = method.get('requirement_text')
    if isinstance(raw, str) and raw:
        subjects.append(_subject('ORIGINAL_REQUIREMENT', {'text': raw}, identifier='requirement_text',
                                 required_basis=[{'kind': 'ADOPTED_ORIGINAL_REQUIREMENT',
                                                  'component': 'complete_requirement_text'}],
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
    support, source_conflicts, all_records, current_targets, current_sources = {}, [], {}, {}, {}
    required_keys = {_evidence_key(r) for r in necessary_support if r.get('delivery_required') is True}
    for row in [*(diagnosis or {}).get('evidence_refs', []),
                *((plan or {}).get('coordination_context') or {}).get('source_support', []),
                *((plan or {}).get('coordination_context') or {}).get('public_observations', []),
                *evidence, *necessary_support]:
        if isinstance(row, Mapping) and isinstance(row.get('id'), str):
            identifier = row['id']
            key = _evidence_key(row)
            if row.get('delivery_required') is True:
                required_keys.add(key)
            previous = support.get(key)
            all_records.setdefault(key, []).append(_clone(row))
            if previous is None:
                support[key] = dict(row)
                continue
            # An identity-only copy may reuse a complete same-identity upstream
            # record. Neither copy can replace another snapshot or change any
            # other metadata; historical namespaces remain independent.
            left, right = dict(previous), dict(row)
            if _source_identity(left) == _source_identity(right):
                if 'excerpt' not in left and isinstance(right.get('excerpt'), str):
                    left['excerpt'] = right['excerpt']
                if 'excerpt' not in right and isinstance(left.get('excerpt'), str):
                    right['excerpt'] = left['excerpt']
            try:
                support[key] = merge_evidence_records([left, right])[0]
            except ValueError:
                source_conflicts.append({'id': identifier, 'snapshot_id': row.get('snapshot_id'),
                                         'records': _clone(all_records[key])})
    prerequisites = {r.get('source_evidence_ref'): r for r in prerequisites
                     if r.get('disposition') != 'NOT_APPLICABLE'}
    for identifier in prerequisites:
        if isinstance(identifier, str) and not any(key[0] == identifier for key in support):
            support[(identifier, snapshot_id)] = {'id': identifier, 'snapshot_id': snapshot_id,
                                                  'actual_original_record': 'MISSING'}
    mandatory_keys = set()
    for ref in mandatory_refs:
        if not isinstance(ref.get('id'), str):
            continue
        key = _evidence_key(ref)
        mandatory_keys.add(key)
        if key not in support:
            support[key] = {**dict(ref), 'actual_original_record': 'MISSING'}
        elif support[key].get('content_digest') != ref.get('content_digest'):
            source_conflicts.append({'id': ref['id'], 'snapshot_id': ref.get('snapshot_id'),
                                     'records': [_clone(support[key]), _clone(ref)]})
    target_paths = {(op.get('target') or {}).get('path') for op in operations
                    if isinstance(op.get('target'), Mapping)}
    for key, row in support.items():
        identifier = key[0]
        basis = []
        if source_requirements and key in required_keys:
            basis.append({'kind': 'EXISTING_DELIVERY_REQUIRED', 'component': 'excerpt'})
        if source_requirements and identifier in prerequisites:
            basis.append({'kind': 'EXISTING_SOURCE_PREREQUISITE', 'component': 'excerpt',
                          'record': prerequisites[identifier]})
        if key in mandatory_keys:
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
                        current_sources[path] = {'content': content, 'snapshot_id': snapshot_id,
                            'source_file_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
                        if (source_requirements and key in required_keys and isinstance(excerpt, str)
                                and excerpt and row.get('content_digest') == _sha(excerpt)
                                and isinstance(row.get('snapshot_id'), str) and row['snapshot_id']):
                            target_key = (path, snapshot_id)
                            if target_key not in current_targets:
                                record = {'id': 'CURRENT_TARGET:' + path, 'snapshot_id': snapshot_id,
                                    'path': path, 'content': content, 'content_digest': _sha(content),
                                    'source_file_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
                                current_targets[target_key] = _subject('CURRENT_TARGET_SOURCE', record)
                            current_targets[target_key]['required_basis'].append({
                                'kind': 'EXISTING_SELECTED_TARGET_CURRENT_SOURCE', 'component': 'current_file_bytes',
                                'source_evidence_id': identifier,
                                'source_snapshot_id': row.get('snapshot_id'),
                                'producer_protocol': 'contracts.delivery.target_supplies_support'})
                            # A path is not delivery. Retain the excerpt gate
                            # until the final prepared projection proves it,
                            # or proves the complete historical target bridge.
                            subject['current_target_delivery_ref'] = current_targets[target_key]['id']
                    if (row.get('snapshot_id') != snapshot_id and isinstance(excerpt, str)
                            and excerpt and content.count(excerpt) == 1):
                        locator = reread_source_locator(row, content)
                        subject['reread_identity'] = {**_source_identity(row), 'snapshot_id': snapshot_id,
                            'locator': locator}
            except (OSError, ValueError, UnicodeError):
                pass
        subjects.append(subject)
    subjects.extend(current_targets.values())
    if feedback_required and not isinstance(feedback, Mapping):
        raise ValueError('Required feedback must be an explicitly adopted feedback object')
    if isinstance(feedback, Mapping):
        subjects.append(_subject('FEEDBACK', feedback, identifier='execution_feedback',
            required_basis=([{'kind': 'ADOPTED_FEEDBACK', 'component': 'complete_context'}]
                            if feedback_required else ())))
    for index, artifact in enumerate(declared_artifacts):
        subjects.append(_subject('DECLARED_ARTIFACT', artifact,
            required_basis=[{'kind': 'ADOPTED_DECLARED_ARTIFACT', 'component': 'content'}]))
    for index, historical in enumerate(historical_contexts):
        if not isinstance(historical, Mapping) or not historical:
            raise ValueError('Adopted historical context must be a nonempty object')
        subjects.append(_subject('HISTORICAL_CONTEXT', historical, identifier='historical_context:' + str(index),
            required_basis=[{'kind': 'ADOPTED_HISTORICAL_CONTEXT', 'component': 'complete_context'}]))
    if execution_mapping is not None:
        subjects.append(_subject('EXECUTION_MAPPING', {'execution_mapping': execution_mapping},
            identifier='execution_mapping',
            required_basis=[{'kind': 'ADOPTED_EXECUTION_MAPPING', 'component': 'complete_context'}]))
    absence = dict(layer_absence or {})
    return {'consumer': consumer, 'run_id': (changeset or diagnosis or {}).get('run_id'),
            'snapshot_id': snapshot_id, 'input_digest': method.get('input_digest'),
            'layers': {key: ('PRESENT' if value is not None else absence.get(key, 'UNKNOWN'))
                       for key, value in [('diagnosis', diagnosis), ('changeset', changeset)]},
            'subjects': subjects, 'source_conflicts': source_conflicts,
            'current_target_sources': current_sources}


def _contains_record(expected, actual):
    return isinstance(actual, Mapping) and all(key in actual and actual[key] == value
                                              for key, value in expected.items())


def _target_entries(nodes):
    for path, value in nodes:
        if not isinstance(value, Mapping) or not isinstance(value.get('current_target_files'), list):
            continue
        acceptance = value.get('acceptance')
        snapshot = acceptance.get('snapshot_id') if isinstance(acceptance, Mapping) else None
        for index, entry in enumerate(value['current_target_files']):
            if isinstance(entry, Mapping):
                yield path + '/current_target_files/' + str(index), entry, snapshot


_UNRESOLVED_CONTEXT_REFERENCE = object()


def _resolve_context_reference(reference, candidate, candidate_path, nodes):
    """Resolve only the existing builders' same-message JSON references."""
    if not isinstance(reference, str):
        return _UNRESOLVED_CONTEXT_REFERENCE
    if reference == 'feedback.public_check_results':
        feedback = candidate.get('feedback')
        return (feedback['public_check_results']
                if isinstance(feedback, Mapping) and 'public_check_results' in feedback
                else _UNRESOLVED_CONTEXT_REFERENCE)
    if not reference.startswith('/'):
        return _UNRESOLVED_CONTEXT_REFERENCE
    # JSON embedded in a message has its own root. A pointer cannot resolve
    # against an unrelated previous message that merely has a similar value.
    roots = [(path, value) for path, value in nodes if isinstance(value, Mapping)
             and path.endswith('#json') and candidate_path.startswith(path + '/')]
    if not roots:
        return _UNRESOLVED_CONTEXT_REFERENCE
    value = max(roots, key=lambda item: len(item[0]))[1]
    try:
        for raw in reference[1:].split('/'):
            key = raw.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not key.isdigit() or str(int(key)) != key:
                    return _UNRESOLVED_CONTEXT_REFERENCE
                value = value[int(key)]
            else:
                value = value[key]
    except (KeyError, IndexError, ValueError, TypeError):
        return _UNRESOLVED_CONTEXT_REFERENCE
    return value


def _context_supplied(original, supplied, path, nodes, depth=0):
    """Exact adopted content, allowing only existing explicit shared copies."""
    if depth > 40:
        return False
    if isinstance(original, Mapping):
        if not isinstance(supplied, Mapping):
            return False
        for key, value in original.items():
            current = supplied.get(key)
            child_path = path + '/' + _pointer(key)
            if key == 'public_observations' and 'public_observation_refs' in supplied:
                current = []
                retained = supplied.get('public_observations', [])
                if not isinstance(retained, (list, tuple)) or not isinstance(supplied['public_observation_refs'], (list, tuple)):
                    return False
                for ref in supplied['public_observation_refs']:
                    if not isinstance(ref, Mapping):
                        return False
                    if 'public_observation_record_ref' in ref:
                        record = _resolve_context_reference(ref['public_observation_record_ref'], supplied, path, nodes)
                        if not isinstance(record, Mapping) or record.get('id') != ref.get('id'):
                            return False
                    else:
                        matches = [record for record in retained if isinstance(record, Mapping)
                                   and all(record.get(k) == ref.get(k) for k in ('id', 'snapshot_id', 'content_digest', 'locator'))]
                        if len(matches) != 1:
                            return False
                        record = matches[0]
                    current.append(record)
            elif key not in supplied:
                ref = supplied.get(key + '_record_ref', supplied.get(key + '_ref'))
                if not isinstance(ref, str):
                    return False
                current = _resolve_context_reference(ref, supplied, path, nodes)
                if current is _UNRESOLVED_CONTEXT_REFERENCE:
                    return False
            if not _context_supplied(value, current, child_path, nodes, depth + 1):
                return False
        return True
    if isinstance(original, (list, tuple)):
        return (isinstance(supplied, (list, tuple)) and len(original) == len(supplied)
                and all(_context_supplied(a, b, path + '/' + str(i), nodes, depth + 1)
                        for i, (a, b) in enumerate(zip(original, supplied))))
    return type(original) is type(supplied) and original == supplied


def _evidence_digest_valid(record):
    excerpt = record.get('excerpt')
    if not isinstance(excerpt, str):
        return False
    if record.get('kind') in {'STATIC_SEARCH', 'SOURCE_INVENTORY'}:
        from contractedge.contracts.evidence import static_search_content_digest
        locator = record.get('locator')
        return isinstance(locator, Mapping) and record.get('content_digest') == static_search_content_digest(
            record.get('snapshot_id'), locator, excerpt)
    return bool(excerpt) and record.get('content_digest') == _sha(excerpt)


def _classify(subject, nodes, authority, current_sources=None, evidence_namespaces=(), *,
              allow_target_window=True, historical_paths=()):
    original, identifier = subject['original'], subject['id']
    locations, identities, drift = [], [], []
    # Evidence owns its `id`; reference-local windows are not replacement
    # evidence bodies. Incomplete bodies with an id still participate in drift
    # checks. Other subjects retain their existing projection conflict signals.
    identity_fields = (('id',) if subject['kind'] == 'EVIDENCE'
                       else ('id', 'change_id', 'source_evidence_ref'))
    candidates = [(path, value) for path, value in nodes if isinstance(value, Mapping)
                  and identifier is not None and any(value.get(key) == identifier for key in identity_fields)]
    if subject['kind'] == 'EVIDENCE':
        candidates = [(path, value) for path, value in candidates
                      if not any(path.startswith(root + '/') for root in historical_paths)
                      and (value.get('snapshot_id') in (None, original.get('snapshot_id'))
                          or _evidence_key(value) not in evidence_namespaces
                          or subject.get('reread_identity') == _source_identity(value))]
    identities = [path for path, value in nodes if value == identifier]
    if authority == 'UNKNOWN_PREPARED_CONTENT':
        return 'UNKNOWN', [], identities
    if subject['kind'] == 'CURRENT_TARGET_SOURCE':
        for path, entry, snapshot in _target_entries(nodes):
            if entry.get('path') != original['path']:
                continue
            pointer = path + '/content'
            if snapshot != original['snapshot_id']:
                return 'IDENTITY_DRIFT', [{'pointer': path}], identities
            if ('selected_target' in original
                    and not _contains_record(original['selected_target'], entry)):
                return 'CONTENT_DRIFT', [{'pointer': path}], identities
            if (entry.get('windowed_from_current_target') is True
                    and entry.get('source_file_sha256') != original.get('source_file_sha256')):
                return 'IDENTITY_DRIFT', [{'pointer': path + '/source_file_sha256'}], identities
            if not target_supplies_source(entry, original['content'], allow_window=allow_target_window):
                return 'CONTENT_DRIFT', [{'pointer': pointer}], identities
            windowed = entry.get('windowed_from_current_target') is True
            return ('WINDOWED_CURRENT_TARGET_BYTES' if windowed else 'FULL_CURRENT_TARGET_BYTES'), [{'pointer': pointer,
                    'snapshot_id': original['snapshot_id'], 'path': original['path'],
                    'content_sha256': original['content_digest'],
                    'source_file_sha256': original['source_file_sha256'],
                    **({'window_segments': entry['window_segments']} if windowed else {})}], identities
        return 'NOT_DELIVERED', [], identities
    if subject['kind'] == 'ORIGINAL_REQUIREMENT':
        text = original['text']
        locations = [{'pointer': path, 'span': [0, len(text)], 'sha256': _sha(text)}
                     for path, value in nodes if isinstance(value, str) and value == text]
    elif subject['kind'] in {'FEEDBACK', 'HISTORICAL_CONTEXT', 'DECLARED_ARTIFACT', 'EXECUTION_MAPPING'}:
        locations = [{'pointer': path} for path, value in nodes
                     if _context_supplied(original, value, path, nodes)]
    else:
        locations = [{'pointer': path} for path, value in candidates if _contains_record(original, value)]
    if subject['kind'] == 'EVIDENCE' and isinstance(original.get('excerpt'), str) and original['excerpt']:
        excerpt = original['excerpt']
        if not _evidence_digest_valid(original):
            return 'UNKNOWN', [], identities
        # The existing execution consumer supplies a target file instead of duplicating
        # a source excerpt. Bind the exact excerpt to that exact declared source path.
        target_path = (original.get('locator') or {}).get('path') if isinstance(original.get('locator'), Mapping) else None
        bound = [(path, value) for path, value in candidates if _source_identity(value) == _source_identity(original)]
        target_identity = {key: value for key, value in original.items()
                           if key not in {'excerpt', 'delivery_required'}}
        target_bound = [(path, value) for path, value in bound if _contains_record(target_identity, value)]
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
        for path, target, snapshot in _target_entries(nodes) if original.get('kind') == 'SOURCE_SPAN' else ():
            if not _legal_source_path(target_path) or target.get('path') != target_path or not target_bound:
                continue
            current = (current_sources or {}).get(target_path)
            if current is not None:
                if snapshot != current['snapshot_id']:
                    continue
                if (target.get('windowed_from_current_target') is True
                        and target.get('source_file_sha256') != current['source_file_sha256']):
                    continue
                actual = current['content']
                if (original.get('snapshot_id') != current['snapshot_id'] and actual.count(excerpt) != 1):
                    continue
            else:
                # Without an independent current file, only a complete target
                # can witness original excerpt bytes. A window needs its real
                # source coordinates, never inferred from rendered positions.
                if target.get('windowed_from_current_target'):
                    continue
                actual = target.get('content')
            if target_supplies_support(target, original, actual):
                start = target['content'].find(excerpt)
                locations.append({'pointer': path + '/content', 'span': [start, start + len(excerpt)],
                                  'sha256': _sha(excerpt), 'binding': 'EXACT_DECLARED_SOURCE_PATH'})
        for path, value in candidates:
            if value.get('excerpt') == excerpt:
                if any(path == bound_path for bound_path, _ in bound):
                    retained = {k: v for k, v in original.items() if k != 'delivery_required'}
                    if _contains_record(retained, value):
                        locations.append({'pointer': path + '/excerpt', 'span': [0, len(excerpt)],
                                          'sha256': _sha(excerpt), 'binding': 'EXACT_IDENTIFIED_EXCERPT'})
                    else:
                        drift.append(path)
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
    # An adopted correction exchange includes the selected assistant response
    # and subsequent user feedback/wrappers. Compare complete logical messages
    # with the prepared wire, retaining the initial current-input namespace.
    context = _with_request_source_subjects(context, request)
    correction_history_started = False
    for index, message in enumerate(request.messages):
        if not isinstance(message, Mapping):
            continue
        role = message.get('role')
        if role == 'assistant':
            correction_history_started = True
        if correction_history_started and role in {'assistant', 'user'}:
            context['subjects'].append(_subject('HISTORICAL_CONTEXT', dict(message),
                identifier='correction_message:' + str(index),
                required_basis=[{'kind': 'ADOPTED_CORRECTION_MESSAGE', 'component': 'complete_context'}]))
    projection = safe_request_projection(request, quote)
    nodes = list(_walk(projection['payload']))
    rows, issues = [], []
    current_sources = context.get('current_target_sources', {})
    evidence_namespaces = {_evidence_key(subject['original']) for subject in context.get('subjects', [])
                           if subject['kind'] == 'EVIDENCE'}
    evidence_subjects = {_evidence_key(subject['original']): subject for subject in context.get('subjects', [])
                         if subject['kind'] == 'EVIDENCE'}
    historical_paths = [path for subject in context.get('subjects', [])
        if subject['kind'] == 'HISTORICAL_CONTEXT' for path, value in nodes
        if _context_supplied(subject['original'], value, path, nodes)]
    # Feedback already owns its historical records. Recognizing their namespace
    # prevents a reused local ID from masquerading as drift of a current record;
    # it never supplies missing current content or adds a new evidence claim.
    for subject in context.get('subjects', []):
        if subject['kind'] in {'FEEDBACK', 'HISTORICAL_CONTEXT'}:
            evidence_namespaces.update(_evidence_key(value) for _, value in _walk(subject['original'])
                if isinstance(value, Mapping) and isinstance(value.get('id'), str)
                and isinstance(value.get('snapshot_id'), str))
    for original_subject in context.get('subjects', []):
        subject = _clone(original_subject)
        original = subject['original']
        source_path = (original.get('locator') or {}).get('path') if isinstance(original.get('locator'), Mapping) else None
        current = current_sources.get(source_path) if isinstance(source_path, str) else None
        if (subject['kind'] == 'EVIDENCE' and subject.get('current_target_delivery_ref')
                and current is not None and original.get('snapshot_id') != current['snapshot_id']
                and not any(b['kind'] == 'EXISTING_SOURCE_PREREQUISITE' for b in subject['required_basis'])
                and any(target.get('path') == source_path and snapshot == current['snapshot_id']
                        and target_supplies_source(target, current['content'])
                        for _path, target, snapshot in _target_entries(nodes))):
            subject['historical_required_basis'] = [b for b in subject['required_basis']
                if b['kind'] == 'EXISTING_DELIVERY_REQUIRED']
            subject['required_basis'] = [b for b in subject['required_basis']
                if b['kind'] != 'EXISTING_DELIVERY_REQUIRED']
        # A changed historical selection needs a complete current target. A
        # valid window plus the old full excerpt cannot hide current gaps.
        needs_complete_target = subject['kind'] == 'CURRENT_TARGET_SOURCE' and any(
            basis.get('source_snapshot_id') != original.get('snapshot_id')
            and not evidence_subjects.get((basis.get('source_evidence_id'),
                                           basis.get('source_snapshot_id')), {}).get('reread_identity')
            for basis in subject['required_basis'] if basis.get('source_evidence_id'))
        classification, locations, identity_locations = _classify(
            subject, nodes, projection['authority'], current_sources, evidence_namespaces,
            allow_target_window=not needs_complete_target, historical_paths=historical_paths)
        original = subject['original']
        concrete = (subject['kind'] == 'EVIDENCE' and _evidence_digest_valid(original)
                    and bool(_evidence_key(original)[1])
                    and (original.get('kind') != 'SOURCE_SPAN' or _legal_source_path(
                        original.get('locator', {}).get('path') if isinstance(original.get('locator'), Mapping) else None)))
        if subject['kind'] == 'EVIDENCE' and original.get('kind') == 'PUBLIC_DEPENDENCY_DECLARATION':
            concrete = (concrete and original.get('read_only') is True
                and all(isinstance(original.get(key), str) and bool(original[key])
                        for key in ('module', 'version', 'member', 'archive_path'))
                and all(isinstance(original.get(key), str) and len(original[key]) == 64
                        for key in ('archive_sha256', 'environment_sha256')))
        if subject['kind'] == 'ORIGINAL_REQUIREMENT':
            concrete = isinstance(original.get('text'), str) and bool(original['text'])
        elif subject['kind'] == 'DECLARED_ARTIFACT':
            concrete = (isinstance(original.get('id'), str) and bool(original['id'])
                and isinstance(original.get('path'), str) and bool(original['path'])
                and isinstance(original.get('content'), str)
                and original.get('content_digest') == _sha(original['content']))
        elif subject['kind'] in {'FEEDBACK', 'HISTORICAL_CONTEXT', 'EXECUTION_MAPPING'}:
            concrete = isinstance(original, Mapping) and bool(original)
        if subject['kind'] == 'CURRENT_TARGET_SOURCE':
            concrete = (isinstance(original.get('content'), str) and original.get('content_digest') == _sha(original['content'])
                and _legal_source_path(original.get('path')) and isinstance(original.get('snapshot_id'), str)
                and bool(original['snapshot_id']) and isinstance(original.get('source_file_sha256'), str)
                and len(original['source_file_sha256']) == 64)
        required = bool(subject['required_basis'])
        historical_prerequisite = (subject['kind'] == 'EVIDENCE' and original.get('kind') == 'SOURCE_SPAN'
            and original.get('snapshot_id') != context.get('snapshot_id')
            and any(b['kind'] == 'EXISTING_SOURCE_PREREQUISITE' for b in subject['required_basis']))
        # Keeping the original quote is honest historical delivery, not evidence
        # that an explicit prerequisite still holds in the current source.
        prerequisite_current = not historical_prerequisite or (
            subject.get('reread_identity') is not None and any(
                isinstance(value, Mapping) and _source_identity(value) == subject['reread_identity']
                and value.get('excerpt') == original.get('excerpt') for _, value in nodes)) or (
            current is not None and any(snapshot == current['snapshot_id']
                and target.get('path') == source_path
                and target_supplies_support(target, original, current['content'])
                for _, target, snapshot in _target_entries(nodes)))
        row = {**subject, 'classification': classification, 'locations': locations,
               'identity_locations': identity_locations, 'required_content_gate': required}
        rows.append(row)
        origin_keys = {(basis.get('source_evidence_id'), basis.get('source_snapshot_id'))
                       for basis in subject['required_basis'] if basis.get('source_evidence_id')}
        conflict = next((row for row in context.get('source_conflicts', [])
                         if _evidence_key(row) == _evidence_key(original)
                         or _evidence_key(row) in origin_keys), None)
        if conflict:
            row['classification'] = classification = 'UNKNOWN_CONFLICTING_ORIGINAL_RECORDS'
        code = ('CONFLICTING_ORIGINAL_SOURCE' if required and conflict else
                'REQUIRED_SOURCE_UNVERIFIABLE' if required and (not concrete or not prerequisite_current) else
                'SEMANTIC_DELIVERY_REQUIRED_CONTENT_MISSING')
        if required and (conflict or not concrete or not prerequisite_current or classification not in {
                'FULL_ORIGINAL', 'EXPLICIT_SOURCE_REREAD', 'FULL_CURRENT_TARGET_BYTES',
                'WINDOWED_CURRENT_TARGET_BYTES'}):
            issues.append({'code': code, 'subject_id': subject['id'],
                'classification': classification, 'consumer': context['consumer'],
                'expected_component': 'current_file_bytes' if subject['kind'] == 'CURRENT_TARGET_SOURCE'
                                      else 'complete original requirement text' if subject['kind'] == 'ORIGINAL_REQUIREMENT'
                                      else 'current prerequisite source' if historical_prerequisite
                                      else 'exact original excerpt', 'locations': locations,
                'identity_locations': identity_locations, 'required_basis': subject['required_basis']})
    required_count = sum(row['required_content_gate'] for row in rows)
    status = ('REQUIRED_CONTENT_GAP' if issues else 'REQUIRED_CONTENT_VERIFIED' if required_count
              else 'RECORDED_WITHOUT_REQUIRED_CONTENT_CLAIM')
    return {'schema_version': 'ce.semantic_delivery.v1', 'status': status,
            'context': {k: v for k, v in context.items() if k not in {'subjects', 'current_target_sources'}},
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
