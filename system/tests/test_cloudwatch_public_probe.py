from pathlib import Path
from contractedge.execution.probe_specs import select_probes,registered_programs
from contractedge.execution.probes import probe_command


def test_registered_checks_reach_review_feedback_and_regression_guard(tmp_path):
    """Exercise the common pipeline, without depending on server replay files."""
    import hashlib
    from contractedge.execution.probes import behavior_records
    from contractedge.execution.review import ModelPublicReviewer
    from contractedge.orchestration.feedback import make_feedback
    from contractedge.orchestration.candidate_guard import compare_candidate_checks, preservation_check_ids
    program, checks = registered_programs()['cloudwatch-tags-v1']
    probe_sha = hashlib.sha256(program.encode()).hexdigest()

    def observed(snapshot, preservation_status):
        directory = tmp_path / snapshot
        (directory / 'cloudwatch-tags-v1').mkdir(parents=True)
        report = dict(status='COMPLETE', snapshot_id=snapshot, probes=[dict(
            probe_id='cloudwatch-tags-v1', status='COMPLETE', probe_sha256=probe_sha,
            scenario='method-cloudwatch-tags-v1', boundary='Local SDK IO only',
            checks=[dict(id=key, status=preservation_status if key == 'unchanged_other_field' else 'PASS',
                         statement=value, observed={'scenario':key}) for key, value in checks.items()])])
        records = behavior_records(report, directory)
        observations = [dict(id='OBS'+str(i), kind='PUBLIC_OBSERVATION', snapshot_id=snapshot,
            excerpt=path.read_text(), locator={'path':path.name},
            content_digest=hashlib.sha256(path.read_bytes()).hexdigest(), **meta)
            for i, (path, meta) in enumerate(records.items())]
        return dict(status='EXECUTION_PASS', snapshot_id=snapshot, input_digest='a'*64,
                    environment_digest='environment', observations=observations)

    incumbent, trial = observed('S0', 'PASS'), observed('S1', 'FAIL')
    preservation = dict(id='S1', outcome_id='O1', statement='Non-tag updates preserve the external tag set',
        verification_kind='PUBLIC_BEHAVIOR', verification_scope={'scenario':'method-cloudwatch-tags-v1'},
        behavior_check_refs=['cloudwatch-tags-v1:unchanged_other_field'], check_coverage='ALL_OF',
        coverage_rationale='This predicate is limited to the fixed non-tag update scenario')
    plan = dict(status='READY', changes=[dict(id='C1', postcondition_refs=['P1'])],
        postconditions=[dict(id='P1', statement='Changed tag value is applied', verification_kind='PUBLIC_BEHAVIOR',
            behavior_check_refs=['cloudwatch-tags-v1:value_change'], check_coverage='ALL_OF')],
        preserve_constraints=[preservation], open_obligations=[])

    class NoModel:
        def complete_json(self, request):
            raise AssertionError('Bound checks must be aggregated without another model call')

    review = ModelPublicReviewer(NoModel(), len, {}).review('Public predicates', plan, trial)
    assert review['decision'] == 'FAIL'
    assert {c['id']:c['status'] for c in review['checks']} == {'P1':'PASS','preserve:S1':'FAIL'}
    feedback = make_feedback(case_id='public', run_id='run', input_digest='a'*64, round_id=0,
        from_snapshot='S0', to_snapshot='S1', patch_digest='b'*64, execution_status='EXECUTION_PASS',
        observations=trial['observations'], plan=plan, review=review)
    assert feedback['semantic_outcome'] == 'PUBLIC_REVIEW_FAIL'
    assert any(p['id'] == 'preserve:S1' for p in feedback['pending_obligations'])
    assert compare_candidate_checks(incumbent, trial, preservation_check_ids(plan))['decision'] == 'REJECT_REGRESSION'


def test_incomplete_cloudwatch_probe_cannot_create_behavior_evidence(tmp_path):
    from contractedge.execution.probes import behavior_records
    assert behavior_records(dict(status='PROBE_ERROR', probes=[]), tmp_path) == {}


def test_registered_cloudwatch_probe_is_requirement_and_source_selected(tmp_path):
    p=tmp_path/'pkg/resource/metric_alarm';p.mkdir(parents=True);(p/'manager.go').write_text('package metric_alarm');(p/'sdk.go').write_text('package metric_alarm')
    result=select_probes('MetricAlarm tag additions, value changes and removals must converge; unchanged tags remain intact.',tmp_path,['cloudwatch-tags-v1'])
    assert len(result)==1 and result[0]['id']=='cloudwatch-tags-v1'
    assert result[0]['api_entrypoints'][0]['symbol']=='Update'
    assert {'addition','value_change','removal','remove_all','unchanged','unchanged_other_field','put_error','tag_error'}==set(result[0]['checks'])
    assert select_probes('Render unrelated configuration',tmp_path,['cloudwatch-tags-v1'])==[]


def test_command_uses_readonly_go_overlay_and_offline_production_test():
    command=' '.join(probe_command('cloudwatch-tags-v1'))
    assert '-overlay=/method-probe/overlay.json' in command
    assert './pkg/resource/metric_alarm' in command and 'GOPROXY=off' in command
    assert 'TestContractEdgePublicTagUpdate' in command
