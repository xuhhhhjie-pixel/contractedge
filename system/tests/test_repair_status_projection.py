"""Repair, crash recovery and evaluation consume the same observed completion facts."""
import json
from copy import deepcopy

import pytest

from contractedge.development import public_demo
from contractedge.evaluation.gt_adapter import project_statuses, score_run
from contractedge.contracts.status import scoring_exclusion
from contractedge.model.provider import ModelResponse


FIELDS = ('method_status', 'execution_status', 'public_status', 'independent_score_status')
GT = dict(case_id='public-fixture', expected_relations=[], violations=[],
          precision_eligible=True, recall_eligible=True)


def consistent(report):
    projected = project_statuses(report)
    assert {key: report[key] for key in FIELDS} == projected
    scored = score_run(report, GT)
    assert {key: scored[key] for key in FIELDS} == projected
    assert scored['denominator_included'] is True
    return scored


def demo(tmp_path, monkeypatch, *, failure=None, public_unknown=False, cleanup=False,
         max_rounds=1, later_failure=False):
    class Provider(public_demo.SyntheticExampleProvider):
        def complete_json(self, request):
            if request.prompt_version.startswith('diagnosis.'):
                if failure == 'unknown':
                    raise TimeoutError('Controlled unknown diagnosis response')
                if failure == 'invalid':
                    return ModelResponse('{}', {'zero_cost': True})
            if request.prompt_version == 'topomen-source-execution.v1':
                if failure == 'execution_unknown':
                    raise TimeoutError('Controlled unknown first execution response')
                if failure == 'rejected':
                    return ModelResponse('{}', {'zero_cost': True})
                if later_failure and self.executions == 1:
                    if failure == 'later_unknown':
                        raise TimeoutError('Controlled unknown second execution response')
                    return ModelResponse('{}', {'zero_cost': True})
                if failure == 'runtime':
                    self.executions += 1
                    return ModelResponse(json.dumps({'edits': [dict(change_id='C1', path='app.py',
                        operation='MODIFY', old_text='primary = "default"', new_text='primary = (')]}),
                        {'zero_cost': True})
            if public_unknown and request.prompt_version.startswith('public-postcondition-review.'):
                body = json.loads(request.messages[1]['content'])
                return ModelResponse(json.dumps(dict(checks=[dict(id=check['id'], status='UNKNOWN',
                    rationale='Insufficient public support', evidence=[]) for check in body['checks']],
                    failure_kind='UNKNOWN', rationale='No completion claim')), {'zero_cost': True})
            if failure == 'review_invalid' and request.prompt_version.startswith('public-postcondition-review.'):
                return ModelResponse('{}', {'zero_cost': True})
            return super().complete_json(request)

    monkeypatch.setattr(public_demo, '_provider', lambda config, case, runner:
                        (Provider(case, runner), public_demo.count_tokens))
    original_config = public_demo.default_config
    monkeypatch.setattr(public_demo, 'default_config', lambda case: {
        **original_config(case), 'max_rounds': max_rounds,
        **({'execution_format_retry_in_round': True} if failure == 'later_invalid' else {}),
        **({'max_total_tokens': 1} if failure == 'budget' else {})})
    if failure == 'execution_budget':
        from contractedge.model.budget import BudgetedProvider
        original_dispatch = BudgetedProvider.complete_quoted
        def dispatch(self, request, quote):
            if request.prompt_version == 'topomen-source-execution.v1':
                self.require_admission(quote, call_limit=0)
            return original_dispatch(self, request, quote)
        monkeypatch.setattr(BudgetedProvider, 'complete_quoted', dispatch)
    if cleanup or failure == 'observer':
        original_observe = public_demo.PublicExampleRunner.observe
        def observe(self, workspace, output):
            value = original_observe(self, workspace, output)
            if 'primary = "default"' not in (self.source / 'app.py').read_text():
                if failure == 'observer':
                    raise OSError('Controlled observer failure')
                if cleanup:
                    value['runtime_cleanup'] = {'status': 'UNCONFIRMED'}
            return value
        monkeypatch.setattr(public_demo.PublicExampleRunner, 'observe', observe)
    public_demo.run_demo('two-consumers', tmp_path / 'run')
    return json.loads((tmp_path / 'run/repair/repair.trace.json').read_text())


@pytest.mark.parametrize('failure', ['unknown', 'invalid', 'budget'])
def test_broken_observation_is_not_repair_execution(tmp_path, monkeypatch, failure):
    report = demo(tmp_path, monkeypatch, failure=failure)
    assert report['execution_status'] == 'NOT_RUN'
    assert report['public_status'] == 'UNKNOWN'
    assert report['initial_snapshot_id'] == report['final_snapshot_id']
    assert report['rounds'] == []
    assert report['repair_execution_facts'] == []
    scored = consistent(report)
    assert scored['precision'] is None and scored['recall'] is None
    assert scored['scoring_status'] != 'SCORED'


