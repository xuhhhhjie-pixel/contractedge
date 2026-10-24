"""Collection capability is distinct from object identity and semantic truth."""
import hashlib
import json
from pathlib import Path

import pytest

from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.support import support_error
from tests.test_operation_consistency import failed

ADAPTER='contractedge.render-inventory.v1'


def inventory():
    body={'source_file':'raw/control.stdout','source_sha256':'a'*64,
          'observation_type':'PARSED_PUBLIC_RESOURCE_INVENTORY','kind':'Job','complete_inventory':True,
          'resources':[{'kind':'Job','name':'other-job','namespace':'team-a'}]}
    text=json.dumps(body)
    return {'id':'INV','kind':'PUBLIC_OBSERVATION','snapshot_id':'S1','scenario':'control',
        'evidence_kind':'PUBLIC_STRUCTURE','adapter_id':ADAPTER,'structured_complete':True,
        'source_file':body['source_file'],'source_file_sha256':body['source_sha256'],
        'locator':{'path':'raw/control.render-kind-Job.json','whole_file_read':True,'truncated':False},
        'content_digest':hashlib.sha256(text.encode()).hexdigest(),'excerpt':text,'origin':'trusted producer fixture'}


def obligation(**resource):
    return {'verification_kind':'PUBLIC_STRUCTURE','verification_scope':{
        'scenario':'control','resource':{'kind':'Job','name':None,'namespace':'team-a',**resource}}}


def claim(record):
    return {'status':'PASS','evidence':[{'observation_id':record['id'],'quote':record['excerpt']}]}


@pytest.mark.parametrize('name,namespace',[(None,'team-a'),('missing-job','team-a'),('missing-job','team-b')])
def test_complete_registered_collection_can_support_scoped_presence_or_absence_review(name,namespace):
    item=inventory()
    assert support_error(obligation(name=name,namespace=namespace),claim(item),[item]) is None


@pytest.mark.parametrize('change',[{'adapter_id':'raw-json'}, {'structured_complete':False},
    {'locator':{'whole_file_read':True,'truncated':True}}, {'locator':{'whole_file_read':False,'truncated':False}},
    {'source_file_sha256':'b'*64}, {'source_file':'other-source'}, {'evidence_kind':'EXECUTION_ONLY'}])
def test_partial_unregistered_or_wrong_source_collection_never_gains_scope_capability(change):
    item={**inventory(),**change}
    assert support_error(obligation(),claim(item),[item]) is not None


@pytest.mark.parametrize('field,value',[('complete_inventory',False),('kind','Pod'),
    ('observation_type','raw-log'),('resources',[{'kind':'Pod','name':'wrong-kind'}]),
    ('resources',[{'kind':'Job','name':None,'namespace':'team-a'}]),
    ('resources',[{'kind':'Job','name':'job','namespace':None}])])
def test_complete_flag_cannot_hide_malformed_or_different_collection(field,value):
    item=inventory();body=json.loads(item['excerpt']);body[field]=value;item['excerpt']=json.dumps(body)
    item['content_digest']=hashlib.sha256(item['excerpt'].encode()).hexdigest()
    assert support_error(obligation(),claim(item),[item]) is not None


def test_wrong_scenario_or_runtime_claim_remains_unsupported():
    item=inventory();other=obligation();other['verification_scope']['scenario']='trigger'
    assert support_error(other,claim(item),[item])=='OBSERVATION_SCENARIO_MISMATCH'
    runtime={**obligation(),'verification_kind':'PUBLIC_BEHAVIOR'}
    assert support_error(runtime,claim(item),[item])=='OBSERVATION_CAPABILITY_INSUFFICIENT'


@pytest.mark.parametrize('namespace,want',[('team-a','OPERATION_SUCCESS_CONFLICT'),('team-b',None),
                                         (None,'OPERATION_SUCCESS_CONFLICT')])
def test_collection_scope_never_hides_a_relevant_operation_failure(namespace,want):
    item=inventory();operation=failed();operation.update(id='OP',scenario='control',
        resource_identity={'kind':'Job','name':'child','namespace':namespace})
    assert support_error(obligation(),claim(item),[item,operation])==want


def test_unknown_operation_identity_cannot_be_declared_unrelated_to_collection():
    item=inventory();operation=failed();operation.update(id='OP',scenario='control')
    operation.pop('resource_identity',None)
    assert support_error(obligation(),claim(item),[item,operation])=='OPERATION_SUCCESS_CONFLICT'


