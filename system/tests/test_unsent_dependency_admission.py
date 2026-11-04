"""Unsent API offers cannot suppress otherwise admissible local evidence.

These probes run the real front and its prepared transport/adoption boundary;
only discovery results and model replies are controlled public fixtures.
"""
import hashlib
import json
from dataclasses import replace

from contractedge.contracts.validation import canonical_digest
from contractedge.diagnosis.evidence import source_context_records
from contractedge.model.provider import ModelResponse
from contractedge.orchestration import runner
from contractedge.orchestration.recorder import ProviderJournal, RunRecorder
from contractedge.orchestration.state import RepairEngine
from contractedge.retrieval import gap_delivery
from contractedge.source import dependency as reader
from contractedge.source.snapshot import compute_snapshot
from tests.test_dependency_acquisition_delivery import dependency
from tests.test_execution_adopted_material_delivery import PreparedTransport
from tests.test_model_product_origins import FlowProvider
from tests.test_planning_evidence_extension import front_fixture


def run_front(tmp_path, monkeypatch, *, support_limit=48000, context_limit=100000,
              offer=True, second_acquisition=False, word_pressure=False):
    source = tmp_path / 'source'
    source.mkdir(parents=True)
    value, _ = front_fixture(source)
    names = ['RequiredLocal', 'RequiredLater'] if second_acquisition else ['RequiredLocal']
    bodies = {name: 'func ' + name + '() { /* complete mandatory local body */ }\n'
              for name in names}
    for name, body in bodies.items():
        (source / (name + '.go')).write_text(body)
    snapshot = compute_snapshot(source)
    value['initial_snapshot_id'] = value['public_evidence'][0]['snapshot_id'] = snapshot.snapshot_id
    value.pop('input_digest')
    value['input_digest'] = canonical_digest(value)
    required = [dict(id=name, path=name + '.go', kind='FUNCTION_OR_TYPE', symbol=name,
        text=body, content_digest=hashlib.sha256(body.encode()).hexdigest(),
        snapshot_id=snapshot.snapshot_id, parser_kind='GO_LEXICAL_DECLARATION',
        declared_symbols=[name], references=[], truncated=False,
        locator=dict(start_line=1, end_line=1, start_char=0, end_char=len(body), truncated=False),
        required_for_gap_delivery=True, gap_role_keys=[f'G{i+1}:anchor'],
        evidence_gap_refs=[f'G{i+1}']) for i, (name, body) in enumerate(bodies.items())]
    first_api = dependency('AdoptedAPI', snapshot=snapshot.snapshot_id, size=10)
    new_api = dependency('UnsentAPI', snapshot=snapshot.snapshot_id, size=500)
    if word_pressure:
        new_api['excerpt'] += '// ' + 'payload ' * 2000 + '\n'
        new_api['content_digest'] = hashlib.sha256(new_api['excerpt'].encode()).hexdigest()
    lookups, searches, wires = [], [], []

    def lookup(*args, **kwargs):
        # Reoffering the old API is valid but must never label it withdrawn.
        rows = ([first_api] if second_acquisition and not lookups else
                ([first_api, new_api] if second_acquisition and offer else
                 [first_api] if second_acquisition else [new_api] if offer else []))
        assert len([r for r in rows if not kwargs['already_supplied'](r)]) <= kwargs['max_records']
        assert sum(len(r['excerpt'].encode()) for r in rows
                   if not kwargs['already_supplied'](r)) <= kwargs['max_bytes']
        lookups.append([r['id'] for r in rows])
        return rows, []

    def locate(*args, **kwargs):
        index = len(searches)
        searches.append(index)
        return [required[index]], {'static_search_evidence': []}

    class Transport(FlowProvider):
        def prepare_request(self, request):
            return PreparedTransport().prepare_request(request)

        def complete_prepared_json(self, request, prepared):
            wire = json.loads(prepared.body)
            wires.append(wire)
            response = super().complete_json(replace(request, messages=tuple(wire['messages'])))
            if (second_acquisition and request.prompt_version.startswith('diagnosis.')
                    and self.counts['diagnosis'] == 2):
                body = json.loads(response.content)
                body.update(status='EVIDENCE_GAP', evidence_gaps=[dict(id='G2',
                    summary='Read the second required local declaration',
                    locator_hint=dict(path='RequiredLater.go', symbol='RequiredLater'))])
                return ModelResponse(json.dumps(body), {})
            return response

    provider = Transport('diagnosis_extension')
    provider._snapshot_digests = {
        'templates/serviceaccount.yaml': snapshot.file_digests['templates/serviceaccount.yaml']}
    config = dict(model={'model': 'fixture'}, run_id='unsent-admission',
        max_context_tokens=context_limit, model_context_tokens=context_limit,
        max_output_tokens=1024, max_total_tokens=300000,
        max_execution_support_bytes=support_limit, max_model_calls=8,
        max_elapsed_seconds=30, max_format_retries=0, gap_role_delivery_enabled=True,
        max_evidence_extensions=2 if second_acquisition else 1)
    recorder = RunRecorder(tmp_path / 'journal', run_id=config['run_id'], input_digest=value['input_digest'])
    journal = ProviderJournal(recorder)
    journal.engine = RepairEngine(recorder, snapshot.snapshot_id, config)
    with monkeypatch.context() as patch:
        patch.setattr(reader, 'gap_dependency_support', lookup)
        patch.setattr(runner, 'extract_gap_extension_units', lambda *a, **kw: ([], {}))
        patch.setattr(gap_delivery, 'locate_gap_roles', locate)
        trace = runner.run_front_half(value, source, provider, config,
            lambda text: len(text.split()), journal=journal)
    records = source_context_records(trace.run_context['retrieval_config']['source_context'])
    return dict(trace=trace, wires=wires, records=records, lookups=lookups, searches=searches,
        policy=list(journal.engine.policy.snapshot()), bodies=bodies, first_api=first_api, new_api=new_api,
        support_bytes=sum(len(r['excerpt'].encode()) for r in records
                          if r['kind'] in {'SOURCE_SPAN', 'PUBLIC_DEPENDENCY_DECLARATION'}))


