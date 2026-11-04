"""Content must survive semantic handoffs; citations do not select context."""
from contractedge.model.budget import BudgetExceeded
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.changeset.prompt import _compact_planning_evidence, build_changeset_request
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_diagnosis
from contractedge.diagnosis.evidence import bind_evidence_ids, build_evidence_catalog
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan


def exact(record, **updates):
    record = copy.deepcopy(record)
    record.update(updates)
    record['content_digest'] = hashlib.sha256(record['excerpt'].encode()).hexdigest()
    return record


def packet():
    raw = valid_diagnosis()
    raw['evidence_refs'] = [exact(record) for record in raw['evidence_refs']]
    preserved = exact(raw['evidence_refs'][0], id='OBS-PRESERVED', excerpt='Disabled rendering retains the default name.')
    uncited = exact(raw['evidence_refs'][0], id='OBS-UNUSED', excerpt='The namespace remains selected by the public input.')
    raw['evidence_refs'].extend([preserved, uncited])
    outcome = {**raw['requirement_spec']['outcomes'][0], 'id': 'O2',
               'statement': 'Retain the disabled default name.', 'condition': 'disabled'}
    raw['requirement_spec']['outcomes'].append(outcome)
    raw['requirement_horizon'].append('O2')
    raw['obligation_atoms'].append({**raw['obligation_atoms'][0], 'id': 'A2', 'outcome_id': 'O2',
                                    'statement': outcome['statement']})
    raw['expected_relations'].append({**raw['expected_relations'][0], 'id': 'E2',
        'condition': 'disabled', 'requirement_refs': ['O2'], 'obligation_refs': ['A2']})
    raw['outcome_coverage'].append(dict(outcome_id='O2', repair_supported=[], preserve_required=['O2'],
        preserve_current_satisfaction=[dict(outcome_id='O2', status='OBSERVED', evidence_refs=[preserved['id']])],
        unresolved=[]))
    return validate_diagnosis(raw, parse_method_input(valid_method_input()), None)


def context(diagnosis, *, compact=True, support=None):
    return SimpleNamespace(run_id=diagnosis.run_id, case_id='RW-DEMO-001',
        snapshot_id=diagnosis.snapshot_id, input_digest=diagnosis.input_digest,
        model_config={'model': 'fixture'}, budget={'max_output_tokens': 1000},
        retrieval_config={'compact_planning_context': compact, 'necessary_source_support': support or []})


