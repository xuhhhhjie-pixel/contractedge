import json
import os
from pathlib import Path
import sys
import time

import pytest

from contractedge.contracts.runtime import FrozenInvocation, RuntimeRejected, RuntimeSpec
from contractedge.execution.runtime_session import RuntimeSession, _capture


IMAGE = 'sha256:' + 'a' * 64


def spec(root, **overrides):
    invocation = FrozenInvocation.freeze(('/usr/bin/python3', '-c', 'print("fixture")'))
    values = dict(image=IMAGE, docker_host='unix:///tmp/contractedge-fixture.sock',
        workspace_root=str(root), session_root=str(root / 'session'),
        allowed_invocation_digests=(invocation.digest,))
    values.update(overrides)
    return RuntimeSpec(**values)


@pytest.mark.parametrize('overrides', [
    {'image': 'python:latest'}, {'uid': 0}, {'gid': 0}, {'cpus': 0},
    {'memory_bytes': -1}, {'pids_limit': 0}, {'tmpfs_bytes': 0},
    {'docker_host': 'tcp://127.0.0.1:2375'},
    {'allowed_invocation_digests': ()}, {'stdout_limit': -1},
    {'docker_api_version': 'bad version'},
])
def test_invalid_frozen_spec_is_rejected_before_session_effects(tmp_path, overrides):
    with pytest.raises(RuntimeRejected):
        RuntimeSession.open(spec(tmp_path, **overrides), deadline=time.monotonic() + 10)
    assert not (tmp_path / 'session').exists()


def test_spec_unknown_privilege_fields_are_rejected(tmp_path):
    values = spec(tmp_path).to_wire()
    values['privileged'] = True
    with pytest.raises(RuntimeRejected):
        RuntimeSpec.from_mapping(values)


def test_shell_string_requires_explicit_trusted_registration():
    with pytest.raises(RuntimeRejected):
        FrozenInvocation.freeze('/bin/sh -c whoami')
    with pytest.raises(RuntimeRejected):
        FrozenInvocation.freeze(('/bin/sh', '-c', 'whoami'))
    invocation = FrozenInvocation.freeze(('/bin/sh', '-c', 'printf fixture'), trusted_shell=True)
    invocation.validate()
    assert len(invocation.digest) == 64


def test_session_token_is_durable_before_any_docker_effect(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('open must not start Docker resources')
    monkeypatch.setattr('subprocess.Popen', unexpected)
    session = RuntimeSession.open(spec(tmp_path), deadline=time.monotonic() + 10)
    saved = json.loads((tmp_path / 'session' / 'session.json').read_text())
    assert saved['session_token'] == session.token
    assert saved['containers'] == []
    assert saved['status'] == 'OPEN'


def test_client_environment_is_explicit_and_does_not_inherit_control_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-control-sentinel')
    monkeypatch.setenv('HTTP_PROXY', 'fake-control-proxy')
    session = RuntimeSession.open(spec(tmp_path), deadline=time.monotonic() + 10)
    environment = session._client_env()
    assert 'DEEPSEEK_API_KEY' not in environment
    assert 'HTTP_PROXY' not in environment
    assert 'DOCKER_API_VERSION' not in environment
    assert environment['DOCKER_HOST'] == 'unix:///tmp/contractedge-fixture.sock'
    assert environment['DOCKER_CONFIG'].startswith(str(tmp_path / 'session'))
    assert environment['HOME'].startswith(str(tmp_path / 'session'))


def test_session_location_rejects_symlink_parent(tmp_path):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'linked').symlink_to(tmp_path / 'real', target_is_directory=True)
    with pytest.raises(RuntimeRejected):
        RuntimeSession.open(spec(tmp_path, session_root=str(tmp_path / 'linked' / 'session')),
            deadline=time.monotonic() + 10)


