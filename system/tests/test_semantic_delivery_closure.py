"""Final-boundary counterexamples; no model API or Docker is used."""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contractedge.contracts.validation import canonical_digest
from contractedge.development import public_demo
from contractedge.model.admission import quote_request, complete_quoted
from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelRequest, ModelResponse
from contractedge.orchestration.recorder import ProviderJournal, RunRecorder
from contractedge.orchestration import runner as front_runner
from contractedge.orchestration.semantic_delivery import (
    SemanticDeliveryError, delivery_context, inspect_delivery, install_monitor,
    require_delivery, safe_request_projection,
)


TEXT = 'Use the selected value and preserve the disabled default.'
SOURCE = {'id': 'SRC-1', 'kind': 'SOURCE_SPAN', 'snapshot_id': 's0',
          'locator': {'path': 'helper.py'}, 'excerpt': 'return selected\n',
          'content_digest': hashlib.sha256(b'return selected\n').hexdigest(), 'delivery_required': True}
ATOM = {'id': 'A1', 'statement': TEXT, 'source_refs': ['requirement_text:0:' + str(len(TEXT))]}
DIAGNOSIS = {'run_id': 'run-1', 'obligation_atoms': [ATOM],
             'expected_relations': [{'id': 'E1', 'obligation_refs': ['A1'], 'statement': 'Consumer uses selected value.'}],
             'violations': [{'id': 'V1', 'relation_id': 'E1', 'evidence_refs': ['SRC-1'], 'statement': 'Default is used.'}],
             'evidence_refs': []}
CHANGE = {'id': 'C1', 'operation': 'REPLACE', 'semantic_action': 'BIND_SELECTED',
          'intent': 'Use selected value.', 'violation_refs': ['V1'], 'postcondition_refs': ['P1'], 'depends_on': []}
CHANGESET = {'run_id': 'run-1', 'planned_violation_ids': ['V1'], 'changes': [CHANGE],
             'postconditions': [{'id': 'P1', 'statement': TEXT, 'obligation_refs': ['A1'],
                                'relation_refs': ['E1'], 'behavior_check_refs': ['public:selected']}]}
METHOD = {'input_digest': 'a' * 64, 'requirement_text': TEXT}


class CountingProvider:
    def __init__(self):
        self.requests, self.quotes = [], 0

    def request_overhead(self, request):
        self.quotes += 1
        return ''

    def complete_json(self, request):
        self.requests.append(asdict(request))
        return ModelResponse('{}', {})


def request(payload, **kwargs):
    return ModelRequest(model='fixture', prompt_version='semantic-fixture.v1',
        messages=({'role': 'user', 'content': json.dumps(payload)},), max_output_tokens=32, **kwargs)


def context(**kwargs):
    return delivery_context(METHOD, consumer=kwargs.pop('consumer', 'EXECUTION'), snapshot_id='s0',
        diagnosis=DIAGNOSIS, changeset=CHANGESET, necessary_support=[SOURCE], **kwargs)


def payload():
    return {'requirement': TEXT, 'obligation_atoms': [ATOM], 'expected_relations': DIAGNOSIS['expected_relations'],
            'violations': DIAGNOSIS['violations'], 'changes': [CHANGE],
            'postconditions': CHANGESET['postconditions'], 'read_only_source_support': [dict(SOURCE)]}


def inspect(body, frame=None):
    provider = CountingProvider()
    req = request(body)
    return inspect_delivery(req, quote_request(provider, req, len), frame or context())


def evidence_row(report):
    return next(row for row in report['subjects'] if row['id'] == 'SRC-1')


def test_full_exact_content_records_separate_obligation_e_v_c_p_and_original_span():
    report = inspect(payload())
    assert report['status'] == 'REQUIRED_CONTENT_VERIFIED'
    assert not report['issues']
    assert {row['kind'] for row in report['subjects']} >= {
        'ORIGINAL_REQUIREMENT', 'OBLIGATION', 'EXPECTED_RELATION', 'VIOLATION', 'CHANGE', 'POSTCONDITION', 'EVIDENCE'}
    atom = next(row for row in report['subjects'] if row['id'] == 'A1')
    assert atom['origin']['spans'][0]['quote'] == TEXT
    assert atom['origin']['spans'][0]['sha256'] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert atom['required_content_gate'] is False
    assert evidence_row(report)['required_content_gate'] is True
    assert report['adoption'] == 'UNVERIFIED'


