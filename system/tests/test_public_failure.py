"""Public fixtures exercise exact front requests; no ACK original is present."""
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_correction_accounting import needs_evidence
from tests.test_planning_evidence_extension import front_fixture

SYSTEM = Path(__file__).resolve().parents[1]
SCRIPT = SYSTEM/'scripts/public_failure.py'
spec = importlib.util.spec_from_file_location('public_failure', SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def fixture(tmp_path, *, nested=False):
    source = tmp_path/'source';source.mkdir()
    value, file = front_fixture(source)
    workspace=source
    if nested:
        workspace=tmp_path/'workspace';workspace.mkdir()
        target=workspace/'repo';source.rename(target);source=target
        file=source/'templates/serviceaccount.yaml';value['source_root']='repo'
        value['input_digest']=tool.sha(tool.canonical({k:v for k,v in value.items() if k!='input_digest'}))
    class InvalidPlan(PipelineFixtureProvider):
        requests = []
        def complete_json(self, request):
            self.requests.append(json.loads(tool.canonical(asdict(request))))
            response = super().complete_json(request)
            if request.prompt_version.startswith('changeset.'):
                body = json.loads(response.content)
                needs_evidence(body)
                body['preserve_constraints'] = [dict(id='KEEP',outcome_id='O1',obligation_refs=['A1'],
                    statement='Keep existing behavior',current_satisfaction='UNKNOWN',evidence_refs=[])]
                body['preservation_impact'] = [dict(preserve_id='KEEP',scope='CHANGES',change_refs=[],rationale='Invalid empty reference fixture')]
                return ModelResponse(json.dumps(body), {'provider':'synthetic-invalid-plan','usage':{'prompt_tokens':10,'completion_tokens':20}})
            return response
    provider = InvalidPlan();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(file.read_bytes()).hexdigest()}
    cfg = dict(run_id='public-failure-fixture',model={'model':'fixture'},max_context_tokens=24000,
               max_output_tokens=1024,max_total_tokens=100000,max_model_calls=12,max_format_retries=1)
    budget=BudgetedProvider(provider,lambda text:len(text.split()),max_tokens=100000,max_seconds=60,max_calls=12)
    trace=run_front_half(value,workspace,budget,cfg,lambda text:len(text.split())).to_wire()
    assert trace['status']=='INVALID_PLAN',trace['errors']
    assert any('PRESERVATION_IMPACT_INVALID' in error for error in trace['errors'])
    assert json.loads(tool.canonical([c.get('request') for c in budget.calls])) == provider.requests
    run=tmp_path/'run';run.mkdir()
    artifacts={'method_input':value,'model_calls':budget.calls,'front_trace':trace,'front_config':cfg,'public_observations':[]}
    for role, body in artifacts.items():write(run/(role+'.json'),body)
    recipe={'scope':'front_semantic_failure','semantic_reviewer':'disabled','tokenizer':'whitespace_fixture',
            'method_source_sha256':tool.method_identity(SYSTEM),'broken_snapshot_id':value['initial_snapshot_id'],
            'input_digest':value['input_digest'],'run_id':cfg['run_id'],
            'source_files':['templates/serviceaccount.yaml'],'files':{role:role+'.json' for role in artifacts}}
    return run,source,recipe,budget,trace


def package(tmp_path):
    run,source,recipe,budget,trace=fixture(tmp_path)
    out=tmp_path/'package'
    result=tool.export_package(run,source,recipe,out)
    assert result['status']=='COMPLETE',result
    return out,run,source,recipe,budget,trace


def invoke(*args):
    result=subprocess.run([sys.executable,str(SCRIPT),*map(str,args)],capture_output=True,text=True,timeout=130)
    return result.returncode,json.loads(result.stdout)


def test_synthetic_invalid_plan_exports_and_replays_exact_requests_without_http(tmp_path):
    out,_,_,recipe,budget,_=package(tmp_path)
    code,result=invoke('replay','--package',out,'--method-root',SYSTEM,'--expected-method-sha256',recipe['method_source_sha256'])
    assert code==0 and result['status']=='REPLAY_MATCH',result
    assert result['matched_calls']==len(budget.calls) and result['replayed_status']=='INVALID_PLAN'
    assert result['real_model_http_calls']==0
    assert budget.calls[-1]['response_metadata']['usage']['completion_tokens']==20


@pytest.mark.parametrize('role',['method_input','model_calls','front_trace','front_config','public_observations'])
def test_missing_roles_remain_incomplete(tmp_path,role):
    run,source,recipe,_,_=fixture(tmp_path)
    (run/(role+'.json')).unlink()
    result=tool.export_package(run,source,recipe,tmp_path/'package')
    assert result['status']=='INCOMPLETE' and role in result['missing']


def test_legacy_ledger_does_not_invent_missing_request_parameters(tmp_path):
    run,source,recipe,budget,_=fixture(tmp_path)
    for call in budget.calls:call.pop('request')
    write(run/'model_calls.json',budget.calls)
    result=tool.export_package(run,source,recipe,tmp_path/'package')
    assert result['status']=='INCOMPLETE'
    assert 'complete_request_parameters:call_0' in result['missing']


