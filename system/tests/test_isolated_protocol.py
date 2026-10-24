"""Real socket/durable-file tests: no paid service or benchmark secrets."""
import importlib.util
import json
from pathlib import Path
import socket
import struct
import threading

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / 'scripts/isolated_protocol.py'
    assert path.is_file(), 'durable isolated protocol is not implemented'
    spec = importlib.util.spec_from_file_location('isolated_protocol_test', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_completed_request_survives_restart_and_never_repeats_effect(tmp_path):
    rpc = module()
    identity = {'op': 'model.complete', 'payload': {'prompt': 'public'}}
    effects = []
    def effect():
        effects.append('HTTP')
        return {'content': '{"answer":1}'}
    assert rpc.Journal(tmp_path).execute('one', identity, effect) == {'content': '{"answer":1}'}
    assert rpc.Journal(tmp_path).execute('one', identity, effect) == {'content': '{"answer":1}'}
    assert effects == ['HTTP']
    with pytest.raises(rpc.ProtocolError, match='IDENTITY_MISMATCH'):
        rpc.Journal(tmp_path).execute('one', {'op': 'model.complete', 'payload': {}}, effect)
    assert effects == ['HTTP']


def test_uncertain_effect_blocks_same_and_new_model_request_after_restart(tmp_path):
    rpc = module()
    effects = []
    def uncertain():
        effects.append('possibly-billed')
        raise RuntimeError('credential must not be returned')
    with pytest.raises(rpc.ProtocolError, match='OUTCOME_UNKNOWN'):
        rpc.Journal(tmp_path).execute('lost', {'op': 'model.complete', 'payload': {}}, uncertain)
    for request_id in ['lost', 'new-id']:
        with pytest.raises(rpc.ProtocolError, match='OUTCOME_UNKNOWN'):
            rpc.Journal(tmp_path).execute(request_id, {'op': 'model.complete', 'payload': {}}, uncertain)
    assert effects == ['possibly-billed']
    assert 'credential' not in ''.join(p.read_text() for p in tmp_path.glob('*.json'))


def test_pending_is_durable_before_effect_starts(tmp_path):
    rpc = module()
    def effect():
        records = [json.loads(p.read_text()) for p in tmp_path.glob('*.json')]
        assert len(records) == 1 and records[0]['status'] == 'PENDING'
        return {'done': True}
    assert rpc.Journal(tmp_path).execute('first', {'op': 'public.prepare', 'payload': {}}, effect) == {'done': True}


@pytest.mark.parametrize('request_id', ['../escape', '', '/tmp/x', 'x' * 97])
def test_request_id_cannot_escape_journal(tmp_path, request_id):
    rpc = module()
    with pytest.raises(rpc.ProtocolError, match='INVALID_REQUEST_ID'):
        rpc.Journal(tmp_path).execute(request_id, {}, lambda: {'bad': True})
    assert list(tmp_path.iterdir()) == []


def test_simultaneous_duplicate_executes_once(tmp_path):
    rpc = module()
    started = threading.Event()
    release = threading.Event()
    effects, results = [], []
    def effect():
        effects.append(1)
        started.set()
        assert release.wait(3)
        return {'count': 1}
    def call():
        results.append(rpc.Journal(tmp_path).execute('same', {'op': 'model.complete'}, effect))
    first = threading.Thread(target=call)
    second = threading.Thread(target=call)
    first.start()
    assert started.wait(3)
    second.start()
    release.set()
    first.join(3)
    second.join(3)
    assert effects == [1] and results == [{'count': 1}, {'count': 1}]


def test_real_channel_rejects_auth_operations_and_secrets_without_dispatch(tmp_path):
    rpc = module()
    calls = []
    def effect(payload):
        calls.append(payload)
        return {'content': 'public answer'}
    secret = 'private-test-canary'
    with rpc.JsonRpcServer(tmp_path/'channel.sock', 'channel-token', rpc.Journal(tmp_path/'journal'),
                           {'model.complete': effect}, sensitive_values=[secret]) as server:
        client = rpc.RpcClient(server.path, 'channel-token')
        assert client.call('model.complete', {'prompt': 'public'}, request_id='good') == {'content': 'public answer'}
        with pytest.raises(rpc.ProtocolError, match='UNAUTHORIZED'):
            rpc.RpcClient(server.path, 'wrong').call('model.complete', {})
        for op, payload, error in [('host.exec', {}, 'OPERATION_DENIED'),
                                   ('model.complete', {'prompt': secret}, 'SENSITIVE_DATA'),
                                   ('model.complete', {'prompt': 'channel-token'}, 'SENSITIVE_DATA')]:
            with pytest.raises(rpc.ProtocolError, match=error):
                client.call(op, payload)
    assert calls == [{'prompt': 'public'}]
    assert secret not in ''.join(p.read_text() for p in (tmp_path/'journal').glob('*.json'))


def test_provider_error_does_not_expose_exception_and_cannot_rebill(tmp_path):
    rpc = module()
    calls = []
    def effect(payload):
        calls.append(1)
        raise ValueError('sk-private-test-api-key')
    with rpc.JsonRpcServer(tmp_path/'channel.sock', 'token', rpc.Journal(tmp_path/'journal'),
                           {'model.complete': effect}) as server:
        for request_id in ['one', 'two']:
            with pytest.raises(rpc.ProtocolError, match='OUTCOME_UNKNOWN') as caught:
                rpc.RpcClient(server.path, 'token').call('model.complete', {}, request_id=request_id)
            assert 'sk-private' not in str(caught.value)
    assert calls == [1]


def test_oversized_and_nonfinite_or_duplicate_json_never_dispatch(tmp_path):
    rpc = module()
    calls = []
    with rpc.JsonRpcServer(tmp_path/'channel.sock', 'token', rpc.Journal(tmp_path/'journal'),
                           {'budget.summary': lambda p: calls.append(p) or {}}) as server:
        for packet in [struct.pack('!I', 8*1024*1024+1),
                       b'{"token":"token","token":"token"}', b'{"value":NaN}']:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                sock.connect(str(server.path))
                sock.sendall(packet if len(packet) == 4 else struct.pack('!I', len(packet))+packet)
                reply = rpc.receive_frame(sock)
                assert reply['ok'] is False
    assert calls == []


def test_client_recovers_lost_local_response_with_same_id_once(tmp_path):
    rpc = module()
    path = tmp_path/'drop.sock'
    ready = threading.Event()
    effects, requests = [], []
    journal = rpc.Journal(tmp_path/'journal')
    def server():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path)); listener.listen(); ready.set()
            for index in range(2):
                conn, _ = listener.accept()
                with conn:
                    request = rpc.receive_frame(conn); requests.append(request['request_id'])
                    result = journal.execute(request['request_id'], {'op': request['op'], 'payload': request['payload']},
                                             lambda: effects.append(1) or {'content': 'saved'})
                    if index:
                        rpc.send_frame(conn, {'ok': True, 'result': result})
    worker = threading.Thread(target=server, daemon=True)
    worker.start(); assert ready.wait(2)
    assert rpc.RpcClient(path, 'token').call('model.complete', {}, request_id='same') == {'content': 'saved'}
    worker.join(3)
    assert requests == ['same', 'same'] and effects == [1]


def test_sensitive_result_is_never_journaled_or_returned(tmp_path):
    rpc = module()
    secret = 'private-result-canary'
    with rpc.JsonRpcServer(tmp_path/'channel.sock', 'token', rpc.Journal(tmp_path/'journal'),
                           {'model.complete': lambda p: {'content': secret}}, sensitive_values=[secret]) as server:
        with pytest.raises(rpc.ProtocolError, match='OUTCOME_UNKNOWN'):
            rpc.RpcClient(server.path, 'token').call('model.complete', {})
    assert secret not in ''.join(p.read_text() for p in (tmp_path/'journal').glob('*.json'))
