from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

from contractedge.contracts.models import MethodInput
from contractedge.contracts.evidence import static_search_content_digest
from contractedge.retrieval.candidates import CandidateSet


def _source_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def source_context_key(record):
    """Use the existing external-declaration/current-source namespace."""
    return (record['id'], record.get('current_source_snapshot')
            if record.get('kind') == 'PUBLIC_DEPENDENCY_DECLARATION' else record.get('snapshot_id'))


SOURCE_CONTEXT_VERSIONS = (1, 2)
# Version 2 (front-half redesign): adoption keeps identity, digest and
# no-eviction rules but has no per-producer record/byte admission quotas; the
# real request boundary and task budget decide what can be sent.
SOURCE_PRODUCERS = {'initial', 'extension', 'planning_helper', 'execution_helper', 'reread', 'inherited'}
SOURCE_PRODUCERS_V2 = SOURCE_PRODUCERS | {'locator'}


# Material gates the v2 contract removes; a v2 config that still sets them is
# a configuration error, not something silently ignored or defaulted.
SUPERSEDED_BY_SOURCE_CONTEXT_V2 = (
    'initial_diagnosis_context_tokens', 'max_execution_support_bytes',      # duplicate request/source quotas
    'top_n', 'max_units', 'max_declared_units', 'max_gap_units',             # candidate pool cuts
    'max_extension_files', 'max_lines_per_unit', 'max_gap_declaration_lines',
    'max_evidence_extensions')                                               # independent extension count


def source_context_contract(config: Mapping) -> int:
    """The frozen source-material contract a new work chain uses (default 1)."""
    value = config.get('source_context_contract', 1)
    if type(value) is not int or value not in SOURCE_CONTEXT_VERSIONS:
        raise ValueError('source_context_contract must be 1 or 2')
    if value == 2:
        legacy = [key for key in SUPERSEDED_BY_SOURCE_CONTEXT_V2 if key in config]
        if legacy:
            raise ValueError('Source context v2 config sets superseded material gates: ' + ', '.join(legacy))
    return value


def source_contract_of(retrieval_config: Mapping | None) -> int:
    """Contract version recorded in a run context's adopted sources (1 when absent)."""
    context = (retrieval_config or {}).get('source_context')
    return context.get('version', 1) if isinstance(context, Mapping) else 1


def source_context_version(context) -> int:
    if not isinstance(context, Mapping) or context.get('version') not in SOURCE_CONTEXT_VERSIONS:
        raise ValueError('Unknown source context contract')
    return context['version']


def source_extension_limits(context):
    """One frozen policy supplies selector and canonical cumulative bounds."""
    if source_context_version(context) == 2:
        raise ValueError('Source context v2 has no independent extension quota')
    limits = context.get('limits', {'evidence_extensions': 1})
    if (not isinstance(limits, Mapping) or not {'evidence_extensions'} <= set(limits) <= {'evidence_extensions', 'initial_records'}
            or type(limits['evidence_extensions']) is not int
            or not 1 <= limits['evidence_extensions'] <= 2
            or type(limits.get('initial_records', 12)) is not int
            or limits.get('initial_records', 12) < 1):
        raise ValueError('Invalid frozen source limits')
    count, size = 12, 24000
    return count, size, count * limits['evidence_extensions'], size * limits['evidence_extensions']


def _require_exact_span(record):
    """v2 adoption needs coordinates that can be re-read, not just a self-consistent digest.

    When byte coordinates are present they must span exactly the excerpt, so a
    cut body whose digest was recomputed cannot keep its original coordinates.
    """
    locator = record.get('locator') or {}
    if record['kind'] == 'PUBLIC_DEPENDENCY_DECLARATION':
        if not all(isinstance(record.get(k), str) and record[k]
                   for k in ('member', 'archive_sha256', 'current_source_snapshot', 'version')):
            raise ValueError('Dependency source record lacks its pinned identity')
    elif record['kind'] == 'SOURCE_SPAN':
        if not isinstance(locator.get('path'), str) or not locator['path'] or not all(
                type(locator.get(k)) is int for k in ('start_line', 'end_line')):
            raise ValueError('Source record lacks exact path and line coordinates')
    if 'start_byte' in locator or 'end_byte' in locator:
        if (type(locator.get('start_byte')) is not int or type(locator.get('end_byte')) is not int
                or locator['end_byte'] - locator['start_byte'] != len(record['excerpt'].encode())):
            raise ValueError('Source record byte coordinates do not span its excerpt')


