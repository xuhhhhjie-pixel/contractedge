"""Selected feedback content is frozen after raw checks, never by model citation alone."""
from copy import deepcopy
import hashlib

import pytest

from contractedge.contracts.models import ContractError
from contractedge.orchestration.feedback import make_feedback, select_feedback_observations


def observation(identifier, text=None, **extra):
    text = text or identifier + ' complete observation'
    return dict(id=identifier, snapshot_id='CURRENT', kind='PUBLIC_OBSERVATION',
        locator={'path': identifier + '.txt'}, excerpt=text,
        content_digest=hashlib.sha256(text.encode()).hexdigest(), origin='public fixture', **extra)


def feedback(records, *, checks=(), links=()):
    value = make_feedback(case_id='CASE', run_id='RUN', input_digest='1' * 64, round_id=1,
        from_snapshot='OLD', to_snapshot='CURRENT', patch_digest='0' * 64,
        execution_status='EXECUTION_FAILED', observations=records, plan={})
    # These represent the final independently enforced checks and reassessment
    # fields, which are already computed before this selection boundary.
    value.update(public_check_results=list(checks), coordination_links=list(links),
        retained_constraints=[dict(id='preserve:stable', statement='Keep the stable control')],
        helper_preconditions={'checks': [{'id': 'H1', 'status': 'CONFLICT'}]},
        observation_diagnostics=[{'kind': 'CANDIDATE_BUILD_FAILURE', 'message': 'compiler output'}])
    return value


def check(identifier, record, status='PASS'):
    return dict(id=identifier, status=status,
        evidence=[dict(observation_id=record['id'], quote=record['excerpt'])])


def test_unselected_large_raw_does_not_return_through_feedback_but_uncited_selected_survives():
    selected = observation('OBS-stable', scenario='stable')
    unselected = observation('OBS-raw', 'unrelated build output ' * 30000)
    value = feedback([selected, unselected])
    before = deepcopy(value)
    result = select_feedback_observations(value, [selected])
    assert result['public_observations'] == [selected]
    assert {key: item for key, item in result.items() if key != 'public_observations'} == {
        key: item for key, item in before.items() if key != 'public_observations'}
    assert value == before and len(value['public_observations'][1]['excerpt']) == 690000


def test_final_pass_preservation_failure_and_link_support_are_added_without_dropping_selected():
    selected, passed, preserved, failed, linked, unused = [observation(name) for name in
        ('OBS-selected', 'OBS-pass', 'OBS-preserve', 'OBS-fail', 'OBS-link', 'OBS-unused')]
    checks = [check('fixed:target', passed), check('preserve:stable', preserved), check('P1', failed, 'FAIL')]
    links = [dict(check_id='linked', observation_snapshot='CURRENT', observation_refs=[linked['id']])]
    value = feedback([selected, passed, preserved, failed, linked, unused], checks=checks, links=links)
    result = select_feedback_observations(value, [selected])
    assert result['public_observations'] == [selected, passed, preserved, failed, linked]
    assert result['public_check_results'] == checks
    assert result['coordination_links'] == links
    assert result['retained_constraints'] == value['retained_constraints']


@pytest.mark.parametrize('status', ['PASS', 'FAIL', 'UNKNOWN'])
def test_final_reference_without_current_raw_support_cannot_be_fabricated_or_downgraded(status):
    selected, missing = observation('OBS-selected'), observation('OBS-missing')
    value = feedback([selected], checks=[check('P1', missing, status)])
    before = deepcopy(value)
    with pytest.raises(ContractError, match='FEEDBACK_EVIDENCE_MISMATCH'):
        select_feedback_observations(value, [selected])
    assert value == before


@pytest.mark.parametrize('change', [
    {'snapshot_id': 'OLD'}, {'scenario': 'another-scenario'}, {'excerpt': 'changed'},
    {'content_digest': '0' * 64}, {'id': 'OBS-missing'}])
def test_selected_identity_or_content_cannot_be_replaced_from_raw(change):
    record = observation('OBS-selected', scenario='stable')
    value = feedback([record])
    with pytest.raises(ContractError, match='FEEDBACK_EVIDENCE_MISMATCH'):
        select_feedback_observations(value, [{**record, **change}])


def test_unselected_raw_id_conflict_cannot_be_hidden_by_selection():
    selected, unselected = observation('OBS-selected'), observation('OBS-raw')
    value = feedback([selected, unselected])
    value['public_observations'].append(observation('OBS-raw', 'conflicting body'))
    with pytest.raises(ContractError, match='FEEDBACK_EVIDENCE_MISMATCH'):
        select_feedback_observations(value, [selected])


def test_unselected_raw_invalid_digest_cannot_be_hidden_by_selection():
    selected, unselected = observation('OBS-selected'), observation('OBS-raw')
    value = feedback([selected, unselected])
    value['public_observations'][1]['content_digest'] = '0' * 64
    with pytest.raises(ContractError, match='FEEDBACK_EVIDENCE_MISMATCH'):
        select_feedback_observations(value, [selected])


def test_exact_selected_duplicates_share_one_body_and_selection_has_no_size_rule():
    selected = observation('OBS-selected', 'selected complete body ' * 30000)
    value = feedback([selected])
    result = select_feedback_observations(value, [selected, deepcopy(selected)])
    assert result['public_observations'] == [selected]
    assert len(result['public_observations'][0]['excerpt']) > 600000


@pytest.mark.parametrize('citations', [None, ['OBS-selected'], [{'quote': 'missing identity'}]])
def test_malformed_final_reference_is_rejected_without_inventing_an_identity(citations):
    selected = observation('OBS-selected')
    value = feedback([selected], checks=[dict(id='P1', status='PASS', evidence=citations)])
    with pytest.raises(ContractError, match='FEEDBACK_EVIDENCE_MISMATCH'):
        select_feedback_observations(value, [selected])
