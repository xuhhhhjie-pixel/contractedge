from copy import deepcopy

from contractedge.changeset.adapter import bind_changeset_envelope
from contractedge.contracts.validation import parse_method_input,validate_diagnosis,validate_changeset
from tests.test_contracts import valid_diagnosis,valid_method_input,valid_plan


def fixture():
    diagnosis=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
    plan=valid_plan();plan['status']='READY_PARTIAL'
    plan['open_obligations']=[dict(id='OPEN1',outcome_id='O1',obligation_refs=['A1'],
        statement='Internal readiness is still unverified',reason='No complete observation')]
    return diagnosis,plan


def bind(diagnosis,plan):
    return bind_changeset_envelope(plan,diagnosis,'run-1','snap-0',valid_method_input()['input_digest'])


def test_explicit_open_record_projects_to_summary_without_a_paid_status_retry():
    diagnosis,plan=fixture();original=deepcopy(plan)
    result,audit=bind(diagnosis,plan)
    assert result['outcome_coverage'][0]['unresolved']==['O1']
    assert result['open_obligations']==plan['open_obligations']
    assert result['planned_violation_ids']==plan['planned_violation_ids']
    assert validate_changeset(result,diagnosis,None).valid
    assert audit['explicit_open_projection']['added_unresolved']==['O1']
    assert plan==original


def test_explicit_open_record_can_only_make_ready_status_more_conservative():
    diagnosis,plan=fixture();plan['status']='READY'
    result,_=bind(diagnosis,plan)
    assert result['status']=='READY_PARTIAL'
    plan['open_obligations']=[];plan['status']='READY_PARTIAL'
    result,_=bind(diagnosis,plan)
    assert result['status']=='READY_PARTIAL'
    assert not validate_changeset(result,diagnosis,None).valid


def test_unknown_or_invalid_open_refs_are_not_repaired_or_erased():
    for field,value in [('outcome_id','unknown'),('obligation_refs',['unknown'])]:
        diagnosis,plan=fixture();plan['open_obligations'][0][field]=value
        result,_=bind(diagnosis,plan)
        assert result['outcome_coverage'][0]['unresolved']==[]
        assert not validate_changeset(result,diagnosis,None).valid


def test_missing_summary_row_is_not_invented_and_declared_unknown_not_deleted():
    diagnosis,plan=fixture();plan['outcome_coverage']=[]
    result,_=bind(diagnosis,plan)
    assert result['outcome_coverage']==[]
    diagnosis,plan=fixture();plan['outcome_coverage'][0]['unresolved']=['unknown']
    result,_=bind(diagnosis,plan)
    assert 'unknown' in result['outcome_coverage'][0]['unresolved']
    assert not validate_changeset(result,diagnosis,None).valid