def source_record_from_read(read: Mapping, *, current_snapshot: str) -> dict:
    """Canonical record for a body actually read through SourceNavigator.read.

    The record copies the tool's exact text and coordinates; nothing is
    summarized or re-cut. Dependency bodies stay read-only API context in the
    current snapshot's namespace.
    """
    text = read['text']
    if hashlib.sha256(text.encode()).hexdigest() != read['content_digest']:
        raise ValueError('Navigation read digest does not match its text')
    suffix = str(read['handle']).split('-', 1)[1]
    locator = {k: read[k] for k in ('start_line', 'end_line', 'start_char', 'end_char', 'start_byte', 'end_byte')}
    locator.update(path=read['path'], truncated=False, complete_declaration=bool(read['complete_declaration']),
                   file_digest=read['file_digest'], parser_kind=read['parser_kind'])
    if read.get('symbol'):
        locator.update(symbol=read['symbol'], receiver=read.get('receiver') or '')
    if read.get('origin') == 'DEPENDENCY':
        dep = read['dependency']
        return {'id': 'PUBLICDEP-' + suffix, 'kind': 'PUBLIC_DEPENDENCY_DECLARATION',
                'module': dep['module'], 'version': dep['version'], 'package': dep.get('package'),
                'current_source_snapshot': current_snapshot, 'archive_path': dep['archive_path'],
                'archive_sha256': dep['archive_sha256'], 'member': dep['member'],
                'member_content_digest': read['file_digest'], 'environment_sha256': dep.get('environment_sha256'),
                'build_constraint': dep.get('build_constraint'), 'locator': locator,
                'content_digest': read['content_digest'], 'excerpt': text, 'read_only': True,
                'interpretation': 'Exact hash-pinned public dependency source read; API context only, '
                                  'not proof that this project calls it, an editable target or causal evidence'}
    if read.get('snapshot_id') != current_snapshot:
        raise ValueError('Navigation read belongs to another snapshot')
    return {'id': 'SRC-NAV-' + suffix, 'kind': 'SOURCE_SPAN', 'snapshot_id': current_snapshot,
            'locator': locator, 'content_digest': read['content_digest'], 'excerpt': text,
            'origin': 'Exact source read through the navigation tool'}


def verify_source_record(record: Mapping, snapshot) -> dict:
    """Re-read the fixed source and compare bytes; a recomputed hash alone proves nothing.

    Returns a verification fact; raises ValueError when the record no longer
    matches its source. Records without byte coordinates (legacy candidates)
    are checked at their character range instead and say so.
    """
    from pathlib import Path
    if record.get('kind') != 'SOURCE_SPAN':
        raise ValueError('Only current-source spans can be re-read here')
    locator = record.get('locator') or {}
    path = locator.get('path')
    root = Path(snapshot.source_root).resolve()
    if path not in snapshot.file_digests or record.get('snapshot_id') != snapshot.snapshot_id:
        raise ValueError('Source record is not in this snapshot')
    target = root / path
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError('Source record path escapes the snapshot')
    raw = target.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != snapshot.file_digests[path] or locator.get('file_digest', digest) != digest:
        raise ValueError('Source file changed since the record was made')
    excerpt = record.get('excerpt', '').encode()
    if hashlib.sha256(excerpt).hexdigest() != record.get('content_digest'):
        raise ValueError('Source record digest does not match its excerpt')
    if 'start_byte' in locator:
        if raw[locator['start_byte']:locator['end_byte']] != excerpt:
            raise ValueError('Source record bytes differ from the fixed source range')
        return {'id': record['id'], 'verified': 'EXACT_BYTE_RANGE', 'file_digest': digest}
    text = raw.decode('utf-8')
    start, end = locator.get('start_char'), locator.get('end_char')
    candidates = [text, text.replace('\r\n', '\n')]   # legacy discovery read CRLF-normalized text
    if not any(isinstance(start, int) and isinstance(end, int) and t[start:end].encode() == excerpt
               for t in candidates):
        raise ValueError('Source record text differs from the fixed source range')
    return {'id': record['id'], 'verified': 'CHARACTER_RANGE', 'file_digest': digest}