def test_output_is_stream_bounded_across_both_channels(tmp_path):
    result = _capture([sys.executable, '-c',
        'import os;os.write(1,b"x"*20000);os.write(2,b"y"*20000)'],
        env={'PATH': '/usr/bin:/bin'}, deadline=time.monotonic() + 10,
        stdout_path=tmp_path / 'stdout', stderr_path=tmp_path / 'stderr',
        stdout_limit=4096, stderr_limit=4096, output_limit=5000)
    assert result['reason'] == 'OUTPUT_LIMIT'
    assert (tmp_path / 'stdout').stat().st_size <= 4096
    assert (tmp_path / 'stderr').stat().st_size <= 4096
    assert result['stdout_bytes'] + result['stderr_bytes'] <= 5000


def test_capture_deadline_terminates_its_process_group(tmp_path):
    started = time.monotonic()
    result = _capture([sys.executable, '-c', 'import time;time.sleep(10)'],
        env={'PATH': '/usr/bin:/bin'}, deadline=started + 0.15,
        stdout_path=tmp_path / 'stdout', stderr_path=tmp_path / 'stderr',
        stdout_limit=4096, stderr_limit=4096, output_limit=8192)
    assert result['reason'] == 'TIMEOUT'
    assert time.monotonic() - started < 3


def test_unregistered_invocation_is_rejected_without_docker_dispatch(tmp_path, monkeypatch):
    session = RuntimeSession.open(spec(tmp_path), deadline=time.monotonic() + 10)
    monkeypatch.setattr(session, '_docker', lambda *args, **kwargs: pytest.fail('unregistered command reached Docker'))
    other = FrozenInvocation.freeze(('/usr/bin/python3', '-c', 'print("unregistered")'))
    with pytest.raises(RuntimeRejected):
        session.run_candidate(other, snapshot=None)

def test_path_fields_are_normalized_and_allowlist_is_frozen(tmp_path):
    registered = list(spec(tmp_path).allowed_invocation_digests)
    original = spec(tmp_path, allowed_invocation_digests=registered,
        workspace_root=tmp_path, session_root=tmp_path / 'session')
    session = RuntimeSession.open(original, deadline=time.monotonic() + 10)
    registered.clear()
    assert session.spec.allowed_invocation_digests
    assert json.loads((session.root / 'session.json').read_text())['spec']['workspace_root'] == str(tmp_path)


def test_parent_traversal_cannot_escape_owned_runtime_workspace(tmp_path):
    with pytest.raises(RuntimeRejected):
        RuntimeSession.open(spec(tmp_path, session_root=tmp_path / '..' / 'escaped-session'),
            deadline=time.monotonic() + 10)


def test_mutated_caller_receipt_cannot_authorize_observer_input(tmp_path, monkeypatch):
    import hashlib
    from contractedge.execution.runtime_session import _atomic_json
    session = RuntimeSession.open(spec(tmp_path), deadline=time.monotonic() + 10)
    original = session.root / 'run-0-candidate'
    original.mkdir()
    stdout, stderr = original / 'stdout.bin', original / 'stderr.bin'
    stdout.write_bytes(b'actual output');stderr.write_bytes(b'')
    candidate = {'run_id': '0', 'role': 'candidate', 'status': 'EXECUTION_PASS',
        'cleanup': {'status': 'CONFIRMED'}, 'stdout_path': str(stdout), 'stderr_path': str(stderr),
        'stdout_bytes': 13, 'stderr_bytes': 0, 'stdout_sha256': hashlib.sha256(b'actual output').hexdigest(),
        'stderr_sha256': hashlib.sha256(b'').hexdigest(), 'truncated': False}
    _atomic_json(original / 'receipt.json', candidate)
    session.runs.append(candidate)
    forged = tmp_path / 'forged-output'
    forged.write_bytes(b'forged PASS')
    candidate.update(stdout_path=str(forged), stdout_bytes=11,
        stdout_sha256=hashlib.sha256(b'forged PASS').hexdigest())
    monkeypatch.setattr(session, '_cleanup_run', lambda *a, **k: {'status': 'CONFIRMED', 'errors': []})
    monkeypatch.setattr(session, '_docker', lambda *a, **k: pytest.fail('forged receipt reached Docker'))
    invocation = FrozenInvocation.freeze(('/usr/bin/python3', '-c', 'print("fixture")'))
    result = session.run_observer(invocation, candidate, tmp_path / 'checks')
    assert result['status'] == 'REJECTED'
    assert 'receipt was changed' in result['error']

