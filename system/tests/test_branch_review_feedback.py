"""Branch intent and incomplete behavior verification need distinct feedback."""
from copy import deepcopy

import pytest

from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.validation import validate_diagnosis, parse_method_input
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan


def fixture():
    packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
    plan=valid_plan();plan['status']='READY_PARTIAL'
    plan['outcome_coverage'][0]['unresolved']=['O1']
    plan['open_obligations']=[dict(id='OB1',outcome_id='O1',obligation_refs=['A1'],
        statement='Other runtime modes remain unverified.',reason='No public behavior observations cover these modes.',
        verification_kind='UNKNOWN')]
    frontier=[dict(source_evidence_id='EV2',path='templates/serviceaccount.yaml',locator=dict(start_line=1,end_line=8))]
    plan['production_path_review']=[dict(source_evidence_id='EV2',disposition='MODIFY',
        rationale='C1 changes this concrete source branch; runtime coverage remains limited.',
        change_refs=['C1'],open_obligation_refs=['OB1'])]
    return packet,plan,frontier


def test_correctly_targeted_edit_with_misplaced_open_ref_gets_specific_actionable_error():
    packet,plan,frontier=fixture();before=deepcopy(plan)
    result=validate_semantic_changeset(plan,packet,None,frontier=frontier)
    codes={e.code for e in result.errors}
    assert 'PRODUCTION_BRANCH_MODIFY_OPEN_REFS' in codes
    assert 'PRODUCTION_BRANCH_CHANGE_INVALID' not in codes
    issue=next(e for e in result.errors if e.code=='PRODUCTION_BRANCH_MODIFY_OPEN_REFS')
    assert issue.path=='production_path_review[0].open_obligation_refs'
    assert 'open_obligations' in issue.message and 'unverified' in issue.message
    assert plan==before


def test_fixing_branch_reference_does_not_discard_unverified_behavior():
    packet,plan,frontier=fixture()
    plan['production_path_review'][0]['open_obligation_refs']=[]
    result=validate_semantic_changeset(plan,packet,None,frontier=frontier)
    assert result.valid,result.errors
    assert plan['open_obligations'][0]['id']=='OB1'
    assert plan['open_obligations'][0]['verification_kind']=='UNKNOWN'


@pytest.mark.parametrize('coverage',['same_file_other_branch','unknown_change'])
def test_real_change_coverage_failure_remains_rejected_and_both_errors_reported(coverage):
    packet,plan,frontier=fixture()
    if coverage=='same_file_other_branch':frontier[0]['locator']=dict(start_line=100,end_line=110)
    else:plan['production_path_review'][0]['change_refs']=['missing']
    result=validate_semantic_changeset(plan,packet,None,frontier=frontier)
    codes={e.code for e in result.errors}
    assert {'PRODUCTION_BRANCH_CHANGE_INVALID','PRODUCTION_BRANCH_MODIFY_OPEN_REFS'}<=codes