def source_context_new(binding: Mapping, *, evidence_extensions=1, initial_records=12, version=1) -> dict:
    if any(not isinstance(binding.get(k), str) or not binding[k]
           for k in ('run_id', 'case_id', 'input_digest', 'front_id')):
        raise ValueError('Source context requires its original work-chain binding')
    if version == 2:
        return dict(version=2, contract='front_half.source_context.v2', binding=_source_copy(dict(binding)),
                    records=[], targets=[], producer_refs={}, candidate_ids=[])
    if version != 1:
        raise ValueError('Unknown source context contract')
    result = dict(version=1, binding=_source_copy(dict(binding)), records=[], targets=[],
                  producer_refs={}, candidate_ids=[], limits={'evidence_extensions': evidence_extensions})
    if initial_records != 12:
        result['limits']['initial_records'] = initial_records
    source_extension_limits(result)
    return result


def source_context_records(context, *, producer=None, kind=None) -> list[dict]:
    if context is None:
        return []
    source_context_version(context)
    selected = (None if producer is None else
                {tuple(ref) for ref in context['producer_refs'].get(producer, [])})
    return [_source_copy(r) for r in context['records']
            if (selected is None or source_context_key(r) in selected)
            and (kind is None or r.get('kind') == kind)]


def source_context_offer(active, records, *, producer, targets=(), candidate_ids=()) -> dict:
    """Produce an immutable admission draft, never claim it was dispatched.

    Existing records cannot be evicted. The caller admits the *whole* request
    and semantic prerequisites before the existing dispatch boundary commits.
    Quotas count first adoption, not repeated reads or compacted wire copies.
    """
    from contractedge.model.budget import BudgetExceeded
    if not isinstance(active, Mapping) or active.get('version') not in SOURCE_CONTEXT_VERSIONS:
        raise ValueError('Missing source context binding')
    version = active['version']
    if producer not in (SOURCE_PRODUCERS_V2 if version == 2 else SOURCE_PRODUCERS):
        raise ValueError('Unknown source producer')
    result = _source_copy(active)
    by_key = {source_context_key(r): r for r in result['records']}
    if len(by_key) != len(result['records']):
        raise ValueError('Duplicate canonical source context identity')
    refs = result['producer_refs'].setdefault(producer, [])
    for record in records:
        if (not isinstance(record, Mapping) or record.get('kind') not in
                {'SOURCE_SPAN', 'DECLARED_ARTIFACT', 'PUBLIC_DEPENDENCY_DECLARATION', 'STATIC_SEARCH'}
                or not isinstance(record.get('id'), str) or not record['id']
                or not isinstance(record.get('excerpt'), str) or not record['excerpt']
                or (record.get('kind') != 'PUBLIC_DEPENDENCY_DECLARATION'
                    and not isinstance(record.get('locator'), Mapping))):
            raise ValueError('Invalid canonical source context record')
        if record['kind'] == 'STATIC_SEARCH' and producer not in {'initial', 'extension', 'inherited', 'locator'}:
            raise ValueError('Static search facts are not helper or reread source material')
        if version == 2:
            _require_exact_span(record)
        digest = (static_search_content_digest(record.get('snapshot_id', ''), record['locator'], record['excerpt'])
                  if record['kind'] == 'STATIC_SEARCH' else hashlib.sha256(record['excerpt'].encode()).hexdigest())
        if digest != record.get('content_digest'):
            raise ValueError('Invalid canonical source context record digest')
        key = source_context_key(record)
        if not isinstance(key[1], str) or not key[1]:
            raise ValueError('Source context record has no snapshot namespace')
        previous = by_key.get(key)
        if previous is not None:
            merged = merge_evidence_records([previous, record])[0]
            previous.clear(); previous.update(merged)
            continue
        if producer == 'reread' and not any(
                r['id'] == record['id'] and r.get('kind') == record.get('kind')
                and r.get('content_digest') == record['content_digest']
                and r.get('excerpt') == record['excerpt']
                and (r.get('locator') or {}).get('path') == (record.get('locator') or {}).get('path')
                for r in active['records']):
            raise ValueError('Source reread cannot introduce a new declaration or changed body')
        value = _source_copy(record)
        result['records'].append(value); by_key[key] = value
        refs.append(list(key))
    selected = [r for r in source_context_records(result, producer=producer) if r['kind'] != 'STATIC_SEARCH']
    limits = {}
    if version == 1:
        _, _, extension_count, extension_bytes = source_extension_limits(result)
        limits = {'initial':(result.get('limits', {}).get('initial_records', 12),None),
                  'extension': (extension_count, extension_bytes), 'planning_helper': (8, 24000),
                  'execution_helper': (8, None)}
    if producer in limits:
        count, byte_limit = limits[producer]
        size = sum(len(r['excerpt'].encode()) for r in selected)
        if len(selected) > count or (byte_limit is not None and size > byte_limit):
            dimension = 'records' if len(selected) > count else 'bytes'
            actual, bound = (len(selected), count) if dimension == 'records' else (size, byte_limit)
            raise BudgetExceeded(f'Cumulative {producer} source admission limit exceeded: {actual} > {bound} {dimension}',
                details={'dimension': producer + '_source_' + dimension, 'actual': actual, 'limit': bound})
    for identifier in candidate_ids:
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('Candidate adoption requires a nonempty ID')
        if not any(r['id'] == 'SRC-' + identifier and r['kind'] in {'SOURCE_SPAN', 'DECLARED_ARTIFACT'}
                   for r in result['records']):
            raise ValueError('Adopted candidate has no canonical source record')
        if identifier not in result['candidate_ids']:
            result['candidate_ids'].append(identifier)
    for target in targets:
        if (not isinstance(target, Mapping) or not isinstance(target.get('path'), str)
                or not isinstance(target.get('snapshot_id'), str)
                or not isinstance(target.get('content'), str)
                or not isinstance(target.get('source_file_sha256'), str)):
            raise ValueError('Adopted target requires its exact source and snapshot binding')
        if dict(target) not in result['targets']:
            result['targets'].append(_source_copy(dict(target)))
    return result


