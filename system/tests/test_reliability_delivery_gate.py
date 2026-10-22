import pytest
from scripts.assess_reliability_delivery import assess


def panel():
    old=['RW-CAND-130','R3-KPT-4673','R4-PROVIDER-HELM-408','R4-ACK-CLOUDWATCH-2985','RW-CERTMANAGER-9116']
    critical=['R3-KOPF-726','RW-ALERTMANAGER-4705']
    reg=dict(A=[{'case_id':c} for c in old+critical],B=[{'case_id':'NEW'}],
             key_repeat_cases=critical,recovery_seed_paths={'R3-KPT-4673':'seed','RW-ALERTMANAGER-4705':'seed'})
    rows=[]
    for group in ['A','B']:
        for item in reg[group]:
            for v in ['baseline','candidate']:
                rows.append(dict(group=group,case_id=item['case_id'],repeat=0,variant=v,status='PASS'))
    for case in critical:
        for v in ['baseline','candidate']:rows.append(dict(group='repeat',case_id=case,repeat=1,variant=v,status='PASS'))
    for case in reg['recovery_seed_paths']:
        for repeat in [0,1]:
            for v in ['baseline','candidate']:rows.append(dict(group='recovery',case_id=case,repeat=repeat,variant=v,status='PASS'))
    return reg,rows


def test_improvements_elsewhere_cannot_offset_one_original_success_lost():
    reg,rows=panel()
    next(r for r in rows if r['case_id']=='RW-CAND-130' and r['variant']=='candidate')['status']='SEMANTIC_FAIL'
    for r in rows:
        if r['case_id'] in reg['key_repeat_cases'] and r['variant']=='baseline':r['status']='NO_CANDIDATE'
    result=assess(reg,rows)
    assert result['observed_improvement'] and result['status']=='NOT_ACCEPTED'
    assert result['regressions']==[{'group':'A','case_id':'RW-CAND-130'}]


def test_missing_attempt_and_duplicate_best_attempt_never_pass_delivery():
    reg,rows=panel()
    assert assess(reg,rows[:-1])['status']=='PENDING'
    with pytest.raises(ValueError,match='Duplicate'):assess(reg,rows+[rows[0]])


def test_equal_outcome_is_not_reported_as_effectiveness_improvement():
    reg,rows=panel();result=assess(reg,rows)
    assert result['behavior_gates_passed']
    assert result['status']=='ACCEPTED' and not result['observed_improvement']
    assert 'No measured' in result['effect_claim']
