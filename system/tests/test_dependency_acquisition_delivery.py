"""Dependency context shares acquisition limits and the actual dispatch boundary."""
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from contractedge.diagnosis.evidence import (source_context_new, source_context_records,
    source_context_offer, source_context_for_candidates)
from contractedge.retrieval.candidates import build_candidate_set, extend_once
from tests.test_required_extension_selection import unit


def dependency(name='Options', *, snapshot='S', size=80):
    text = 'type ' + name + ' struct {}\n' + '// ' + 'x' * size + '\n'
    return dict(id='PUBLICDEP-' + name, kind='PUBLIC_DEPENDENCY_DECLARATION',
        current_source_snapshot=snapshot, module='example.org/lib', version='v1.2.3',
        package='options', archive_path='deps.tar.gz', archive_sha256='a' * 64,
        environment_sha256='b' * 64, member='lib/options.go', read_only=True,
        content_digest=hashlib.sha256(text.encode()).hexdigest(), excerpt=text,
        interpretation='Pinned public read-only declaration, not editable or causal proof')


def selection():
    method = SimpleNamespace(case_id='case', declared_artifacts=(), public_evidence=())
    active = source_context_new(dict(run_id='run', case_id='case', input_digest='input',
        front_id='front'), evidence_extensions=2)
    base = build_candidate_set('Added', None, [unit('base', 'func Base() {}\n')], [],
        top_n=12, max_context_tokens=100000, token_counter=len)
    return method, base, source_context_for_candidates(active, method, base, 'S')


def grow(base, active, rows, *, prefix='fresh', size=20):
    proposals = [unit(prefix + str(i), 'func Added' + str(i) + '() { /*' + 'x' * size + '*/ }\n')
                 for i in range(20)]
    return extend_once(base, [{'id': 'G', 'locator_hint': {'symbol': 'Added'}}], proposals,
        max_context_tokens=100000, token_counter=len, source_context=active,
        allow_reextension=base.extension_used, source_reservations=rows)


def test_quota_is_shared_by_dependency_and_local_source_in_each_acquisition():
    method, base, active = selection()
    original = json.dumps(active, sort_keys=True)
    apis = [dependency('Options' + str(i)) for i in range(4)]
    grown = grow(base, active, apis)
    assert len(grown.candidates) - len(base.candidates) == 8
    assert json.dumps(active, sort_keys=True) == original, 'Reservation is not adoption'
    adopted = source_context_offer(source_context_for_candidates(active, method, grown, 'S'),
        apis, producer='extension')
    assert len(source_context_records(adopted, producer='extension')) == 12
    # Resending identical API context neither consumes another batch slot nor
    # permits cumulative source quotas to reset.
    second = grow(grown, adopted, apis, prefix='second')
    assert len(second.candidates) - len(grown.candidates) == 12
    final = source_context_offer(source_context_for_candidates(adopted, method, second, 'S'),
        apis, producer='extension')
    assert len(source_context_records(final, producer='extension')) == 24
    assert grow(second, final, apis, prefix='third').selected_ids == second.selected_ids


def test_quota_counts_full_dependency_bytes_before_optional_local_selection():
    method, base, active = selection()
    api = dependency(size=23000)
    grown = grow(base, active, [api], size=300)
    added = [u for u in grown.candidates if u['id'] not in base.selected_ids]
    assert len(api['excerpt'].encode()) + sum(len(u['text'].encode()) for u in added) <= 24000
    assert len(added) < 11
    draft = source_context_offer(source_context_for_candidates(active, method, grown, 'S'),
        [api], producer='extension')
    assert api in source_context_records(draft)


def test_quota_rejects_an_oversized_single_acquisition_even_with_two_total_allowances():
    _, base, active = selection()
    with pytest.raises(ValueError, match='acquisition quota'):
        grow(base, active, [dependency(size=25000)])