def test_intermediate_content_does_not_certify_missing_final_request():
    intermediate = context()
    body = payload()
    del body['read_only_source_support']
    report = inspect(body, intermediate)
    assert evidence_row(report)['classification'] == 'IDENTITY_ONLY'
    assert report['issues'][0]['identity_locations']
    with pytest.raises(SemanticDeliveryError, match='SRC-1:IDENTITY_ONLY'):
        require_delivery(report)


def test_same_id_with_changed_content_is_a_located_drift():
    body = payload()
    body['read_only_source_support'][0]['excerpt'] = 'return default\n'
    report = inspect(body)
    assert evidence_row(report)['classification'] == 'CONTENT_DRIFT'
    assert any('/read_only_source_support/0/excerpt' in item['pointer'] for item in evidence_row(report)['locations'])
    with pytest.raises(SemanticDeliveryError, match='CONTENT_DRIFT'):
        require_delivery(report)


def test_explicit_summary_is_never_original_or_sufficient_for_required_excerpt():
    frame = context()
    subject = next(row for row in frame['subjects'] if row['id'] == 'SRC-1')
    summary = {'id': 'SRC-1', 'summary': 'The helper returns the selected value.'}
    subject['explicit_projections'] = [{'kind': 'SUMMARY', 'recipe': 'TEST_DECLARED_SUMMARY',
        'source_digest': subject['original_digest'], 'value': summary}]
    body = payload()
    body['read_only_source_support'] = [summary]
    report = inspect(body, frame)
    assert evidence_row(report)['classification'] == 'EXPLICIT_SUMMARY'
    with pytest.raises(SemanticDeliveryError, match='EXPLICIT_SUMMARY'):
        require_delivery(report)
    subject['explicit_projections'][0]['source_digest'] = '0' * 64
    assert evidence_row(inspect(body, frame))['classification'] == 'IDENTITY_ONLY'


@pytest.mark.parametrize('consumer', ['EXECUTION_FORMAT_RETRY', 'FEEDBACK_CHANGESET_REVISION'])
def test_correction_and_feedback_final_transformations_are_checked_again(consumer):
    assert not inspect(payload(), context(consumer=consumer))['issues']
    final = payload()
    final['read_only_source_support'] = [{'id': 'SRC-1', 'record_ref': '/not-present'}]
    report = inspect(final, context(consumer=consumer))
    assert report['issues'][0]['consumer'] == consumer
    assert evidence_row(report)['classification'] == 'IDENTITY_ONLY'
    require_delivery(inspect(payload(), context(consumer=consumer)))
    with pytest.raises(SemanticDeliveryError):
        require_delivery(report)


def test_exact_declared_target_content_is_bound_without_duplicating_support_excerpt():
    body = payload()
    body['read_only_source_support'] = [{k: v for k, v in SOURCE.items() if k != 'excerpt'}]
    body['current_target_files'] = [{'path': 'helper.py', 'content': 'def helper():\n    return selected\n'}]
    report = inspect(body)
    assert evidence_row(report)['classification'] == 'FULL_ORIGINAL'
    assert any(item.get('binding') == 'EXACT_DECLARED_SOURCE_PATH' for item in evidence_row(report)['locations'])
    body['current_target_files'][0]['path'] = 'unrelated.py'
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(body))


@pytest.mark.parametrize('key,value', [('snapshot_id', 'stale-snapshot'), ('locator', {'path': 'another.py'})])
def test_review_same_id_excerpt_cannot_relabel_snapshot_or_locator(key, value):
    body = payload()
    body['read_only_source_support'][0][key] = value
    report = inspect(body)
    with pytest.raises(SemanticDeliveryError, match='IDENTITY_DRIFT'):
        require_delivery(report)
    assert evidence_row(report)['classification'] == 'IDENTITY_DRIFT'


@pytest.mark.parametrize('malformation', ['missing-excerpt', 'wrong-digest'])
def test_review_explicit_required_source_cannot_become_optional_when_unverifiable(malformation):
    invalid = json.loads(json.dumps(SOURCE))
    if malformation == 'missing-excerpt':
        del invalid['excerpt']
    else:
        invalid['content_digest'] = '0' * 64
    frame = delivery_context(METHOD, consumer='EXECUTION', snapshot_id='s0', necessary_support=[invalid])
    report = inspect(payload(), frame)
    with pytest.raises(SemanticDeliveryError, match='REQUIRED_SOURCE_UNVERIFIABLE'):
        require_delivery(report)
    assert evidence_row(report)['required_content_gate'] is True