@pytest.mark.parametrize('compact', [False, True])
@pytest.mark.parametrize('revision', [False, True])
def test_planning_preserves_preservation_only_and_uncited_observation_bodies(compact, revision):
    diagnosis = packet()
    before = copy.deepcopy(diagnosis.raw)
    source = next(row for row in before['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    previous = dict(run_id=diagnosis.run_id, snapshot_id=diagnosis.snapshot_id,
        input_digest=diagnosis.input_digest, diagnosis_digest=canonical_digest(diagnosis.raw),
        state='REJECTED_PLAN_DRAFT', response_content='{"changes": [], "rejected": true}') if revision else None
    request = build_changeset_request(diagnosis, context(diagnosis, compact=compact, support=[source]),
        [{'code': 'MISSING_CHANGES'}] if revision else None, previous_attempt=previous)
    body = json.loads(request.messages[1]['content'])
    delivered = {row['id']: row for row in body['diagnosis_packet']['evidence_refs']}
    for record in before['evidence_refs']:
        if record['kind'] == 'PUBLIC_OBSERVATION':
            assert delivered[record['id']]['excerpt'] == record['excerpt']
    assert body['diagnosis_digest'] == canonical_digest(before)
    assert diagnosis.raw == before
    if revision:
        assert request.messages[2]['content'] == previous['response_content']
        assert json.loads(request.messages[3]['content'])['binding']['diagnosis_digest'] == canonical_digest(before)


@pytest.mark.parametrize('change', ['snapshot_id', 'locator', 'excerpt', 'content_digest', 'id'])
def test_source_excerpt_cannot_be_replaced_by_a_same_id_or_text_only_match(change):
    raw = packet().raw
    source = next(row for row in raw['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    replacement = copy.deepcopy(source)
    if change == 'locator':
        replacement['locator']['start_line'] += 1
    elif change == 'excerpt':
        replacement = exact(replacement, excerpt=source['excerpt'] + ' changed')
    else:
        replacement[change] = 'other'
    projected = _compact_planning_evidence(raw, [replacement])
    assert next(row for row in projected['evidence_refs'] if row['id'] == source['id'])['excerpt'] == source['excerpt']


def test_exact_source_copy_is_delivered_once_without_changing_diagnosis_identity():
    diagnosis = packet()
    source = next(row for row in diagnosis.raw['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    request = build_changeset_request(diagnosis, context(diagnosis, support=[dict(source, delivery_required=True)]))
    body = json.loads(request.messages[1]['content'])
    projected = next(row for row in body['diagnosis_packet']['evidence_refs'] if row['id'] == source['id'])
    assert 'excerpt' not in projected
    assert body['read_only_source_support'][0]['excerpt'] == source['excerpt']
    assert body['read_only_source_support'][0]['locator'] == source['locator']
    assert body['diagnosis_digest'] == canonical_digest(diagnosis.raw)


def test_legacy_compact_switch_keeps_the_same_semantics_and_validation_schema():
    diagnosis = packet()
    source = next(row for row in diagnosis.raw['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    requests = [build_changeset_request(diagnosis, context(diagnosis, compact=compact, support=[source]))
                for compact in (False, True)]
    assert requests[0].output_schema == requests[1].output_schema
    for request in requests:
        body = json.loads(request.messages[1]['content'])
        resolved = copy.deepcopy(body['diagnosis_packet'])
        support = {row['id']: row for row in body['read_only_source_support']}
        for row in resolved['evidence_refs']:
            if row['kind'] == 'SOURCE_SPAN' and 'excerpt' not in row:
                row['excerpt'] = support[row['id']]['excerpt']
        assert resolved == diagnosis.raw
        assert body['diagnosis_digest'] == canonical_digest(diagnosis.raw)
        assert body['existing_target_paths'] == [{'path': source['locator']['path'], 'unit_ids': ['U2']}]


@pytest.mark.parametrize('entry', ['catalog', 'binder'])
def test_current_evidence_id_conflict_is_rejected_before_binding(entry):
    first = packet().raw['evidence_refs'][0]
    conflicting = exact(first, excerpt='A different observed behavior under the same ID.')
    if entry == 'binder':
        with pytest.raises(ValueError, match='[Cc]onflict'):
            bind_evidence_ids([first['id']], [first, conflicting])
    else:
        method = SimpleNamespace(public_evidence=[conflicting], declared_artifacts=[])
        selected = SimpleNamespace(candidates=(), supplemental_evidence=[first])
        with pytest.raises(ValueError, match='[Cc]onflict'):
            build_evidence_catalog(method, selected, first['snapshot_id'])


def test_identical_catalog_records_do_not_lose_the_complete_body():
    first = packet().raw['evidence_refs'][0]
    method = SimpleNamespace(public_evidence=[first], declared_artifacts=[])
    selected = SimpleNamespace(candidates=(), supplemental_evidence=[copy.deepcopy(first)])
    assert build_evidence_catalog(method, selected, first['snapshot_id']) == [first]
    assert bind_evidence_ids([first['id']], [first, copy.deepcopy(first)]) == [first]


@pytest.mark.parametrize('change', ['locator', 'excerpt'])
def test_translation_rejects_conflicting_current_source_id_instead_of_overwriting(change):
    diagnosis = packet().raw
    source = next(row for row in diagnosis['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    replacement = copy.deepcopy(source)
    if change == 'locator':
        replacement['locator']['path'] = 'different.py'
    else:
        replacement = exact(replacement, excerpt='a different implementation')
    with pytest.raises(ValueError, match='[Cc]onflict'):
        translate_for_topomen(valid_plan(), 'contractedge.topomen_plan.v1',
            diagnosis=diagnosis, source_support=[replacement])


def test_translation_preserves_uncited_source_and_all_observations_without_expanding_edits():
    diagnosis = copy.deepcopy(packet().raw)
    source = next(row for row in diagnosis['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    uncited = exact(source, id='SRC-UNUSED', locator={'path': 'owner.go', 'start_line': 1, 'end_line': 1},
                    excerpt='type Owner struct { client *Client }')
    diagnosis['evidence_refs'].append(uncited)
    before = copy.deepcopy(diagnosis)
    changeset = valid_plan()
    plan = translate_for_topomen(changeset, 'contractedge.topomen_plan.v1', diagnosis=diagnosis,
        source_support=[dict(source, delivery_required=True), dict(uncited, delivery_required=True)])
    result = plan['coordination_context']
    assert {row['id'] for row in result['source_support']} == {source['id'], uncited['id']}
    assert all(row['delivery_required'] for row in result['source_support'])
    assert result['public_observations'] == [row for row in before['evidence_refs'] if row['kind'] == 'PUBLIC_OBSERVATION']
    assert result['diagnosis_snapshot'] == diagnosis['snapshot_id']
    assert {op['target']['path'] for op in plan['operations']} == {'templates/serviceaccount.yaml'}
    assert diagnosis == before


def test_translation_without_additional_support_keeps_all_diagnosed_source():
    diagnosis = copy.deepcopy(packet().raw)
    source = next(row for row in diagnosis['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    uncited = exact(source, id='SRC-UNUSED')
    diagnosis['evidence_refs'].append(uncited)
    plan = translate_for_topomen(valid_plan(), 'contractedge.topomen_plan.v1', diagnosis=diagnosis)
    assert plan['coordination_context']['source_support'] == [source, uncited]


@pytest.mark.parametrize('compact', [False, True])
def test_planning_conflicting_support_is_rejected_with_either_legacy_switch(compact):
    diagnosis = packet()
    source = next(row for row in diagnosis.raw['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    wrong = exact(source, locator={**source['locator'], 'path': 'another.go'})
    with pytest.raises(ValueError, match='Conflicting evidence'):
        build_changeset_request(diagnosis, context(diagnosis, compact=compact, support=[wrong]))


def test_delivery_required_can_be_strengthened_but_not_overwritten():
    from contractedge.diagnosis.evidence import merge_evidence_records
    source = next(row for row in packet().raw['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    records = [source, dict(source, delivery_required=True), dict(source, delivery_required=False)]
    before = copy.deepcopy(records)
    assert merge_evidence_records(records) == [dict(source, delivery_required=True)]
    assert records == before


def test_shared_matching_rule_keeps_legacy_observation_metadata_and_structured_absence():
    from contractedge.diagnosis.evidence import merge_evidence_records, same_evidence_content
    observation = dict(id='OBS', snapshot_id='snap-0', excerpt='build passed', scenario='a')
    assert same_evidence_content(observation, copy.deepcopy(observation))
    assert not same_evidence_content(observation, dict(observation, scenario='b'))
    with pytest.raises(ValueError, match='Conflicting evidence'):
        merge_evidence_records([observation, dict(observation, scenario='b')])
    absence = dict(id='SEARCH', kind='STATIC_SEARCH', snapshot_id='snap-0',
        search_scope=['a.go'], matches=[], search_complete=True)
    assert merge_evidence_records([absence, copy.deepcopy(absence)]) == [absence]


@pytest.mark.parametrize('field', ['id', 'snapshot_id', 'locator', 'content_digest', 'excerpt'])
def test_incomplete_source_identity_never_qualifies_as_exact_copy(field):
    from contractedge.diagnosis.evidence import same_evidence_content
    source = next(row for row in packet().raw['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    source.pop(field)
    assert not same_evidence_content(source, copy.deepcopy(source))


def test_full_current_support_and_uncited_observations_reach_actual_executor_request(tmp_path):
    from contractedge.execution.topomen import PlanRejected, build_execution_request
    from contractedge.source.snapshot import compute_snapshot
    (tmp_path / 'templates').mkdir()
    target_text = 'name: default\n'
    helper_text = 'package support\nfunc DefaultName() string { return "default" }\n'
    (tmp_path / 'templates/serviceaccount.yaml').write_text(target_text)
    (tmp_path / 'readonly.go').write_text(helper_text)
    snapshot = compute_snapshot(tmp_path)
    diagnosis = copy.deepcopy(packet().raw)
    diagnosis['snapshot_id'] = snapshot.snapshot_id
    for row in diagnosis['evidence_refs']:
        row['snapshot_id'] = snapshot.snapshot_id
    source = next(row for row in diagnosis['evidence_refs'] if row['kind'] == 'SOURCE_SPAN')
    source.update(excerpt=target_text, content_digest=hashlib.sha256(target_text.encode()).hexdigest(),
                  locator={'path': 'templates/serviceaccount.yaml', 'start_line': 1, 'end_line': 1})
    helper = exact(source, id='SRC-READONLY', excerpt=helper_text,
        locator={'path': 'readonly.go', 'start_line': 1, 'end_line': 2})
    # The final selected support includes source the diagnosis never cited.
    support = [dict(source, delivery_required=True), dict(helper, delivery_required=True)]
    changeset = valid_plan()
    changeset['snapshot_id'] = snapshot.snapshot_id
    changeset['changes'][0]['target'].update(snapshot_id=snapshot.snapshot_id,
        content_digest=snapshot.file_digests['templates/serviceaccount.yaml'], locator={'start_line': 1, 'end_line': 1})
    plan = translate_for_topomen(changeset, 'contractedge.topomen_plan.v1', diagnosis=diagnosis, source_support=support)
    config = dict(model='fixture', max_context_tokens=50000, execution_max_output_tokens=1000)
    requirement = diagnosis['requirement_spec']['raw_text']
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']}, len, config,
                                      requirement=requirement)
    body = json.loads(request.messages[1]['content'])
    assert any(row['id'] == helper['id'] and row['excerpt'] == helper_text for row in body['read_only_source_support'])
    assert body['current_target_files'][0]['content'] == target_text
    assert body['accepted_plan']['coordination_context']['public_observations'] == [
        row for row in diagnosis['evidence_refs'] if row['kind'] == 'PUBLIC_OBSERVATION']
    assert request.output_schema['properties']['edits']['items']['properties']['path']['enum'] == ['templates/serviceaccount.yaml']
    with pytest.raises(BudgetExceeded, match='read-only support budget'):
        build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']}, len,
                                {**config, 'max_execution_support_bytes': 1}, requirement=requirement)
    assert compute_snapshot(tmp_path).snapshot_id == snapshot.snapshot_id
