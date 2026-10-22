import json
import hashlib
import tempfile
from pathlib import Path

from contractedge.orchestration.feedback import make_feedback
from tests.test_observation_capability_gate import record, claim, plan


def behavior_feedback(*, round_id=0, changed=True, bound=True, status='FAIL'):
    observation = record('{"status":"FAIL"}', 'PUBLIC_BEHAVIOR', 'method-probe')
    observation['behavior_check'] = {'id':'probe:keep', 'status':'FAIL', 'probe_sha256':'a'*64}
    p = plan('PUBLIC_BEHAVIOR', **({'behavior_check_refs':['probe:keep']} if bound else {}))
    p.update(status='READY', changes=[{'id':'C1', 'postcondition_refs':['P']}])
    c = claim('FAIL'); c['status'] = status
    return make_feedback(case_id='CASE',run_id='run',input_digest='a'*64,round_id=round_id,
        from_snapshot='S0' if changed else 'S1',to_snapshot='S1',patch_digest='b'*64,
        execution_status='EXECUTION_PASS',observations=[observation],plan=p,
        review={'decision':'FAIL','checks':[c],'failure_kind':'PLAN','rationale':'Postcondition failed, therefore plan failure'})


def test_bound_behavior_failure_first_preserves_accepted_intent_for_execution():
    f = behavior_feedback()
    assert f['semantic_outcome'] == 'PUBLIC_REVIEW_FAIL'
    assert f['route'] == 'EXECUTE'
    assert f['route_confidence'] == 'LOW'
    assert f['failure_kind'] == 'PLAN'  # Preserve submitted uncertain attribution.
    assert 'not proof' in f['rationale']
    assert len(f['pending_obligations']) == 1


def test_execution_preference_does_not_create_unbounded_or_unsupported_retries():
    assert behavior_feedback(round_id=1)['route'] == 'CHANGESET'
    assert behavior_feedback(changed=False)['route'] == 'CHANGESET'
    assert behavior_feedback(bound=False)['route'] == 'STOP'
    assert behavior_feedback(status='UNKNOWN')['route'] == 'STOP'


def test_failed_rediagnosis_preserves_inner_validation_errors_in_repair_trace():
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.repair import repair_case
    from tests.test_benchmark_current import make_public_fixture
    from tests.test_orchestration import PipelineFixtureProvider
    from tests.test_repair_execution import PublicLoopRunner, TwoStepExecutor, current_public_citation
    class InvalidSecondDiagnosis(PipelineFixtureProvider):
        diagnoses = 0
        def complete_json(self, request):
            if request.prompt_version.startswith('diagnosis.'):
                self.diagnoses += 1
                if self.diagnoses > 1:
                    return ModelResponse('not-json', {})
            return super().complete_json(request)
    class Reviewer:
        def review(self, requirement, p, observation):
            return {'decision':'FAIL','failure_kind':'PLAN','checks':[
                {'id':'P1','status':'FAIL','evidence':current_public_citation(observation)}]}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); public,_ = make_public_fixture(root/'benchmark')
        provider = InvalidSecondDiagnosis()
        provider._snapshot_digests = {'templates/serviceaccount.yaml':hashlib.sha256(
            b'apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n').hexdigest()}
        result = repair_case(public,root,'CASE-1',root/'run',provider,lambda t:len(t.split()),
            {'model':'fixture','max_context_tokens':12000,'max_output_tokens':2048,'max_rounds':2},
            public_runner=PublicLoopRunner(public,raw_only=True),executor=TwoStepExecutor(),reviewer=Reviewer())
        assert result['status'] == 'INVALID_OUTPUT', result['errors']
        front = json.loads((root/'run/rounds/1/front.trace.json').read_text())
        assert front['errors']
        assert result['errors'] == front['errors']


def test_mechanical_failure_can_try_accepted_intent_once_without_claiming_root_cause():
    f=behavior_feedback()
    from contractedge.orchestration.feedback import make_feedback
    obs=f['public_observations']
    plan={'status':'READY','changes':[{'id':'C1','postcondition_refs':['P']}],
      'postconditions':[{'id':'P','statement':'Retain config','behavior_check_refs':['probe:keep']}],
      'preserve_constraints':[],'open_obligations':[]}
    review={'method':'BOUND_CHECK_AGGREGATION','decision':'FAIL','failure_kind':'UNKNOWN','checks':f['public_check_results']}
    r=make_feedback(case_id=f['case_id'],run_id=f['run_id'],input_digest=f['input_digest'],round_id=0,
      from_snapshot=f['from_snapshot'],to_snapshot=f['to_snapshot'],patch_digest=f['patch_digest'],
      execution_status='EXECUTION_PASS',observations=obs,plan=plan,review=review)
    assert r['route']=='EXECUTE'
    assert r['failure_kind']=='UNKNOWN'
    assert r['routing_policy']=='BOUNDED_ACCEPTED_INTENT_RETRY'
