from scripts.assess_reliability_delivery_v2 import assess
from tests.test_reliability_delivery_gate import panel


def test_equal_primary_outcomes_do_not_meet_declared_delivery_improvement():
    reg,rows=panel();result=assess(reg,rows)
    assert result['behavior_gates_passed']
    assert not result['delivery_effect_gates_passed']
    assert result['status']=='NOT_ACCEPTED'


def test_recovery_improvement_alone_does_not_substitute_for_primary_effect():
    reg,rows=panel()
    next(r for r in rows if r['group']=='recovery' and r['variant']=='baseline')['status']='SEMANTIC_FAIL'
    result=assess(reg,rows)
    assert result['observed_improvement']
    assert not result['paired_primary_improvement'] and result['status']=='NOT_ACCEPTED'


def test_fixed_panel_primary_improvement_and_all_behavior_gates_can_pass():
    reg,rows=panel()
    next(r for r in rows if r['group']=='A' and r['variant']=='baseline')['status']='SEMANTIC_FAIL'
    result=assess(reg,rows)
    assert result['paired_primary_improvement'] and result['B_not_below_baseline']
    assert result['status']=='ACCEPTED'
    # Primary gains cannot override a migration regression or missing attempt.
    next(r for r in rows if r['group']=='B' and r['variant']=='candidate')['status']='SEMANTIC_FAIL'
    result=assess(reg,rows)
    assert not result['B_not_below_baseline'] and result['status']=='NOT_ACCEPTED'
    assert assess(reg,rows[:-1])['status']=='PENDING'
