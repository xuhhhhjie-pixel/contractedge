"""Unsent dependency support cannot displace required local source bodies."""
import json
from dataclasses import replace

import pytest

from contractedge.diagnosis.evidence import (source_context_for_candidates,
    source_context_offer, source_context_records)
from contractedge.retrieval.candidates import (extend_once,
    source_extension_dependency_allowance)
from tests.test_dependency_acquisition_delivery import dependency, selection
from tests.test_required_extension_selection import unit


GAPS = [{'id': 'G', 'locator_hint': {'symbol': 'Added'}}]


def extend(base, active, proposals, rows=()):
    return extend_once(base, GAPS, proposals, max_context_tokens=100000,
        token_counter=len, source_context=active, source_reservations=rows,
        allow_reextension=base.extension_used)


def required_units(count=9, *, prefix='required', size=0):
    return [unit(prefix + str(i), 'func Added' + str(i) + '() { /*' + 'x' * size + '*/ }\n',
        required=True, role='G' + str(i) + ':anchor') for i in range(count)]


@pytest.mark.parametrize('pressure', ['count', 'bytes'])
def test_direct_reservations_reject_displacement_of_required_local_source(pressure):
    _, base, active = selection()
    required = required_units() if pressure == 'count' else required_units(1, size=2000)
    rows = ([dependency('Options' + str(i)) for i in range(4)] if pressure == 'count'
            else [dependency(size=23000)])
    local = extend(base, active, required)
    assert set(local.required_endpoint_ids) <= set(local.selected_ids)
    original = json.dumps(active, sort_keys=True)
    with pytest.raises(ValueError, match='required local source admission'):
        extend(base, active, required, rows)
    assert json.dumps(active, sort_keys=True) == original


def test_required_endpoints_leave_space_for_dependencies_instead_of_reserving_optional_source():
    method, base, active = selection()
    required = required_units()
    optional = [unit('optional' + str(i), 'func Extra' + str(i) + '() {}\n') for i in range(8)]
    local = extend(base, active, required + optional)
    assert len(local.candidates) - len(base.candidates) == 12
    count, size = source_extension_dependency_allowance(local, active)
    assert count == 3
    apis = [dependency('Options' + str(i)) for i in range(count)]
    final = extend(base, active, required + optional, apis)
    assert set(final.required_endpoint_ids) <= set(final.selected_ids)
    assert not set(u['id'] for u in optional) & set(final.selected_ids)
    adopted = source_context_offer(source_context_for_candidates(active, method, final, 'S'),
        apis, producer='extension')
    assert len(source_context_records(adopted, producer='extension')) == 12
    assert size == 24000 - sum(len(u['text'].encode()) for u in required)


def test_canonical_alias_and_adopted_required_source_are_not_reserved_twice():
    _, base, active = selection()
    required = required_units(1)[0]
    alias = {**required, 'id': 'alias'}
    local = extend(base, active, [required, alias])
    assert len(local.required_endpoint_ids) == 1
    count, size = source_extension_dependency_allowance(local, active)
    assert count == 11
    assert size == 24000 - len(required['text'].encode())


def test_repeated_api_and_required_source_keep_global_quota_without_double_charging():
    method, base, active = selection()
    first_required = required_units()
    old_apis = [dependency('Options' + str(i)) for i in range(3)]
    first = extend(base, active, first_required, old_apis)
    adopted = source_context_offer(source_context_for_candidates(active, method, first, 'S'),
        old_apis, producer='extension')
    new_required = required_units(10, prefix='second')
    local = extend(first, adopted, first_required + new_required)
    count, _ = source_extension_dependency_allowance(local, adopted)
    assert count == 2, 'Previously adopted required endpoints consume no new batch slots'
    new_apis = [dependency('NextOptions' + str(i)) for i in range(count)]
    final = extend(first, adopted, first_required + new_required, old_apis + new_apis)
    assert set(final.required_endpoint_ids) <= set(final.selected_ids)
    committed = source_context_offer(source_context_for_candidates(adopted, method, final, 'S'),
        old_apis + new_apis, producer='extension')
    records = source_context_records(committed, producer='extension')
    assert len(records) == 24 and len({r['id'] for r in records}) == 24
    assert all(r in records for r in source_context_records(adopted, producer='extension'))
    assert source_extension_dependency_allowance(final, committed)[0] == 0
    repeated = extend(final, committed, [], old_apis + new_apis)
    assert repeated.selected_ids == final.selected_ids


