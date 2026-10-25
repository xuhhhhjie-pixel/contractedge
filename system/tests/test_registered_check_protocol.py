"""Registered predicates must survive planning and real checker interpretation.

Runtime doubles replace only container transport; the frozen checkers and
planning/semantic/support implementations under test are real. No model HTTP.
"""
import copy
import json
from types import SimpleNamespace

import pytest

from contractedge.changeset.prompt import build_changeset_request
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_diagnosis
from contractedge.execution.public_observer import public_check_capabilities, enforce_public_profile
from contractedge.execution.support import deterministic_check_result, support_error
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan
from tests import test_public_observer as observer_tests


def planning(capabilities=None):
    diagnosis = validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)
    capabilities = capabilities if capabilities is not None else {'registered_public_checks': public_check_capabilities()}
    context = SimpleNamespace(run_id='run-1', case_id='RW-DEMO', snapshot_id='snap-0',
        input_digest=diagnosis.input_digest, model_config={'model': 'fixture'}, budget={'max_output_tokens': 1000},
        retrieval_config={'public_execution_capabilities': capabilities})
    return diagnosis, context, capabilities


@pytest.mark.parametrize('kind', ['postconditions', 'preserve_constraints', 'open_obligations'])
def test_registered_only_checks_are_bindable_in_actual_planning_schema(kind):
    diagnosis, context, _ = planning()
    request = build_changeset_request(diagnosis, context)
    body = json.loads(request.messages[1]['content'])
    fields = body['output_schema']['properties'][kind]['items']['properties']
    assert 'behavior_check_refs' in fields
    assert set(fields['behavior_check_refs']['items']['enum']) == {
        'contractedge-public-process-v1:selected', 'contractedge-public-process-v1:disabled',
        'contractedge-public-process-v1:stable'}
    assert 'check_coverage' in fields and 'coverage_rationale' in fields
    assert body['output_schema'] == request.output_schema
    checks = body['public_execution_capabilities']['registered_public_checks']['checks']
    assert checks[0]['scenario'] == 'selected-and-preservation'
    assert checks[2]['scenario_members'] == ['selected', 'disabled', 'stable']


def test_both_check_sources_survive_the_same_request():
    caps = {'registered_public_checks': public_check_capabilities(),
            'method_behavior_probes': [{'id': 'method-local', 'checks': ['ready'], 'scenario': 'method-ready'}]}
    d, ctx, _ = planning(caps)
    fields = build_changeset_request(d, ctx).output_schema['properties']['postconditions']['items']['properties']
    assert set(fields['behavior_check_refs']['items']['enum']) == {
        'method-local:ready', 'contractedge-public-process-v1:selected',
        'contractedge-public-process-v1:disabled', 'contractedge-public-process-v1:stable'}


def bound_plan(diagnosis):
    plan = valid_plan()
    plan['diagnosis_digest'] = canonical_digest(diagnosis.raw)
    plan['postconditions'][0].update(verification_kind='PUBLIC_BEHAVIOR',
        verification_scope={'scenario': 'selected-and-preservation'},
        behavior_check_refs=['contractedge-public-process-v1:selected'],
        check_coverage='ALL_OF', coverage_rationale='Only the declared finite enabled output is asserted.')
    return plan


@pytest.mark.parametrize('mutation,code', [
    ('unknown', 'UNREGISTERED_BEHAVIOR_CHECK'),
    ('missing-scenario', 'BEHAVIOR_CHECK_SCENARIO_MISMATCH'),
    ('other-scenario', 'BEHAVIOR_CHECK_SCENARIO_MISMATCH'),
    ('missing-coverage', 'CHECK_COVERAGE_REQUIRED'),
])
def test_invalid_registered_binding_is_rejected_at_plan_admission(mutation, code):
    d, _, caps = planning()
    plan = bound_plan(d)
    item = plan['postconditions'][0]
    if mutation == 'unknown': item['behavior_check_refs'] = ['invented:check']
    if mutation == 'missing-scenario': item.pop('verification_scope')
    if mutation == 'other-scenario': item['verification_scope'] = {'scenario': 'different-operation'}
    if mutation == 'missing-coverage': item.pop('check_coverage')
    result = validate_semantic_changeset(plan, d, None, capabilities=caps)
    assert code in {e.code for e in result.errors}


