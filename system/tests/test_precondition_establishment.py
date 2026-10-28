"""A runnable check is not an implementation of the condition it checks."""
import hashlib
import json

import pytest

from contractedge.changeset.prompt import build_changeset_request
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.models import RunContext
from tests.test_plan_precondition_coverage import fixture, review


SOURCE = 'func Start() { go consume() }\n'


def inputs():
    diagnosis, plan = fixture()
    plan['postconditions'][0]['verification_kind'] = 'PUBLIC_BEHAVIOR'
    plan['precondition_review'] = [review()]
    record = dict(id='EV2', kind='SOURCE_SPAN', snapshot_id=diagnosis.snapshot_id,
        excerpt=SOURCE, content_digest=hashlib.sha256(SOURCE.encode()).hexdigest(),
        locator=dict(path='worker.go', start_line=1, end_line=1))
    prerequisites = [dict(source_evidence_ref='EV2', reason='Review before publication',
        establishment_required=True)]
    return diagnosis, plan, record, prerequisites


def establishment(mode='PLANNED_CHANGE', changes=None):
    return dict(mode=mode, source_quote=SOURCE.strip(), change_refs=['C1'] if changes is None else changes,
        mechanism='C1 adds an explicit completion boundary before publishing the replacement; launching work alone does not establish populated state.')


def check(plan, diagnosis, record, prerequisites):
    return validate_semantic_changeset(plan, diagnosis, None,
        prerequisites=prerequisites, source_support=[record])


def test_launch_plus_future_behavior_check_cannot_omit_establishment_account():
    diagnosis, plan, record, prerequisites = inputs()
    result = check(plan, diagnosis, record, prerequisites)
    assert not result.valid
    assert 'PRECONDITION_ESTABLISHMENT_MISSING' in {e.code for e in result.errors}


def test_new_establishment_requires_actual_planned_change():
    diagnosis, plan, record, prerequisites = inputs()
    plan['precondition_review'][0]['establishment'] = establishment(changes=['missing'])
    result = check(plan, diagnosis, record, prerequisites)
    assert not result.valid
    assert 'PRECONDITION_ESTABLISHMENT_INVALID' in {e.code for e in result.errors}


@pytest.mark.parametrize('alter', ['empty_changes', 'wrong_quote', 'stale_source', 'tampered_digest'])
def test_establishment_cannot_use_absent_or_unbound_support(alter):
    diagnosis, plan, record, prerequisites = inputs()
    plan['precondition_review'][0]['establishment'] = establishment()
    if alter == 'empty_changes': plan['precondition_review'][0]['establishment']['change_refs'] = []
    if alter == 'wrong_quote': plan['precondition_review'][0]['establishment']['source_quote'] = 'wait_until_ready()'
    if alter == 'stale_source': record['snapshot_id'] = 'old-snapshot'
    if alter == 'tampered_digest': record['excerpt'] = 'already_ready()'
    assert not check(plan, diagnosis, record, prerequisites).valid


def test_planned_establishment_is_preserved_without_claiming_verified_behavior():
    diagnosis, plan, record, prerequisites = inputs()
    plan['precondition_review'][0]['establishment'] = establishment()
    result = check(plan, diagnosis, record, prerequisites)
    assert result.valid, result.errors
    assert result.value['precondition_review'][0]['establishment']['mode'] == 'PLANNED_CHANGE'
    from contractedge.changeset.topomen import translate_for_topomen
    translated = translate_for_topomen(plan, 'contractedge.topomen_plan.v1')
    assert translated['precondition_review'] == plan['precondition_review']


def test_current_source_establishment_has_no_fake_edit():
    diagnosis, plan, record, prerequisites = inputs()
    record['excerpt'] = 'func Ready() { <-done }\n'
    record['content_digest'] = hashlib.sha256(record['excerpt'].encode()).hexdigest()
    plan['precondition_review'][0]['establishment'] = dict(mode='CURRENT_SOURCE',
        source_quote='<-done', change_refs=[], mechanism='Existing completion wait precedes consumption in the evidenced path.')
    result = check(plan, diagnosis, record, prerequisites)
    assert result.valid, result.errors


def test_mode_must_match_applicability_and_unresolved_is_not_silently_satisfied():
    diagnosis, plan, record, prerequisites = inputs()
    plan['precondition_review'][0]['establishment'] = establishment('UNRESOLVED', [])
    assert not check(plan, diagnosis, record, prerequisites).valid
    plan['precondition_review'][0] = review('NOT_APPLICABLE')
    plan['precondition_review'][0]['establishment'] = dict(mode='NOT_APPLICABLE',source_quote='',
        change_refs=[],mechanism='This helper is unrelated to the configuration edit.')
    result = check(plan, diagnosis, record, prerequisites)
    assert result.valid, result.errors


def test_real_unresolved_condition_retains_its_open_obligation():
    diagnosis, plan, record, prerequisites = inputs()
    plan['precondition_review'][0] = review('UNRESOLVED')
    plan['precondition_review'][0]['establishment'] = establishment('UNRESOLVED', [])
    plan['open_obligations'] = [dict(id='OPEN1',outcome_id='O1',obligation_refs=['A1'],
        statement='Consumption completion remains unknown',reason='No evidenced completion contract')]
    plan['outcome_coverage'][0]['unresolved'] = ['O1']; plan['status'] = 'READY_PARTIAL'
    result = check(plan, diagnosis, record, prerequisites)
    assert result.valid, result.errors


def test_legacy_prerequisites_do_not_silently_gain_new_resume_requirements():
    diagnosis, plan, record, prerequisites = inputs()
    prerequisites[0].pop('establishment_required')
    result = check(plan, diagnosis, record, prerequisites)
    assert result.valid, result.errors


def test_schema_and_correction_keep_same_establishment_requirement():
    diagnosis, plan, record, prerequisites = inputs()
    context = RunContext('r','c',0,diagnosis.snapshot_id,None,'input',retrieval_config={
        'source_prerequisites':prerequisites,'necessary_source_support':[record]})
    for feedback in [None,[dict(code='PRECONDITION_ESTABLISHMENT_MISSING',path='precondition_review[0]',message='Describe establishment')]]:
        request = build_changeset_request(diagnosis, context, feedback)
        item = request.output_schema['properties']['precondition_review']['items']
        assert 'establishment' in item['required']
        assert item['properties']['establishment']['properties']['change_refs']['items']['type'] == 'string'
        body=json.loads(request.messages[1]['content'])
        assert body['read_only_source_support'] == [record]
        assert 'completion' in request.messages[0]['content']
