import importlib.util
import json
from contractedge.execution.support import support_error
from tests.test_observation_capability_gate import record, claim


def api():
    assert importlib.util.find_spec('contractedge.diagnosis.operation_consistency'), 'Operation fact consistency gate missing'
    from contractedge.diagnosis import operation_consistency
    return operation_consistency


def failed():
    r=record(json.dumps({'name':'child','resource':{'kind':'Namespace','metadata':{'name':'child'}},
                        'exception':{'type':'LookupError','message':'Owner required'}}))
    r.update(adapter_id='public-object-adoption-v1',structured_complete=True,
             producer_manifest_sha256='a'*64,source_pointer='/observations/1',source_file_sha256='b'*64)
    return r


def test_fields_cannot_prove_success_after_operation_exception():
    assert support_error({'verification_kind':'PUBLIC_STRUCTURE'},claim('child'),[failed()])=='OPERATION_SUCCESS_CONFLICT'


def test_known_failures_cannot_be_ignored_in_satisfaction_diagnosis():
    op=api().operation_facts([failed()])[0]
    payload={'outcome_coverage':[{'outcome_id':'O1','preserve_current_satisfaction':[{'status':'OBSERVED'}]}]}
    errors=api().validate_operation_claims(payload,[op],{'requirement_text':'Must adopt successfully.'})
    assert errors and errors[0]['code']=='OPERATION_SUCCESS_CONFLICT'


def test_expected_exception_requires_exact_public_contract_and_matching_type():
    op=api().operation_facts([failed()])[0]
    text='A rejected invalid owner may raise LookupError.'
    row=dict(operation_id=op['operation_id'],disposition='EXPECTED_EXCEPTION',outcome_refs=['O1'],
             rationale='Invalid-owner negative control',exception_type='LookupError',
             contract_quote=text,contract_ref='REQ',scenario='trigger')
    payload={'operation_assessments':[row]}
    context={'requirement_text':text,'public_contract_evidence':[{'id':'REQ','excerpt':text}],
             'operation_expectations':[{'operation_id':op['operation_id'],'scenario':'trigger',
                 'exception_type':'LookupError','contract_ref':'REQ','contract_quote':text}]}
    assert api().validate_operation_claims(payload,[op],context)==[]
    row['exception_type']='TypeError'
    assert api().validate_operation_claims(payload,[op],context)
    row['exception_type']='LookupError';row['contract_quote']='fabricated allowance'
    assert api().validate_operation_claims(payload,[op],context)


def test_incomplete_unknown_or_nonregistered_output_has_no_operation_authority():
    assert api().operation_facts([record('error: failure')])==[]
    item=failed();item['structured_complete']=False
    assert api().operation_facts([item])==[]


def test_operation_fact_is_bound_to_source_and_snapshot():
    item=failed();op=api().operation_facts([item])[0]
    assert op['status']=='FAILED' and op['snapshot_id']=='S1' and op['result_complete'] is False
    item['snapshot_id']='S2'
    assert api().operation_facts([item])[0]['operation_id']!=op['operation_id']


def test_satisfied_unrelated_scope_is_not_blocked_by_other_resource_exception():
    item=failed();item['resource_identity']={'kind':'Namespace','name':'child'}
    good=record('{"resource":{"kind":"Pod","metadata":{"name":"other"}}}',scenario='control');good['id']='OTHER'
    good['resource_identity']={'kind':'Pod','name':'other'}
    c=claim('other');c['evidence']=[{'observation_id':'OTHER','quote':'other'}]
    assert support_error({'verification_kind':'PUBLIC_STRUCTURE','verification_scope':{'scenario':'control','resource':{'name':'other'}}},c,[item,good]) is None


def test_unquoted_failure_same_object_and_snapshot_cannot_be_hidden_by_good_quote():
    bad=failed();bad['resource_identity']={'kind':'Namespace','name':'child'}
    good=record('{"name":"child","namespace":"ok"}');good['id']='GOOD'
    good['resource_identity']={'kind':'Namespace','name':'child'}
    c=claim('child');c['evidence']=[{'observation_id':'GOOD','quote':'child'}]
    assert support_error({'verification_kind':'PUBLIC_STRUCTURE','verification_scope':{'scenario':'trigger','resource':{'name':'child'}}},c,[good,bad])=='OPERATION_SUCCESS_CONFLICT'
    bad['snapshot_id']='S0'
    assert support_error({'verification_kind':'PUBLIC_STRUCTURE','verification_scope':{'scenario':'trigger','resource':{'name':'child'}}},c,[good,bad]) is None


def test_global_satisfaction_cannot_ignore_a_fresh_registered_failed_predicate():
    payload={'outcome_coverage':[{'outcome_id':'O1','repair_supported':[], 'unresolved':[],
        'preserve_required':['O1'],'preserve_current_satisfaction':[{'status':'OBSERVED'}]}]}
    fact=dict(id='OBS-fail',snapshot_id='S1',evidence_kind='PUBLIC_BEHAVIOR',
        behavior_check={'id':'registered:target','status':'FAIL','probe_sha256':'a'*64})
    issues=api().validate_public_check_claims(payload,[fact],'S1')
    assert issues and issues[0]['code']=='PUBLIC_PREDICATE_SATISFACTION_CONFLICT'
    assert api().validate_public_check_claims(payload,[fact],'S2')==[]
    payload['outcome_coverage'][0]['preserve_current_satisfaction'][0]['status']='UNKNOWN'
    assert api().validate_public_check_claims(payload,[fact],'S1')==[]


def test_unresolved_or_separate_outcome_is_not_forced_into_a_causal_repair():
    fact=dict(id='OBS-fail',snapshot_id='S1',evidence_kind='PUBLIC_BEHAVIOR',
        behavior_check={'id':'registered:target','status':'FAIL','probe_sha256':'a'*64})
    payload={'outcome_coverage':[{'outcome_id':'O1','preserve_required':['O1'],
        'preserve_current_satisfaction':[{'status':'OBSERVED'}]}, {'outcome_id':'O2','unresolved':['O2']}]}
    assert api().validate_public_check_claims(payload,[fact],'S1')==[]


def test_separate_failed_operation_does_not_block_preservation_of_another_outcome():
    op=api().operation_facts([failed()])[0]
    assessment=dict(operation_id=op['operation_id'],outcome_refs=['O2'],disposition='REQUIRES_SUCCESS',satisfaction='UNSATISFIED')
    payload={'operation_assessments':[assessment],'outcome_coverage':[
        {'outcome_id':'O1','preserve_required':['O1'],'preserve_current_satisfaction':[{'status':'OBSERVED'}]},
        {'outcome_id':'O2','repair_supported':['V1']}]}
    assert api().validate_operation_claims(payload,[op],{})==[]
    assessment['outcome_refs']=['O999']
    assert any(e['code']=='OPERATION_OUTCOME_INVALID' for e in api().validate_operation_claims(payload,[op],{}))