@pytest.mark.parametrize('pressure,transport_loss,reader_overflow', [
    ('count', False, False), ('bytes', False, False),
    ('count', True, False), ('count', False, True), ('reader_cap', False, True),
])
def test_runner_preserves_required_source_and_sends_only_admitted_dependencies(
        tmp_path, monkeypatch, pressure, transport_loss, reader_overflow):
    from contractedge.contracts.validation import canonical_digest
    from contractedge.orchestration import runner
    from contractedge.orchestration.recorder import RunRecorder, ProviderJournal
    from contractedge.orchestration.state import RepairEngine
    from contractedge.source import dependency as reader
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.retrieval import gap_delivery
    from tests.test_execution_adopted_material_delivery import PreparedTransport
    from tests.test_model_product_origins import FlowProvider
    from tests.test_planning_evidence_extension import front_fixture

    source = tmp_path / 'source'
    source.mkdir()
    value, _ = front_fixture(source)
    required = (required_units() if pressure == 'count' else
                required_units(1, size=2000 if pressure == 'bytes' else 0))
    optional = [unit('optional' + str(i), 'func Extra' + str(i) + '() {}\n') for i in range(12)]
    for row in required + optional:
        (source / row['path']).write_text(row['text'])
    snapshot = compute_snapshot(source)
    for row in required + optional:
        row['snapshot_id'] = snapshot.snapshot_id
    value['initial_snapshot_id'] = snapshot.snapshot_id
    value['public_evidence'][0]['snapshot_id'] = snapshot.snapshot_id
    value.pop('input_digest')
    value['input_digest'] = canonical_digest(value)
    apis = ([dependency('Options' + str(i), snapshot=snapshot.snapshot_id)
             for i in range(5 if pressure == 'reader_cap' else 4)]
            if pressure != 'bytes' else
            [dependency('Oversized', snapshot=snapshot.snapshot_id, size=23000),
             dependency('Fits', snapshot=snapshot.snapshot_id)])
    lookup_calls, offered = [], []

    def lookup(*args, **kwargs):
        lookup_calls.append(kwargs)
        used = 0
        rows = []
        for api in apis:
            size = len(api['excerpt'].encode())
            if not reader_overflow and (len(rows) >= kwargs['max_records']
                                       or used + size > kwargs['max_bytes']):
                continue
            rows.append(api)
            used += size
        offered.extend(rows)
        return rows, []

    monkeypatch.setattr(reader, 'gap_dependency_support', lookup)
    monkeypatch.setattr(runner, 'extract_gap_extension_units', lambda *a, **kw: ([], {}))
    monkeypatch.setattr(gap_delivery, 'locate_gap_roles',
        lambda *a, **kw: (required + optional, {'static_search_evidence': []}))

    class WireProvider(FlowProvider):
        def prepare_request(self, request):
            def mutate(payload):
                if transport_loss:
                    for row in payload.get('adopted_source_context', {}).get('records', []):
                        if row.get('kind') == 'PUBLIC_DEPENDENCY_DECLARATION':
                            row.pop('excerpt', None)
            return PreparedTransport(mutate).prepare_request(request)

        def complete_prepared_json(self, request, prepared):
            return super().complete_json(replace(request,
                messages=tuple(json.loads(prepared.body)['messages'])))

    provider = WireProvider('diagnosis_extension')
    provider._snapshot_digests = {
        'templates/serviceaccount.yaml': snapshot.file_digests['templates/serviceaccount.yaml']}
    config = dict(model={'model': 'fixture'}, run_id='required-local-quota',
        max_context_tokens=100000, max_output_tokens=1024, max_total_tokens=300000,
        max_execution_support_bytes=48000, max_model_calls=8, max_elapsed_seconds=30,
        max_format_retries=0, gap_role_delivery_enabled=True)
    recorder = RunRecorder(tmp_path / 'journal', run_id=config['run_id'], input_digest=value['input_digest'])
    journal = ProviderJournal(recorder)
    journal.engine = RepairEngine(recorder, value['initial_snapshot_id'], config)
    trace = runner.run_front_half(value, source, provider, config,
        lambda text: len(text.split()), journal=journal)
    assert len(lookup_calls) == 1
    assert journal.engine.policy.used('evidence_extension', 'front') == 1
    assert lookup_calls[0]['max_records'] == (3 if pressure == 'count' else 4)
    assert lookup_calls[0]['max_bytes'] == 24000 - sum(len(u['text'].encode()) for u in required)
    adopted = trace.run_context['retrieval_config']['source_context']
    actual_api = source_context_records(adopted, kind='PUBLIC_DEPENDENCY_DECLARATION')
    if reader_overflow or transport_loss:
        assert len(provider.requests) == 1 and not actual_api
        assert trace.status == ('EVIDENCE_GAP' if reader_overflow else 'EVIDENCE_DELIVERY_INCOMPLETE')
        return
    assert trace.status == 'READY', trace.errors
    assert len(provider.requests) == 3
    assert actual_api == offered
    assert len(actual_api) == (3 if pressure == 'count' else 1)
    assert set(u['id'] for u in required) <= set(trace.candidate_set.selected_ids)
    records = source_context_records(adopted, producer='extension')
    assert len([r for r in records if r['kind'] != 'STATIC_SEARCH']) <= 12
    assert sum(len(r['excerpt'].encode()) for r in records if r['kind'] != 'STATIC_SEARCH') <= 24000
    initial, extended, planning = [json.loads(r.messages[1]['content']) for r in provider.requests]
    assert not any(api['id'] in json.dumps(initial) for api in apis)
    for body in (extended, planning):
        assert all(api in body['adopted_source_context']['records'] for api in actual_api)
        assert all(api['id'] not in json.dumps(body) for api in apis if api not in actual_api)
    assert all(any(r['id'] == 'SRC-' + u['id'] and r['excerpt'] == u['text']
                   for r in extended['evidence_catalog']) for u in required)