def test_review_conflicting_original_records_are_preserved_and_rejected_not_overwritten():
    changed = {**SOURCE, 'excerpt': 'return other\n',
               'content_digest': hashlib.sha256(b'return other\n').hexdigest()}
    diagnosis = {**DIAGNOSIS, 'evidence_refs': [SOURCE]}
    frame = delivery_context(METHOD, consumer='EXECUTION', snapshot_id='s0',
                             diagnosis=diagnosis, necessary_support=[changed])
    body = payload()
    body['read_only_source_support'] = [changed]
    report = inspect(body, frame)
    with pytest.raises(SemanticDeliveryError, match='CONFLICTING_ORIGINAL_SOURCE'):
        require_delivery(report)
    conflict = frame['source_conflicts'][0]
    assert conflict['id'] == 'SRC-1'
    assert {r['excerpt'] for r in conflict['records']} == {SOURCE['excerpt'], changed['excerpt']}


@pytest.mark.parametrize('invalid_path', [None, '', '../outside.py'])
def test_review_target_source_bridge_requires_a_legal_nonempty_declared_path(invalid_path):
    invalid = {**SOURCE, 'locator': {'path': invalid_path}}
    frame = delivery_context(METHOD, consumer='EXECUTION', snapshot_id='s0', necessary_support=[invalid])
    body = {'support_identity': [{k: v for k, v in invalid.items() if k != 'excerpt'}],
            'current_target_files': [{'path': invalid_path, 'content': SOURCE['excerpt']}]}
    report = inspect(body, frame)
    with pytest.raises(SemanticDeliveryError, match='REQUIRED_SOURCE_UNVERIFIABLE'):
        require_delivery(report)


def test_required_prerequisite_without_its_actual_origin_record_is_not_filled_from_the_request():
    frame = delivery_context(METHOD, consumer='EXECUTION', snapshot_id='s0',
                             prerequisites=[{'source_evidence_ref': 'SRC-1', 'reason': 'Existing caller prerequisite.'}])
    report = inspect(payload(), frame)
    assert evidence_row(report)['original']['actual_original_record'] == 'MISSING'
    with pytest.raises(SemanticDeliveryError, match='REQUIRED_SOURCE_UNVERIFIABLE'):
        require_delivery(report)


def test_historical_source_reread_requires_independent_exact_current_file_binding(tmp_path):
    excerpt = SOURCE['excerpt']
    (tmp_path / 'helper.py').write_text(excerpt)
    frame = delivery_context(METHOD, consumer='EXECUTION', snapshot_id='s1',
                             necessary_support=[SOURCE], source_root=tmp_path)
    current = {**SOURCE, 'snapshot_id': 's1', 'locator': {'path': 'helper.py', 'start_line': 1, 'end_line': 1}}
    report = inspect({'support': [current]}, frame)
    assert evidence_row(report)['classification'] == 'EXPLICIT_SOURCE_REREAD'
    assert evidence_row(report)['locations'][0]['origin_identity']['snapshot_id'] == 's0'
    assert evidence_row(report)['locations'][0]['current_identity']['snapshot_id'] == 's1'
    require_delivery(report)
    # The same claimed fresh identity has no authority without the actual re-read.
    without_read = delivery_context(METHOD, consumer='EXECUTION', snapshot_id='s1', necessary_support=[SOURCE])
    with pytest.raises(SemanticDeliveryError, match='IDENTITY_DRIFT'):
        require_delivery(inspect({'support': [current]}, without_read))


def test_current_selected_target_projection_preserves_historical_identity_and_partial_repairs(tmp_path):
    content = 'return already_partly_repaired\n'
    (tmp_path / 'helper.py').write_text(content)
    plan = {'operations': [{'change_id': 'C1', 'target': {'path': 'helper.py'}}]}
    frame = delivery_context(METHOD, consumer='EXECUTION', snapshot_id='s1', plan=plan,
                             necessary_support=[SOURCE], source_root=tmp_path)
    body = {'support_identity': [{k: v for k, v in SOURCE.items() if k != 'excerpt'}],
            'current_target_files': [{'path': 'helper.py', 'content': content}], 'acceptance': {'snapshot_id': 's1'}}
    report = inspect(body, frame)
    row = evidence_row(report)
    assert row['classification'] == 'IDENTITY_ONLY'
    assert row['original']['snapshot_id'] == 's0'
    assert row['required_content_gate'] is False
    current = next(r for r in report['subjects'] if r['kind'] == 'CURRENT_TARGET_SOURCE')
    assert current['classification'] == 'FULL_CURRENT_TARGET_BYTES'
    assert current['original']['snapshot_id'] == 's1'
    assert current['required_content_gate'] is True
    assert current['required_basis'][0]['component'] == 'current_file_bytes'
    require_delivery(report)
    body['current_target_files'][0]['content'] = 'arbitrary replacement\n'
    with pytest.raises(SemanticDeliveryError, match='CONTENT_DRIFT'):
        require_delivery(inspect(body, frame))
    body['current_target_files'][0]['content'] = content
    prerequisite_frame = delivery_context(METHOD, consumer='EXECUTION', snapshot_id='s1', plan=plan,
        necessary_support=[SOURCE], source_root=tmp_path,
        prerequisites=[{'source_evidence_ref': 'SRC-1', 'reason': 'The original caller contract is necessary.'}])
    with pytest.raises(SemanticDeliveryError, match='SRC-1:IDENTITY_ONLY'):
        require_delivery(inspect(body, prerequisite_frame))


