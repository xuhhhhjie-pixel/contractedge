import importlib.util
import json
from pathlib import Path
import pytest


def api():
    assert importlib.util.find_spec('contractedge.orchestration.candidate_guard'), 'Semantic candidate guard missing'
    from contractedge.orchestration import candidate_guard
    return candidate_guard


def run(statuses,snapshot,sha='a'*64,scenario='public-case'):
    return {'snapshot_id':snapshot,'status':'EXECUTION_PASS','input_digest':'fixture-input','environment_digest':'fixture-env','observations':[
        dict(id='OBS'+str(i),snapshot_id=snapshot,kind='PUBLIC_OBSERVATION',scenario=scenario,
             excerpt=json.dumps({'id':k,'status':v}),behavior_check=dict(id=k,status=v,probe_sha256=sha))
        for i,(k,v) in enumerate(statuses.items())]}


def test_key_preservation_regression_cannot_be_offset_by_more_passes():
    before=run({'retain':'PASS','target1':'FAIL','target2':'FAIL'},'S1')
    trial=run({'retain':'FAIL','target1':'PASS','target2':'PASS'},'S2')
    result=api().compare_candidate_checks(before,trial,['retain'])
    assert result['decision']=='REJECT_REGRESSION'
    assert result['regressions'][0]['check_id']=='retain'
    assert result['from_snapshot']=='S1' and result['trial_snapshot']=='S2'


def test_intended_change_is_not_implicitly_locked_and_unknown_candidate_can_be_exported():
    before=run({'old_behavior':'PASS'},'S0');after=run({'old_behavior':'FAIL'},'S1')
    assert api().compare_candidate_checks(before,after,[])['decision']=='PROMOTE'
    assert api().compare_candidate_checks(run({},'S0'),run({},'S1'),[])['decision']=='PROMOTE'


@pytest.mark.parametrize('change',['program','scenario','missing','environment'])
def test_noncomparable_observation_never_pretends_improvement(change):
    before=run({'retain':'PASS'},'S0');trial=run({'retain':'FAIL'},'S1')
    if change=='program':trial['observations'][0]['behavior_check']['probe_sha256']='b'*64
    if change=='scenario':trial['observations'][0]['scenario']='other'
    if change=='missing':trial['observations']=[]
    if change=='environment':trial['status']='INFRA_ERROR'
    assert api().compare_candidate_checks(before,trial,['retain'])['decision']=='UNCOMPARABLE'


def test_absent_input_and_environment_identity_is_not_a_comparable_match():
    before=run({'retain':'PASS'},'S0');after=run({'retain':'FAIL'},'S1')
    for item in [before,after]:
        item.pop('input_digest');item.pop('environment_digest')
    assert api().compare_candidate_checks(before,after,['retain'])['decision']=='UNCOMPARABLE'


def test_checkpoint_restores_only_owned_files_and_survives_serialization(tmp_path):
    root=tmp_path/'source';root.mkdir();(root/'a.py').write_text('before\n');(root/'untouched').write_text('keep\n')
    checkpoint=api().checkpoint_candidate(root,['a.py','created.py'],tmp_path/'checkpoint.json')
    (root/'a.py').write_text('after\n');(root/'created.py').write_text('new\n')
    restored=api().restore_candidate(root,json.loads((tmp_path/'checkpoint.json').read_text()))
    assert restored['snapshot_id']==checkpoint['snapshot_id']
    assert (root/'a.py').read_text()=='before\n' and not (root/'created.py').exists()
    assert (root/'untouched').read_text()=='keep\n'