def test_registered_plan_is_admitted_without_adding_or_rewriting_bindings():
    d, _, caps = planning()
    plan = bound_plan(d)
    original = copy.deepcopy(plan)
    result = validate_semantic_changeset(plan, d, None, capabilities=caps)
    assert result.valid, result.errors
    assert plan == original


@pytest.mark.parametrize('state', ['REJECTED_PLAN_DRAFT', 'CURRENT_PLAN_FOR_REVISION'])
def test_correction_and_revision_preserve_bindings_in_the_serialized_schema(state):
    from jsonschema import Draft202012Validator
    d, ctx, _ = planning()
    plan = bound_plan(d)
    prior = {'state': state, 'response_content': json.dumps(plan), 'run_id': ctx.run_id,
             'snapshot_id': ctx.snapshot_id, 'input_digest': ctx.input_digest,
             'diagnosis_digest': canonical_digest(d.raw)}
    request = build_changeset_request(d, ctx, [{'code': 'TEST_REVISE'}], previous_attempt=prior)
    body = json.loads(request.messages[1]['content'])
    schema = body['output_schema']['properties']['postconditions']['items']
    assert list(Draft202012Validator(schema).iter_errors(plan['postconditions'][0])) == []
    wrong = copy.deepcopy(plan['postconditions'][0]); wrong['behavior_check_refs'] = ['other:check']
    assert list(Draft202012Validator(schema).iter_errors(wrong))
    assert request.messages[2]['content'] == json.dumps(plan)
    assert body['public_execution_capabilities']['registered_public_checks']['checks'][0]['scenario'] == 'selected-and-preservation'


def test_method_only_and_no_check_requests_keep_their_existing_protocol():
    for caps, expected in [({}, None), ({'method_behavior_probes': [
            {'id': 'legacy-method', 'scenario': 'method-legacy', 'checks': ['ready']}]}, ['legacy-method:ready'])]:
        d, ctx, _ = planning(caps)
        request = build_changeset_request(d, ctx)
        assert request.prompt_version == 'changeset.v1.1.12'
        fields = request.output_schema['properties']['postconditions']['items']['properties']
        if expected is None: assert 'behavior_check_refs' not in fields
        else: assert fields['behavior_check_refs']['items']['enum'] == expected


def test_duplicate_ids_across_declaration_sources_are_not_silently_overwritten():
    caps = {'registered_public_checks': public_check_capabilities(), 'method_behavior_probes': [
        {'id': 'contractedge-public-process-v1', 'checks': ['selected'], 'scenario': 'other'}]}
    d, ctx, _ = planning(caps)
    with pytest.raises(ValueError, match='unique'): build_changeset_request(d, ctx)


@pytest.fixture
def ready_context():
    from tests.test_feedback_context import FeedbackContextTests
    fixture = FeedbackContextTests(); fixture.setUp()
    caps = {'registered_public_checks': public_check_capabilities()}
    fixture.trace['run_context']['retrieval_config']['public_execution_capabilities'] = copy.deepcopy(caps)
    fixture.trace['changeset']['postconditions'][0].update(
        verification_kind='PUBLIC_BEHAVIOR', verification_scope={'scenario': 'selected-and-preservation'},
        behavior_check_refs=['contractedge-public-process-v1:selected'], check_coverage='ALL_OF',
        coverage_rationale='Only the finite enabled check is asserted.')
    try: yield fixture, caps
    finally: fixture.doCleanups()