def assert_local_only_ready(result):
    trace = result['trace']
    assert trace.status == 'READY', trace.errors
    assert len(result['wires']) == 3
    assert result['lookups'] == [['PUBLICDEP-UnsentAPI']]
    assert result['searches'] == [0]
    assert result['policy'] == [dict(policy='evidence_extension', scope='front', used=1, limit=1)]
    assert trace.retrieval_rounds[-1]['withheld_unsent_dependency_ids'] == ['PUBLICDEP-UnsentAPI']
    assert 'RequiredLocal' in trace.candidate_set.selected_ids
    assert not any(r['kind'] == 'PUBLIC_DEPENDENCY_DECLARATION' for r in result['records'])
    for wire in result['wires'][1:]:
        body = json.dumps(wire['messages'], ensure_ascii=False)
        assert 'PUBLICDEP-UnsentAPI' not in body
        assert result['bodies']['RequiredLocal'].rstrip() in body
    before = result['wires'][0]['messages']
    assert 'PUBLICDEP-UnsentAPI' not in json.dumps(before)


def test_unsent_api_support_pressure_keeps_the_exact_local_fit(tmp_path, monkeypatch):
    control = run_front(tmp_path / 'control', monkeypatch, offer=False)
    assert control['trace'].status == 'READY', control['trace'].errors
    result = run_front(tmp_path / 'pressure', monkeypatch, support_limit=control['support_bytes'])
    assert_local_only_ready(result)
    assert result['support_bytes'] == control['support_bytes']


def test_whole_prepared_request_pressure_uses_local_fit_once(tmp_path, monkeypatch):
    admitted = run_front(tmp_path / 'room', monkeypatch, word_pressure=True)
    assert admitted['trace'].status == 'READY', admitted['trace'].errors
    assert admitted['new_api'] in admitted['records']
    assert admitted['new_api']['excerpt'] in json.loads(
        admitted['wires'][1]['messages'][1]['content'])['adopted_source_context']['records'][-1]['excerpt']
    assert admitted['trace'].model_calls[1]['prompt_tokens_reserved'] + 1024 > 4500
    result = run_front(tmp_path / 'context-pressure', monkeypatch, context_limit=4500,
                       word_pressure=True)
    assert_local_only_ready(result)
    assert all(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] <= 4500
               for c in result['trace'].model_calls)


def test_local_evidence_still_too_large_remains_an_explicit_stop(tmp_path, monkeypatch):
    control = run_front(tmp_path / 'control', monkeypatch, offer=False)
    result = run_front(tmp_path / 'impossible', monkeypatch,
                       support_limit=control['support_bytes'] - 1)
    trace = result['trace']
    assert trace.status == 'EVIDENCE_GAP', trace.errors
    assert any('REQUIRED_SOURCE_NOT_DELIVERED' in error for error in trace.errors)
    assert len(result['wires']) == len(result['lookups']) == len(result['searches']) == 1
    assert result['policy'] == [dict(policy='evidence_extension', scope='front', used=1, limit=1)]
    assert trace.retrieval_rounds[-1]['withheld_unsent_dependency_ids'] == ['PUBLICDEP-UnsentAPI']
    assert 'RequiredLocal' not in trace.candidate_set.selected_ids
    assert not any(r['kind'] == 'PUBLIC_DEPENDENCY_DECLARATION' for r in result['records'])


def test_second_acquisition_preserves_adopted_api_when_new_offer_is_withheld(tmp_path, monkeypatch):
    control = run_front(tmp_path / 'control', monkeypatch, offer=False, second_acquisition=True)
    assert control['trace'].status == 'READY', control['trace'].errors
    result = run_front(tmp_path / 'pressure', monkeypatch, second_acquisition=True,
                       support_limit=control['support_bytes'])
    trace = result['trace']
    assert trace.status == 'READY', trace.errors
    assert len(result['wires']) == 4
    assert len(result['lookups']) == len(result['searches']) == 2
    assert result['policy'] == [dict(policy='evidence_extension', scope='front', used=2, limit=2)]
    assert result['first_api'] in result['records']
    assert result['new_api'] not in result['records']
    assert trace.retrieval_rounds[-1]['withheld_unsent_dependency_ids'] == ['PUBLICDEP-UnsentAPI']
    assert 'withheld_unsent_dependency_ids' not in trace.retrieval_rounds[-2]
    assert set(result['bodies']) <= set(trace.candidate_set.selected_ids)
    for wire in result['wires'][1:]:
        payload = json.loads(wire['messages'][1]['content'])
        assert result['first_api'] in payload['adopted_source_context']['records']
        assert 'PUBLICDEP-UnsentAPI' not in json.dumps(wire)
    assert result['support_bytes'] == control['support_bytes']
