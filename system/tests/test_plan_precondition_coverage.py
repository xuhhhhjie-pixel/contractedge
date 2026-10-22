import copy
import json
from types import SimpleNamespace

from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.changeset.prompt import build_changeset_request
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import validate_diagnosis, parse_method_input
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan


REQUIRED = [dict(source_evidence_ref='EV2', reason='Read initialization and publication prerequisites')]


def fixture():
    diagnosis = validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)
    return diagnosis, valid_plan()


def review(disposition='REQUIRED'):
    return dict(source_evidence_ref='EV2', condition='Replacement state is populated before publication',
        disposition=disposition, verification_refs=['P1'] if disposition=='REQUIRED' else [],
        open_obligation_refs=['OPEN1'] if disposition=='UNRESOLVED' else [],
        rationale='The public initialization path creates empty state; sampled behavior is a necessary check')


def validate(diagnosis, plan):
    return validate_semantic_changeset(plan, diagnosis, None, prerequisites=REQUIRED)


def test_missing_prerequisite_review_is_not_an_executable_complete_plan():
    diagnosis, plan = fixture()
    result = validate(diagnosis, plan)
    assert not result.valid
    assert any(e.code == 'PRECONDITION_REVIEW_INCOMPLETE' for e in result.errors)


def test_required_condition_needs_real_verification_not_just_prose():
    diagnosis, plan = fixture(); plan['precondition_review'] = [review()]
    plan['precondition_review'][0]['verification_refs'] = ['missing-check']
    result = validate(diagnosis, plan)
    assert not result.valid and any(e.code == 'PRECONDITION_VERIFICATION_INVALID' for e in result.errors)


def test_required_with_finite_behavior_check_is_coverage_not_proof():
    diagnosis, plan = fixture(); plan['precondition_review'] = [review()]
    plan['postconditions'][0].update(verification_kind='PUBLIC_BEHAVIOR',
        check_coverage='NECESSARY_ONLY', coverage_rationale='Finite sampled behavior only')
    result = validate(diagnosis, plan)
    assert result.valid, result.errors
    assert result.value['postconditions'][0]['check_coverage'] == 'NECESSARY_ONLY'
    translated = translate_for_topomen(plan, 'contractedge.topomen_plan.v1')
    assert translated['precondition_review'] == plan['precondition_review']


def test_build_only_cannot_discharge_runtime_prerequisite_without_open_record():
    diagnosis, plan = fixture(); plan['precondition_review'] = [review()]
    plan['postconditions'][0]['verification_kind'] = 'BUILD_ONLY'
    result = validate(diagnosis, plan)
    assert not result.valid and any(e.code == 'PRECONDITION_UNVERIFIED' for e in result.errors)


def test_unresolved_requires_existing_open_obligation():
    diagnosis, plan = fixture(); plan['precondition_review'] = [review('UNRESOLVED')]
    result = validate(diagnosis, plan)
    assert not result.valid
    assert any(e.code == 'PRECONDITION_VERIFICATION_INVALID' for e in result.errors)


def test_unresolved_with_real_open_record_remains_explicitly_partial():
    diagnosis, plan = fixture(); plan['precondition_review'] = [review('UNRESOLVED')]
    plan['open_obligations'] = [dict(id='OPEN1',outcome_id='O1',obligation_refs=['A1'],
        statement='Readiness outside the sampled observation is not verified',reason='No exhaustive runtime observation')]
    plan['outcome_coverage'][0]['unresolved'] = ['O1']
    plan['status'] = 'READY_PARTIAL'
    result = validate(diagnosis, plan)
    assert result.valid, result.errors
    assert result.value['status'] == 'READY_PARTIAL'


def test_nonapplicable_keeps_explicit_reason_without_fake_verification():
    diagnosis, plan = fixture(); plan['precondition_review'] = [review('NOT_APPLICABLE')]
    assert validate(diagnosis, plan).valid
    plan['precondition_review'][0]['verification_refs'] = ['P1']
    assert not validate(diagnosis, plan).valid


def test_duplicate_and_unexpected_review_cannot_replace_required_one():
    diagnosis, plan = fixture(); plan['precondition_review'] = [review(), review()]
    assert not validate(diagnosis, plan).valid
    plan['precondition_review'] = [{**review(), 'source_evidence_ref':'other'}]
    assert not validate(diagnosis, plan).valid


def test_model_schema_requests_review_only_for_actual_supplied_prerequisites():
    context = RunContext('r','c',0,'snap-0',None,'input',
        retrieval_config={'source_prerequisites':REQUIRED})
    diagnosis, _ = fixture()
    request = build_changeset_request(diagnosis, context)
    body = json.loads(request.messages[1]['content'])
    assert body['source_prerequisites'] == REQUIRED
    assert 'precondition_review' in request.output_schema['required']
    assert request.output_schema['properties']['precondition_review']['minItems']==1
    regular = build_changeset_request(diagnosis, RunContext('r','c',0,'snap-0',None,'input'))
    assert 'precondition_review' not in regular.output_schema['properties']


