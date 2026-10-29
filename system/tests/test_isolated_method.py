"""Exercise the real namespace and typed provider boundary, without HTTP."""
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


def load(name):
    scripts = Path(__file__).resolve().parents[1]/'scripts'
    path = scripts/(name+'.py')
    assert path.is_file(), f'{name} is not implemented'
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(scripts))
    try: spec.loader.exec_module(result)
    finally: sys.path.pop(0)
    return result


def test_actual_method_namespace_hides_private_env_network_hostproc_and_socket(tmp_path):
    isolated = load('run_isolated_method')
    work = tmp_path/'method'; work.mkdir()
    private = tmp_path/'private'; private.write_text('private canary')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as host:
        host.bind(('127.0.0.1', 0)); host.listen()
        script = '''import json,os,socket
from pathlib import Path
try:
 s=socket.create_connection(('127.0.0.1', __PORT__), timeout=.2); connected=True
except OSError: connected=False
Path('allowed').write_text('own output')
print(json.dumps(dict(private=Path(__PRIVATE_PATH__).exists(), secret=os.environ.get('CE_PRIVATE_CANARY'),
 socket=Path('/var/run/docker.sock').exists(), hostproc=Path('/proc/HOSTPID/environ').exists(),
 connected=connected, pid=os.getpid(), output=Path('allowed').read_text())))
'''.replace('__PORT__', str(host.getsockname()[1])).replace('__PRIVATE_PATH__', repr(str(private))).replace('HOSTPID', str(os.getpid()))
        command = isolated.namespace_command(command=['/usr/bin/python3', '-c', script],
            readonly={}, writable={work:work}, cwd=work, environment={'PATH':'/usr/bin:/bin'})
        result = subprocess.run(command, env={**os.environ, 'CE_PRIVATE_CANARY':'never inherit'},
                                capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed == dict(private=False, secret=None, socket=False, hostproc=False,
                            connected=False, pid=2, output='own output')
    assert (work/'allowed').read_text() == 'own output'


def test_namespace_mounts_are_readonly_and_environment_is_explicit(tmp_path):
    isolated = load('run_isolated_method')
    source = tmp_path/'frozen'; source.mkdir(); (source/'x').write_text('fixed')
    work = tmp_path/'method'; work.mkdir()
    script = f'''from pathlib import Path
try: Path({str(source/'x')!r}).write_text('corrupt')
except OSError: print('READONLY')
else: print('CORRUPTED')
'''
    command = isolated.namespace_command(command=['/usr/bin/python3','-c',script],
        readonly={source:source},writable={work:work},cwd=work,environment={})
    result = subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == 'READONLY'
    assert (source/'x').read_text() == 'fixed'
    with pytest.raises(ValueError):
        isolated.namespace_command(command=['true'], readonly={}, writable={work:work}, cwd=work,
                                    environment={'CONTRACTEDGE_API_KEY':'secret'})


def test_typed_provider_preserves_request_schema_metadata_overhead_and_response(tmp_path):
    isolated = load('run_isolated_method'); rpc = load('isolated_protocol')
    from contractedge.model.provider import ModelRequest
    request = ModelRequest(model='test-model', prompt_version='diagnosis.v1',
        messages=({'role':'user','content':'public requirement'},), max_output_tokens=10,
        metadata={'snapshot_id':'S0'}, output_schema={'type':'object'}, thinking='disabled')
    captured = []
    def complete(payload):
        captured.append(payload)
        return {'content':'{"answer":1}','metadata':{'finish_reason':'stop','model':'test-model'}}
    token='rpc-auth-97a386af7a5642b093f10736'
    handlers = {'model.complete':complete,'model.overhead':lambda p:{'text':'transport schema'},
                'model.metadata':lambda p:{'metadata':{'output_mode':'json_schema'}},
                'budget.summary':lambda p:{'summary':{'limit_cny':150,'remaining_upper_cny':50}}}
    with rpc.JsonRpcServer(tmp_path/'rpc.sock',token,rpc.Journal(tmp_path/'journal'),handlers) as server:
        provider = isolated.ProxyProvider(rpc.RpcClient(server.path,token), timeout_seconds=240)
        assert provider.request_overhead(request) == 'transport schema'
        assert provider.request_metadata(request) == {'output_mode':'json_schema'}
        response = provider.complete_json(request)
        assert response.content == '{"answer":1}' and response.metadata['finish_reason'] == 'stop'
        assert provider.money_budget.summary()['limit_cny'] == 150
    assert captured == [{'request':json.loads(json.dumps(asdict(request))), 'timeout_seconds':240}]


def test_known_received_provider_rejection_preserves_response_for_existing_budget_logic(tmp_path):
    isolated=load('run_isolated_method'); rpc=load('isolated_protocol')
    from contractedge.model.provider import ModelRequest, ProviderResponseError
    from contractedge.model.budget import BudgetedProvider
    request=ModelRequest('test','phase',({'role':'user','content':'public'},),5)
    token='rpc-auth-97a386af7a5642b093f10736'
    handlers={'model.complete':lambda p:{'received_error':True,'content':'partial',
                                       'metadata':{'model_identity_status':'MISMATCH'}},
              'model.overhead':lambda p:{'text':''},'model.metadata':lambda p:{'metadata':{}}}
    with rpc.JsonRpcServer(tmp_path/'s',token,rpc.Journal(tmp_path/'j'),handlers) as server:
        provider=isolated.ProxyProvider(rpc.RpcClient(server.path,token))
        budget=BudgetedProvider(provider,len,max_tokens=100,max_seconds=10,max_calls=1)
        with pytest.raises(ProviderResponseError):budget.complete_json(request)
        assert budget.calls[0]['response_content']=='partial'
        assert budget.calls[0]['response_metadata']['model_identity_status']=='MISMATCH'


def test_public_proxy_binds_each_operation_to_current_source_snapshot(tmp_path):
    isolated=load('run_isolated_method');rpc=load('isolated_protocol')
    from contractedge.source.snapshot import compute_snapshot
    workspace=tmp_path/'workspace';(workspace/'source').mkdir(parents=True)
    (workspace/'source/file').write_text('S1')
    (workspace/'workspace.json').write_text(json.dumps({'source_root':str(workspace/'source')}))
    calls=[]
    def capture(payload):calls.append(payload);return {'status':'EXECUTION_PASS'}
    with rpc.JsonRpcServer(tmp_path/'s','token',rpc.Journal(tmp_path/'j'),{'public.observe':capture}) as server:
        runner=isolated.ProxyPublicRunner(rpc.RpcClient(server.path,'token'),timeout_seconds=2400)
        runner.timeout_seconds=20
        assert runner.observe(workspace,tmp_path/'public')['status']=='EXECUTION_PASS'
    assert calls==[{'workspace':str(workspace),'output':str(tmp_path/'public'),
                   'snapshot_id':compute_snapshot(workspace/'source').snapshot_id,'timeout_seconds':20}]


def test_parent_independently_enforces_model_context_calls_and_received_error(tmp_path):
    isolated=load('run_isolated_method')
    from contractedge.model.provider import ModelRequest,ModelResponse,ProviderResponseError
    from contractedge.model.budget import BudgetExceeded
    class PublicService:
        timeout_seconds=240
        def __init__(self):self.calls=0
        def complete_json(self, request):
            self.calls+=1
            if self.calls==2:raise ProviderResponseError('provider private detail',ModelResponse('received',{'model_identity_status':'MISMATCH'}))
            return ModelResponse('answer',{'model':'test'})
        def request_overhead(self,request):return 'schema'
        def request_metadata(self,request):return {'output_mode':'json_object'}
    provider=PublicService()
    config=dict(model='test',max_model_calls=2,max_total_tokens=200,max_elapsed_seconds=30,
                max_context_tokens=100,max_output_tokens=10,execution_max_output_tokens=10,
                review_max_output_tokens=5,timeout_seconds=240)
    broker=isolated.ModelBroker(provider,len,config,tmp_path/'calls.json')
    req=asdict(ModelRequest('test','phase',({'role':'user','content':'public'},),5))
    with pytest.raises(ValueError):broker.complete({'request':{**req,'model':'other'},'timeout_seconds':1})
    oversized={**req,'messages':[{'role':'user','content':'x'*95}]}
    with pytest.raises(ValueError):broker.complete({'request':oversized,'timeout_seconds':1})
    assert provider.calls==0
    assert broker.complete({'request':req,'timeout_seconds':1})['content']=='answer'
    received=broker.complete({'request':req,'timeout_seconds':1})
    assert received['received_error'] is True and received['content']=='received'
    assert any(x['category']=='MODEL_POLICY' for x in broker.policy_failures)
    with pytest.raises(BudgetExceeded):broker.complete({'request':req,'timeout_seconds':1})
    assert provider.calls==2 and len(json.loads((tmp_path/'calls.json').read_text()))==2


def test_historical_method_without_new_provider_error_type_receives_normal_response(monkeypatch):
    from types import ModuleType
    from contractedge.model.provider import ModelRequest, ModelResponse
    isolated=load('run_isolated_method')
    historical=ModuleType('contractedge.model.provider');historical.ModelResponse=ModelResponse
    monkeypatch.setitem(sys.modules,'contractedge.model.provider',historical)
    class Channel:
        def call(self,op,payload):return {'content':'historical answer','metadata':{'model':'test'}}
    response=isolated.ProxyProvider(Channel()).complete_json(ModelRequest('test','phase',(),5))
    assert response.content=='historical answer' and response.metadata=={'model':'test'}


def test_common_strict_transport_preserves_registered_legacy_json_wire_parameters(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from contractedge.model.provider import ModelRequest,OpenAICompatibleProvider
    captured=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body=json.dumps({'model':'test','choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}).encode()
            self.send_response(200);self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    monkeypatch.setenv('CE_UNIT_ONLY_API_KEY','local-fake')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        provider=OpenAICompatibleProvider(f'http://127.0.0.1:{server.server_port}','CE_UNIT_ONLY_API_KEY',
            thinking='enabled',reasoning_effort='low',model_identity_policy='require_match')
        response=provider.complete_json(ModelRequest('test','phase',({'role':'user','content':'Return JSON.'},),10))
    finally:server.shutdown();server.server_close();thread.join()
    assert response.metadata['model_identity_verified'] is True
    assert captured==[{'model':'test','messages':[{'role':'user','content':'Return JSON.'}],
        'max_tokens':10,'temperature':0.0,'response_format':{'type':'json_object'},
        'thinking':{'type':'enabled'},'reasoning_effort':'low'}]