def test_nonzero_candidate_prefix_cannot_become_complete_observer_input(tmp_path, monkeypatch):
    session = RuntimeSession.open(spec(tmp_path), deadline=time.monotonic() + 10)
    candidate = {'run_id': '0', 'role': 'candidate', 'status': 'EXECUTION_FAILED',
        'cleanup': {'status': 'CONFIRMED'}, 'truncated': False}
    session.runs.append(candidate)
    monkeypatch.setattr(session, '_cleanup_run', lambda *a, **k: {'status': 'CONFIRMED', 'errors': []})
    monkeypatch.setattr(session, '_docker', lambda *a, **k: pytest.fail('failed prefix reached Docker'))
    invocation = FrozenInvocation.freeze(('/usr/bin/python3', '-c', 'print("fixture")'))
    result = session.run_observer(invocation, candidate, tmp_path / 'checks')
    assert result['status'] == 'REJECTED'
    assert 'complete, cleanup-confirmed' in result['error']


def test_created_id_is_durable_before_start_and_execution(tmp_path, monkeypatch):
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.execution.runtime_session import _OWNER, _OWNER_LABEL, _TOKEN_LABEL, _RUN_LABEL
    session = RuntimeSession.open(spec(tmp_path), deadline=time.monotonic() + 10)
    invocation = FrozenInvocation.freeze(('/usr/bin/python3', '-c', 'print("fixture")'))
    source = tmp_path / 'source'
    source.mkdir();(source / 'app.py').write_text('print("fixture")')
    cid, state, steps = 'b' * 64, {'gone': False}, []
    def result(stdout=b''):
        return {'reason': 'COMPLETE', 'exit_code': 0, 'stdout': stdout, 'stderr': b''}
    def durable():
        saved = json.loads((session.root / 'session.json').read_text())
        assert saved['runs'][0]['container_id'] == cid
        assert cid in saved['containers']
    def docker(args, **kwargs):
        if args[:2] == ['image', 'inspect']:
            return result(json.dumps([{'Id': IMAGE, 'Config': {}}]).encode())
        if args[0] == 'create':
            saved = json.loads((session.root / 'session.json').read_text())
            assert saved['session_token'] == session.token
            assert saved['runs'][0]['phase'] == 'CREATING'
            Path(args[args.index('--cidfile') + 1]).write_text(cid)
            steps.append('create')
            return result(cid.encode())
        if args[0] == 'start':
            durable();steps.append('start')
            return result(cid.encode())
        if args[0] == 'ps':
            return result(b'' if state['gone'] else cid.encode())
        if args[0] == 'rm':
            durable();state['gone'] = True;steps.append('rm')
            return result(cid.encode())
        pytest.fail('Unexpected Docker action: ' + repr(args))
    def inspect(identifier, **kwargs):
        if state['gone']:
            return None
        durable()
        return {'Id': cid, 'Config': {'Labels': {_OWNER_LABEL: _OWNER,
            _TOKEN_LABEL: session.token, _RUN_LABEL: '0'}}}
    def capture(argv, **kwargs):
        durable();steps.append('exec')
        Path(kwargs['stdout_path']).write_bytes(b'fixture')
        Path(kwargs['stderr_path']).write_bytes(b'')
        return {'reason': 'COMPLETE', 'exit_code': 0, 'truncated': False}
    monkeypatch.setattr(session, '_docker', docker)
    monkeypatch.setattr(session, '_inspect', inspect)
    monkeypatch.setattr(session, '_validate_inspect', lambda *a: {'container_id': cid})
    monkeypatch.setattr(session, '_kernel_attestation', lambda *a: {'uid': 10001})
    monkeypatch.setattr('contractedge.execution.runtime_session._capture', capture)
    actual = session.run_candidate(invocation, compute_snapshot(source))
    assert actual['status'] == 'EXECUTION_PASS'
    assert actual['cleanup']['status'] == 'CONFIRMED'
    assert steps == ['create', 'start', 'exec', 'rm']


