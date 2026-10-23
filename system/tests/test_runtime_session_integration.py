"""Real-container runtime counterexamples; missing Docker is an explicit failure.

All candidate programs are fixed test fixtures. They use the cached trusted
image, synthetic sentinels and this test's disposable directories only.
"""
from contextlib import contextmanager
from decimal import Decimal
import errno
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import threading
import time
import uuid

import pytest

from contractedge.contracts.runtime import FrozenInvocation, RuntimeSpec
from contractedge.execution.runtime_session import RuntimeSession
from contractedge.source.snapshot import compute_snapshot


PYTHON = '/usr/bin/python3'


def docker(*arguments, check=True, timeout=15):
    environment = {key: os.environ[key] for key in
                   ('PATH', 'DOCKER_HOST', 'DOCKER_CONFIG', 'DOCKER_API_VERSION') if key in os.environ}
    result = subprocess.run(['docker', *arguments], env=environment,
                            capture_output=True, text=True, timeout=timeout)
    if check:
        assert result.returncode == 0, (arguments, result.returncode, result.stderr)
    return result


@pytest.fixture(scope='module')
def runtime_config():
    image = os.environ.get('CONTRACTEDGE_RUNTIME_IMAGE')
    endpoint = os.environ.get('DOCKER_HOST')
    assert image and '@sha256:' in image, 'A trusted cached digest-pinned test image is required'
    assert endpoint and endpoint.startswith('unix://'), 'Tests require an explicitly selected local task Docker socket'
    docker('version', '--format', '{{.Server.APIVersion}}')
    docker('image', 'inspect', image, '--format', '{{.Id}}')
    return {'image': image, 'docker_host': endpoint,
            'docker_api_version': os.environ.get('DOCKER_API_VERSION') or None}


def prepare_session(root, runtime_config, program, *, args=(), timeout=15, **limits):
    source = root / 'source'
    source.mkdir(parents=True)
    (source / 'fixture.txt').write_text('published candidate source\n')
    inputs = root / 'public-inputs'
    inputs.mkdir()
    (inputs / 'public.json').write_text('{"published": true}\n')
    invocation = FrozenInvocation.freeze((PYTHON, '-c', program, *map(str, args)))
    settings = dict(runtime_config, workspace_root=root, session_root=root / ('session-' + uuid.uuid4().hex),
                    allowed_invocation_digests=(invocation.digest,), python_executable=PYTHON,
                    uid=10001, gid=10001, cpus=2, memory_bytes=2 * 1024**3,
                    pids_limit=64, tmpfs_bytes=16 * 1024**2,
                    stdout_limit=8192, stderr_limit=8192, output_limit=16384,
                    cleanup_seconds=5)
    settings.update(limits)
    spec = RuntimeSpec(**settings)
    session = RuntimeSession.open(spec, deadline=time.monotonic() + timeout)
    return source, inputs, invocation, spec, session


@contextmanager
def session_for(root, runtime_config, program, **options):
    values = prepare_session(root, runtime_config, program, **options)
    try:
        yield values
    finally:
        values[-1].close()


def output(run, stream='stdout'):
    return Path(run[stream + '_path']).read_bytes()


def assert_removed(run):
    assert run['cleanup']['status'] == 'CONFIRMED', run
    container = run.get('container_id')
    assert container, run
    result = docker('inspect', container, check=False)
    assert result.returncode != 0 and 'no such' in result.stderr.lower(), result.stderr


