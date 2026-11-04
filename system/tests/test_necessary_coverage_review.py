"""Positive necessary evidence must not spend a model call on forbidden PASS.

These are finite public predicates, independent of any benchmark case. Unknown
coverage remains an open obligation; negative and complete evidence keep their
existing verdicts.
"""
import json

import pytest

from contractedge.execution.review import ModelPublicReviewer
from contractedge.execution.support import deterministic_check_result, support_error
from contractedge.model.provider import FixtureProvider
from contractedge.orchestration.feedback import make_feedback
from tests.test_check_coverage import obs


def obligation():
    return {'id': 'P', 'statement': 'Preserve behavior outside these finite examples',
            'verification_kind': 'PUBLIC_BEHAVIOR',
            'verification_scope': {'scenario': 'trigger'},
            'behavior_check_refs': ['a', 'b'], 'check_coverage': 'NECESSARY_ONLY',
            'coverage_rationale': 'These checks do not cover all later consumers.'}


def observed(records=None):
    return {'status': 'EXECUTION_PASS', 'snapshot_id': 'S1',
            'observations': records if records is not None else [obs('a', 'PASS'), obs('b', 'PASS')]}


class NeverCall:
    def complete_json(self, request):
        raise AssertionError('Only UNKNOWN is admissible for the complete positive necessary evidence')


@pytest.mark.parametrize('group,prefix', [('postconditions', ''), ('preserve_constraints', 'preserve:'),
                                         ('open_obligations', 'open:')])
def test_positive_necessary_coverage_is_local_unknown_for_every_obligation_kind(group, prefix):
    result = ModelPublicReviewer(NeverCall(), len, {'model': 'fixture', 'max_context_tokens': 12000}).review(
        'Public requirement', {group: [obligation()]}, observed())
    assert result['decision'] == 'UNKNOWN'
    assert result['method'] == 'BOUND_CHECK_AGGREGATION'
    check = result['checks'][0]
    assert check['id'] == prefix + 'P' and check['status'] == 'UNKNOWN'
    assert {e['observation_id'] for e in check['evidence']} == {'a', 'b'}
    assert 'necessary' in check['rationale'].lower()
    assert obligation()['coverage_rationale'] in check['rationale']
    assert support_error(obligation(), check, observed()['observations']) is None


def test_internal_mechanical_shortcut_never_promotes_unknown_to_pass():
    result = ModelPublicReviewer(NeverCall(), len, {'model': 'fixture', 'max_context_tokens': 12000})._review_model(
        'Public requirement', {'postconditions': [obligation()]}, observed())
    assert result['decision'] == 'UNKNOWN' and result['checks'][0]['status'] == 'UNKNOWN'


def test_known_failure_keeps_priority_over_positive_necessary_sibling():
    failed = {**obligation(), 'id': 'Q', 'behavior_check_refs': ['failed', 'missing']}
    result = ModelPublicReviewer(NeverCall(), len, {'model': 'fixture', 'max_context_tokens': 12000}).review(
        'Public requirement', {'postconditions': [obligation(), failed]},
        observed([obs('a', 'PASS'), obs('b', 'PASS'), obs('failed', 'FAIL')]))
    assert result['decision'] == 'FAIL'
    assert [(c['id'], c['status']) for c in result['checks']] == [('P', 'UNKNOWN'), ('Q', 'FAIL')]


@pytest.mark.parametrize('change', ['missing', 'stale', 'scenario', 'capability', 'empty', 'supporting'])
def test_ineligible_or_incomplete_evidence_is_not_locally_resolved(change):
    records = [obs('a', 'PASS'), obs('b', 'PASS')]
    condition = obligation()
    if change == 'missing': records.pop()
    elif change == 'stale': records[1]['snapshot_id'] = 'S0'
    elif change == 'scenario': records[1]['scenario'] = 'control'
    elif change == 'capability': records[1]['evidence_kind'] = 'EXECUTION_ONLY'
    elif change == 'empty': records[1]['excerpt'] = ''
    elif change == 'supporting': condition['check_coverage'] = 'SUPPORTING'
    assert deterministic_check_result(condition, records, snapshot_id='S1') is None