@pytest.mark.parametrize('mutation', ['removed', 'scenario', 'members', 'observer', 'statement'])
@pytest.mark.parametrize('entry', ['ready', 'revision'])
def test_ready_and_revision_reject_current_check_contract_drift(ready_context, mutation, entry):
    from contractedge.orchestration.repair import validate_ready_trace, revise_plan
    fixture, original = ready_context
    method = parse_method_input(fixture.method)
    validate_ready_trace(fixture.trace, method, fixture.source, capabilities=original)
    current = copy.deepcopy(original)
    registry = current['registered_public_checks']
    if mutation == 'removed': registry['checks'].pop(0)
    if mutation == 'scenario': registry['checks'][0]['scenario'] = 'other'
    if mutation == 'members': registry['checks'][0]['scenario_members'] = ['disabled']
    if mutation == 'observer': registry['observer_sha256'] = 'f'*64
    if mutation == 'statement': registry['checks'][0]['statement'] = 'A different public predicate'
    class NeverCall:
        def complete_json(self, request): raise AssertionError('Changed registry must be rejected before dispatch')
    with pytest.raises(ValueError, match='public check'):
        if entry == 'ready':
            validate_ready_trace(fixture.trace, method, fixture.source, capabilities=current)
        else:
            revise_plan(fixture.trace, method, fixture.source, fixture.feedback, NeverCall(), len,
                        {**fixture.config, 'public_execution_capabilities': current})


@pytest.mark.parametrize('mutation', ['duplicate', 'missing-scenario', 'empty-members', 'wrong-type'])
def test_malformed_registration_stops_planning(mutation):
    caps = {'registered_public_checks': public_check_capabilities()}
    check = caps['registered_public_checks']['checks'][0]
    if mutation == 'duplicate': caps['registered_public_checks']['checks'].append(copy.deepcopy(check))
    if mutation == 'missing-scenario': check.pop('scenario', None)
    if mutation == 'empty-members': check['scenario_members'] = []
    if mutation == 'wrong-type': caps['registered_public_checks'] = []
    d, ctx, _ = planning(caps)
    with pytest.raises(ValueError): build_changeset_request(d, ctx)


@pytest.fixture
def process_report():
    case = observer_tests.RequiredPublicProfileTests()
    case.setUp()
    try: yield case.observation()
    finally: case.doCleanups()


def predicate(ref, scenario='selected-and-preservation'):
    return {'id': 'P', 'statement': 'The finite published predicate holds.',
        'verification_kind': 'PUBLIC_BEHAVIOR', 'verification_scope': {'scenario': scenario},
        'behavior_check_refs': [ref], 'check_coverage': 'ALL_OF',
        'coverage_rationale': 'Only the frozen finite predicate is claimed.'}


def test_checker_observation_can_support_the_registered_scenario(process_report):
    from contractedge.execution.review import ModelPublicReviewer
    class NoModel:
        def complete_json(self, request): raise AssertionError('Fully bound checks need no extra model call')
    plan = {'postconditions': [predicate('contractedge-public-process-v1:selected')]}
    review = ModelPublicReviewer(NoModel(), len, {'model': 'fixture'}).review('Finite output', plan, process_report)
    assert review['decision'] == 'PASS'
    assert review['method'] == 'BOUND_CHECK_AGGREGATION'
    record = process_report['observations'][2]
    assert record['scenario'] == 'selected-and-preservation'
    assert json.loads(record['excerpt'])['scenario_members'] == ['selected', 'disabled', 'stable']


def test_wrong_scenario_stale_snapshot_and_partial_coverage_do_not_pass(process_report):
    records = process_report['observations']; snapshot = process_report['snapshot_id']
    ob = predicate('contractedge-public-process-v1:selected')
    assert deterministic_check_result(ob, records, snapshot_id=snapshot)['status'] == 'PASS'
    assert deterministic_check_result(ob, records, snapshot_id='old') is None
    ob['verification_scope']['scenario'] = 'unrelated'
    assert deterministic_check_result(ob, records, snapshot_id=snapshot) is None
    assert support_error(ob, {'status': 'PASS', 'evidence': [{'observation_id': 'OBS-selected'}]}, records) == 'OBSERVATION_SCENARIO_MISMATCH'
    ob['verification_scope']['scenario'] = 'selected-and-preservation'
    ob['check_coverage'] = 'NECESSARY_ONLY'
    assert deterministic_check_result(ob, records, snapshot_id=snapshot) is None