def test_checkpoint_refuses_symlinks_and_tampered_backup(tmp_path):
    root=tmp_path/'source';root.mkdir();(root/'a').write_text('safe');(root/'link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError):api().checkpoint_candidate(root,['link'],tmp_path/'cp.json')
    cp=api().checkpoint_candidate(root,['a'],tmp_path/'cp.json');cp['files']['a']['data']='corrupt'
    with pytest.raises(ValueError):api().restore_candidate(root,cp)


@pytest.mark.parametrize('review_transport_error',[False,True])
def test_real_loop_exports_incumbent_and_its_feedback_after_regression(tmp_path,review_transport_error):
    from contractedge.orchestration.repair import repair_case
    from contractedge.model.provider import ModelResponse
    from contractedge.execution.topomen import apply_edits
    from tests.test_benchmark_current import make_public_fixture
    from tests.test_orchestration import PipelineFixtureProvider
    from tests.test_repair_execution import PublicLoopRunner, current_public_citation
    public,_=make_public_fixture(tmp_path/'benchmark')
    class Provider(PipelineFixtureProvider):
        def complete_json(self,request):
            from contractedge.dataset.benchmark import file_sha
            self._snapshot_digests={'templates/serviceaccount.yaml':file_sha(tmp_path/'run/workspace/source/repo/templates/serviceaccount.yaml')}
            response=super().complete_json(request)
            if not request.prompt_version.startswith('changeset.'):return response
            body=json.loads(response.content);p=body['postconditions'][0]
            body['postconditions']=[dict(p,id='P1',statement='Preserve already corrected namespace'),dict(p,id='P2',statement='Fix requested name')]
            body['changes'][0]['postcondition_refs']=['P1','P2']
            return ModelResponse(json.dumps(body),{})
    class Executor:
        calls=0
        def execute(self,plan,source,policy,**kw):
            self.calls+=1
            old,new=('name: default','name: intermediate') if self.calls==1 else ('name: intermediate','name: regressed')
            return apply_edits(plan,source,policy,{'edits':[dict(change_id='C1',path='templates/serviceaccount.yaml',operation='MODIFY',old_text=old,new_text=new)]})
    class Reviewer:
        calls=0
        def review(self,requirement,plan,observation):
            self.calls+=1
            if self.calls==2 and review_transport_error:raise RuntimeError('review transport interrupted')
            return dict(decision='FAIL',failure_kind='EXECUTION',checks=[dict(id=key,status=status,evidence=current_public_citation(observation))
                for key,status in [('P1','PASS' if self.calls==1 else 'FAIL'),('P2','FAIL' if self.calls==1 else 'PASS')]])
    report=repair_case(public,tmp_path,'CASE-1',tmp_path/'run',Provider(),lambda t:len(t.split()),
        dict(model='fixture',max_context_tokens=12000,max_output_tokens=2048,max_rounds=2,candidate_guard_enabled=True),
        public_runner=PublicLoopRunner(public,raw_only=True),executor=Executor(),reviewer=Reviewer())
    patch=Path(report['candidate']['patch_path']).read_text()
    assert '+  name: intermediate' in patch and '+  name: regressed' not in patch
    assert report['last_feedback']['to_snapshot']==report['final_snapshot_id']
    assert report['candidate_selection']['rejected_trials']==1
    assert (tmp_path/'run/rounds/1/candidate.patch').read_text()!=patch


@pytest.mark.parametrize('failure,rounds,expected_calls',[('check',2,2),('startup_panic',2,2),('unclassified',2,1),('stale_panic',2,1),('startup_panic',1,1),('unpreserved_probe',2,1)])
def test_first_trial_regression_can_revise_from_restored_source_within_two_rounds(tmp_path,failure,rounds,expected_calls):
    from contractedge.orchestration.repair import repair_case
    from contractedge.model.provider import ModelResponse
    from contractedge.execution.topomen import apply_edits
    from tests.test_benchmark_current import make_public_fixture
    from tests.test_orchestration import PipelineFixtureProvider
    from tests.test_repair_execution import PublicLoopRunner, current_public_citation
    from contractedge.dataset.benchmark import file_sha
    public,_=make_public_fixture(tmp_path/'benchmark')
    class Provider(PipelineFixtureProvider):
        def complete_json(self,request):
            self._snapshot_digests={'templates/serviceaccount.yaml':file_sha(tmp_path/'run/workspace/source/repo/templates/serviceaccount.yaml')}
            response=super().complete_json(request)
            if not request.prompt_version.startswith('changeset.'):return response
            body=json.loads(response.content)
            body['preserve_constraints']=[dict(id='S1',outcome_id='O1',obligation_refs=['A1'],
                statement='Metadata remains valid',current_satisfaction='OBSERVED',
                evidence_refs=[self.diagnosis['evidence_refs'][0]],behavior_check_refs=['retain'],
                check_coverage='ALL_OF',coverage_rationale='Checks valid metadata',verification_kind='PUBLIC_BEHAVIOR')]
            if failure=='unpreserved_probe':body['preserve_constraints']=[]
            return ModelResponse(json.dumps(body),{})
    class Runner(PublicLoopRunner):
        def observe(self,workspace,output):
            result=super().observe(workspace,output)
            state='FAIL' if 'regressed' in result['observations'][0]['excerpt'] else 'PASS'
            if state=='FAIL' and failure!='check':
                result.update(status='EXECUTION_FAILED' if failure=='startup_panic' else 'EXECUTION_PASS',method_probe_status='PROBE_ERROR')
                if failure in {'startup_panic','stale_panic'}:
                    result['status']='EXECUTION_FAILED'
                    result['candidate_runtime_failures']=[dict(operation='PRODUCTION_PROCESS_START',status='FAILED',snapshot_id=result['snapshot_id'],source_locations=[dict(path='templates/serviceaccount.yaml',line=1)],stderr_excerpt='panic: source fixture startup failed',stderr_sha256='c'*64)]
                    if failure=='stale_panic':result['candidate_runtime_failures'][0]['snapshot_id']='stale'
                return result
            result['observations'].append(dict(result['observations'][0],id='OBS2',evidence_kind='PUBLIC_BEHAVIOR',
                behavior_check=dict(id='retain',status=state,probe_sha256='a'*64)))
            return result
    class Executor:
        calls=0
        def execute(self,plan,source,policy,**kw):
            self.calls+=1
            if self.calls==2:
                rejected=kw['previous_attempt']['rejected_trial']
                assert rejected['snapshot_id']!=plan['snapshot_id']
                if failure=='startup_panic':
                    assert 'panic: source fixture' in rejected['feedback']['failure_tasks']['shared_execution_errors'][0]['actual_error']
                assert 'regressed' not in (source/'templates/serviceaccount.yaml').read_text()
            return apply_edits(plan,source,policy,{'edits':[dict(change_id='C1',path='templates/serviceaccount.yaml',
                operation='MODIFY',old_text='name: default',new_text='name: regressed' if self.calls==1 else 'name: selected')]})
    class Reviewer:
        def review(self,requirement,plan,observation):
            return dict(decision='UNKNOWN',checks=[])
    executor=Executor()
    report=repair_case(public,tmp_path,'CASE-1',tmp_path/'run',Provider(),lambda t:len(t.split()),
        dict(model='fixture',max_context_tokens=12000,max_output_tokens=2048,max_rounds=rounds,candidate_guard_enabled=True),
        public_runner=Runner(public,raw_only=True),executor=executor,reviewer=Reviewer())
    assert executor.calls==expected_calls,report['errors']
    patch=Path(report['candidate']['patch_path']).read_text()
    if expected_calls==2:
        assert '+  name: selected' in patch
        assert report['last_feedback']['to_snapshot']==report['final_snapshot_id']
    else:
        assert 'regressed' not in patch and 'selected' not in patch
    assert report['candidate_selection']['rejected_trials']==1


@pytest.mark.parametrize('after,decision,phrase',[('FAIL','REJECT_REGRESSION','regressed'),(None,'UNCOMPARABLE','could not be compared'),('PASS','PROMOTE','not semantic correctness')])
def test_candidate_decision_explanation_agrees_with_actual_observation(after,decision,phrase):
    before=run({'retain':'PASS'},'S0');trial=run({} if after is None else {'retain':after},'S1')
    result=api().compare_candidate_checks(before,trial,['retain'])
    assert result['decision']==decision
    assert phrase in result['interpretation']
    if decision!='PROMOTE':assert 'No known comparable preservation regression' not in result['interpretation']