def test_regular_configuration_plan_remains_compatible_without_prerequisites():
    diagnosis, plan = fixture()
    assert validate_semantic_changeset(plan, diagnosis, None).valid


def test_translation_retains_prerequisite_source_even_if_not_cited_by_diagnosis():
    import hashlib
    diagnosis, plan = fixture(); plan['precondition_review'] = [review()]
    record = dict(id='EV2', kind='SOURCE_SPAN', snapshot_id=plan['snapshot_id'],
        excerpt='initialize()', content_digest=hashlib.sha256(b'initialize()').hexdigest(),
        locator=dict(path='worker.go', start_line=1, end_line=1))
    translated = translate_for_topomen(plan, 'contractedge.topomen_plan.v1',
        diagnosis=diagnosis.raw, source_support=[record])
    assert record in translated['coordination_context']['source_support']


def test_translation_rejects_unbound_or_stale_prerequisite_source():
    import pytest
    diagnosis, plan = fixture(); plan['precondition_review'] = [review()]
    with pytest.raises(ValueError, match='prerequisite source'):
        translate_for_topomen(plan, 'contractedge.topomen_plan.v1',
            diagnosis=diagnosis.raw, source_support=[dict(id='EV2',kind='SOURCE_SPAN',snapshot_id='old')])


def execution_fixture(root):
    import hashlib
    from tests.test_repair_execution import bound_plan
    from contractedge.source.snapshot import compute_snapshot
    (root/'templates').mkdir(); (root/'templates/serviceaccount.yaml').write_text('name: default\n')
    (root/'main.go').write_text('package main\nfunc main() { serve() }\n')
    helper='func initialize() { prepare() }\n'; (root/'worker.go').write_text(helper)
    plan=bound_plan(root); snap=compute_snapshot(root)
    plan['operations'][0]['target'].update(path='main.go',content_digest=snap.file_digests['main.go'],
        locator={'start_line':2,'end_line':2})
    plan['coordination_context']={'source_support':[dict(id='H1',kind='SOURCE_SPAN',
        snapshot_id=snap.snapshot_id,locator=dict(path='worker.go',start_line=1,end_line=1),
        excerpt=helper,content_digest=hashlib.sha256(helper.encode()).hexdigest())]}
    plan['precondition_review']=[dict(source_evidence_ref='H1',disposition='REQUIRED')]
    config={'model':'fixture','provider':'openai_compatible','max_context_tokens':50000,
        'execution_max_output_tokens':1000,'max_execution_support_bytes':24000}
    return plan,config


def test_execution_continuation_rereads_unchanged_prerequisite_on_new_snapshot(tmp_path):
    from contractedge.orchestration.repair import rebind_execution_plan
    from contractedge.execution.topomen import build_execution_request
    plan,config=execution_fixture(tmp_path)
    (tmp_path/'main.go').write_text('package main\nfunc main() { fixedServe() }\n')
    rebound=rebind_execution_plan(plan,tmp_path,{})
    request=build_execution_request(rebound,tmp_path,{'allowed_paths':['main.go']},len,config,requirement='Preserve state')
    record=json.loads(request.messages[1]['content'])['read_only_source_support'][0]
    assert record['snapshot_id']==rebound['snapshot_id']!=plan['snapshot_id']
    assert record['excerpt']==(tmp_path/'worker.go').read_text()


def test_changed_prerequisite_requires_replanning_instead_of_false_freshness(tmp_path):
    import pytest
    from contractedge.orchestration.repair import rebind_execution_plan
    from contractedge.execution.topomen import build_execution_request,PlanRejected
    plan,config=execution_fixture(tmp_path)
    (tmp_path/'worker.go').write_text('func initialize() { differentContract() }\n')
    rebound=rebind_execution_plan(plan,tmp_path,{})
    with pytest.raises(PlanRejected,match='Prerequisite source'):
        build_execution_request(rebound,tmp_path,{'allowed_paths':['main.go']},len,config,requirement='Preserve state')


def test_required_source_is_not_silently_removed_for_budget(tmp_path):
    import pytest
    from contractedge.execution.topomen import build_execution_request,PlanRejected
    plan,config=execution_fixture(tmp_path)
    with pytest.raises(PlanRejected,match='Prerequisite source'):
        build_execution_request(plan,tmp_path,{'allowed_paths':['main.go']},len,
            {**config,'max_execution_support_bytes':0},requirement='Preserve state')