def test_all_of_positive_predicate_still_passes():
    condition = {**obligation(), 'check_coverage': 'ALL_OF'}
    result = deterministic_check_result(condition, observed()['observations'], snapshot_id='S1')
    assert result['status'] == 'PASS'


def test_unresolved_semantic_sibling_still_reaches_model_with_only_its_own_check():
    class Capture(FixtureProvider):
        def complete_json(self, request):
            checks = json.loads(request.messages[1]['content'])['checks']
            assert [c['id'] for c in checks] == ['Q']
            return super().complete_json(request)
    provider = Capture({'checks': [{'id': 'Q', 'status': 'UNKNOWN', 'rationale': 'No sufficient evidence',
                                    'evidence': []}], 'failure_kind': 'UNKNOWN', 'rationale': 'Public limit'})
    unresolved = {'id': 'Q', 'statement': 'Unresolved requirement', 'verification_kind': 'PUBLIC_BEHAVIOR'}
    result = ModelPublicReviewer(provider, len, {'model': 'fixture', 'max_context_tokens': 12000}).review(
        'Public requirement', {'postconditions': [obligation(), unresolved]}, observed())
    assert result['decision'] == 'UNKNOWN'
    assert [(c['id'], c['status']) for c in result['checks']] == [('P', 'UNKNOWN'), ('Q', 'UNKNOWN')]


def test_local_unknown_stays_pending_in_feedback():
    plan = {'status': 'READY_PARTIAL', 'changes': [{'id': 'C', 'postcondition_refs': ['P']}],
            'postconditions': [obligation()]}
    observation = observed()
    review = ModelPublicReviewer(NeverCall(), len, {'model': 'fixture', 'max_context_tokens': 12000}).review(
        'Public requirement', plan, observation)
    feedback = make_feedback(case_id='FINITE-PUBLIC', run_id='r', input_digest='a'*64,
        round_id=0, from_snapshot='S0', to_snapshot='S1', patch_digest='b'*64,
        execution_status='EXECUTION_PASS', observations=observation['observations'], plan=plan, review=review)
    assert feedback['semantic_outcome'] == 'UNKNOWN'
    assert feedback['pending_obligations']
    assert not feedback['retained_constraints']


@pytest.mark.parametrize('adapter', ['public-object-adoption-v1', 'public-oci-transport-v1'])
@pytest.mark.parametrize('scope_case,blocked', [
    ('same-object', True), ('unknown-object', True), ('other-object', False),
    ('other-scenario', False), ('old-snapshot', False), ('global', True),
])
def test_mechanical_success_keeps_uncited_operation_context(adapter, scope_case, blocked):
    from tests.test_operation_consistency import failed

    condition = {**obligation(), 'check_coverage': 'ALL_OF',
                 'verification_scope': {'scenario': 'trigger',
                                        'resource': {'kind': 'Pod', 'name': 'selected'}}}
    records = [obs('a', 'PASS'), obs('b', 'PASS')]
    for record in records:
        record['resource_identity'] = {'kind': 'Pod', 'name': 'selected'}
    operation = failed()
    operation['id'] = 'OP-uncited'
    operation['resource_identity'] = {'kind': 'Pod', 'name': 'selected'}
    if adapter == 'public-oci-transport-v1':
        import hashlib
        operation['adapter_id'] = adapter
        operation['excerpt'] = json.dumps({'requests': [], 'client_error': 'Connection rejected'})
        operation['content_digest'] = hashlib.sha256(operation['excerpt'].encode()).hexdigest()
    if scope_case == 'unknown-object': operation.pop('resource_identity')
    elif scope_case == 'other-object': operation['resource_identity']['name'] = 'control'
    elif scope_case == 'other-scenario': operation['scenario'] = 'control'
    elif scope_case == 'old-snapshot': operation['snapshot_id'] = 'S0'
    elif scope_case == 'global': condition.pop('verification_scope')
    records.append(operation)
    result = deterministic_check_result(condition, records, snapshot_id='S1')
    if blocked:
        assert result is None, 'A filtered check list must not hide a relevant failed operation'
    else:
        assert result['status'] == 'PASS'