@pytest.mark.parametrize('failure,cleanup', [('runtime', False), (None, True), ('runtime', True)])
def test_actual_repair_process_or_cleanup_failure_is_error(tmp_path, monkeypatch, failure, cleanup):
    report = demo(tmp_path, monkeypatch, failure=failure, cleanup=cleanup)
    assert report['execution_status'] == 'ERROR'
    assert report['public_status'] == 'UNKNOWN'
    consistent(report)
    observation = json.loads((tmp_path / 'run/repair/rounds/0/public-observation.json').read_text())
    if failure == 'runtime':
        assert observation['status'] == 'EXECUTION_FAILED'
    if cleanup:
        assert observation['runtime_cleanup']['status'] == 'UNCONFIRMED'
        assert report['status'] == 'INFRASTRUCTURE_ERROR'


@pytest.mark.parametrize('failure,execution', [
    ('rejected', 'INCOMPLETE'), ('execution_budget', 'INCOMPLETE'), ('execution_unknown', 'UNKNOWN')])
def test_started_trial_without_runtime_completion_is_not_execution_error(tmp_path, monkeypatch, failure, execution):
    report = demo(tmp_path, monkeypatch, failure=failure)
    assert report['repair_execution_facts']
    assert scoring_exclusion(report, independent_repair=True) is not None
    assert report['execution_status'] == execution
    assert report['final_snapshot_id'] == report['initial_snapshot_id']
    assert not (tmp_path / 'run/repair/rounds/0/public-observation.json').exists()
    assert consistent(report)['scoring_status'] != 'SCORED'


def test_actual_observer_exception_has_error_evidence_without_inventing_observation(tmp_path, monkeypatch):
    report = demo(tmp_path, monkeypatch, failure='observer')
    assert report['execution_status'] == 'ERROR'
    row = report['repair_execution_facts'][-1]
    assert row['observation']['status'] == 'OBSERVATION_ERROR'
    event_path = tmp_path / 'run/repair' / row['observation']['event_ref']['relative_path']
    event = json.loads(event_path.read_text())
    evidence = json.loads((tmp_path / 'run/repair' / event['observation_ref']['relative_path']).read_text())
    assert evidence['observation_produced'] is False
    assert 'Controlled observer failure' in evidence['error']
    assert consistent(report)['scoring_status'] != 'SCORED'


def test_exhausted_review_format_error_is_distinct_from_valid_unknown(tmp_path, monkeypatch):
    report = demo(tmp_path, monkeypatch, failure='review_invalid')
    assert report['execution_status'] == 'COMPLETE'
    assert report['public_status'] == 'UNKNOWN'
    assert report['method_status'] == 'INVALID_OUTPUT'
    assert scoring_exclusion(report, independent_repair=True) == ('METHOD_OUTPUT', 'METHOD_FAILURE')
    assert consistent(report)['scoring_status'] == 'METHOD_FAILURE'


@pytest.mark.parametrize('public_unknown', [False, True])
def test_selected_repair_execution_completion_keeps_public_verdict_separate(tmp_path, monkeypatch, public_unknown):
    report = demo(tmp_path, monkeypatch, public_unknown=public_unknown, max_rounds=2)
    assert report['execution_status'] == 'COMPLETE'
    assert report['public_status'] == ('UNKNOWN' if public_unknown else 'PASS')
    assert report['repair_execution_facts']
    assert scoring_exclusion(report, independent_repair=True) is None
    scored = consistent(report)
    if public_unknown:
        assert report['method_status'] == 'SEMANTIC_UNKNOWN'
        assert scored['scoring_status'] == 'SEMANTIC_UNKNOWN'


@pytest.mark.parametrize('failure', ['later_unknown', 'later_invalid'])
def test_later_model_failure_does_not_erase_error_or_prior_selected_execution(tmp_path, monkeypatch, failure):
    report = demo(tmp_path, monkeypatch, failure=failure, later_failure=True, max_rounds=2)
    # The first partial candidate really ran and remains selected after the
    # unfinished second trial; that fact does not erase the later method error.
    assert report['execution_status'] == 'COMPLETE'
    assert report['method_status'] in {'INFRASTRUCTURE_ERROR', 'INVALID_OUTPUT'}
    scored = consistent(report)
    assert scored['scoring_status'] != 'SCORED'
    assert scored['precision'] is None and 'expected_relation_counts' not in scored


