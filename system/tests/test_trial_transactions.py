"""Hard process exits against repair_case; no Docker, network, or hidden inputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot
from tests.test_benchmark_current import make_public_fixture
from tests.test_repair_execution import PublicLoopRunner


CONFIG=dict(model='fixture',max_context_tokens=12000,max_output_tokens=2048,max_rounds=1,
    candidate_guard_enabled=True,max_format_retries=1,run_id='trial-hard-exit-fixture')

CHILD='''
import json,os,sys
from pathlib import Path
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration.trial import TrialSession
from contractedge.model.provider import ModelResponse
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_repair_execution import PublicLoopRunner,AcceptPublicReview
root=Path(sys.argv[1]);kind=sys.argv[2];config=json.loads(sys.argv[3])
class Provider(PipelineFixtureProvider):
    def complete_json(self,request):
        from contractedge.dataset.benchmark import file_sha
        self._snapshot_digests={'templates/serviceaccount.yaml':file_sha(root/'run/workspace/source/repo/templates/serviceaccount.yaml')}
        if request.prompt_version=='topomen-source-execution.v1':
            if kind=='RESPONSE_UNKNOWN':os._exit(73)
            return ModelResponse(json.dumps({'edits':[{'change_id':'C1','path':'templates/serviceaccount.yaml',
                'operation':'MODIFY','old_text':'name: default','new_text':'name: selected'}]}),{})
        return super().complete_json(request)
original=TrialSession.record
def record(self,event,**payload):
    result=original(self,event,**payload)
    if event==kind:os._exit(73)
    return result
TrialSession.record=record
if kind=='BEFORE_SELECTION':
    def stop(*args,**kwargs):os._exit(73)
    TrialSession.select=stop
if kind=='UNKNOWN_SELECTED':
    class Reviewer:
        def review(self,*args,**kwargs):return {'decision':'UNKNOWN','checks':[],'failure_kind':'UNKNOWN','method':'fixture'}
    reviewer=Reviewer()
else:reviewer=AcceptPublicReview()
repair_case(root/'benchmark/public',root,'CASE-1',root/'run',Provider(),lambda t:len(t.split()),config,
    public_runner=PublicLoopRunner(root/'benchmark/public'),reviewer=reviewer)
'''


def hard_exit(tmp_path,kind):
    public,_=make_public_fixture(tmp_path/'benchmark')
    # Fixture path is deliberately obtained from the actual public producer.
    program=CHILD.replace("root/'benchmark/public'",repr(public.as_posix()))
    program=program.replace("public_runner=PublicLoopRunner("+repr(public.as_posix())+")",
        "public_runner=PublicLoopRunner(Path("+repr(public.as_posix())+"))")
    script=tmp_path/'child.py';script.write_text(program)
    result=subprocess.run([sys.executable,str(script),str(tmp_path),kind,json.dumps(CONFIG)],text=True,capture_output=True)
    if kind=='UNKNOWN_SELECTED': assert result.returncode==0,result.stderr
    else: assert result.returncode==73,result.stderr+result.stdout
    return public


def resume(tmp_path,public,**kwargs):
    class Forbidden:
        def complete_json(self,*args):raise AssertionError('Recovery dispatched a model request')
    return repair_case(public,tmp_path,'CASE-1',tmp_path/'run',Forbidden(),lambda t:len(t.split()),CONFIG,
        public_runner=PublicLoopRunner(public),resume=True,**kwargs)


def events(tmp_path):
    return [json.loads(path.read_bytes()) for path in sorted((tmp_path/'run/journal/trials/round-0').glob('[0-9]*.json'))]


@pytest.mark.parametrize('point',['APPLIED','OBSERVED','BEFORE_SELECTION'])
def test_hard_exit_recovers_incumbent_without_dispatch_edit_or_budget_reset(tmp_path,point):
    public=hard_exit(tmp_path,point)
    ledger_before=(tmp_path/'run/model_calls.json').read_bytes()
    initial=json.loads((tmp_path/'run/journal/recovery.identity.json').read_bytes())['initial_snapshot']
    report=resume(tmp_path,public)
    assert report['status']=='RECOVERED_INCUMBENT' and report['final_snapshot_id']==initial
    assert report['candidate']['patch_bytes']==0 and report['last_feedback'] is None
    assert report['recovery']['terminal'] and not report['recovery']['continuation_allowed']
    assert report['budget']['model_calls']==len(json.loads(ledger_before))
    assert report['budget']['reserved_tokens']>0
    state=json.loads((tmp_path/'run/state.json').read_bytes())
    assert state['current_snapshot']==report['final_snapshot_id'] and state['selected']['snapshot_id']==report['final_snapshot_id']
    assert (tmp_path/'run/model_calls.json').read_bytes()==ledger_before
    before=len(events(tmp_path));again=resume(tmp_path,public)
    assert again==report and len(events(tmp_path))==before
    assert sum(event['kind']=='ROLLBACK_COMPLETED' for event in events(tmp_path))==1


@pytest.mark.parametrize('point',['SELECTION_INTENT','SELECTION_COMPLETED'])
def test_complete_selection_decision_finishes_same_selected_patch_and_feedback(tmp_path,point):
    public=hard_exit(tmp_path,point)
    selected=next(event for event in events(tmp_path) if event['kind']=='SELECTION_INTENT')
    report=resume(tmp_path,public)
    assert report['status']=='RECOVERED_TRIAL'
    assert report['final_snapshot_id']==selected['selected_snapshot']
    assert report['last_feedback']['to_snapshot']==report['final_snapshot_id']
    assert report['candidate']['patch_sha256']==selected['patch_sha256']
    assert '+  name: selected' in Path(report['candidate']['patch_path']).read_text()
    assert sum(event['kind']=='SELECTION_COMPLETED' for event in events(tmp_path))==1


def test_unknown_dispatch_retains_original_reservation_and_never_resends(tmp_path):
    public=hard_exit(tmp_path,'RESPONSE_UNKNOWN')
    original=json.loads((tmp_path/'run/model_calls.json').read_bytes())
    report=resume(tmp_path,public,consume_received_response=True)
    assert report['status']=='RESPONSE_UNKNOWN'
    assert report['recovery']['unknown_reservations_retained'] and report['candidate']['patch_bytes']==0
    assert report['budget']['reserved_tokens']==sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in original)
    assert json.loads((tmp_path/'run/model_calls.json').read_bytes())==original


def test_received_response_can_be_consumed_once_then_incomplete_trial_restores_incumbent(tmp_path):
    public=hard_exit(tmp_path,'RESPONSE_DURABLE')
    ledger=(tmp_path/'run/model_calls.json').read_bytes()
    report=resume(tmp_path,public,consume_received_response=True)
    assert report['candidate']['patch_bytes']==0 and report['status']=='RECOVERED_INCUMBENT'
    kinds=[event['kind'] for event in events(tmp_path)]
    assert kinds.count('RESPONSE_CONSUME_INTENT')==kinds.count('RESPONSE_CONSUME_COMPLETED')==1
    assert kinds.count('APPLIED')==1 and kinds.count('ROLLBACK_COMPLETED')==1
    assert resume(tmp_path,public,consume_received_response=True)==report
    assert (tmp_path/'run/model_calls.json').read_bytes()==ledger


@pytest.mark.parametrize('change',['owned-source','other-source','config','plan','checkpoint','input','code','ledger'])
def test_external_identity_changes_refuse_recovery_without_overwriting_workspace(tmp_path,change,monkeypatch):
    public=hard_exit(tmp_path,'APPLIED')
    source=tmp_path/'run/workspace/source/repo'
    kwargs={}
    if change=='owned-source': (source/'templates/serviceaccount.yaml').write_text('external source')
    elif change=='other-source': (source/'new.txt').write_text('external source')
    elif change=='config':
        monkeypatch.setitem(CONFIG,'max_rounds',2)
    elif change=='plan':
        path=tmp_path/'run/rounds/0/plan.json';path.write_text('{}')
    elif change=='checkpoint':
        path=tmp_path/'run/journal/trials/round-0/checkpoint.json';path.write_text('{}')
    elif change=='input':
        (tmp_path/'run/method_input.json').write_text('{}')
    elif change=='code':
        monkeypatch.setattr('contractedge.orchestration.trial.method_identity',lambda:'changed-code')
    elif change=='ledger':
        path=tmp_path/'run/journal/ledger/segment-000000.inflight.json'
        value=json.loads(path.read_text());value['calls'][0]['model']='external-model';path.write_text(json.dumps(value))
    before=compute_snapshot(source).snapshot_id
    with pytest.raises(ValueError):resume(tmp_path,public,**kwargs)
    assert compute_snapshot(source).snapshot_id==before
    assert not (tmp_path/'run/journal/recovery.completed.json').exists()


def test_previous_rollback_failure_prohibits_export(tmp_path,monkeypatch):
    public=hard_exit(tmp_path,'APPLIED')
    def fail(*args):raise OSError('fixture restore failure')
    monkeypatch.setattr('contractedge.orchestration.resume.restore_candidate',fail)
    with pytest.raises(ValueError,match='rollback failed'):resume(tmp_path,public)
    monkeypatch.undo()
    with pytest.raises(ValueError,match='Prior rollback failed'):resume(tmp_path,public)
    assert not (tmp_path/'run/candidate.patch').exists()


def test_unknown_candidate_remains_exportable_without_semantic_pass(tmp_path):
    public=hard_exit(tmp_path,'UNKNOWN_SELECTED')
    report=resume(tmp_path,public)
    assert report['exportable'] and report['candidate']['patch_bytes']>0
    assert report['public_semantic_status']=='UNKNOWN'


def test_c1_crash_lock_is_preserved_and_recovery_fails_closed_without_overwriting_source(tmp_path):
    public=hard_exit(tmp_path,'APPLIED')
    lock=tmp_path/'run/journal/transitions/writer.lock';lock.write_text('unverified owner')
    source=tmp_path/'run/workspace/source/repo';before=compute_snapshot(source).snapshot_id
    with pytest.raises(ValueError,match='Unresolved transition writer lock'):resume(tmp_path,public)
    assert lock.read_text()=='unverified owner' and compute_snapshot(source).snapshot_id==before


def test_live_process_lease_refuses_concurrent_recovery_without_touching_source(tmp_path):
    public=hard_exit(tmp_path,'APPLIED')
    from contractedge.orchestration.trial import run_lease
    source=tmp_path/'run/workspace/source/repo';before=compute_snapshot(source).snapshot_id
    with run_lease(tmp_path/'run'):
        with pytest.raises(ValueError,match='live writer'):resume(tmp_path,public)
    assert compute_snapshot(source).snapshot_id==before


def test_policy_consumption_is_restored_without_new_allowances(tmp_path):
    public=hard_exit(tmp_path,'APPLIED')
    from contractedge.orchestration.trial import atomic_json
    usage=[{'policy':'format_correction','scope':'execution:case','used':1,'limit':1}]
    atomic_json(tmp_path/'run/journal/policy-usage.json',usage)
    report=resume(tmp_path,public)
    assert report['policy_usage']==usage and report['recovery']['budget_reinitialized'] is False


def test_consumption_intent_without_edit_does_not_repeat_response(tmp_path):
    public=hard_exit(tmp_path,'RESPONSE_CONSUME_INTENT')
    report=resume(tmp_path,public,consume_received_response=True)
    assert report['candidate']['patch_bytes']==0
    kinds=[event['kind'] for event in events(tmp_path)]
    assert kinds.count('RESPONSE_CONSUME_INTENT')==1 and kinds.count('APPLIED')==0


def test_edit_intent_after_durable_response_is_rolled_back_without_duplicate_edit(tmp_path):
    public=hard_exit(tmp_path,'RESPONSE_CONSUME_COMPLETED')
    report=resume(tmp_path,public,consume_received_response=True)
    assert report['candidate']['patch_bytes']==0
    kinds=[event['kind'] for event in events(tmp_path)]
    assert kinds.count('RESPONSE_CONSUME_COMPLETED')==1 and kinds.count('APPLIED')==0


def test_production_cli_resume_does_not_construct_provider_or_probe_model(tmp_path,monkeypatch):
    monkeypatch.setitem(CONFIG,'ollama_model_digest','frozen-fixture-no-network')
    public=hard_exit(tmp_path,'APPLIED')
    from contractedge import cli
    def forbidden(*args,**kwargs):raise AssertionError('Recovery constructed/probed a model provider')
    monkeypatch.setattr(cli,'_build_provider',forbidden)
    monkeypatch.setattr(cli,'_verify_ollama_model',forbidden)
    monkeypatch.setattr('contractedge.execution.public.BenchmarkPublicRunner',lambda *args,**kwargs:PublicLoopRunner(public))
    path=tmp_path/'config.json';path.write_text(json.dumps(CONFIG))
    assert cli.main(['repair','--public-root',str(public),'--tools-root',str(tmp_path),
        '--case-id','CASE-1','--config',str(path),'--output',str(tmp_path/'run'),'--resume-output'])==0


def test_real_native_default_agent_hard_exit_uses_same_candidate_recovery_boundary(tmp_path,monkeypatch):
    from tests.test_topomen_agent import AVAILABLE,native_config
    if not AVAILABLE:pytest.skip('Vendored TopoMen optional dependencies unavailable')
    for key,value in native_config().items():monkeypatch.setitem(CONFIG,key,value)
    public=hard_exit(tmp_path,'APPLIED')
    applied=next(event for event in events(tmp_path) if event['kind']=='APPLIED')
    assert applied['execution']['backend']['agent_class']=='topomen.collaboration.base_agent.DefaultAgent'
    ledger=(tmp_path/'run/model_calls.json').read_bytes()
    with pytest.raises(ValueError,match='Native TopoMen.*unsupported'):
        resume(tmp_path,public,consume_received_response=True)
    assert (tmp_path/'run/model_calls.json').read_bytes()==ledger
    report=resume(tmp_path,public)
    assert report['status']=='RECOVERED_INCUMBENT' and report['candidate']['patch_bytes']==0
    assert report['last_feedback'] is None and report['budget']['model_calls']==3
    assert resume(tmp_path,public)==report


def test_published_config_binding_native_default_agent_hard_exit_restores_selected_source_patch_feedback(tmp_path):
    from tests.test_topomen_agent import AVAILABLE
    if not AVAILABLE:pytest.skip('Vendored TopoMen optional dependencies unavailable')
    from contractedge.development.public_demo import freeze_example,default_config,PublicExampleRunner
    public=freeze_example('config-binding',tmp_path)
    config={**default_config('config-binding'),'max_rounds':1,'run_id':'native-config-binding-hard-exit'}
    script=tmp_path/'native-child.py'
    script.write_text('''import json,os,sys
from pathlib import Path
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration.trial import TrialSession
from contractedge.development.public_demo import PublicExampleRunner,SyntheticExampleProvider,count_tokens
root=Path(sys.argv[1]);config=json.loads(sys.argv[2]);public=root/'frozen-public/public'
runner=PublicExampleRunner(public,'config-binding');original=TrialSession.record
def record(self,kind,**payload):
 event=original(self,kind,**payload)
 if kind=='APPLIED':os._exit(73)
 return event
TrialSession.record=record
repair_case(public,root/'frozen-public','config-binding',root/'run',SyntheticExampleProvider('config-binding',runner),count_tokens,config,public_runner=runner)
''')
    result=subprocess.run([sys.executable,str(script),str(tmp_path),json.dumps(config)],capture_output=True,text=True)
    assert result.returncode==73,result.stderr+result.stdout
    applied=next(event for event in events(tmp_path) if event['kind']=='APPLIED')
    assert applied['execution']['backend']['agent_class']=='topomen.collaboration.base_agent.DefaultAgent'
    initial=events(tmp_path)[0]
    checks=[json.loads(row['excerpt']) for row in initial['incumbent_observation']['observations']]
    assert next(row for row in checks if row['id']=='selected')['status']=='FAIL'
    assert all(row['status']=='PASS' for row in checks if row['id'] in {'disabled','stable'})
    ledger=(tmp_path/'run/model_calls.json').read_bytes()
    report=repair_case(public,tmp_path/'frozen-public','config-binding',tmp_path/'run',None,None,config,
        public_runner=PublicExampleRunner(public,'config-binding'),resume=True)
    assert report['final_snapshot_id']==initial['parent_snapshot']
    assert report['candidate']['patch_bytes']==0 and report['last_feedback'] is None
    assert json.loads((tmp_path/'run/selected-candidate.json').read_text())['snapshot_id']==report['final_snapshot_id']
    assert (tmp_path/'run/model_calls.json').read_bytes()==ledger


@pytest.mark.parametrize('point',['SELECTION_INTENT','SELECTION_COMPLETED'])
@pytest.mark.parametrize('cleanup',['CONFIRMED','UNKNOWN'])
def test_recovery_rechecks_isolated_cleanup_before_completing_selection(tmp_path,point,cleanup):
    from tests.test_topomen_agent import AVAILABLE
    if not AVAILABLE:pytest.skip('Vendored TopoMen optional dependencies unavailable')
    from contractedge.development.public_demo import freeze_example,default_config,IsolatedPublicExampleRunner
    from tests.test_public_isolated_demo import MOCK_RUNTIME
    public=freeze_example('config-binding',tmp_path,runtime='isolated')
    config={**default_config('config-binding'),'max_rounds':1,'run_id':'isolated-selection-hard-exit',
        'public_runtime':'isolated','public_runtime_config':MOCK_RUNTIME}
    script=tmp_path/'isolated-child.py'
    script.write_text('''import json,os,sys
from pathlib import Path
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration.trial import TrialSession
from contractedge.development.public_demo import IsolatedPublicExampleRunner,SyntheticExampleProvider,count_tokens
from contractedge.execution import public_observer
from tests.test_public_isolated_demo import BoundarySessions,MOCK_RUNTIME
root=Path(sys.argv[1]);config=json.loads(sys.argv[2]);point=sys.argv[3];public=root/'frozen-public/public'
boundary=BoundarySessions(check_sequences=[{'selected':'FAIL','disabled':'PASS','stable':'PASS'},
 {'selected':'PASS','disabled':'PASS','stable':'PASS'}])
public_observer.RuntimeSession.open=boundary.open
runner=IsolatedPublicExampleRunner(public,'config-binding',MOCK_RUNTIME);original=TrialSession.record
def record(self,kind,**payload):
 event=original(self,kind,**payload)
 if kind==point:os._exit(73)
 return event
TrialSession.record=record
repair_case(public,root/'frozen-public','config-binding',root/'run',SyntheticExampleProvider('config-binding',runner),count_tokens,config,public_runner=runner)
''')
    result=subprocess.run([sys.executable,str(script),str(tmp_path),json.dumps(config),point],capture_output=True,text=True)
    assert result.returncode==73,result.stderr+result.stdout
    if cleanup=='UNKNOWN':
        from contractedge.orchestration.trial import atomic_json,sha
        from contractedge.contracts.validation import canonical_digest
        previous=None
        for path in sorted((tmp_path/'run/journal/trials/round-0').glob('[0-9]*.json')):
            value=json.loads(path.read_bytes());value['previous_sha256']=previous
            if value['kind']=='OBSERVED':
                value['observation']['runtime']['candidate']['cleanup']['status']='UNKNOWN'
                value['observation_digest']=canonical_digest(value['observation'])
            atomic_json(path,value);previous=sha(path)
    runner=IsolatedPublicExampleRunner(public,'config-binding',MOCK_RUNTIME)
    def recover():return repair_case(public,tmp_path/'frozen-public','config-binding',tmp_path/'run',None,None,
        config,public_runner=runner,resume=True)
    if cleanup=='UNKNOWN':
        with pytest.raises(ValueError,match='observation/cleanup'):recover()
        assert not (tmp_path/'run/candidate.patch').exists()
    else:
        report=recover()
        assert report['candidate']['patch_bytes']>0 and report['last_feedback']['to_snapshot']==report['final_snapshot_id']
        assert report['status']=='RECOVERED_TRIAL'


def test_unknown_next_front_keeps_already_committed_incumbent_and_original_reservation(tmp_path):
    from contractedge.development.public_demo import freeze_example,default_config,PublicExampleRunner
    public=freeze_example('two-consumers',tmp_path)
    config={**default_config('two-consumers'),'run_id':'selected-then-unknown-front'}
    script=tmp_path/'next-front-child.py'
    script.write_text('''import json,os,sys
from pathlib import Path
from contractedge.orchestration.repair import repair_case
from contractedge.development.public_demo import PublicExampleRunner,SyntheticExampleProvider,count_tokens
from contractedge.model.provider import ModelResponse
root=Path(sys.argv[1]);config=json.loads(sys.argv[2]);public=root/'frozen-public/public'
runner=PublicExampleRunner(public,'two-consumers')
class Provider(SyntheticExampleProvider):
 diagnoses=0
 def complete_json(self,request):
  if request.prompt_version.startswith('diagnosis.'):
   self.diagnoses+=1
   if self.diagnoses==2:os._exit(73)
  response=super().complete_json(request)
  if request.prompt_version in {'public-postcondition-review.v1','public-postcondition-review.v2'}:
   body=json.loads(response.content);body['failure_kind']='DIAGNOSIS';return ModelResponse(json.dumps(body),response.metadata)
  return response
repair_case(public,root/'frozen-public','two-consumers',root/'run',Provider('two-consumers',runner),count_tokens,config,public_runner=runner)
''')
    result=subprocess.run([sys.executable,str(script),str(tmp_path),json.dumps(config)],capture_output=True,text=True)
    assert result.returncode==73,result.stderr+result.stdout
    committed=next(event for event in events(tmp_path) if event['kind']=='SELECTION_INTENT')
    report=repair_case(public,tmp_path/'frozen-public','two-consumers',tmp_path/'run',None,None,config,
        public_runner=PublicExampleRunner(public,'two-consumers'),resume=True)
    assert report['status']=='RESPONSE_UNKNOWN'
    assert report['final_snapshot_id']==committed['selected_snapshot']
    assert report['candidate']['patch_sha256']==committed['patch_sha256'] and report['candidate']['patch_bytes']>0
    assert report['last_feedback']['to_snapshot']==report['final_snapshot_id']
    assert report['budget']['model_calls']==5 and report['recovery']['unknown_reservations_retained']
    assert report['policy_usage'] and not report['recovery']['continuation_allowed']


def test_crash_after_recovered_selection_commit_finishes_without_duplicate_transition_or_effect(tmp_path):
    public=hard_exit(tmp_path,'APPLIED')
    script=tmp_path/'resume-child.py'
    script.write_text('''import json,os,sys
from pathlib import Path
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration import resume
from tests.test_repair_execution import PublicLoopRunner
root=Path(sys.argv[1]);public=Path(sys.argv[2]);config=json.loads(sys.argv[3]);original=resume.atomic_json
def write(path,value,**kwargs):
 if Path(path).name=='recovery.completed.json':os._exit(73)
 return original(path,value,**kwargs)
resume.atomic_json=write
repair_case(public,root,'CASE-1',root/'run',None,None,config,public_runner=PublicLoopRunner(public),resume=True)
''')
    result=subprocess.run([sys.executable,str(script),str(tmp_path),str(public),json.dumps(CONFIG)],capture_output=True,text=True)
    assert result.returncode==73,result.stderr+result.stdout
    transition_paths=list((tmp_path/'run/journal/transitions').glob('revision-*.json'))
    event_count=len(events(tmp_path));ledger=(tmp_path/'run/model_calls.json').read_bytes()
    report=resume(tmp_path,public)
    assert report['candidate']['patch_bytes']==0 and report['status']=='RECOVERED_INCUMBENT'
    assert len(list((tmp_path/'run/journal/transitions').glob('revision-*.json')))==len(transition_paths)
    assert len(events(tmp_path))==event_count and (tmp_path/'run/model_calls.json').read_bytes()==ledger