def source_context_commit(active, draft, dispatch_state):
    """Consume the dispatch owner's fact; this is not a second dispatch ledger."""
    if dispatch_state == 'not_sent':
        return _source_copy(active)
    if dispatch_state not in {'response_known', 'response_unknown'}:
        raise ValueError('Source adoption requires a known dispatch classification')
    return source_context_continuation(active, draft)


def source_context_continuation(active, draft):
    """Validate same-chain preservation without making a dispatch claim."""
    source_context_validate(draft)
    if source_context_version(active) != source_context_version(draft):
        raise ValueError('Source context contract changed during continuation')
    if active['version'] == 1 and (
            source_extension_limits(active) != source_extension_limits(draft)
            or active.get('limits', {}).get('initial_records', 12) != draft.get('limits', {}).get('initial_records', 12)):
        raise ValueError('Frozen source limits changed during continuation')
    if active.get('binding') != draft.get('binding'):
        raise ValueError('Source context belongs to another work chain')
    records = {source_context_key(r): r for r in source_context_records(draft)}
    for prior in source_context_records(active):
        current = records.get(source_context_key(prior))
        if current is None:
            raise ValueError('Previously adopted source was removed')
        merged = merge_evidence_records([prior, current])[0]
        if merged != current:
            raise ValueError('Previously adopted delivery requirement was weakened')
    if any(t not in draft['targets'] for t in active['targets']):
        raise ValueError('Previously adopted target view was removed')
    if not set(active['candidate_ids']) <= set(draft['candidate_ids']):
        raise ValueError('Previously adopted candidate was removed')
    for producer, refs in active['producer_refs'].items():
        if not {tuple(r) for r in refs} <= {tuple(r) for r in draft['producer_refs'].get(producer, [])}:
            raise ValueError('Cumulative source admission quota was reset')
    return _source_copy(draft)


