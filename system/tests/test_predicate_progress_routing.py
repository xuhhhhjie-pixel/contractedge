import pytest

from contractedge.orchestration.feedback import reassess_failed_target_predicates


def fixture():
    def report(sid):
        return dict(snapshot_id=sid, input_digest='input', environment_digest='env',
            status='EXECUTION_PASS', observations=[dict(id=k, snapshot_id=sid,
                scenario='scenario', evidence_kind='PUBLIC_BEHAVIOR',
                behavior_check=dict(id=k, status='FAIL', probe_sha256='program'))
                for k in ['target1', 'target2', 'control']])
    before, after = report('S0'), report('S1')
    feedback = dict(from_snapshot='S0', to_snapshot='S1', build_state='PASSED',
        semantic_outcome='PUBLIC_REVIEW_FAIL', route='EXECUTE', failure_kind='UNKNOWN',
        public_check_results=[dict(status='FAIL', evidence=[dict(observation_id='target1')])])
    plan = dict(postconditions=[dict(verification_kind='PUBLIC_BEHAVIOR',
        verification_scope=dict(scenario='scenario'), behavior_check_refs=['target1', 'target2'])])
    return before, after, feedback, plan


def test_unchanged_targets_reassess_cause_without_rewriting_outcomes():
    before, after, feedback, plan = fixture()
    result = reassess_failed_target_predicates(feedback, before, after, plan)
    assert result['route'] == 'DIAGNOSIS' and result['route_confidence'] == 'LOW'
    assert result['semantic_outcome'] == 'PUBLIC_REVIEW_FAIL'
    assert len(result['predicate_progress_comparison']['unchanged_failures']) == 2
    assert feedback['route'] == 'EXECUTE'


def test_route_change_does_not_carry_the_opposite_current_instruction():
    import json
    from contractedge.orchestration.feedback import feedback_prompt_view
    before, after, feedback, plan = fixture()
    feedback['rationale'] = 'Use the same accepted intent; do not rediagnose solely from a failed postcondition.'
    result = reassess_failed_target_predicates(feedback, before, after, plan)
    prompt = json.dumps(feedback_prompt_view(result))
    assert result['route'] == 'DIAGNOSIS'
    assert 'do not rediagnose' not in prompt
    assert feedback['rationale'].startswith('Use the same'), 'Original feedback stays intact'
    assert result['predicate_progress_comparison']['previous_rationale_sha256']


def test_unrelated_control_improvement_does_not_count_as_target_progress():
    before, after, feedback, plan = fixture()
    after['observations'][2]['behavior_check']['status'] = 'PASS'
    assert reassess_failed_target_predicates(feedback, before, after, plan)['route'] == 'DIAGNOSIS'


def test_duplicate_conflicting_results_never_establish_no_progress():
    before, after, feedback, plan = fixture()
    after['observations'].append({**after['observations'][0], 'id':'duplicate',
        'behavior_check':dict(id='target1', status='PASS', probe_sha256='program')})
    assert reassess_failed_target_predicates(feedback, before, after, plan)['route'] == 'EXECUTE'


@pytest.mark.parametrize('change', ['partial', 'helper', 'input', 'env', 'program', 'stale',
    'unknown', 'missing', 'scope', 'untyped', 'uncited', 'same_snapshot', 'incomplete'])
def test_progress_known_implementation_conflict_or_incomparable_targets_keep_route(change):
    before, after, feedback, plan = fixture()
    if change == 'partial': after['observations'][1]['behavior_check']['status'] = 'PASS'
    if change == 'helper': feedback['helper_preconditions'] = dict(checks=[dict(status='CONFLICT')])
    if change == 'input': after['input_digest'] = 'other'
    if change == 'env': before['environment_digest'] = None
    if change == 'program': after['observations'][0]['behavior_check']['probe_sha256'] = 'other'
    if change == 'stale': after['observations'][0]['snapshot_id'] = 'S0'
    if change == 'unknown': after['observations'][1]['behavior_check']['status'] = 'UNKNOWN'
    if change == 'missing': after['observations'].pop(0)
    if change == 'scope': plan['postconditions'][0]['verification_scope']['scenario'] = 'other'
    if change == 'untyped': after['observations'][0]['evidence_kind'] = 'EXECUTION_ONLY'
    if change == 'uncited': feedback['public_check_results'] = []
    if change == 'same_snapshot': feedback['from_snapshot'] = 'S1'
    if change == 'incomplete': after['status'] = 'EXECUTION_FAILED'
    assert reassess_failed_target_predicates(feedback, before, after, plan)['route'] == 'EXECUTE'