KERNEL_AND_FILESYSTEM = r'''
import errno,json,os,pathlib
def read(path): return pathlib.Path(path).read_text().strip()
if pathlib.Path('/sys/fs/cgroup/cpu.max').exists():
    cpu=read('/sys/fs/cgroup/cpu.max'); memory=read('/sys/fs/cgroup/memory.max'); pids=read('/sys/fs/cgroup/pids.max')
else:
    c=pathlib.Path('/sys/fs/cgroup/cpu')
    if not (c/'cpu.cfs_quota_us').exists(): c=pathlib.Path('/sys/fs/cgroup/cpu,cpuacct')
    cpu=read(c/'cpu.cfs_quota_us')+' '+read(c/'cpu.cfs_period_us')
    memory=read('/sys/fs/cgroup/memory/memory.limit_in_bytes'); pids=read('/sys/fs/cgroup/pids/pids.max')
status=dict(line.split(':',1) for line in read('/proc/self/status').splitlines() if ':' in line)
mounts={line.split()[1]:line.split()[3].split(',') for line in read('/proc/mounts').splitlines()}
def attempt(path):
    try:
        with open(path,'ab') as f: f.write(b'candidate attempted write\n')
    except OSError as e: return e.errno
    return None
source_error=attempt('/source/fixture.txt'); root_error=attempt('/var/tmp/ce-readonly-check')
pathlib.Path('/tmp/ce-writable-check').write_text('temporary scratch')
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'cpu_max':cpu,'memory_max':memory,'pids_max':pids,
 'cap_eff':status['CapEff'].strip(),'no_new_privs':status['NoNewPrivs'].strip(),
 'root_options':mounts['/'],'source_options':mounts['/source'],'inputs_options':mounts['/inputs'],
 'source_error':source_error,'root_error':root_error,
 'source_text':pathlib.Path('/source/fixture.txt').read_text(),
 'public_input':json.loads(pathlib.Path('/inputs/public.json').read_text()),
 'tmp_written':pathlib.Path('/tmp/ce-writable-check').read_text()},sort_keys=True))
'''


def test_actual_kernel_limits_nonroot_privileges_and_readonly_mounts(tmp_path, runtime_config):
    with session_for(tmp_path, runtime_config, KERNEL_AND_FILESYSTEM,
                     docker_api_version=None) as (source, inputs, inv, spec, session):
        before = compute_snapshot(source).snapshot_id
        run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
        assert run['status'] == 'EXECUTION_PASS', run
        actual = json.loads(output(run))
        quota, period = actual['cpu_max'].split()
        assert Decimal(quota) / Decimal(period) == Decimal('2')
        assert int(actual['memory_max']) == 2 * 1024**3 and int(actual['pids_max']) == 64
        assert actual['uid'] == actual['gid'] == 10001
        assert int(actual['cap_eff'], 16) == 0 and int(actual['no_new_privs']) == 1
        assert all('ro' in actual[key] for key in ('root_options', 'source_options', 'inputs_options'))
        assert actual['source_error'] in {errno.EROFS, errno.EACCES}
        assert actual['root_error'] == errno.EROFS
        assert actual['source_text'] == 'published candidate source\n'
        assert actual['public_input'] == {'published': True} and actual['tmp_written'] == 'temporary scratch'
        assert compute_snapshot(source).snapshot_id == before
        assert run['attestation']['status'] == 'VERIFIED'
        kernel = run['attestation']['kernel']
        for field in ('uid', 'gid', 'cpu_max', 'memory_max', 'pids_max'):
            assert str(kernel[field]) == str(actual[field])
        assert_removed(run)


BOUNDARIES = r'''
import json,os,pathlib,sys
def access(path):
    try: return {'readable':True,'body':pathlib.Path(path).read_text()}
    except OSError as e: return {'readable':False,'errno':e.errno,'exists':os.path.exists(path)}
print(json.dumps({'sentinel':access(sys.argv[1]),'host_socket':access(sys.argv[2]),
 'standard_socket_exists':os.path.exists('/var/run/docker.sock'),
 'control_secret':os.environ.get('CE_RUNTIME_INTEGRATION_CONTROL_SECRET'),
 'environment_keys':sorted(os.environ)},sort_keys=True))
'''