def test_process_failure_remains_primary_when_source_and_cleanup_fail(tmp_path, monkeypatch):
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.execution import runtime_session as runtime_module

    session = RuntimeSession.open(spec(tmp_path), deadline=time.monotonic() + 10)
    session._api_version = '1.43'
    invocation = FrozenInvocation.freeze(('/usr/bin/python3', '-c', 'print("fixture")'))
    source = tmp_path / 'source'
    source.mkdir(); (source / 'app.py').write_text('print("fixture")\n')
    cid = 'c' * 64

    def result(stdout=b'', stderr=b'', exit_code=0):
        return {'reason': 'COMPLETE', 'exit_code': exit_code,
                'stdout': stdout, 'stderr': stderr}

    def docker(args, **kwargs):
        if args[:2] == ['image', 'inspect']:
            return result(json.dumps([{'Id': IMAGE, 'Config': {}}]).encode())
        if args[0] == 'create':
            Path(args[args.index('--cidfile') + 1]).write_text(cid)
            return result(cid.encode())
        if args[0] == 'start':
            return result(cid.encode())
        pytest.fail('Unexpected Docker action: ' + repr(args))

    def capture(argv, **kwargs):
        Path(kwargs['stdout_path']).write_bytes(b'failed')
        Path(kwargs['stderr_path']).write_bytes(b'error')
        # Simulate an external edit after the process has returned.
        (source / 'app.py').write_text('print("changed")\n')
        return {'reason': 'COMPLETE', 'exit_code': 7, 'truncated': False}

    monkeypatch.setattr(session, '_docker', docker)
    monkeypatch.setattr(session, '_inspect', lambda *a, **k: {'Id': cid})
    monkeypatch.setattr(session, '_validate_inspect', lambda *a: {'container_id': cid})
    monkeypatch.setattr(session, '_kernel_attestation', lambda *a: {'uid': 10001})
    monkeypatch.setattr(session, '_cleanup_run', lambda *a, **k: {
        'status': 'FAILED', 'container_id': cid, 'errors': ['cleanup unavailable']})
    monkeypatch.setattr(runtime_module, '_capture', capture)

    run = session.run_candidate(invocation, compute_snapshot(source))
    assert run['status'] == 'EXECUTION_FAILED'
    assert run['primary_failure']['code'] == 'EXECUTION_FAILED'
    assert [item['code'] for item in run['secondary_failures']] == [
        'SOURCE_CHANGED', 'CLEANUP_UNCONFIRMED']
    assert run['failure_records'][0] == run['primary_failure']
    receipt = json.loads((session.root / 'run-0-candidate' / 'receipt.json').read_text())
    assert receipt['status'] == 'EXECUTION_FAILED'
    assert receipt['primary_failure']['code'] == 'EXECUTION_FAILED'


def test_close_persists_close_cleanup_failure_without_replacing_process_root(tmp_path, monkeypatch):
    from contractedge.contracts.failures import make_failure
    session = RuntimeSession.open(spec(tmp_path), deadline=time.monotonic() + 10)
    run_dir = session.root / 'run-0-candidate'
    run_dir.mkdir()
    primary = make_failure('candidate_process', 'TIMEOUT', failure_class='execution',
                           dispatch_state='response_known')
    run = {'run_id': '0', 'role': 'candidate', 'status': 'TIMEOUT',
           'cleanup': {'status': 'CONFIRMED', 'errors': []},
           'primary_failure': primary, 'secondary_failures': [],
           'failure_records': [primary]}
    (run_dir / 'receipt.json').write_text(json.dumps(run))
    session.runs.append(run)
    monkeypatch.setattr(session, '_cleanup_run', lambda *a, **k: {
        'status': 'FAILED', 'errors': ['close cleanup unavailable']})

    session.close()
    assert run['status'] == 'TIMEOUT'
    assert run['primary_failure']['code'] == 'TIMEOUT'
    assert run['close_cleanup_failure']['code'] == 'CLEANUP_UNCONFIRMED'
    saved = json.loads((run_dir / 'receipt.json').read_text())
    assert saved['status'] == 'TIMEOUT'
    assert saved['failure_records'][0]['code'] == 'TIMEOUT'
    assert saved['failure_records'][1]['code'] == 'CLEANUP_UNCONFIRMED'
