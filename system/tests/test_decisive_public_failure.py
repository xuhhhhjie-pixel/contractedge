"""A decisive bound FAIL cannot be hidden behind an optional model review."""
import pytest

from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.budget import BudgetExceeded
from tests.test_necessary_coverage_review import obligation, observed
from tests.test_check_coverage import obs


class Exhausted:
    def complete_json(self, request):
        raise BudgetExceeded('No calls remain; known negative must not need a model')


@pytest.mark.parametrize('group,prefix',[('postconditions',''),('preserve_constraints','preserve:'),('open_obligations','open:')])
def test_bound_necessary_fail_keeps_unknown_sibling_without_model_call(group,prefix):
    failed={**obligation(),'behavior_check_refs':['a'],'check_coverage':'NECESSARY_ONLY'}
    unresolved=dict(id='UNOBSERVED',statement='Other runtime property',verification_kind='PUBLIC_BEHAVIOR')
    plan={group:[failed], 'open_obligations':[unresolved] if group!='open_obligations' else [failed,unresolved]}
    result=ModelPublicReviewer(Exhausted(),len,dict(model='fixture',max_context_tokens=12000)).review('Public requirement',plan,observed([obs('a','FAIL')]))
    assert result['decision']=='FAIL'
    assert [(c['id'],c['status']) for c in result['checks']]==[(prefix+'P','FAIL'),('open:UNOBSERVED','UNKNOWN')]
    assert result['checks'][0]['evidence'] and not result['checks'][1]['evidence']


def test_supporting_only_failure_is_not_a_decisive_negative():
    supporting={**obligation(),'check_coverage':'SUPPORTING','behavior_check_refs':['a']}
    with pytest.raises(BudgetExceeded):
        ModelPublicReviewer(Exhausted(),len,dict(model='fixture',max_context_tokens=12000)).review('Requirement',{'postconditions':[supporting]},observed([obs('a','FAIL')]))


@pytest.mark.parametrize('mutate',['snapshot','scenario','capability'])
def test_wrong_context_negative_cannot_skip_unknown_review(mutate):
    record=obs('a','FAIL')
    if mutate=='snapshot':record['snapshot_id']='OLD'
    elif mutate=='scenario':record['scenario']='CONTROL'
    else:record['evidence_kind']='EXECUTION_ONLY'
    failed={**obligation(),'behavior_check_refs':['a']}
    with pytest.raises(BudgetExceeded):
        ModelPublicReviewer(Exhausted(),len,dict(model='fixture',max_context_tokens=12000)).review('Requirement',{'postconditions':[failed]},observed([record]))