def test_required_original_prerequisite_cannot_be_replaced_by_current_target_bytes(tmp_path):
    content = 'arbitrary current target bytes\n'
    (tmp_path / 'helper.py').write_text(content)
    frame = delivery_context(METHOD, consumer='EXECUTION_RETRY', snapshot_id='s1',
        plan={'operations': [{'change_id': 'C1', 'target': {'path': 'helper.py'}}]},
        necessary_support=[SOURCE], source_root=tmp_path,
        prerequisites=[{'source_evidence_ref': 'SRC-1', 'reason': 'This original source contract is still necessary.'}])
    body = {'support_identity': [{k: v for k, v in SOURCE.items() if k != 'excerpt'}],
        'current_target_files': [{'path': 'helper.py', 'content': content}], 'acceptance': {'snapshot_id': 's1'}}
    report = inspect(body, frame)
    with pytest.raises(SemanticDeliveryError, match='SRC-1:IDENTITY_ONLY'):
        require_delivery(report)


def test_legacy_and_explicit_ablation_do_not_synthesize_full_layers_or_complete():
    frame = delivery_context({}, consumer='LEGACY', layer_absence={'diagnosis': 'NOT_APPLICABLE'})
    report = inspect({}, frame)
    assert frame['layers'] == {'diagnosis': 'NOT_APPLICABLE', 'changeset': 'UNKNOWN'}
    assert report['subjects'] == []
    assert report['status'] == 'RECORDED_WITHOUT_REQUIRED_CONTENT_CLAIM'
    assert 'COMPLETE' not in report['status']
    assert report['adoption'] == 'UNVERIFIED'


def test_prepared_projection_uses_final_messages_and_never_opaque_headers_or_auth():
    secret = 'DO-NOT-SERIALIZE-OPAQUE-AUTH'
    req = request(payload())
    final = payload()
    del final['read_only_source_support']
    prepared = SimpleNamespace(body=json.dumps({'messages': [{'role': 'user', 'content': json.dumps(final)}],
        'headers': {'Authorization': secret}, 'api_key': secret}).encode(), metadata={'credential': secret}, headers={'Authorization': secret})
    quote = SimpleNamespace(prepared=prepared, request_digest=canonical_digest(asdict(req)), _prepared_digest='b' * 64)
    safe = safe_request_projection(req, quote)
    assert secret not in json.dumps(safe) and 'headers' not in safe['payload']
    report = inspect_delivery(req, quote, context())
    assert report['request_projection']['authority'] == 'PREPARED_MESSAGES_SCHEMA_TOOLS'
    with pytest.raises(SemanticDeliveryError):
        require_delivery(report)


@pytest.mark.parametrize('location', ['messages', 'schema', 'tools'])
def test_final_content_locations_are_request_pointers(location):
    req = request({})
    wire = {'messages': []}
    if location == 'messages':
        wire['messages'] = [{'role': 'user', 'content': json.dumps({'support': [SOURCE]})}]
    elif location == 'schema':
        wire['response_format'] = {'json_schema': {'schema': {'$comment': json.dumps(SOURCE)}}}
    else:
        wire['tools'] = [{'function': {'parameters': {'support': SOURCE}}}]
    quote = SimpleNamespace(prepared=SimpleNamespace(body=json.dumps(wire).encode()),
                            request_digest=canonical_digest(asdict(req)), _prepared_digest='b' * 64)
    report = inspect_delivery(req, quote, context())
    assert evidence_row(report)['classification'] == 'FULL_ORIGINAL'
    expected = {'messages': '/messages/', 'schema': '/output_schema/', 'tools': '/tools/'}[location]
    assert any(row['pointer'].startswith(expected) for row in evidence_row(report)['locations'])