@pytest.mark.parametrize('change',['file','snapshot','input','run','method','configuration'])
def test_corruption_and_bound_identity_mismatches_refuse_replay(tmp_path,change):
    out,_,_,recipe,_,_=package(tmp_path)
    manifest=tool.load(out/'manifest.json')
    if change=='file':(out/'broken/templates/serviceaccount.yaml').write_text('different')
    elif change in {'snapshot','input','run','method'}:
        key={'snapshot':'broken_snapshot_id','input':'input_digest','run':'run_id','method':'method_source_sha256'}[change]
        manifest['identity'][key]='0'*64
        write(out/'manifest.json',manifest)
    else:
        name='artifacts/front_config.json';path=out/name;cfg=tool.load(path);cfg['model']['model']='different';write(path,cfg)
        manifest['files'][name].update(sha256=tool.sha(path.read_bytes()),bytes=path.stat().st_size);write(out/'manifest.json',manifest)
    code,result=invoke('replay','--package',out,'--method-root',SYSTEM,'--expected-method-sha256',recipe['method_source_sha256'])
    assert code!=0 and result['status']!='REPLAY_MATCH'
    if change=='configuration':
        assert result['first_request_divergence']['call_index']==0
        assert 'model' in result['first_request_divergence']['fields']


def test_request_tape_cannot_disagree_with_persisted_ledger(tmp_path):
    run,source,recipe,budget,_=fixture(tmp_path)
    budget.calls[0]['request']['messages'][0]['content']='changed'
    write(run/'model_calls.json',budget.calls)
    result=tool.export_package(run,source,recipe,tmp_path/'package')
    assert result['status']=='INVALID' and any('LEDGER_REQUEST_MISMATCH' in e for e in result['errors'])


@pytest.mark.parametrize('name',['../outside','.env','evaluation/secret.json','fixed/main.py'])
def test_nonpublic_source_paths_rejected_before_read(tmp_path,name):
    run,source,recipe,_,_=fixture(tmp_path);recipe['source_files']=[name]
    with pytest.raises(ValueError):tool.export_package(run,source,recipe,tmp_path/'package')


def test_extra_run_files_not_exported_and_extra_package_files_rejected(tmp_path):
    out,run,_,_,_,_=package(tmp_path)
    (run/'unrelated.txt').write_text('do not export')
    assert not (out/'unrelated.txt').exists()
    (out/'extra.txt').write_text('unregistered')
    assert 'UNREGISTERED_PACKAGE_FILES' in tool.check_package(out)['errors']


def test_symlink_source_and_credentials_refused(tmp_path):
    run,source,recipe,_,_=fixture(tmp_path)
    (source/'link').symlink_to(run/'front_trace.json');recipe['source_files']=['link']
    with pytest.raises(ValueError,match='Symlink'):tool.export_package(run,source,recipe,tmp_path/'link-package')
    recipe['source_files']=['templates/serviceaccount.yaml']
    write(run/'front_config.json',{'api_key':'example-sensitive-field'})
    with pytest.raises(ValueError,match='Credential'):tool.export_package(run,source,recipe,tmp_path/'secret-package')


def test_worker_rejects_wrong_trusted_method_before_import(tmp_path):
    out,_,_,_,_,_=package(tmp_path)
    with pytest.raises(ValueError,match='TRUSTED_METHOD_IDENTITY_MISMATCH'):
        tool.worker(out,SYSTEM,'0'*64)


def test_export_never_overwrites_existing_directory(tmp_path):
    run,source,recipe,_,_=fixture(tmp_path)
    with pytest.raises(ValueError,match='new independent'):
        tool.export_package(run,source,recipe,run)


def test_explicit_front_range_preserves_original_whole_ledger(tmp_path):
    run,source,recipe,budget,_=fixture(tmp_path)
    count=len(budget.calls)
    ledger=[{'stage':'prior-public-call'},*budget.calls,{'stage':'later-public-call'}]
    write(run/'model_calls.json',ledger);recipe['model_call_range']=[1,1+count]
    out=tmp_path/'package';result=tool.export_package(run,source,recipe,out)
    assert result['status']=='COMPLETE' and result['total_ledger_calls']==count+2
    code,result=invoke('replay','--package',out,'--method-root',SYSTEM,'--expected-method-sha256',recipe['method_source_sha256'])
    assert code==0 and result['matched_calls']==count,result


def test_front_recording_retains_effective_inputs_and_ledger_indices(tmp_path):
    from contractedge.orchestration.repair import run_front_recorded
    provider=type('Provider',(),{'calls':[{'prior':True}]})()
    config={'model':{'model':'fixture'},'run_id':'fixture'}
    def fake(*args,**kwargs):
        provider.calls.append({'this_call':True})
        return 'trace-fixture'
    with patch('contractedge.orchestration.repair.run_front_half',side_effect=fake):
        assert run_front_recorded({},tmp_path,provider,config,len,directory=tmp_path/'round',
                                  public_observations=[{'public':True}])=='trace-fixture'
    assert tool.load(tmp_path/'round/front.config.json')==config
    assert tool.load(tmp_path/'round/front.public_observations.json')==[{'public':True}]
    assert tool.load(tmp_path/'round/front.execution_feedback.json') is None
    assert tool.load(tmp_path/'round/front.replay.json')['model_call_range']==[1,2]


def test_relative_source_root_replays_without_rewriting_method_input(tmp_path):
    run,source,recipe,_,_=fixture(tmp_path,nested=True)
    out=tmp_path/'package';assert tool.export_package(run,source,recipe,out)['status']=='COMPLETE'
    code,result=invoke('replay','--package',out,'--method-root',SYSTEM,'--expected-method-sha256',recipe['method_source_sha256'])
    assert code==0 and result['status']=='REPLAY_MATCH',result