def test_host_sentinel_control_environment_and_docker_socket_are_not_exposed(tmp_path, runtime_config, monkeypatch):
    sentinel = tmp_path.parent / ('outside-' + uuid.uuid4().hex)
    sentinel.write_text('synthetic-host-sentinel-' + uuid.uuid4().hex)
    monkeypatch.setenv('CE_RUNTIME_INTEGRATION_CONTROL_SECRET', 'synthetic-control-' + uuid.uuid4().hex)
    try:
        root = tmp_path / 'case'
        socket_path = runtime_config['docker_host'].removeprefix('unix://')
        with session_for(root, runtime_config, BOUNDARIES, args=(sentinel, socket_path)) as (source, inputs, inv, spec, session):
            run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
            assert run['status'] == 'EXECUTION_PASS', run
            actual = json.loads(output(run))
            assert not actual['sentinel']['readable'] and not actual['sentinel']['exists']
            assert not actual['host_socket']['readable'] and not actual['standard_socket_exists']
            assert actual['control_secret'] is None
            assert not {'DOCKER_HOST', 'DOCKER_CONFIG', 'PYTHONPATH', 'CE_RUNTIME_INTEGRATION_CONTROL_SECRET'} & set(actual['environment_keys'])
            assert sentinel.read_bytes() not in output(run)
            assert_removed(run)
    finally:
        sentinel.unlink(missing_ok=True)


def test_source_symlink_escape_is_rejected_before_execution(tmp_path, runtime_config):
    with session_for(tmp_path, runtime_config, 'print("must not execute")') as (source, inputs, inv, spec, session):
        outside = tmp_path / 'not-source.txt'
        outside.write_text('synthetic outside file')
        (source / 'escape.txt').symlink_to(outside)
        run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
        assert run['status'] == 'REJECTED', run
        assert run.get('container_id') is None and run['cleanup']['status'] == 'CONFIRMED'
        assert outside.read_text() == 'synthetic outside file'


NETWORK = r'''
import json,pathlib,socket,sys
try:
    with socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=1) as s: s.sendall(b'candidate')
    connected=True; error=None
except OSError as e: connected=False; error=type(e).__name__
print(json.dumps({'connected':connected,'error':error,'routes':pathlib.Path('/proc/net/route').read_text()}))
'''


def test_candidate_cannot_connect_to_a_control_verified_host_listener(tmp_path, runtime_config):
    # UDP connect selects an existing local interface without sending a packet.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(('192.0.2.1', 9))
        address = probe.getsockname()[0]
    hits = []
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            hits.append(self.request.recv(64))
    with socketserver.ThreadingTCPServer(('0.0.0.0', 0), Handler) as listener:
        thread = threading.Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            port = listener.server_address[1]
            with socket.create_connection((address, port), timeout=2) as check:
                check.sendall(b'control-before')
            with session_for(tmp_path, runtime_config, NETWORK, args=(address, port)) as (source, inputs, inv, spec, session):
                run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
                assert run['status'] == 'EXECUTION_PASS', run
                actual = json.loads(output(run))
                assert actual['connected'] is False and actual['error']
                rows = actual['routes'].splitlines()[1:]
                assert not any(row.split()[1] == '00000000' for row in rows if row.split())
                assert_removed(run)
            with socket.create_connection((address, port), timeout=2) as check:
                check.sendall(b'control-after')
        finally:
            listener.shutdown()
            thread.join(timeout=2)
    assert b'candidate' not in hits and set(hits) == {b'control-before', b'control-after'}


TMPFS_FILL = r'''
import errno,json,os
written=0; failure=None
try:
    with open('/tmp/fill','wb',buffering=0) as target:
        for i in range(64): written+=target.write(b'x'*(1024*1024))
except OSError as e: failure=e.errno
stat=os.statvfs('/tmp')
print(json.dumps({'written':written,'errno':failure,'capacity':stat.f_blocks*stat.f_frsize}))
'''


def test_tmpfs_fills_at_its_declared_bound_without_host_disk_writes(tmp_path, runtime_config):
    with session_for(tmp_path, runtime_config, TMPFS_FILL, tmpfs_bytes=8 * 1024**2) as (source, inputs, inv, spec, session):
        run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
        assert run['status'] == 'EXECUTION_PASS', run
        actual = json.loads(output(run))
        assert actual['errno'] == errno.ENOSPC and actual['capacity'] == 8 * 1024**2
        assert 0 < actual['written'] <= actual['capacity']
        assert not (source / 'fill').exists()
        assert_removed(run)


PID_LIMIT = r'''
import errno,json,os
reader,writer=os.pipe(); children=[]; failure=None
try:
    for i in range(80):
        try: child=os.fork()
        except OSError as e: failure=e.errno; break
        if child==0:
            os.close(writer); os.read(reader,1); os._exit(0)
        children.append(child)
finally:
    os.close(reader); os.close(writer)
    for child in children: os.waitpid(child,0)
print(json.dumps({'spawned':len(children),'errno':failure}))
'''