@pytest.mark.parametrize('calls_allowed,strip_api', [(1, False), (8, False), (8, True)])
def test_actual_diagnosis_and_planning_receive_readonly_api_only_after_admitted_extension(
        tmp_path, monkeypatch, calls_allowed, strip_api):
    from contractedge.source import dependency as reader
    from contractedge.orchestration.runner import run_front_half
    from tests.test_planning_evidence_extension import front_fixture
    from tests.test_model_product_origins import FlowProvider
    from contractedge.orchestration.recorder import RunRecorder, ProviderJournal
    from contractedge.orchestration.state import RepairEngine
    from contractedge.source.snapshot import compute_snapshot
    source = tmp_path / 'source'
    source.mkdir()
    value, _ = front_fixture(source)
    api = dependency(snapshot=value['initial_snapshot_id'])
    offered = []

    def lookup(*args, **kwargs):
        offered.append(kwargs)
        return [api], [{'result': 'controlled-reader-offer'}]

    monkeypatch.setattr(reader, 'gap_dependency_support', lookup)

    class WireProvider(FlowProvider):
        def prepare_request(self, request):
            messages = json.loads(json.dumps(request.messages))
            if strip_api:
                def remove(value):
                    if isinstance(value, dict):
                        if value.get('id') == api['id']:
                            value.pop('excerpt', None)
                        for child in value.values(): remove(child)
                    elif isinstance(value, list):
                        for child in value: remove(child)
                for message in messages:
                    if message['role'] == 'user':
                        body = json.loads(message['content'])
                        remove(body)
                        message['content'] = json.dumps(body)
            raw = json.dumps({'messages': messages}).encode()
            return SimpleNamespace(body=raw, metadata={}, overhead='', output_schema=None,
                wire_schema=None, audit_record=lambda: {'body_sha256': hashlib.sha256(raw).hexdigest()})

        def complete_prepared_json(self, request, prepared):
            return super().complete_json(replace(request,
                messages=tuple(json.loads(prepared.body)['messages'])))

    provider = WireProvider('diagnosis_extension')
    provider._snapshot_digests = compute_snapshot(source).file_digests
    config = dict(model={'model': 'fixture'}, run_id='dependency-delivery',
        max_context_tokens=24000, max_output_tokens=1024, max_total_tokens=100000,
        max_model_calls=calls_allowed, max_elapsed_seconds=30, max_format_retries=0)
    recorder = RunRecorder(tmp_path / 'journal', run_id=config['run_id'], input_digest=value['input_digest'])
    journal = ProviderJournal(recorder)
    journal.engine = RepairEngine(recorder, value['initial_snapshot_id'], config)
    trace = run_front_half(value, source, provider, config,
        lambda text: len(text.split()), journal=journal)
    assert offered
    adopted = trace.run_context['retrieval_config']['source_context']
    api_rows = source_context_records(adopted, kind='PUBLIC_DEPENDENCY_DECLARATION')
    if calls_allowed == 1:
        assert trace.status == 'BUDGET_EXHAUSTED'
        assert len(provider.requests) == 1 and api_rows == []
        return
    if strip_api:
        assert trace.status == 'EVIDENCE_DELIVERY_INCOMPLETE', trace.errors
        assert len(provider.requests) == 1 and api_rows == []
        return
    assert trace.status == 'READY', trace.errors
    assert api_rows == [api]
    assert len(provider.requests) == 3
    initial, extended, planning = [json.loads(r.messages[1]['content']) for r in provider.requests]
    assert api['id'] not in json.dumps(initial)
    for body in (extended, planning):
        assert api in body['adopted_source_context']['records']
    assert api['id'] not in extended['allowed_evidence_ids']
    assert all(r['id'] != api['id'] for r in extended['evidence_catalog'])
    assert all(r['id'] != api['id'] for r in trace.diagnosis['evidence_refs'])
    assert api['id'] not in trace.candidate_set.selected_ids