@pytest.mark.parametrize('field,value', [('scenario', 'other'), ('scenario_members', ['selected'])])
def test_rehashed_scenario_forgery_is_not_trusted(process_report, field, value):
    import hashlib
    record = process_report['observations'][2]
    body = json.loads(record['excerpt']); body[field] = value
    record[field] = value; record['excerpt'] = json.dumps(body, sort_keys=True)
    record['content_digest'] = hashlib.sha256(record['excerpt'].encode()).hexdigest()
    process_report['required_public_profile']['observations_digest'] = canonical_digest(process_report['observations'])
    result = enforce_public_profile({'decision': 'PASS'}, process_report)
    assert result['decision'] == 'UNKNOWN'
    assert result['required_public_profile']['binding_valid'] is False


@pytest.mark.parametrize('case_id', ['config-binding/base', 'reload-state/base'])
def test_six_case_observer_uses_its_declared_scope_not_check_id(tmp_path, case_id):
    from tests.test_isolated_method_panel import observe
    from contractedge.development.method_panel_fixtures import check_capabilities
    report, _ = observe(tmp_path, case_id)
    assert report['status'] == 'EXECUTION_PASS', report['errors']
    for check in check_capabilities(case_id)['checks']:
        ob = predicate(check['id'], 'public-requirement-panel')
        result = deterministic_check_result(ob, report['observations'], snapshot_id=report['snapshot_id'])
        assert result is not None and result['status'] == 'PASS'
        assert check['scenario'] == 'public-requirement-panel'
        assert check['scenario_members']


def test_bound_failure_reaches_feedback_without_a_model_inventing_a_reason():
    from contractedge.execution.review import ModelPublicReviewer
    from contractedge.orchestration.feedback import make_feedback
    case = observer_tests.RequiredPublicProfileTests(); case.setUp()
    try: report = case.observation(selected='wrong')
    finally: case.doCleanups()
    class NoModel:
        def complete_json(self, request): raise AssertionError('Bound failure must be aggregated')
    plan = {'status': 'READY', 'changes': [{'id': 'C', 'postcondition_refs': ['P']}],
            'postconditions': [predicate('contractedge-public-process-v1:selected')]}
    review = ModelPublicReviewer(NoModel(), len, {'model': 'fixture'}).review('Finite output', plan, report)
    feedback = make_feedback(case_id='C', run_id='r', input_digest='a'*64, round_id=0,
        from_snapshot='S0', to_snapshot=report['snapshot_id'], patch_digest='b'*64,
        execution_status='EXECUTION_PASS', observations=report['observations'], plan=plan, review=review)
    assert review['decision'] == 'FAIL'
    assert feedback['route'] == 'EXECUTE'


def test_panel_rehashed_scenario_tampering_is_rejected(tmp_path):
    import hashlib
    from tests.test_isolated_method_panel import observe
    from contractedge.execution.panel_observer import enforce_isolated_panel_profile
    report, _ = observe(tmp_path)
    record = report['observations'][0]
    body = json.loads(record['excerpt']); body['scenario'] = 'invented'
    record['scenario'] = 'invented'; record['excerpt'] = json.dumps(body, sort_keys=True)
    record['content_digest'] = hashlib.sha256(record['excerpt'].encode()).hexdigest()
    report['requirement_profile']['observations_digest'] = canonical_digest(report['observations'])
    report['required_public_profile']['requirement_profile_digest'] = canonical_digest(report['requirement_profile'])
    result = enforce_isolated_panel_profile({'decision': 'PASS'}, report)
    assert result['decision'] == 'UNKNOWN'
    assert result['required_public_profile']['binding_valid'] is False