def budget_with_monitor(tmp_path):
    provider = CountingProvider()
    journal = ProviderJournal(RunRecorder(tmp_path, run_id='run-1', input_digest='a' * 64))
    budget = BudgetedProvider(provider, len, max_tokens=100000, max_seconds=60, max_calls=4,
                              on_update=journal.update)
    journal.attach(budget)
    return provider, budget, journal, install_monitor(budget, journal)


def test_required_gap_is_rejected_before_the_unique_budget_or_dispatch(tmp_path):
    provider, budget, journal, monitor = budget_with_monitor(tmp_path)
    final = payload()
    del final['read_only_source_support']
    req = request(final)
    quote = quote_request(budget, req, len)
    with monitor.scope(context()), pytest.raises(SemanticDeliveryError):
        complete_quoted(budget, req, quote)
    assert provider.requests == [] and budget.calls == [] and budget.reserved_tokens == 0
    stage = json.loads(next((tmp_path / 'journal/stages/semantic_delivery').rglob('*.json')).read_text())
    assert stage['call_refs'] == []


def test_monitor_keeps_the_same_quote_and_actual_sealed_call_fact(tmp_path):
    provider, budget, journal, monitor = budget_with_monitor(tmp_path)
    req = request(payload())
    quote = quote_request(budget, req, len)
    with monitor.scope(context()):
        complete_quoted(budget, req, quote)
    assert provider.quotes == 1 and len(provider.requests) == len(budget.calls) == 1
    assert provider.requests[0] == budget.calls[0]['request'] == asdict(req)
    assert budget.calls[0]['request_digest'] == quote.request_digest
    assert budget.reserved_tokens == quote.total_tokens
    stage = json.loads(next((tmp_path / 'journal/stages/semantic_delivery').rglob('*.json')).read_text())
    ref = stage['call_refs'][0]
    assert ref['absolute_call_index'] == 0
    assert ref['ledger_segment_path'].startswith('journal/ledger/segment-')
    assert 'model_calls' not in ref['ledger_segment_path']
    assert hashlib.sha256((tmp_path / ref['ledger_segment_path']).read_bytes()).hexdigest() == ref['ledger_segment_digest']


def test_real_repair_entry_stops_a_final_prepared_required_source_gap_without_retry_or_fake_call(tmp_path, monkeypatch):
    build_candidates = front_runner.build_candidate_set
    def selected_extension(*args, **kwargs):
        # This existing producer rule makes finally selected extension source
        # necessary; the semantic gate does not invent an A1-wide requirement.
        return replace(build_candidates(*args, **kwargs), extension_used=True)
    monkeypatch.setattr(front_runner, 'build_candidate_set', selected_extension)
    dispatched = []
    class FinalProjectionProvider(public_demo.SyntheticExampleProvider):
        def prepare_request(self, req):
            messages = json.loads(json.dumps(req.messages))
            if req.prompt_version.startswith('changeset.'):
                def remove_final_excerpts(value):
                    if isinstance(value, dict):
                        if value.get('kind') == 'SOURCE_SPAN' or str(value.get('id', '')).startswith('SRC-'):
                            value.pop('excerpt', None)
                        for child in value.values():
                            remove_final_excerpts(child)
                    elif isinstance(value, list):
                        for child in value:
                            remove_final_excerpts(child)
                for message in messages:
                    try:
                        body = json.loads(message['content'])
                    except (ValueError, TypeError):
                        continue
                    remove_final_excerpts(body)
                    message['content'] = json.dumps(body)
            body = json.dumps({'messages': messages}).encode()
            return SimpleNamespace(body=body, metadata={}, overhead='', output_schema=None, wire_schema=None,
                                   audit_record=lambda: {'body_sha256': hashlib.sha256(body).hexdigest()})
        def complete_prepared_json(self, req, prepared):
            dispatched.append(req.prompt_version)
            messages = tuple(json.loads(prepared.body)['messages'])
            return super().complete_json(replace(req, messages=messages))
    def provider(settings, case, runner):
        return FinalProjectionProvider(case, runner), public_demo.count_tokens
    monkeypatch.setattr(public_demo, '_provider', provider)
    result = public_demo.run_demo('reload-state', tmp_path / 'run')
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    repair = tmp_path / 'run/repair'
    ledger = json.loads((repair / 'model_calls.json').read_text())
    trace = json.loads((repair / 'repair.trace.json').read_text())
    front = json.loads((repair / 'rounds/0/front.trace.json').read_text())
    assert len(dispatched) == len(ledger) == len(front['model_calls']) == 1
    assert dispatched[0].startswith('diagnosis.')
    assert trace['budget']['reserved_tokens'] == sum(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] for c in ledger)
    assert any('SEMANTIC_DELIVERY_REQUIRED_CONTENT_MISSING' in error for error in result['errors'])
    assert not any('INFRASTRUCTURE_ERROR' in error for error in result['errors'])
    receipts = [json.loads(p.read_text()) for p in (repair / 'journal/stages/semantic_delivery').rglob('*.json')]
    refused = next(r for r in receipts if r['status'] == 'REQUIRED_CONTENT_GAP')
    assert refused['call_refs'] == []
    selected = json.loads(next((repair / 'journal/stages/selection').rglob('*.json')).read_text())
    assert any('/semantic_delivery/' in ref['relative_path'] for ref in selected['input_refs'])
    assert any('/front/' in ref['relative_path'] for ref in selected['input_refs'])


