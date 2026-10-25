"""A broad UNKNOWN must not discard its already-PASS necessary safety checks."""
import pytest
from contractedge.orchestration.candidate_guard import preservation_check_ids, compare_candidate_checks
from tests.test_candidate_regression_guard import run


def prior(kind='PRESERVE'):
    return dict(retained_constraints=[],pending_obligations=[dict(id='preserve:old',kind=kind,
        statement='Failed update performs no additional write',stable_obligation_keys=['OB-safety'],
        check_coverage='NECESSARY_ONLY',behavior_check_refs=['error-control'])])


@pytest.mark.parametrize('coverage',['NECESSARY_ONLY','ALL_OF'])
@pytest.mark.parametrize('trial_state,want',[('FAIL','REJECT_REGRESSION'),('MISSING','UNCOMPARABLE')])
def test_previous_unresolved_preservation_keeps_passed_necessary_check_protected(trial_state,want,coverage):
    plan=dict(preserve_constraints=[],obligation_lineage={})
    before=run({'error-control':'PASS'},'S1')
    trial=run({} if trial_state=='MISSING' else {'error-control':trial_state},'S2')
    previous=prior();previous['pending_obligations'][0]['check_coverage']=coverage
    result=compare_candidate_checks(before,trial,preservation_check_ids(plan,previous))
    assert result['decision']==want


def test_pending_target_is_not_mistaken_for_a_preservation_lock():
    before=run({'error-control':'PASS'},'S1');trial=run({'error-control':'FAIL'},'S2')
    assert compare_candidate_checks(before,trial,preservation_check_ids({},prior('POSTCONDITION')))['decision']=='PROMOTE'


def test_disputed_pending_preservation_does_not_lock_old_interpretation():
    plan=dict(obligation_lineage=dict(nodes={'OB-safety':dict(interpretation_status='DISPUTED')}))
    before=run({'error-control':'PASS'},'S1');trial=run({'error-control':'FAIL'},'S2')
    assert compare_candidate_checks(before,trial,preservation_check_ids(plan,prior()))['decision']=='PROMOTE'


def test_pending_preservation_with_no_passed_observation_creates_no_false_pass():
    before=run({'error-control':'UNKNOWN'},'S1');trial=run({'error-control':'FAIL'},'S2')
    previous=prior();assert compare_candidate_checks(before,trial,preservation_check_ids({},previous))['decision']=='PROMOTE'
    assert previous['retained_constraints']==[]
    assert before['observations'][0]['behavior_check']['status']=='UNKNOWN'


@pytest.mark.parametrize('coverage',['SUPPORTING',None,[],{}])
def test_supporting_or_unclassified_pending_check_does_not_lock_intended_behavior(coverage):
    before=run({'error-control':'PASS'},'S1');trial=run({'error-control':'FAIL'},'S2')
    previous=prior();previous['pending_obligations'][0]['check_coverage']=coverage
    assert compare_candidate_checks(before,trial,preservation_check_ids({},previous))['decision']=='PROMOTE'