def test_recovery_cannot_erase_completed_attempt_failure_with_an_older_executed_patch(tmp_path, monkeypatch):
    from contractedge.orchestration.repair import repair_case
    report = demo(tmp_path, monkeypatch, failure='later_invalid', later_failure=True, max_rounds=2)
    assert report['method_status'] == 'INVALID_OUTPUT' and report['execution_status'] == 'COMPLETE'
    root = tmp_path / 'run'
    # The frozen final stage receipt is authoritative, not this mutable view.
    (root / 'repair/repair.trace.json').write_text(json.dumps({**report, 'status': 'COMPLETE',
        'failure_records': [], 'primary_failure': None}))
    class Forbidden:
        def complete_json(self, request):
            raise AssertionError('Recovery dispatched a model request')
    def recover():
        return repair_case(root / 'frozen-public/public', root / 'frozen-public', 'two-consumers',
            root / 'repair', Forbidden(), public_demo.count_tokens,
            json.loads((root / 'run.config.json').read_text()), resume=True,
            public_runner=public_demo.PublicExampleRunner(root / 'frozen-public/public', 'two-consumers'))
    recovered = recover()
    assert recovered['execution_status'] == 'COMPLETE'
    assert recovered['method_status'] == 'INVALID_OUTPUT'
    assert recovered['failure_records'] == report['failure_records']
    assert scoring_exclusion(recovered, independent_repair=True) == ('METHOD_OUTPUT', 'METHOD_FAILURE')
    assert consistent(recovered)['scoring_status'] == 'METHOD_FAILURE'
    assert recover() == recovered


@pytest.mark.parametrize('point,execution', [
    ('APPLIED', 'INCOMPLETE'), ('OBSERVED', 'INCOMPLETE'),
    ('SELECTION_INTENT', 'COMPLETE'), ('RESPONSE_UNKNOWN', 'UNKNOWN')])
def test_real_recovery_uses_trial_evidence_and_same_evaluation_projection(tmp_path, point, execution):
    from tests.test_trial_transactions import hard_exit, resume
    public = hard_exit(tmp_path, point)
    report = resume(tmp_path, public)
    assert report['execution_status'] == execution
    consistent(report)
    assert resume(tmp_path, public) == report


@pytest.mark.parametrize('trace', [
    {'status': 'COMPLETE'}, {'status': 'PUBLIC_REVIEW_COMPLETE'},
    {'status': 'PUBLIC_EXECUTION_COMPLETE', 'last_feedback': {'build_state': 'PASSED'}},
    {'status': 'COMPLETE', 'candidate': {'patch_bytes': 5000}},
    {'status': 'COMPLETE', 'initial_snapshot_id': 'old', 'final_snapshot_id': 'new'},
])
def test_legacy_completion_name_patch_or_build_flag_cannot_prove_execution(trace):
    result = project_statuses(trace)
    assert result['execution_status'] == 'UNKNOWN'
    assert result['public_status'] == 'UNKNOWN'


def test_selected_evidence_cannot_be_borrowed_by_other_snapshot_or_stale_success(tmp_path, monkeypatch):
    report = demo(tmp_path, monkeypatch, max_rounds=2)
    assert report['execution_status'] == 'COMPLETE' and report['public_status'] == 'PASS'
    for mutation in ('snapshot', 'observation', 'selection', 'execution', 'rollback', 'feedback'):
        trace = deepcopy(report)
        selected = trace['repair_execution_facts'][-1]
        if mutation == 'snapshot':
            trace['final_snapshot_id'] = 'a' * 64
        elif mutation == 'rollback':
            selected['rollback'] = {'status': 'ROLLBACK_COMPLETED'}
        elif mutation == 'feedback':
            trace['last_feedback']['to_snapshot'] = trace['initial_snapshot_id']
        else:
            selected[mutation].pop('event_ref')
        projected = project_statuses(trace)
        assert projected['public_status'] == 'UNKNOWN'
        assert score_run(trace, GT)['scoring_status'] != 'SCORED'
        if mutation != 'feedback':
            assert projected['execution_status'] != 'COMPLETE'
    for failure, method in (({'dispatch_state': 'response_unknown'}, 'RESPONSE_UNKNOWN'),
                            ({'failure_class': 'infrastructure'}, 'INFRASTRUCTURE_ERROR')):
        trace = {**report, 'primary_failure': failure}
        assert project_statuses(trace)['execution_status'] == 'COMPLETE'
        assert project_statuses(trace)['method_status'] == method
        assert score_run(trace, GT)['scoring_status'] != 'SCORED'
        assert scoring_exclusion(trace, independent_repair=True) is not None


@pytest.mark.parametrize('kind', ['linked_round', 'linked_lock', 'illegal_round', 'unexpected_file'])
def test_shared_trial_loader_rejects_unowned_paths(tmp_path, kind):
    from contractedge.orchestration.recorder import RunRecorder
    from contractedge.orchestration.trial import load_trials
    recorder = RunRecorder(tmp_path / 'run', run_id='trial-loader', input_digest='a' * 64)
    directory = recorder._parent('journal/trials/writer.lock').parent
    (directory / 'writer.lock').touch()
    assert load_trials(recorder) == []
    if kind.startswith('linked_'):
        outside = tmp_path / 'outside'
        outside.mkdir()
        path = directory / ('round-0' if kind == 'linked_round' else 'writer.lock')
        path.unlink(missing_ok=True)
        path.symlink_to(outside, target_is_directory=True)
    elif kind == 'illegal_round':
        (directory / 'round-unknown').mkdir()
    else:
        (directory / 'round-0').touch()
    with pytest.raises(ValueError):
        load_trials(recorder)