def source_context_validate(context):
    """Check recorded adoption, identity and cumulative bounds without reselection."""
    if source_context_version(context) == 2:
        if set(context) - {'version', 'contract', 'binding', 'records', 'targets', 'producer_refs',
                           'candidate_ids', 'inherited_from'} or context.get('contract') != 'front_half.source_context.v2':
            raise ValueError('Source context v2 carries unknown or legacy quota fields')
        rebuilt = source_context_new(context['binding'], version=2)
    else:
        source_extension_limits(context)
        rebuilt = source_context_new(context['binding'],
            evidence_extensions=context.get('limits', {}).get('evidence_extensions', 1),
            initial_records=context.get('limits', {}).get('initial_records', 12))
    owners = {}
    for producer, refs in context['producer_refs'].items():
        for ref in refs:
            key = tuple(ref)
            if key in owners:
                raise ValueError('Source context charges the same record more than once')
            owners[key] = producer
    for record in context['records']:
        producer = owners.pop(source_context_key(record), None)
        if producer is None:
            raise ValueError('Source context record has no unique adoption origin')
        rebuilt = source_context_offer(rebuilt,[record],producer=producer)
    if owners:
        raise ValueError('Source adoption references an absent record')
    rebuilt = source_context_offer(rebuilt,[],producer='initial',targets=context['targets'],
                                   candidate_ids=context['candidate_ids'])
    if rebuilt['records'] != context['records'] or rebuilt['candidate_ids'] != context['candidate_ids']:
        raise ValueError('Source context contains noncanonical or duplicate adopted material')
    return _source_copy(context)


def source_context_projection(context, *, supplied_records=(), supplied_targets=(), target_supplied_records=()):
    """Lossless same-request sharing, independently checked on prepared bytes.

    Target coverage shares only excerpt bytes: the canonical identity remains
    in the request for the final target-bound witness. A full canonical copy
    elsewhere in this same request may share the whole record. Neither names
    nor bare digests witness either kind of sharing.
    """
    return {'records':[({k:v for k,v in r.items() if k!='excerpt'}
                        if any(same_evidence_content(r,s) for s in target_supplied_records) else r)
                       for r in source_context_records(context)
                       if not any(same_evidence_content(r, s) for s in supplied_records)],
            'targets':[source_context_target_view(t) for t in context.get('targets', [])
                       if not any(dict(t) == dict(s) for s in supplied_targets)]}


def source_context_target_view(target):
    """The selected view is adopted, not the full file used to prove its bounds."""
    view = dict(target.get('selected_target') or target)
    return _source_copy({**view, 'snapshot_id':target['snapshot_id'],
                         'source_file_sha256':target['source_file_sha256']})


def source_context_restart(active, binding, inherited_inputs, *, evidence_extensions=1, initial_records=12,
                           version=1):
    """Only the engine's explicit full-diagnosis route may replace the root.

    Exact references inherit their canonical bodies. An unqualified ID cannot
    choose the newest snapshot: retain every matching namespace conservatively.
    An unrelated discovery has no lifetime in the new work chain. A chain is
    never continued under another contract version.
    """
    result = source_context_new(binding, evidence_extensions=evidence_extensions, initial_records=initial_records,
                                version=version)
    if active is None:
        return result
    if source_context_version(active) != version:
        raise ValueError('Cannot continue a source context under another contract version')
    source_context_validate(active)
    wanted = set()
    records = source_context_records(active)
    def visit(value, namespace=None):
        if isinstance(value, Mapping):
            local = value.get('snapshot_id', value.get('source_snapshot', namespace))
            for item in value.values():
                visit(item, local)
        elif isinstance(value, (list, tuple)):
            for item in value: visit(item, namespace)
        elif isinstance(value, str):
            wanted.update(source_context_key(r) for r in records if r['id'] == value
                          and (namespace is None or source_context_key(r)[1] == namespace))
    visit(inherited_inputs)
    selected = [r for r in records if source_context_key(r) in wanted]
    result = source_context_offer(result, selected, producer='inherited')
    # This is a lineage pointer to an existing context, not a new event ledger.
    result['inherited_from'] = {'binding':_source_copy(active['binding']),
        'context_digest':hashlib.sha256(json.dumps(active,sort_keys=True,separators=(',',':'),
            ensure_ascii=False).encode()).hexdigest()}
    return result


