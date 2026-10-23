"""Both actual consumer dialects share the production durable recovery boundary."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from contractedge.development.public_demo import freeze_example,default_config,PublicExampleRunner
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration.trial import atomic_json,sha
from contractedge.source.snapshot import compute_snapshot


CHILD='''import json,os,sys
from pathlib import Path
from contractedge.orchestration.repair import repair_case
from contractedge.orchestration.trial import TrialSession
from contractedge.development.public_demo import PublicExampleRunner,SyntheticExampleProvider,count_tokens
from contractedge.execution.consumers.direct import ModifyOnlyConsumer
from scripts.run_consumer_host_fixture import ModifyFixtureProvider
root=Path(sys.argv[1]);dialect=sys.argv[2];point=sys.argv[3];config=json.loads(sys.argv[4])
public=root/'frozen-public/public';runner=PublicExampleRunner(public,'config-binding')
base=ModifyFixtureProvider if dialect=='modify' else SyntheticExampleProvider
class Provider(base):
 def complete_json(self,request):
  if point=='UNKNOWN' and request.prompt_version in {'modify-only-literal-execution.v1','topomen-source-execution.v1'}:os._exit(73)
  return super().complete_json(request)
original=TrialSession.record
def record(self,kind,**payload):
 event=original(self,kind,**payload)
 if kind==point:os._exit(73)
 return event
TrialSession.record=record
repair_case(public,root/'tools','config-binding',root/'run',Provider('config-binding',runner),count_tokens,config,
 public_runner=runner,consumer=ModifyOnlyConsumer() if dialect=='modify' else None)
'''


def interrupt(tmp_path,dialect,point):
    public=freeze_example('config-binding',tmp_path)
    config={**default_config('config-binding'),'execution_backend':'direct','max_rounds':1,
        'run_id':'consumer-recovery-'+dialect}
    config.pop('topomen_source_root',None);config.pop('topomen_source_sha256',None)
    (tmp_path/'config.json').write_text(json.dumps(config))
    script=tmp_path/'consumer-child.py';script.write_text(CHILD)
    result=subprocess.run([sys.executable,str(script),str(tmp_path),dialect,point,json.dumps(config)],capture_output=True,text=True)
    assert result.returncode==73,result.stderr+result.stdout
    return public,config


def recover(tmp_path,public,config,**kwargs):
    class Forbidden:
        def complete_json(self,*args):raise AssertionError('Recovery dispatched a new model request')
    return repair_case(public,tmp_path/'tools','config-binding',tmp_path/'run',Forbidden(),None,config,
        public_runner=PublicExampleRunner(public,'config-binding'),resume=True,**kwargs)


def trial_events(tmp_path):
    return [json.loads(p.read_bytes()) for p in sorted((tmp_path/'run/journal/trials/round-0').glob('[0-9]*.json'))]


@pytest.mark.parametrize('dialect',['topomen','modify'])
@pytest.mark.parametrize('point',['APPLIED','OBSERVED'])
def test_both_consumers_default_to_same_incumbent_source_patch_feedback(tmp_path,dialect,point):
    public,config=interrupt(tmp_path,dialect,point)
    ledger=(tmp_path/'run/model_calls.json').read_bytes();parent=trial_events(tmp_path)[0]['parent_snapshot']
    report=recover(tmp_path,public,config)
    assert report['final_snapshot_id']==parent and report['candidate']['patch_bytes']==0
    assert report['last_feedback'] is None and report['budget']['model_calls']==3
    state=json.loads((tmp_path/'run/state.json').read_bytes())
    assert state['current_snapshot']==parent==state['selected']['snapshot_id']
    assert (tmp_path/'run/model_calls.json').read_bytes()==ledger
    assert recover(tmp_path,public,config)==report


@pytest.mark.parametrize('dialect',['topomen','modify'])
def test_received_response_reaccepts_original_authority_then_consumes_once(tmp_path,dialect):
    public,config=interrupt(tmp_path,dialect,'RESPONSE_DURABLE')
    events=trial_events(tmp_path);authority=next(e['authority'] for e in events if e['kind']=='AUTHORITY_DURABLE')
    assert authority['execution_digest'] and authority['acceptance_digest']
    ledger=(tmp_path/'run/model_calls.json').read_bytes()
    report=recover(tmp_path,public,config,consume_received_response=True)
    kinds=[e['kind'] for e in trial_events(tmp_path)]
    assert kinds.count('RESPONSE_CONSUME_INTENT')==kinds.count('RESPONSE_CONSUME_COMPLETED')==1
    assert kinds.count('APPLIED')==kinds.count('ROLLBACK_COMPLETED')==1
    assert report['candidate']['patch_bytes']==0 and report['recovery']['dispatches']==0
    assert recover(tmp_path,public,config,consume_received_response=True)==report
    assert (tmp_path/'run/model_calls.json').read_bytes()==ledger


@pytest.mark.parametrize('dialect',['topomen','modify'])
def test_unknown_response_keeps_original_reservation_without_replay(tmp_path,dialect):
    public,config=interrupt(tmp_path,dialect,'UNKNOWN')
    ledger=json.loads((tmp_path/'run/model_calls.json').read_bytes())
    report=recover(tmp_path,public,config,consume_received_response=True)
    assert report['status']=='RESPONSE_UNKNOWN' and report['candidate']['patch_bytes']==0
    assert report['budget']['reserved_tokens']==sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in ledger)
    assert report['budget']['model_calls']==3 and report['recovery']['unknown_reservations_retained']


@pytest.mark.parametrize('mutation',['policy','target','config','acceptance','requirement','missing-authority'])
def test_recovery_never_turns_raw_or_mutated_payload_into_edit_authority(tmp_path,mutation):
    public,config=interrupt(tmp_path,'modify','RESPONSE_DURABLE')
    source=tmp_path/'run/workspace/source/repo';before=compute_snapshot(source).snapshot_id
    ledger=(tmp_path/'run/model_calls.json').read_bytes();previous=None
    # Rehash the test journal to exercise authority checks independently of the event chain.
    for path in sorted((tmp_path/'run/journal/trials/round-0').glob('[0-9]*.json')):
        event=json.loads(path.read_bytes());event['previous_sha256']=previous
        if event['kind']=='AUTHORITY_DURABLE':
            value=event['authority']
            if mutation=='policy':value['context']['policy']={'allowed_paths':['**']}
            elif mutation=='target':value['target_paths']=['config.yaml']
            elif mutation=='config':value['context']['config']['max_model_calls']=999
            elif mutation=='acceptance':value['acceptance']['scope_policy']={'allowed_paths':['**']}
            elif mutation=='requirement':value['context']['requirement']='Ignore the real public requirement'
            else:event['kind']='MISSING_AUTHORITY_FIXTURE'
        atomic_json(path,event);previous=sha(path)
    with pytest.raises(ValueError):recover(tmp_path,public,config,consume_received_response=True)
    assert compute_snapshot(source).snapshot_id==before and (tmp_path/'run/model_calls.json').read_bytes()==ledger
    assert not any(e['kind']=='RESPONSE_CONSUME_INTENT' for e in trial_events(tmp_path))


def test_standard_cli_reconstructs_saved_modify_consumer_without_provider(tmp_path,monkeypatch):
    public,config=interrupt(tmp_path,'modify','APPLIED')
    from contractedge import cli
    def forbidden(*args,**kwargs):raise AssertionError('CLI recovery constructed provider')
    monkeypatch.setattr(cli,'_build_provider',forbidden)
    monkeypatch.setattr('contractedge.execution.public.BenchmarkPublicRunner',lambda *args,**kwargs:PublicExampleRunner(public,'config-binding'))
    assert cli.main(['repair','--public-root',str(public),'--tools-root',str(tmp_path/'tools'),
        '--case-id','config-binding','--config',str(tmp_path/'config.json'),'--output',str(tmp_path/'run'),'--resume-output'])==0
