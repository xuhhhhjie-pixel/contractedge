"""Correct the submitted graph from supplied evidence, without inventing edges."""
from copy import deepcopy
import hashlib
import json

import pytest

from contractedge.orchestration.runner import _append_diagnosis_retry_turn


def fixture():
    excerpt = 'func reconcilePolicy() { consumeSelection() }\n'
    source = dict(id='code-ref', kind='SOURCE_SPAN', snapshot_id='current',
                  content_digest=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt,
                  locator=dict(path='policy.go', start_line=20, end_line=20))
    request = dict(run_context=dict(snapshot_id='current'), evidence_catalog=[source])
    units = [dict(id='configuration', semantic_role_class='CONFIGURATION_INPUT', existence='PRESENT',
                  locator_refs=[], requirement_refs=['goal']),
             dict(id='implementation', semantic_role_class='PRODUCTION_CONSUMER', existence='PRESENT',
                  locator_refs=['code-ref'], requirement_refs=['goal']),
             dict(id='result', semantic_role_class='OBSERVED_STATE', existence='PRESENT',
                  locator_refs=['observation'], requirement_refs=['goal'])]
    draft = dict(units=units, expected_relations=[dict(id='bypass', source_unit_ids=['configuration'],
                 target_unit_ids=['result'], requirement_refs=['goal'])])
    return [dict(role='system', content='contract'), dict(role='user', content=json.dumps(request))], draft


def retry(messages, draft, feedback='PRODUCTION_PATH_INCOMPLETE at expected_relations'):
    return _append_diagnosis_retry_turn(messages, json.dumps(draft), feedback)


def inventory(turns):
    feedback = turns[-1]['content']
    assert 'Rejected graph inventory (not verified causality): ' in feedback
    return json.loads(feedback.split('Rejected graph inventory (not verified causality): ', 1)[1])


def test_existing_consumers_and_bypassing_endpoints_are_located_without_new_edges():
    messages, draft = fixture(); original = deepcopy((messages, draft))
    turns = retry(messages, draft); result = inventory(turns)
    assert result['source_anchored_consumers'] == [dict(unit_id='implementation',
            requirement_refs=['goal'], source_refs=['code-ref'])]
    assert result['configurations'][0]['unit_id'] == 'configuration'
    assert result['terminals'][0]['unit_id'] == 'result'
    assert result['relations'] == draft['expected_relations']
    assert result['current_source_spans'][0]['id'] == 'code-ref'
    assert 'mechanism text' in turns[-1]['content']
    assert 'Do not invent' in turns[-1]['content']
    assert turns[:-2] == tuple(messages)
    assert turns[-2] == dict(role='assistant', content=json.dumps(draft))
    assert (messages, draft) == original


@pytest.mark.parametrize('mutation', ['stale', 'digest', 'observation', 'duplicate', 'missing_snapshot', 'fake_model_source'])
def test_only_unambiguous_current_canonical_source_can_anchor_consumer(mutation):
    messages, draft = fixture(); request = json.loads(messages[1]['content']); source = request['evidence_catalog'][0]
    if mutation == 'stale': source['snapshot_id'] = 'old'
    if mutation == 'digest': source['excerpt'] += ' changed'
    if mutation == 'observation': source['kind'] = 'PUBLIC_OBSERVATION'
    if mutation == 'duplicate': request['evidence_catalog'].append(deepcopy(source))
    if mutation == 'missing_snapshot': request['run_context'] = {}
    if mutation == 'fake_model_source':
        draft['evidence_refs'] = [deepcopy(source)]; request['evidence_catalog'] = []
    messages[1]['content'] = json.dumps(request)
    result = inventory(retry(messages, draft))
    assert result['source_anchored_consumers'] == []
    assert result['unanchored_consumers'][0]['unit_id'] == 'implementation'


def test_latest_request_wins_and_old_snapshot_is_not_revived_by_retry_history():
    messages, draft = fixture()
    request = json.loads(messages[1]['content']); request['run_context']['snapshot_id'] = 'new'
    messages += [dict(role='assistant', content=json.dumps(draft)),
                 dict(role='user', content=json.dumps(request)), dict(role='user', content='earlier correction')]
    assert not inventory(retry(messages, draft))['source_anchored_consumers']


def test_duplicate_units_are_not_silently_selected():
    messages, draft = fixture(); draft['units'].append(deepcopy(draft['units'][1]))
    result = inventory(retry(messages, draft))
    assert not result['source_anchored_consumers']
    assert result['ambiguous_unit_ids'] == ['implementation']


@pytest.mark.parametrize('draft', [None, [], 'bad', {'units': None}, {'units': [None, 3], 'expected_relations': [None]},
                                    {'units': [{'id': [], 'locator_refs': None}]}])
def test_malformed_rejected_output_never_crashes_feedback(draft):
    messages, _ = fixture()
    turns = retry(messages, draft)
    assert len(turns) == len(messages) + 2
    assert len(turns[-1]['content'].encode()) <= 4200