def same_evidence_content(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Whether a same-request copy can supply this record's exact content.

    Source copies require a complete, digest-checked identity. Other records,
    including legacy observations and structured absence evidence, must match
    in full; matching prose alone never equates their provenance or meaning.
    """
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    if left.get('kind') != 'SOURCE_SPAN' and right.get('kind') != 'SOURCE_SPAN':
        return dict(left) == dict(right)
    fields = ('id', 'kind', 'snapshot_id', 'locator', 'content_digest', 'excerpt')
    for record in (left, right):
        if (record.get('kind') != 'SOURCE_SPAN'
                or any(not isinstance(record.get(key), str) or not record[key]
                       for key in ('id', 'snapshot_id', 'content_digest'))
                or not isinstance(record.get('locator'), Mapping)
                or not isinstance(record['locator'].get('path'), str)
                or not record['locator']['path']
                or not isinstance(record.get('excerpt'), str)
                or hashlib.sha256(record['excerpt'].encode()).hexdigest() != record['content_digest']):
            return False
    # Content identity includes provenance and all locator/completeness fields.
    # Only the runner's delivery assertion may differ between exact copies.
    return ({k: v for k, v in left.items() if k != 'delivery_required'}
            == {k: v for k, v in right.items() if k != 'delivery_required'})


def merge_evidence_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Index one current evidence namespace without overwriting a conflict.

    Historical trial/snapshot containers must remain separate. The sole allowed
    source-record difference is the runner's delivery_required flag; merging
    can strengthen that assertion, never erase it or change evidence content.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get('id'), str) or not record['id']:
            raise ValueError('Evidence catalog records require a nonempty string ID')
        item = dict(record)
        if 'delivery_required' in item and type(item['delivery_required']) is not bool:
            raise ValueError('Evidence delivery_required must be boolean')
        previous = by_id.get(item['id'])
        if previous is None:
            by_id[item['id']] = item
            continue
        source = item.get('kind') == previous.get('kind') == 'SOURCE_SPAN'
        previous_body = {key: value for key, value in previous.items()
                         if not (source and key == 'delivery_required')}
        item_body = {key: value for key, value in item.items()
                     if not (source and key == 'delivery_required')}
        if previous_body != item_body or not same_evidence_content(previous_body, item_body):
            raise ValueError(f"Conflicting evidence catalog ID {item['id']!r}")
        if source and ('delivery_required' in previous or 'delivery_required' in item):
            previous['delivery_required'] = previous.get('delivery_required', False) or item.get('delivery_required', False)
    return list(by_id.values())


def build_evidence_catalog(method_input: MethodInput, candidates: CandidateSet | Mapping[str, Any],
                           snapshot_id: str, *, source_context=None) -> list[dict[str, Any]]:
    selected = list(candidates.candidates) if hasattr(candidates, "candidates") else list(candidates.get("candidates", ()))
    records = []
    artifacts = {a['id']: a for a in merge_evidence_records(method_input.declared_artifacts)}
    for item in selected:
        if item.get('canonical_record') is not None:
            # Locator-delivered material: the adopted record itself, not a re-derivation.
            record = dict(item['canonical_record'])
            if record.get('id') != f"SRC-{item['id']}":
                raise ValueError('Candidate projection does not match its canonical record')
            records.append(record)
            continue
        locator = {"path": str(item['path']), **dict(item['locator'])}
        if 'truncated' in item:
            if type(item['truncated']) is not bool:
                raise ValueError('Source candidate truncated must be boolean')
            if 'truncated' in locator and locator['truncated'] != item['truncated']:
                raise ValueError('Source candidate completeness metadata conflicts with its locator')
            locator['truncated'] = item['truncated']
        record = {"id": f"SRC-{item['id']}", "snapshot_id": snapshot_id,
                  "kind": "SOURCE_SPAN", "locator": locator,
                  "content_digest": str(item['content_digest']), "excerpt": str(item['text']),
                  "origin": f"selected candidate {item['id']}"}
        if item.get('origin_kind') == 'DECLARED_ARTIFACT':
            artifact = artifacts[item['artifact_id']]
            start = sum(len(line) for line in artifact['content'].splitlines(keepends=True)[:item['locator']['start_line'] - 1])
            end = start + len(item['text'])
            if artifact['content'][start:end] != item['text']:
                raise ValueError('Declared artifact unit does not match its exact source span')
            record.update(kind='DECLARED_ARTIFACT', locator={
                'path': f"cases/{method_input.case_id}/{artifact['path']}",
                'artifact_id': artifact['id'], 'artifact_content_digest': artifact['content_digest'],
                'start_char': start, 'end_char': end}, origin='Immutable public input span')
        records.append(record)
    if source_context is not None:
        adopted = source_context_records(source_context)
        by_key = {source_context_key(r): r for r in adopted}
        for record in records:
            original = by_key.get(source_context_key(record))
            if original is None:
                raise ValueError('Candidate has not entered the canonical source context')
            merge_evidence_records([original, record])
        records = [r for r in adopted if source_context_key(r)[1] == snapshot_id
                   and r.get('kind') in {'SOURCE_SPAN','DECLARED_ARTIFACT','STATIC_SEARCH'}]
    if hasattr(candidates, "supplemental_evidence"):
        records.extend(dict(item) for item in candidates.supplemental_evidence)
    records.extend(dict(item) for item in method_input.public_evidence)
    return merge_evidence_records(records)


def source_context_for_candidates(active, method_input, candidates, snapshot_id):
    records = [r for r in build_evidence_catalog(method_input, candidates, snapshot_id)
               if r['kind'] in {'SOURCE_SPAN', 'DECLARED_ARTIFACT', 'STATIC_SEARCH'}]
    producer = ('locator' if active.get('version') == 2 else
                'extension' if active['candidate_ids'] else 'initial')
    return source_context_offer(active, records, producer=producer, candidate_ids=candidates.selected_ids)


def bind_evidence_ids(evidence_refs: Any, catalog: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(evidence_refs, list):
        raise ValueError("evidence_refs must be an array")
    by_id = {item["id"]: item for item in merge_evidence_records(catalog)}
    bound = []
    seen = set()
    for index, reference in enumerate(evidence_refs):
        if isinstance(reference, Mapping):
            # Fixture and legacy responses already carry the canonical records.
            record_id = reference.get("id")
            if record_id not in by_id or dict(reference) != by_id[record_id]:
                raise ValueError(f"evidence_refs[{index}] is not an exact catalog record")
            record = dict(reference)
        elif isinstance(reference, str):
            if reference not in by_id:
                raise ValueError(f"evidence_refs[{index}] cites unknown catalog ID {reference!r}")
            record = by_id[reference]
        else:
            raise ValueError(f"evidence_refs[{index}] must be a catalog ID")
        if record["id"] in seen:
            raise ValueError(f"evidence_refs[{index}] duplicates catalog ID {record['id']!r}")
        seen.add(record["id"])
        bound.append(record)
    return bound


def field_contract_contexts(units, query, *, max_records=4, max_bytes=8000):
    """Exact local Go JSON-field comments; lexical context, not API truth.

    Highlight relevant source already selected. Never introduce a repository path,
    executable observation, absence claim or edit permission from a comment.
    """
    terms = {word.casefold() for word in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', query)}
    output, seen = [], set()
    for unit in units:
        text = unit.get('text', '')
        if not unit.get('path','').endswith('.go') or not re.match(r'\s*type\s+\w+\s+struct\s*\{', text):
            continue
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            match = re.match(r'^\s*([A-Za-z_]\w*)\s+[^`{}]+`[^`]*json:"([^",]+)[^`]*`', line)
            if not match or not ({match[1].casefold(), match[2].casefold()} & terms):
                continue
            start = index
            while start > 0 and lines[start-1].lstrip().startswith('//'):
                start -= 1
            if start == index:
                continue
            excerpt = ''.join(lines[start:index+1])
            first = unit['locator']['start_line'] + start
            identity = (unit['snapshot_id'], unit['path'], first, excerpt)
            if identity in seen:
                continue
            record = dict(source_evidence_ref='SRC-'+unit['id'], candidate_id=unit['id'],
                snapshot_id=unit['snapshot_id'], source_content_digest=unit['content_digest'],
                field_name=match[1], json_key=match[2],
                locator=dict(path=unit['path'], start_line=first, end_line=unit['locator']['start_line']+index),
                excerpt=excerpt, excerpt_digest=hashlib.sha256(excerpt.encode()).hexdigest(),
                interpretation='Exact selected-source comment/field window; applicability requires review. Not runtime evidence, causal proof or edit permission.')
            if len(output)>=max_records or len(json.dumps(output+[record],ensure_ascii=False).encode())>max_bytes:
                continue
            output.append(record);seen.add(identity)
    return output


def reader_reference_contexts(units, *, max_records=4, max_bytes=8000):
    """Highlight a simple local callback read and later variable assignments.

    Uses only exact selected text. This is a lexical locator, never a proof of
    callback invocation, alias identity, readiness, causality or edit authority.
    Complex closures and known shadowing are deliberately not resolved.
    """
    from contractedge.retrieval.gap_delivery import _go_code
    output, seen = [], set()
    pattern = r'(?m)^[ \t]*(\w+)\s*:=\s*func\s*\(([^\n]*)\)[^\n{]*\{\s*return\s+(\w+)\.(\w+)\([^\n{}]*\)\s*\}'
    for unit in units:
        text=unit.get('text','')
        if (not unit.get('path','').endswith('.go') or unit.get('truncated')
                or not unit.get('id') or not unit.get('snapshot_id')
                or hashlib.sha256(text.encode()).hexdigest()!=unit.get('content_digest')
                or not re.match(r'\s*func\s+\w+\s*\(',text)):
            continue
        code=_go_code(text); lines=text.splitlines(keepends=True)
        def window(start,end):
            first=code.count('\n',0,start); last=code.count('\n',0,max(start,end-1))
            excerpt=''.join(lines[first:last+1]); base=unit['locator']['start_line']
            return dict(locator=dict(path=unit['path'],start_line=base+first,end_line=base+last),
                        excerpt=excerpt,excerpt_digest=hashlib.sha256(excerpt.encode()).hexdigest())
        for match in re.finditer(pattern,code):
            callback, parameters, variable, method=match.groups()
            if (re.search(r'\b'+re.escape(variable)+r'\b',parameters)
                    or any(variable in re.findall(r'\w+', lhs) for lhs in
                           re.findall(r'\b(\w+(?:\s*,\s*\w+)*)\s*:=', code))
                    # Grouped declarations can introduce a different binding.
                    # Skip conservatively; this locator does not resolve scope.
                    or any(re.search(r'\b'+re.escape(variable)+r'\b', group)
                           for group in re.findall(r'\bvar\s*\(([^)]*)', code))
                    or re.search(r'\bfunc\s*\([^)]*\b'+re.escape(variable)+r'\b',code)
                    or len(re.findall(r'(?m)^[ \t]*var\s+'+re.escape(variable)+r'\s+',code))!=1
                    or not re.search(r'(?m)^[ \t]*var\s+'+re.escape(variable)+r'\s+[^\n]+',code[:match.start()])):
                continue
            writes=list(re.finditer(r'(?m)^[ \t]*'+re.escape(variable)+r'\s*=(?!=)[^\n]+',code[match.end():]))
            if not writes:continue
            identity=(unit['snapshot_id'],unit['path'],unit['locator']['start_line'],match.start())
            if identity in seen:continue
            row=dict(source_evidence_ref='SRC-'+unit['id'],candidate_id=unit['id'],snapshot_id=unit['snapshot_id'],
                source_content_digest=unit['content_digest'],variable=variable,callback=callback,receiver_method=method,
                reader=window(match.start(),match.end()),
                writes=[window(match.end()+m.start(),match.end()+m.end()) for m in writes[:4]],
                interpretation='Exact selected-source read/reassignment windows; review shared-reference publication. '
                    'Not verified invocation, same-scope alias proof, runtime readiness, causality or edit permission.')
            if len(output)>=max_records or len(json.dumps(output+[row],ensure_ascii=False).encode())>max_bytes:continue
            output.append(row);seen.add(identity)
    return output
