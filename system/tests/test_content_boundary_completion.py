"""Selected content must survive the final request, including exact shared copies."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.contracts.delivery import source_span_matches, reread_source_locator
from contractedge.contracts.evidence import static_search_content_digest
from contractedge.contracts.validation import _load_schema
from contractedge.diagnosis.evidence import build_evidence_catalog, same_evidence_content
from contractedge.model.admission import quote_request
from contractedge.model.provider import ModelRequest
from contractedge.orchestration.feedback import make_feedback, validate_feedback, feedback_prompt_view, historical_trial_prompt_view
from contractedge.orchestration.semantic_delivery import delivery_context, inspect_delivery, require_delivery, SemanticDeliveryError
from contractedge.retrieval.candidates import CandidateSet
from contractedge.source.parsers import parse_source_units


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def inspect(body, **kwargs):
    frame = delivery_context({'requirement_text': 'Preserve selected input', 'input_digest': 'a' * 64},
        consumer='CONTENT_BOUNDARY', snapshot_id='1' * 64, **kwargs)
    request = ModelRequest(model='offline', prompt_version='offline', max_output_tokens=8,
        messages=({'role': 'user', 'content': json.dumps({'requirement': 'Preserve selected input', **body})},))
    return inspect_delivery(request, quote_request(None, request, len), frame)


def valid_feedback():
    observation = dict(id='OBS1', kind='PUBLIC_OBSERVATION', snapshot_id='1' * 64,
        locator={'path': 'public.json'}, excerpt='Complete public observation', origin='Public output')
    observation['content_digest'] = digest(observation['excerpt'])
    value = make_feedback(case_id='SYNTHETIC-A1', run_id='run', input_digest='a' * 64,
        round_id=0, from_snapshot='0' * 64, to_snapshot='1' * 64, patch_digest='c' * 64,
        execution_status='EXECUTION_PASS', observations=[observation], plan={},
        review=dict(decision='UNKNOWN', checks=[], failure_kind='UNKNOWN', rationale='Unverified'))
    return validate_feedback(value, case_id='SYNTHETIC-A1', input_digest='a' * 64, to_snapshot='1' * 64)


def test_adopted_valid_feedback_cannot_disappear_while_selected_observation_survives():
    feedback = valid_feedback()
    observations = feedback['public_observations']
    report = inspect({'public_observations': observations}, feedback=feedback, feedback_required=True,
                     evidence=observations, mandatory_refs=observations)
    with pytest.raises(SemanticDeliveryError):
        require_delivery(report)


def test_unadopted_feedback_is_not_automatically_forced_into_every_stage():
    require_delivery(inspect({}, feedback=valid_feedback()))


@pytest.mark.parametrize('mutate', [None, 'omit', 'wrong_snapshot', 'wrong_ref'])
def test_feedback_exact_shared_observation_must_resolve_in_same_request(mutate):
    feedback = valid_feedback()
    observations = json.loads(json.dumps(feedback['public_observations']))
    view = feedback_prompt_view(feedback, supplied_observations=observations)
    body = {'public_observations': observations, 'execution_feedback': view}
    if mutate == 'omit':
        body['public_observations'] = []
    elif mutate == 'wrong_snapshot':
        observations[0]['snapshot_id'] = '2' * 64
    elif mutate == 'wrong_ref':
        view['public_observation_refs'][0]['public_observation_record_ref'] = '/elsewhere/0'
    report = inspect(body, feedback=feedback, feedback_required=True)
    if mutate is None:
        require_delivery(report)
    else:
        with pytest.raises(SemanticDeliveryError):
            require_delivery(report)


def test_feedback_shared_plan_field_is_exact_and_same_request():
    feedback = valid_feedback()
    field = {'consumer_id': 'fixture', 'supported_operations': ['MODIFY']}
    feedback['public_execution_capabilities'] = field
    view = feedback_prompt_view(feedback)
    view.pop('public_execution_capabilities')
    view['public_execution_capabilities_record_ref'] = '/accepted_plan/public_execution_capabilities'
    body = {'execution_feedback': view, 'accepted_plan': {'public_execution_capabilities': field}}
    require_delivery(inspect(body, feedback=feedback, feedback_required=True))
    body['accepted_plan']['public_execution_capabilities'] = {'consumer_id': 'other'}
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(body, feedback=feedback, feedback_required=True))


@pytest.mark.parametrize('mode', ['complete', 'missing_content', 'wrong_metadata'])
def test_declared_artifact_requires_whole_content_and_identity(mode):
    artifact = dict(id='INPUT1', path='input.yaml', media_type='text/plain',
                    content='enabled: true\n', content_digest=digest('enabled: true\n'))
    supplied = dict(artifact)
    if mode == 'missing_content':
        supplied.pop('content')
    elif mode == 'wrong_metadata':
        supplied['path'] = 'other.yaml'
    report = inspect({'declared_artifacts': [supplied]}, declared_artifacts=[artifact])
    if mode == 'complete':
        require_delivery(report)
    else:
        with pytest.raises(SemanticDeliveryError):
            require_delivery(report)


def test_historical_container_not_replaced_by_same_local_current_id():
    feedback = valid_feedback()
    old = dict(feedback, run_id='old-run', to_snapshot='2' * 64,
               public_observations=[dict(feedback['public_observations'][0], snapshot_id='2' * 64)])
    trial = dict(snapshot_id='2' * 64, feedback=old, model_response='failed edits')
    current = {'execution_feedback': feedback}
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(current, historical_contexts=[trial]))
    current['previous_attempt'] = {**trial, 'feedback': historical_trial_prompt_view(old)}
    require_delivery(inspect(current, historical_contexts=[trial]))


def test_different_trials_may_reuse_local_observation_ids_at_same_source_snapshot():
    feedback = valid_feedback()
    current = feedback['public_observations'][0]
    historical = dict(current, excerpt='Earlier execution at the same source snapshot')
    historical['content_digest'] = digest(historical['excerpt'])
    trial = {'trial_id': 'prior-trial', 'feedback': dict(feedback, run_id='older-run',
                                                       public_observations=[historical])}
    body = {'public_observations': [current], 'previous_attempt': trial}
    require_delivery(inspect(body, evidence=[current], mandatory_refs=[current], historical_contexts=[trial]))
    body['public_observations'] = []
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(body, evidence=[current], mandatory_refs=[current], historical_contexts=[trial]))


def test_structured_absence_uses_its_structured_digest_not_source_excerpt_hash():
    locator = dict(scope='snapshot_file_inventory', query_kind='EXACT_PATH', query='missing.go',
        complete=True, truncated=False, match_count=0, matched_paths=[], inspected_file_count=3)
    record = dict(id='ABSENCE1', snapshot_id='1' * 64, kind='STATIC_SEARCH', locator=locator,
                  excerpt='', origin='Complete inventory')
    record['content_digest'] = static_search_content_digest(record['snapshot_id'], locator, '')
    require_delivery(inspect({'evidence_catalog': [record]}, evidence=[record], mandatory_refs=[record]))
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect({'evidence_catalog': []}, evidence=[record], mandatory_refs=[record]))


def test_truncated_function_keeps_state_and_exact_character_range_in_catalog():
    text = 'def function():\n' + ''.join(f'    value_{i} = {i}\n' for i in range(120))
    unit = parse_source_units('worker.py', text, '1' * 64, max_chars=50)[0]
    candidates = CandidateSet((unit,), (unit['id'],), (), {}, (), 0, 200000, 12)
    record = build_evidence_catalog(SimpleNamespace(declared_artifacts=(), public_evidence=()),
                                    candidates, '1' * 64)[0]
    assert unit['truncated'] is True
    assert record['locator']['truncated'] is True
    start, end = record['locator']['start_char'], record['locator']['end_char']
    assert text[start:end] == record['excerpt']
    assert record['locator']['end_line'] == len(record['excerpt'].splitlines())
    assert record['locator']['original_end_line'] > record['locator']['end_line']
    assert source_span_matches(record, text)
    from jsonschema import Draft202012Validator
    schema = _load_schema('ce.diagnosis_packet.v1_1')
    # The locator is already open in v1.1; no production schema extension.
    definition = next(v for v in schema['$defs'].values()
                      if isinstance(v, dict) and 'excerpt' in v.get('required', []))
    Draft202012Validator({'$defs': schema['$defs'], **definition}).validate(record)


def test_exact_range_and_truncated_metadata_cannot_drift_or_be_deduplicated():
    text = 'abcdef\nabcdef\n'
    record = dict(id='SRC1', kind='SOURCE_SPAN', snapshot_id='1' * 64,
        locator=dict(path='x.py', start_line=2, end_line=2, column_start=1,
                     start_char=7, end_char=10, truncated=True),
        excerpt='abc', content_digest=digest('abc'), origin='Selected prefix')
    assert source_span_matches(record, text)
    changed = json.loads(json.dumps(record))
    changed['locator']['start_char'] = 0
    changed['locator']['end_char'] = 3
    assert not source_span_matches(changed, text)
    assert not same_evidence_content(record, changed)
    changed = json.loads(json.dumps(record))
    changed['locator'].pop('truncated')
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect({'support': [changed]}, evidence=[record], mandatory_refs=[record]))


def test_historical_prefix_reread_has_current_exact_range_without_claiming_full_declaration():
    prior = dict(id='S1', kind='SOURCE_SPAN', snapshot_id='old', excerpt='prefix',
        content_digest=digest('prefix'), locator=dict(path='x.py', start_line=1, end_line=80,
            truncated=True, original_end_line=122))
    current = '\n\nprefix plus current body\n'
    locator = reread_source_locator(prior, current)
    assert locator['truncated'] is True
    assert locator['start_char'] == 2 and locator['end_char'] == 8
    assert locator['start_line'] == locator['end_line'] == 3
    assert 'original_end_line' not in locator
    assert source_span_matches({**prior, 'locator': locator}, current)
    assert prior['locator']['original_end_line'] == 122