def test_fork_stops_at_actual_pid_limit_and_children_are_reaped(tmp_path, runtime_config):
    with session_for(tmp_path, runtime_config, PID_LIMIT, pids_limit=16) as (source, inputs, inv, spec, session):
        run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
        assert run['status'] == 'EXECUTION_PASS', run
        actual = json.loads(output(run))
        assert actual['errno'] == errno.EAGAIN and 0 < actual['spawned'] < 16
        assert int(run['attestation']['kernel']['pids_max']) == 16
        assert_removed(run)


def test_memory_exhaustion_is_a_failed_execution_inside_the_effective_limit(tmp_path, runtime_config):
    program = "import sys;print('ALLOCATING',flush=True);blocks=[]\nwhile True: blocks.append(bytearray(8*1024*1024))"
    with session_for(tmp_path, runtime_config, program, memory_bytes=128 * 1024**2) as (source, inputs, inv, spec, session):
        run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
        assert run['status'] == 'EXECUTION_FAILED', run
        assert run['exit_code'] != 0 and b'ALLOCATING' in output(run)
        assert int(run['attestation']['kernel']['memory_max']) == 128 * 1024**2
        assert_removed(run)


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_output_flood_is_terminated_and_durable_files_remain_bounded(tmp_path, runtime_config, stream):
    descriptor = 1 if stream == 'stdout' else 2
    program = f"import os\nwhile True: os.write({descriptor}, b'x'*65536)"
    with session_for(tmp_path, runtime_config, program, stdout_limit=4096,
                     stderr_limit=4096, output_limit=8192) as (source, inputs, inv, spec, session):
        run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
        assert run['status'] == 'OUTPUT_LIMIT', run
        assert len(output(run, stream)) == 4096
        assert sum(len(output(run, key)) for key in ('stdout', 'stderr')) <= 8192
        for key in ('stdout', 'stderr'):
            assert run[key + '_bytes'] == len(output(run, key))
        assert_removed(run)


def test_timeout_kills_a_term_ignoring_process_and_its_child(tmp_path, runtime_config):
    program = r'''
import os,signal,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child=os.fork()
if child==0:
    while True: time.sleep(1)
print('PARENT='+str(os.getpid())+' CHILD='+str(child),flush=True)
while True: time.sleep(1)
'''
    started = time.monotonic()
    with session_for(tmp_path, runtime_config, program, timeout=5) as (source, inputs, inv, spec, session):
        run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
        assert run['status'] == 'TIMEOUT', run
        assert b'PARENT=' in output(run) and b'CHILD=' in output(run)
        assert time.monotonic() - started < 15
        assert_removed(run)


def test_cleanup_does_not_remove_an_unrelated_canary_mounting_the_session_directory(tmp_path, runtime_config):
    with session_for(tmp_path, runtime_config, 'print("finished")') as (source, inputs, inv, spec, session):
        token = uuid.uuid4().hex
        canary = None
        try:
            canary = docker('run', '-d', '--rm', '--pull=never', '--network=none', '--read-only',
                            '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--user=10001:10001',
                            '--cpus=0.1', '--memory=67108864', '--pids-limit=8',
                            '--label', 'contractedge.integration.canary=' + token,
                            '--mount', 'type=bind,src=' + str(spec.session_root) + ',dst=/unrelated,readonly',
                            spec.image, PYTHON, '-c', 'import time;time.sleep(60)').stdout.strip()
            run = session.run_candidate(inv, snapshot=compute_snapshot(source), public_inputs=inputs)
            assert run['status'] == 'EXECUTION_PASS', run
            assert_removed(run)
            session.close()
            assert docker('inspect', '--format', '{{.State.Running}}', canary).stdout.strip() == 'true'
        finally:
            if canary:
                owner = docker('inspect', '--format', '{{index .Config.Labels "contractedge.integration.canary"}}',
                               canary, check=False)
                assert owner.returncode == 0 and owner.stdout.strip() == token
                docker('rm', '-f', canary)