def test_unicode_inventory_is_bounded_with_complete_records_and_omission_counts():
    messages, draft = fixture()
    draft['expected_relations'] *= 60
    draft['units'] += [dict(id='長' * 2000, semantic_role_class='CONFIGURATION_INPUT', existence='PRESENT',
                           requirement_refs=['goal'], locator_refs=[])]
    result = inventory(retry(messages, draft))
    added = retry(messages, draft)[-1]['content'].split('PRODUCTION_PATH_INCOMPLETE at expected_relations', 1)[1]
    assert len(added.encode()) <= 4096
    assert result['omitted']['relations'] >= 52
    assert result['omitted']['configurations'] == 1
    assert result['configurations'][0]['unit_id'] == 'configuration'
    assert all(row == draft['expected_relations'][0] for row in result['relations'])


def test_other_errors_do_not_change_and_no_new_source_is_read():
    messages, draft = fixture()
    turns = retry(messages, draft, 'STATUS_INCONSISTENT')
    assert turns[-1]['content'] == 'STATUS_INCONSISTENT'
    assert turns[-2]['content'] == json.dumps(draft)


def test_conventional_diagnosis_envelope_is_supported():
    messages, draft = fixture()
    assert inventory(retry(messages, {'diagnosis': draft}))['source_anchored_consumers']


@pytest.mark.parametrize('role', [[], {}, 42, None])
def test_malformed_semantic_role_never_breaks_error_reporting(role):
    messages, draft = fixture(); draft['units'][1]['semantic_role_class'] = role
    assert inventory(retry(messages, draft))['configurations']


def test_invalid_unicode_record_does_not_break_bounded_correction():
    messages, draft = fixture(); draft['units'][1]['id'] = '\ud800'
    assert inventory(retry(messages, draft))['configurations']


def test_actual_front_loop_serializes_grounded_inventory_in_existing_retry(tmp_path):
    from contractedge.contracts.validation import canonical_digest
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.runner import run_front_half
    from contractedge.source.snapshot import compute_snapshot
    from tests.test_contracts import valid_method_input
    from tests.test_orchestration import PipelineFixtureProvider

    source = tmp_path / 'templates/serviceaccount.yaml'; source.parent.mkdir()
    source.write_text('apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n')
    method = valid_method_input()
    method['requirement_text'] = 'When enabled, render the selected ServiceAccount.'
    method['public_observable_text'] = 'The rendered ServiceAccount name is default instead of selected.'
    method['scope_hints'] = [dict(kind='path', value='templates/serviceaccount.yaml',
                                raw_text='templates/serviceaccount.yaml', origin_ref='artifact_scope[0]')]
    method['initial_snapshot_id'] = compute_snapshot(tmp_path).snapshot_id
    method['public_evidence'] = [dict(id='PUB1', snapshot_id=method['initial_snapshot_id'],
        kind='PUBLIC_OBSERVATION', locator=dict(path='evidence/PUB1.json'),
        content_digest=hashlib.sha256(b'public observation').hexdigest(),
        excerpt=method['public_observable_text'], origin='fixture')]
    method.pop('input_digest', None); method['input_digest'] = canonical_digest(method)

    class Provider(PipelineFixtureProvider):
        calls = 0
        requests = []
        def complete_json(self, request):
            self.requests.append(request)
            if request.prompt_version == 'diagnosis.v1.1.24':
                self.calls += 1
                if self.calls == 2:
                    return ModelResponse(json.dumps(self.corrected), {'provider': 'fixture'})
                response = super().complete_json(request)
                body = json.loads(response.content)
                body['units'].append(dict(id='U3', role='rendered output', kind='state',
                    semantic_role_class='OBSERVED_STATE', existence='PRESENT', locator_refs=['PUB1'],
                    requirement_refs=['O1']))
                self.corrected = deepcopy(body)
                self.corrected['expected_relations'].append(dict(body['expected_relations'][0],
                    id='E2', source_unit_ids=['U2'], target_unit_ids=['U3']))
                body['expected_relations'][0]['target_unit_ids'] = ['U3']
                return ModelResponse(json.dumps(body), {'provider': 'fixture'})
            return super().complete_json(request)

    provider = Provider(); provider._snapshot_digests = {source.relative_to(tmp_path).as_posix(): hashlib.sha256(source.read_bytes()).hexdigest()}
    trace = run_front_half(method, tmp_path, provider, dict(run_id='graph-loop', model=dict(model='fixture'),
        top_n=12, max_context_tokens=16000, max_total_tokens=80000, max_format_retries=1),
        token_counter=lambda text: len(text.split()))
    assert trace.status == 'READY', trace.errors
    assert [call['stage'] for call in trace.model_calls] == ['DIAGNOSIS', 'DIAGNOSIS_FORMAT_RETRY', 'SEMANTIC_CHANGESET']
    result = inventory(provider.requests[1].messages)
    assert result['source_anchored_consumers'][0]['unit_id'] == 'U2'
    assert result['relations'][0]['target_unit_ids'] == ['U3']
    assert provider.calls == 2
