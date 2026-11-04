import hashlib
import json

import pytest

from contractedge.source import units
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot
from contractedge.retrieval.candidates import requires_gap_delivery, merge_gap_context_optional


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('limits', [{'max_lines': 3}, {'max_chars': 30}])
def test_full_receiver_declaration_keeps_original_coordinates(tmp_path, newline, limits):
    body = newline.join(['package sample', 'func (a First) Run() {', '  Wrong()', '}',
        'func (b Second) Run() {', *['  // 保留行为 ' + 'x' * 220] * 90, '  RequiredTail()', '}', ''])
    (tmp_path / 'sample.go').write_bytes(body.encode())
    snapshot = compute_snapshot(tmp_path)
    fragments = parse_source_units('sample.go', body, snapshot.snapshot_id, **limits)
    old = next(u for u in fragments if u['locator']['start_line'] == 5)
    result = units.materialize_complete_units(snapshot, [old])[0]
    assert 'RequiredTail()' in result['text'] and 'Wrong()' not in result['text']
    assert len(result['text']) > 16000 and not result['truncated']
    assert result['locator']['start_line'] == 5
    assert result['text'] == body[result['locator']['start_char']:result['locator']['end_char']]
    assert result['id'] != old['id'] and old['truncated']
    assert result['content_digest'] == hashlib.sha256(result['text'].encode()).hexdigest()


def test_declared_artifact_never_reads_same_path_repository(tmp_path):
    (tmp_path / 'input.yaml').write_text('kind: WrongRepositoryObject\n')
    snapshot = compute_snapshot(tmp_path)
    content = 'kind: ConfigMap\ndata:\n' + ''.join(f'  k{i}: v{i}\n' for i in range(95))
    artifact = dict(id='A', path='input.yaml', content=content)
    old = units.extract_declared_artifact_units([artifact], snapshot.snapshot_id, max_lines=3)
    full = units.materialize_complete_units(snapshot, old, artifacts=[artifact])
    root = next(u for u in full if u['node_path'] == 'document[0]')
    assert root['text'] == content and 'WrongRepository' not in root['text']
    assert root['origin_kind'] == 'DECLARED_ARTIFACT' and root['id'].startswith('CU-INPUT-A-')
    assert root['mutable'] is False


def test_changed_file_refuses_to_complete_old_snapshot(tmp_path):
    p = tmp_path / 'sample.py'; p.write_text('def run():\n    a = 1\n    return a\n')
    snapshot = compute_snapshot(tmp_path)
    old = parse_source_units(p.name, p.read_text(), snapshot.snapshot_id, max_lines=1)
    p.write_text('def run():\n    return "new"\n')
    with pytest.raises(ValueError, match='snapshot'):
        units.materialize_complete_units(snapshot, old)


def test_adopted_body_and_explicit_window_are_not_replaced(tmp_path):
    p = tmp_path / 'sample.py'; p.write_text('def run():\n    return 2\n')
    snapshot = compute_snapshot(tmp_path)
    old = parse_source_units(p.name, p.read_text(), snapshot.snapshot_id, max_lines=1)
    assert units.materialize_complete_units(snapshot, old, adopted_ids=[old[0]['id']]) == old
    window = {**old[0], 'parser_kind': 'BOUNDED_TEXT_FALLBACK'}
    assert units.materialize_complete_units(snapshot, [window]) == [window]


@pytest.mark.parametrize('record,required', [
    ({'gap_role_keys': ['SOURCE_CONTRACT:x']}, False),
    ({'gap_role_keys': ['SOURCE_READER:x']}, False),
    ({'gap_role_keys': ['SOURCE_CONTRACT:x'], 'required_for_gap_delivery': True}, True),
    ({'gap_role_keys': ['SOURCE_READER:x', 'G:anchor'], 'optional_gap_context': True}, True),
    ({'gap_role_keys': ['G:connector']}, True),
    ({'gap_role_keys': ['SOURCE_CONTRACT:x', 'G:connector'], 'optional_gap_context': True}, False),
])
def test_lexical_role_is_only_a_hint(record, required):
    assert requires_gap_delivery(record) is required
    assert merge_gap_context_optional(record) is (not required)


@pytest.mark.parametrize('extension', [False, True])
def test_complete_body_reaches_actual_front_dispatch(tmp_path, monkeypatch, extension):
    from contractedge.orchestration import runner
    from contractedge.contracts.validation import canonical_digest
    from contractedge.model.provider import ModelResponse
    from tests.test_planning_evidence_extension import front_fixture
    from tests.test_orchestration import PipelineFixtureProvider
    method, _ = front_fixture(tmp_path)
    body = 'def required_handler():\n' + '    # ' + 'x' * 17000 + '\n' + '    pass\n' * 90 + '    return "required_tail"\n'
    (tmp_path / 'handler.py').write_text(body)
    method['scope_hints'].append(dict(kind='path', value='handler.py', raw_text='handler.py', origin_ref='public.scope'))
    method['initial_snapshot_id'] = method['public_evidence'][0]['snapshot_id'] = compute_snapshot(tmp_path).snapshot_id
    method.pop('input_digest'); method['input_digest'] = canonical_digest(method)
    if extension:
        original = runner.extract_units
        monkeypatch.setattr(runner, 'extract_units', lambda *a, **kw:
                            [u for u in original(*a, **kw) if u['path'] != 'handler.py'])
    requests = []
    class Capture(PipelineFixtureProvider):
        def complete_json(self, request):
            requests.append(request)
            if len(requests) == (2 if extension else 1):
                raise RuntimeError('stop after observing actual dispatch')
            reply = json.loads(super().complete_json(request).content)
            reply.update(status='EVIDENCE_GAP', evidence_gaps=[dict(id='G1', summary='Read handler',
                locator_hint={'path': 'handler.py', 'symbol': 'required_handler'})])
            return ModelResponse(json.dumps(reply), {})
    trace = runner.run_front_half(method, tmp_path, Capture(), dict(model='fixture',
        max_context_tokens=100000, max_output_tokens=1024, max_total_tokens=200000,
        max_execution_support_bytes=48000, max_evidence_extensions=2,
        gap_role_delivery_enabled=True), lambda t: len(t.split()))
    assert len(requests) == (2 if extension else 1), trace.errors
    delivered = requests[-1].metadata['source_context']['records']
    assert any(row['excerpt'] == body for row in delivered)
    assert 'required_tail' in json.dumps(requests[-1].messages)


def test_initial_quota_uses_frozen_selection_count():
    from contractedge.diagnosis.evidence import source_context_new, source_context_offer, source_context_validate
    binding = dict(run_id='r', case_id='c', input_digest='i', front_id='f')
    context = source_context_new(binding, initial_records=16)
    records = [dict(id=f'SRC-{i}', kind='SOURCE_SPAN', snapshot_id='s', locator={'path': f'{i}.py'},
                    excerpt='code', content_digest=hashlib.sha256(b'code').hexdigest()) for i in range(16)]
    adopted = source_context_offer(context, records, producer='initial')
    source_context_validate(adopted)
    assert len(adopted['records']) == 16
