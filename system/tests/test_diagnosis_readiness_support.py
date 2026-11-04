"""Local causal support, planning readiness and behavioral proof are distinct."""
from copy import deepcopy

import pytest

from contractedge.contracts.models import ContractError
from contractedge.contracts.validation import parse_method_input, validate_diagnosis
from tests.test_contracts import valid_diagnosis, valid_method_input


def check(body):
    return validate_diagnosis(body, parse_method_input(valid_method_input()), None)


def with_terminal(*, existence='UNKNOWN', refs=(), edge_refs=()):
    body = valid_diagnosis()
    body['units'].append(dict(id='U3', role='rendered output', kind='resource',
        semantic_role_class='GENERATED_STATE', existence=existence,
        locator_refs=list(refs), requirement_refs=['O1']))
    body['expected_relations'].append(dict(body['expected_relations'][0], id='E2',
        source_unit_ids=['U2'], target_unit_ids=['U3'], support_refs=list(edge_refs)))
    return body


def blocked(body):
    body['status'] = 'EVIDENCE_GAP'
    body['unverified_outcomes'] = ['O1']
    body['evidence_gaps'] = [dict(id='G1', summary='Locate the output handoff implementation',
        locator_hint=dict(path='templates/output.yaml'))]
    return body


def test_local_supported_violation_survives_an_explicit_blocking_gap():
    body = blocked(with_terminal())
    before = deepcopy(body)
    packet = check(body)
    assert packet.status == 'EVIDENCE_GAP'
    assert packet.violations[0]['causal_status'] == 'SUPPORTED'
    assert packet.outcome_coverage[0]['repair_supported'] == ['V1']
    assert packet.evidence_gaps and packet.unverified_outcomes == ('O1',)
    assert body == before


def test_no_declared_runtime_terminal_does_not_force_a_local_template_into_a_deployment_graph():
    # READY is permission to plan a local repair, not a claim that rendering
    # or deployment already succeeded. A terminal is not a mandatory label.
    packet = check(valid_diagnosis())
    assert packet.status == 'READY'
    assert len(packet.units) == 2


@pytest.mark.parametrize('refs,edge_refs', [([], []), ([], ['EV2']), (['EV1'], ['EV2'])])
def test_unknown_terminal_cannot_become_proven_by_adding_an_expected_edge(refs, edge_refs):
    with pytest.raises(ContractError, match='PRODUCTION_PATH_INCOMPLETE'):
        check(with_terminal(refs=refs, edge_refs=edge_refs))


@pytest.mark.parametrize('edge_refs', [[], ['EV1']])
def test_connected_terminal_without_production_support_is_not_a_ready_path(edge_refs):
    with pytest.raises(ContractError, match='PRODUCTION_PATH_INCOMPLETE'):
        check(with_terminal(existence='PRESENT', refs=['EV1'], edge_refs=edge_refs))


def test_test_source_cannot_stand_in_for_production_handoff():
    body = with_terminal(existence='PRESENT', refs=['EV1'], edge_refs=['TEST'])
    body['evidence_refs'].append(dict(body['evidence_refs'][1], id='TEST',
        locator=dict(path='tests/render_test.go', start_line=1, end_line=8)))
    with pytest.raises(ContractError, match='PRODUCTION_PATH_INCOMPLETE'):
        check(body)


def test_new_source_support_can_make_the_claimed_path_ready_without_public_behavior_pass():
    body = blocked(with_terminal())
    assert check(body).status == 'EVIDENCE_GAP'
    body['units'][-1].update(existence='PRESENT', locator_refs=['EV2'])
    body['expected_relations'][-1]['support_refs'] = ['EV2']
    body.update(status='READY', evidence_gaps=[])
    # Future behavior remains unverified even though the static path is
    # sufficiently supported to attempt a plan. No PASS observation is added.
    packet = check(body)
    assert packet.status == 'READY'
    assert packet.unverified_outcomes == ('O1',)


def test_supported_absent_terminal_can_be_a_repair_target():
    body = with_terminal(existence='EXPECTED_MISSING', refs=['EV1'], edge_refs=['EV2'])
    body['evidence_refs'][0]['locator'].update(observed_state='ABSENT', target='ServiceAccount')
    assert check(body).status == 'READY'


@pytest.mark.parametrize('mutation,code', [
    ('foreign_source', 'REFERENCE_NOT_FOUND'),
    ('unanchored_consumer', 'PRODUCTION_CONSUMER_UNEVIDENCED'),
    ('observation_only', 'CAUSAL_CLAIM_UNSUPPORTED'),
    ('stale', 'SNAPSHOT_MISMATCH'),
])
def test_blocking_gap_does_not_waive_local_evidence_checks(mutation, code):
    body = blocked(with_terminal())
    if mutation == 'foreign_source':
        body['units'][1]['locator_refs'] = ['fabricated']
    elif mutation == 'unanchored_consumer':
        body['units'][1]['locator_refs'] = ['EV1']
    elif mutation == 'observation_only':
        body['violations'][0]['support_basis'] = 'OBSERVED_ONLY'
    else:
        body['evidence_refs'][1]['snapshot_id'] = 'old'
    with pytest.raises(ContractError, match=code):
        check(body)