def test_empty_complete_collection_still_covers_missing_kind():
    item=inventory();body=json.loads(item['excerpt']);body['resources']=[];item['excerpt']=json.dumps(body)
    item['content_digest']=hashlib.sha256(item['excerpt'].encode()).hexdigest()
    assert support_error(obligation(),claim(item),[item]) is None


def test_collection_capability_never_promotes_a_model_unknown():
    from contractedge.execution.review import ModelPublicReviewer
    from contractedge.model.provider import FixtureProvider
    provider=FixtureProvider({'checks':[{'id':'P','status':'UNKNOWN','rationale':'Predicate still uncertain','evidence':[]}],
                              'failure_kind':'UNKNOWN','rationale':'No semantic verdict'})
    item=inventory();plan={'postconditions':[{'id':'P','statement':'Required condition',**obligation()}]}
    result=ModelPublicReviewer(provider,len,{'model':'fixture','max_context_tokens':20000}).review(
        'Required condition',plan,{'status':'EXECUTION_PASS','snapshot_id':'S1','observations':[item]})
    assert result['decision']=='UNKNOWN' and result['checks'][0]['status']=='UNKNOWN'


def test_malformed_scope_cannot_turn_collection_into_matching_object():
    item=inventory()
    assert support_error(obligation(namespace={'not':'an identity'}),claim(item),[item]) is not None


def test_excessively_nested_payload_is_rejected_without_crashing_review():
    item=inventory();item['excerpt']='['*1200+'0'+']'*1200
    item['content_digest']=hashlib.sha256(item['excerpt'].encode()).hexdigest()
    assert support_error(obligation(),claim(item),[item]) is not None


@pytest.mark.parametrize('field,value',[('namespace',[]),('kind',{}),('namespace',''),('namespace','  ')])
def test_malformed_operation_identity_is_unknown_not_proof_of_unrelatedness(field,value):
    item=inventory();operation=failed();operation.update(id='OP',scenario='control',
        resource_identity={'kind':'Job','name':'child','namespace':'team-a'})
    operation['resource_identity'][field]=value
    assert support_error(obligation(),claim(item),[item,operation])=='OPERATION_SUCCESS_CONFLICT'


@pytest.mark.parametrize('namespace',['','  '])
def test_empty_effective_namespace_does_not_prove_explicit_namespace_enumeration(namespace):
    item=inventory();body=json.loads(item['excerpt']);body['resources'][0]['namespace']=namespace
    item['excerpt']=json.dumps(body);item['content_digest']=hashlib.sha256(item['excerpt'].encode()).hexdigest()
    assert support_error(obligation(),claim(item),[item]) is not None


def test_collection_cannot_fallback_to_object_identity_when_truncated():
    item=inventory();item['locator']['truncated']=True
    item['resource_identity']=obligation()['verification_scope']['resource']
    assert support_error(obligation(),claim(item),[item]) is not None


def test_real_producer_registers_only_successful_complete_render(tmp_path,monkeypatch):
    tools=tmp_path/'tools';tools.mkdir()
    for name in ['prepare_case.py','collect_patch.py','launch_public_case.py']:(tools/name).touch()
    workspace=tmp_path/'workspace';source=workspace/'source';source.mkdir(parents=True)
    (source/'public.txt').write_text('public source');inputs=workspace/'inputs';inputs.mkdir()
    (workspace/'workspace.json').write_text(json.dumps({'case_id':'DEMO','source_root':str(source),'inputs_root':str(inputs)}))
    public=tmp_path/'public';public.mkdir()
    def execute(argv,log,timeout):
        log.mkdir();(log/'tool.stdout').write_text('');(log/'tool.stderr').write_text('')
        output=Path(argv[argv.index('--output')+1]);output.mkdir()
        (output/'control.stdout').write_text('# Source: demo/job.yaml\nkind: Job\nmetadata:\n  name: other-job\n  namespace: team-a\n')
        return {'returncode':0,'timed_out':False}
    monkeypatch.setattr('contractedge.execution.public.run_tool',execute)
    monkeypatch.setattr('contractedge.execution.public.cleanup_owned_containers',lambda *a,**k:{'status':'OWNED_CONTAINERS_CLOSED','removed':[]})
    report=BenchmarkPublicRunner(public,tools,lock_root=tmp_path/'locks').observe(workspace,tmp_path/'output')
    item=next(o for o in report['observations'] if '.render-kind-Job.json' in o['locator']['path'])
    assert item.get('adapter_id')==ADAPTER and item.get('structured_complete') is True
    assert support_error(obligation(),claim(item),[item]) is None