def test_real_two_consumer_retry_binds_selected_current_bytes_without_claiming_old_excerpt_delivered(tmp_path, monkeypatch):
    build_candidates = front_runner.build_candidate_set
    monkeypatch.setattr(front_runner, 'build_candidate_set',
        lambda *args, **kwargs: replace(build_candidates(*args, **kwargs), extension_used=True))
    result = public_demo.run_demo('two-consumers', tmp_path / 'run')
    assert result['status'] == 'PUBLIC_REVIEW_COMPLETE' and result['rounds'] == 2, result
    repair = tmp_path / 'run/repair'
    reports = []
    for path in (repair / 'journal/stages/semantic_delivery').rglob('*.json'):
        stage = json.loads(path.read_text())
        report = json.loads((repair / stage['output_refs'][0]['relative_path']).read_text())
        if report['context']['consumer'].startswith('EXECUTION_ROUND_'):
            reports.append(report)
    assert len(reports) == 2
    snapshots = set()
    saw_historical_gap = False
    for report in reports:
        assert not report['issues']
        current = [row for row in report['subjects'] if row['kind'] == 'CURRENT_TARGET_SOURCE']
        assert current and all(row['classification'] == 'FULL_CURRENT_TARGET_BYTES' for row in current)
        snapshots.update(row['original']['snapshot_id'] for row in current)
        assert not any(row['required_content_gate'] for row in report['subjects'] if row['kind'] == 'OBLIGATION')
        saw_historical_gap |= any(row['kind'] == 'EVIDENCE' and row.get('historical_required_basis')
            and row['classification'] == 'IDENTITY_ONLY' for row in report['subjects'])
    assert len(snapshots) == 2 and saw_historical_gap


@pytest.mark.parametrize('case', ['config-binding', 'reload-state', 'two-consumers'])
def test_host_synthetic_records_actual_final_delivery_and_bounded_results(tmp_path, case):
    result = public_demo.run_demo(case, tmp_path / 'run')
    assert result['status'] == 'PUBLIC_REVIEW_COMPLETE', result
    repair = tmp_path / 'run/repair'
    stages = [json.loads(path.read_text()) for path in (repair / 'journal/stages').rglob('*.json')]
    deliveries = [stage for stage in stages if stage['stage'] == 'semantic_delivery']
    assert len(deliveries) == result['model_calls']
    assert any(stage['stage'] == 'semantic_outcome' for stage in stages)
    selection = next(stage for stage in stages if stage['stage'] == 'selection')
    pending, reached, calls = [selection], set(), []
    while pending:
        stage = pending.pop()
        if stage['relative_path'] in reached:
            continue
        reached.add(stage['relative_path'])
        calls.extend(stage['call_refs'])
        for ref in stage['input_refs']:
            if ref['kind'] == 'STAGE_RECEIPT':
                pending.append(json.loads((repair / ref['relative_path']).read_text()))
    assert any('/semantic_outcome/' in path for path in reached)
    assert any('/semantic_delivery/' in path for path in reached)
    assert {ref['absolute_call_index'] for ref in calls} == set(range(result['model_calls']))
    for stage in deliveries:
        report = json.loads((repair / stage['output_refs'][0]['relative_path']).read_text())
        assert report['adoption'] == 'UNVERIFIED'
        assert not report['issues']
        assert len(report['call_refs']) == 1
        assert report['call_refs'] == stage['call_refs']
