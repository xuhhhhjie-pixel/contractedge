"""Docker-only, bounded public candidate execution with durable ownership.

The control plane copies the exact source snapshot, never imports candidate
modules, and sends only explicitly frozen argv into an attested container.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import subprocess
import time
import uuid

from contractedge.contracts.runtime import (
    FrozenInvocation, RuntimeRejected, RuntimeSpec, canonical_digest, no_symlink_path,
)
from contractedge.source.snapshot import compute_snapshot
from contractedge.contracts.failures import make_failure, project_terminal_status


_OWNER = 'contractedge-runtime-v1'
_TOKEN_LABEL = 'contractedge.runtime.session'
_OWNER_LABEL = 'contractedge.runtime.owner'
_RUN_LABEL = 'contractedge.runtime.run'
_CANDIDATE_ENV = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp',
    'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}
_KERNEL_PROBE = r"""
import json,os
from pathlib import Path
def read(*names):
    for name in names:
        p=Path('/sys/fs/cgroup')/name
        if p.is_file(): return p.read_text().strip()
    return None
status={}
for row in Path('/proc/self/status').read_text().splitlines():
    if ':' in row:
        k,v=row.split(':',1);status[k]=v.strip()
cpu=read('cpu.max')
if cpu is None:
    q=read('cpu/cpu.cfs_quota_us','cpu.cfs_quota_us')
    p=read('cpu/cpu.cfs_period_us','cpu.cfs_period_us')
    cpu=(q+' '+p) if q and p else None
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'cpu_max':cpu,
 'memory_max':read('memory.max','memory/memory.limit_in_bytes','memory.limit_in_bytes'),
 'pids_max':read('pids.max','pids/pids.max'),'cap_eff':status.get('CapEff'),
 'no_new_privs':status.get('NoNewPrivs'),'seccomp':status.get('Seccomp'),
 'tmpfs_bytes':os.statvfs('/tmp').f_frsize*os.statvfs('/tmp').f_blocks,
 'tmp_mount':[r for r in Path('/proc/self/mountinfo').read_text().splitlines() if r.split()[4]=='/tmp']}))
"""
_EXEC = "import json,os,sys;a=json.loads(sys.argv[1]);e=json.loads(sys.argv[2]);os.execvpe(a[0],a,e)"


def _atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _capture(argv, *, env, deadline, stdout_path, stderr_path,
             stdout_limit, stderr_limit, output_limit):
    """Continuously drain two binary pipes, storing only their bounded prefix."""
    paths = {'stdout': Path(stdout_path), 'stderr': Path(stderr_path)}
    counts, caps = {'stdout': 0, 'stderr': 0}, {'stdout': stdout_limit, 'stderr': stderr_limit}
    streams = {name: path.open('wb') for name, path in paths.items()}
    selector = selectors.DefaultSelector()
    process, reason, exit_code = None, 'COMPLETE', None
    try:
        if time.monotonic() >= deadline:
            reason = 'TIMEOUT'
        else:
            process = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=dict(env), shell=False, start_new_session=True)
            for name, pipe in [('stdout', process.stdout), ('stderr', process.stderr)]:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = 'TIMEOUT'
                    break
                for event, _ in selector.select(min(remaining, 0.1)):
                    data = os.read(event.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(event.fileobj)
                        event.fileobj.close()
                        continue
                    name = event.data
                    available = min(caps[name] - counts[name], output_limit - sum(counts.values()))
                    accepted = data[:max(0, available)]
                    streams[name].write(accepted)
                    counts[name] += len(accepted)
                    if len(accepted) != len(data):
                        reason = 'OUTPUT_LIMIT'
                        break
                if reason != 'COMPLETE':
                    break
            if reason != 'COMPLETE' and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                exit_code = process.wait(timeout=max(0.1, min(1.0, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                reason = 'TIMEOUT'
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                exit_code = process.wait(timeout=1)
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
        selector.close()
        for stream in streams.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        if process is not None:
            for pipe in (process.stdout, process.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
    return {'reason': reason, 'exit_code': exit_code,
        'stdout_path': str(paths['stdout']), 'stderr_path': str(paths['stderr']),
        'stdout_bytes': counts['stdout'], 'stderr_bytes': counts['stderr'],
        'stdout_sha256': _hash(paths['stdout']), 'stderr_sha256': _hash(paths['stderr']),
        'truncated': reason == 'OUTPUT_LIMIT'}


def _mount_points():
    points = []
    for row in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = row.split()
        if len(fields) > 4:
            value = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), fields[4])
            points.append(Path(value))
    return points


def _validate_input_path(path, workspace):
    path = no_symlink_path(path)
    if path == workspace or not path.is_relative_to(workspace):
        raise RuntimeRejected('Only a task-owned source/input descendant may be mounted')
    denied = {'.ssh', '.codex', 'secrets', 'credentials', 'evaluator', 'evaluation',
        'gt', 'fixed', 'hidden', 'oracle'}
    relative = path.relative_to(workspace)
    if any(part.casefold() in denied for part in relative.parts):
        raise RuntimeRejected('Private, evaluator or credential paths cannot enter runtime')
    if not path.exists():
        raise RuntimeRejected('Runtime source/input does not exist')
    for mount in _mount_points():
        if mount == path or mount.is_relative_to(path):
            raise RuntimeRejected('Runtime input contains an additional host mount')
    targets = [path]
    if path.is_dir():
        for directory, directories, names in os.walk(path, followlinks=False):
            targets.extend(Path(directory) / name for name in directories + names)
            if len(targets) > 20000:
                raise RuntimeRejected('Runtime input file inventory is too large')
    for target in targets:
        mode = target.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise RuntimeRejected('Runtime source/input must contain only regular files and directories')
    return path


def _readonly_tree(root):
    for directory, _, names in os.walk(root):
        for name in names:
            file = Path(directory) / name
            file.chmod(0o444 | (file.stat().st_mode & 0o111))
        Path(directory).chmod(0o555)


class RuntimeSession:
    @classmethod
    def open(cls, trusted_spec, deadline):
        spec = RuntimeSpec.from_mapping(trusted_spec.to_wire() if isinstance(trusted_spec, RuntimeSpec) else trusted_spec)
        spec.validate()
        if not isinstance(deadline, (int, float)) or not math.isfinite(deadline) or deadline <= time.monotonic():
            raise RuntimeRejected('A future absolute monotonic runtime deadline is required')
        root = no_symlink_path(spec.session_root)
        if root.exists():
            raise RuntimeRejected('Runtime session directory must be new')
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(mode=0o700)
        session = cls()
        session.spec, session.root, session.deadline = spec, root, float(deadline)
        session.workspace = Path(spec.workspace_root).absolute()
        session.token, session.closed, session._command_index = uuid.uuid4().hex, False, 0
        session._api_version = spec.docker_api_version
        session.runs, session.containers = [], []
        (root / 'docker-config').mkdir(mode=0o700)
        (root / 'client-home').mkdir(mode=0o700)
        (root / 'control').mkdir(mode=0o700)
        session.manifest = {'schema_version': 'ce.runtime_session.v1',
            'session_token': session.token, 'status': 'OPEN', 'spec': spec.to_wire(),
            'spec_digest': canonical_digest(spec.to_wire()), 'containers': session.containers,
            'runs': session.runs, 'opened_at': datetime.now(timezone.utc).isoformat()}
        session._save()
        return session

    def _save(self):
        _atomic_json(self.root / 'session.json', self.manifest)

    def _client_env(self):
        result = {'PATH': '/usr/bin:/bin', 'HOME': str(self.root / 'client-home'),
            'DOCKER_CONFIG': str(self.root / 'docker-config'), 'DOCKER_HOST': self.spec.docker_host,
            'LANG': 'C.UTF-8'}
        if self._api_version is not None:
            result['DOCKER_API_VERSION'] = self._api_version
        return result

    def _negotiate_api(self, deadline):
        # Some packaged CLIs do not automatically downgrade against older local
        # daemons. Read the unversioned endpoint; never inherit a user's context.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Runtime deadline exhausted before Docker API negotiation')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(min(remaining, 3.0))
            connection.connect(self.spec.docker_host[7:])
            connection.sendall(b'GET /version HTTP/1.0\r\nHost: localhost\r\n\r\n')
            response = http.client.HTTPResponse(connection)
            response.begin()
            content = response.read(65537)
            if response.status != 200 or len(content) > 65536:
                raise RuntimeError('Local Docker version negotiation returned invalid bounded data')
        version = json.loads(content)
        maximum, minimum = version.get('ApiVersion'), version.get('MinAPIVersion', '1.24')
        if not isinstance(maximum, str) or not re.fullmatch(r'1\.[0-9]+', maximum):
            raise RuntimeError('Docker server did not declare a valid API version')
        self._api_version = maximum
        client = self._docker(['version', '--format', '{{json .Client.APIVersion}}'], deadline=deadline, cap=8192)
        try:
            client_api = json.loads(self._success(client))
            chosen = min((maximum, client_api), key=lambda value: tuple(map(int, value.split('.'))))
            if tuple(map(int, chosen.split('.'))) < tuple(map(int, minimum.split('.'))):
                raise ValueError('Client API is below server minimum')
        except (TypeError, ValueError, AttributeError) as exc:
            self._api_version = None
            raise RuntimeError('Docker client/server API negotiation failed') from exc
        self._api_version = chosen
        self.manifest['docker_api'] = {'negotiated': chosen, 'server_max': maximum,
            'server_min': minimum, 'client_max': client_api}
        self._save()

    def _docker(self, args, *, deadline=None, cap=256 * 1024):
        effective_deadline = self.deadline if deadline is None else deadline
        if self._api_version is None:
            self._negotiate_api(effective_deadline)
        self._command_index += 1
        prefix = self.root / 'control' / str(self._command_index)
        result = _capture([self.spec.docker_binary, *args], env=self._client_env(),
            deadline=self.deadline if deadline is None else deadline,
            stdout_path=prefix.with_suffix('.stdout'), stderr_path=prefix.with_suffix('.stderr'),
            stdout_limit=cap, stderr_limit=cap, output_limit=2 * cap)
        result['stdout'] = Path(result['stdout_path']).read_bytes()
        result['stderr'] = Path(result['stderr_path']).read_bytes()
        return result

    @staticmethod
    def _success(result):
        if result['reason'] == 'TIMEOUT':
            raise TimeoutError('Runtime deadline exhausted during Docker operation')
        if result['reason'] != 'COMPLETE' or result['exit_code'] != 0:
            raise RuntimeError('Docker operation failed: ' + result['stderr'].decode(errors='replace')[:2000])
        return result['stdout']

    def _inspect(self, identifier, *, deadline=None):
        result = self._docker(['container', 'inspect', identifier], deadline=deadline)
        if result['reason'] == 'COMPLETE' and result['exit_code'] != 0:
            error = result['stderr'].decode(errors='replace').casefold()
            if 'no such object' in error or 'no such container' in error:
                return None
        records = json.loads(self._success(result))
        if not isinstance(records, list) or len(records) != 1:
            raise RuntimeError('Docker inspect did not return one container')
        return records[0]

    def _verify_owner(self, inspected, run=None):
        labels = inspected.get('Config', {}).get('Labels') or {}
        if (labels.get(_OWNER_LABEL) != _OWNER or labels.get(_TOKEN_LABEL) != self.token
                or run is not None and labels.get(_RUN_LABEL) != run['run_id']):
            raise RuntimeError('Container ownership mismatch; cleanup and execution prohibited')
        if not re.fullmatch(r'[0-9a-f]{64}', inspected.get('Id', '')):
            raise RuntimeError('Container identity is invalid')
        return inspected['Id']

    def _validate_inspect(self, inspected, run, mounts, image_id):
        self._verify_owner(inspected, run)
        host, config = inspected['HostConfig'], inspected['Config']
        failures = []
        expected = {'ReadonlyRootfs': True, 'Privileged': False, 'NetworkMode': 'none',
            'PidsLimit': self.spec.pids_limit, 'Memory': self.spec.memory_bytes,
            'MemorySwap': self.spec.memory_bytes, 'NanoCpus': round(self.spec.cpus * 1e9),
            'IpcMode': 'private', 'CgroupnsMode': 'private'}
        for key, value in expected.items():
            if host.get(key) != value:
                failures.append(key)
        if host.get('PidMode') not in ('', 'private') or host.get('Devices') or host.get('DeviceRequests'):
            failures.append('namespace/devices')
        if set(host.get('CapDrop') or []) != {'ALL'} or host.get('CapAdd'):
            failures.append('capabilities')
        if not any(value in ('no-new-privileges', 'no-new-privileges:true') for value in host.get('SecurityOpt') or []):
            failures.append('no-new-privileges')
        if config.get('User') != f'{self.spec.uid}:{self.spec.gid}' or inspected.get('Image') != image_id:
            failures.append('user/image')
        actual = {m['Destination']: m for m in inspected.get('Mounts', [])}
        if set(actual) != set(mounts):
            failures.append('mount inventory')
        for destination, source in mounts.items():
            record = actual.get(destination, {})
            if record.get('Type') != 'bind' or record.get('Source') != str(source) or record.get('RW') is not False:
                failures.append('readonly mount:' + destination)
        if set(host.get('Tmpfs') or {}) != {'/tmp'}:
            failures.append('tmpfs inventory')
        if failures:
            raise RuntimeError('Effective container configuration rejected: ' + ', '.join(failures))
        return {'container_id': inspected['Id'], 'image_id': inspected['Image'],
            'user': config['User'], 'network_mode': host['NetworkMode'],
            'mounts': inspected.get('Mounts', []), 'host_config': {key: host.get(key) for key in
                [*expected, 'CapDrop', 'CapAdd', 'SecurityOpt', 'Tmpfs', 'PidMode']}}

    def _kernel_attestation(self, cid):
        output = self._success(self._docker(['exec', cid, self.spec.python_executable,
            '-I', '-c', _KERNEL_PROBE], cap=16384))
        kernel = json.loads(output)
        try:
            quota, period = kernel['cpu_max'].split()
            valid_cpu = quota not in ('max', '-1') and 0 < int(quota) / int(period) <= self.spec.cpus + 0.001
            valid = (kernel['uid'] == self.spec.uid and kernel['gid'] == self.spec.gid
                and valid_cpu and 0 < int(kernel['memory_max']) <= self.spec.memory_bytes
                and 0 < int(kernel['pids_max']) <= self.spec.pids_limit
                and int(kernel['cap_eff'], 16) == 0 and kernel['no_new_privs'] == '1'
                and kernel['seccomp'] == '2'
                and 0 < kernel['tmpfs_bytes'] <= self.spec.tmpfs_bytes
                and len(kernel['tmp_mount']) == 1 and ' - tmpfs ' in kernel['tmp_mount'][0]
                and all(flag in kernel['tmp_mount'][0].split()[5].split(',')
                        for flag in ('rw', 'nosuid', 'nodev', 'noexec')))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            valid = False
        if not valid:
            raise RuntimeError('Effective kernel resource/security limits were not verified: ' + json.dumps(kernel))
        return kernel

    def _copy_source(self, snapshot, destination):
        wire = asdict(snapshot) if is_dataclass(snapshot) else dict(snapshot)
        root = _validate_input_path(wire['source_root'], self.workspace)
        current = compute_snapshot(root)
        if current.snapshot_id != wire.get('snapshot_id') or dict(current.file_digests) != wire.get('file_digests'):
            raise RuntimeRejected('Source snapshot differs from frozen runtime input')
        total = 0
        for relative, digest in current.file_digests.items():
            if '..' in Path(relative).parts or Path(relative).is_absolute():
                raise RuntimeRejected('Snapshot inventory path escapes source')
            if any(part.casefold() in {'.ssh', '.codex', 'secrets', 'credentials', 'evaluator', 'gt', 'fixed'}
                   for part in Path(relative).parts):
                raise RuntimeRejected('Forbidden snapshot artifact cannot enter runtime')
            data = (root / relative).read_bytes()
            total += len(data)
            if total > self.spec.source_max_bytes or time.monotonic() >= self.deadline:
                raise RuntimeRejected('Source snapshot exceeds runtime preparation limits')
            if hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeRejected('Source changed during snapshot copy')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod((root / relative).stat().st_mode & 0o777)
        if compute_snapshot(root).snapshot_id != current.snapshot_id or compute_snapshot(destination).snapshot_id != current.snapshot_id:
            raise RuntimeRejected('Source snapshot copy identity mismatch')
        _readonly_tree(destination)
        return current.snapshot_id

    def _copy_public(self, source, destination, *, single_name):
        if source is None:
            _readonly_tree(destination)
            return canonical_digest({})
        root = _validate_input_path(source, self.workspace)
        files = [root] if root.is_file() else sorted(path for path in root.rglob('*') if path.is_file())
        inventory, total = {}, 0
        for path in files:
            data = path.read_bytes()
            total += len(data)
            if total > self.spec.inputs_max_bytes or time.monotonic() >= self.deadline:
                raise RuntimeRejected('Public input exceeds runtime preparation limits')
            name = single_name if root.is_file() else path.relative_to(root).as_posix()
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            inventory[name] = hashlib.sha256(data).hexdigest()
        _readonly_tree(destination)
        return canonical_digest(inventory)

    def _register_invocation(self, invocation):
        if self.closed:
            raise RuntimeRejected('Runtime session is closed')
        if not isinstance(invocation, FrozenInvocation):
            raise RuntimeRejected('Runtime requires a trusted frozen invocation')
        invocation.validate()
        if invocation.digest not in self.spec.allowed_invocation_digests:
            raise RuntimeRejected('Invocation is not authorized by the frozen runtime spec')

    def run_candidate(self, frozen_invocation, snapshot, public_inputs=None):
        self._register_invocation(frozen_invocation)
        return self._run('candidate', frozen_invocation, snapshot=snapshot, public_inputs=public_inputs)

    def run_observer(self, frozen_observer, bounded_output, public_check_spec):
        self._register_invocation(frozen_observer)
        return self._run('observer', frozen_observer, bounded_output=bounded_output, checks=public_check_spec)

    def _run(self, role, invocation, *, snapshot=None, public_inputs=None, bounded_output=None, checks=None):
        run_id = str(len(self.runs))
        directory = self.root / ('run-' + run_id + '-' + role)
        directory.mkdir(mode=0o700)
        run = {'run_id': run_id, 'role': role, 'invocation_digest': invocation.digest,
            'status': 'PREPARING', 'phase': 'PREPARING', 'container_id': None,
            'planned_container_name': 'ce-' + self.token + '-' + run_id,
            'stdout_path': str(directory / 'stdout.bin'), 'stderr_path': str(directory / 'stderr.bin'),
            'cleanup': {'status': 'PENDING', 'errors': []}}
        for name in ('stdout.bin', 'stderr.bin'):
            (directory / name).write_bytes(b'')
        self.runs.append(run)
        self._save()
        interrupted = None
        try:
            mounts = {}
            if role == 'candidate':
                source, inputs = directory / 'source', directory / 'inputs'
                source.mkdir(); inputs.mkdir()
                run['snapshot_id'] = self._copy_source(snapshot, source)
                run['source_root'] = str(snapshot.source_root if is_dataclass(snapshot) else snapshot['source_root'])
                run['public_inputs_digest'] = self._copy_public(public_inputs, inputs, single_name='public-inputs.json')
                mounts = {'/source': source, '/inputs': inputs}
            else:
                if not isinstance(bounded_output, dict) or not any(bounded_output is item for item in self.runs):
                    raise RuntimeRejected('Observer output must be the actual result of this session')
                if (bounded_output.get('role') != 'candidate' or bounded_output['cleanup']['status'] != 'CONFIRMED'
                        or bounded_output.get('status') != 'EXECUTION_PASS'
                        or bounded_output.get('truncated')):
                    raise RuntimeRejected('Observer requires complete, cleanup-confirmed candidate output')
                persisted = json.loads((self.root / ('run-' + bounded_output['run_id'] + '-candidate') / 'receipt.json').read_text())
                if canonical_digest(persisted) != canonical_digest(bounded_output):
                    raise RuntimeRejected('Candidate output receipt was changed after trusted recording')
                observations, check_root = directory / 'observations', directory / 'checks'
                observations.mkdir(); check_root.mkdir()
                for channel in ('stdout', 'stderr'):
                    path = Path(bounded_output[channel + '_path'])
                    data = path.read_bytes()
                    if len(data) != bounded_output[channel + '_bytes'] or hashlib.sha256(data).hexdigest() != bounded_output[channel + '_sha256']:
                        raise RuntimeRejected('Bounded candidate output changed before observation')
                    (observations / (channel + '.bin')).write_bytes(data)
                _atomic_json(observations / 'run.json', {key: bounded_output.get(key) for key in
                    ('run_id', 'snapshot_id', 'public_inputs_digest', 'status', 'exit_code', 'truncated',
                     'stdout_sha256', 'stderr_sha256', 'stdout_bytes', 'stderr_bytes')})
                _readonly_tree(observations)
                run['snapshot_id'] = bounded_output.get('snapshot_id')
                run['public_inputs_digest'] = bounded_output.get('public_inputs_digest')
                run['checks_digest'] = self._copy_public(checks, check_root, single_name='spec.json')
                mounts = {'/observations': observations, '/checks': check_root}
            image = json.loads(self._success(self._docker(['image', 'inspect', self.spec.image])))
            if not isinstance(image, list) or len(image) != 1 or not re.fullmatch(r'sha256:[0-9a-f]{64}', image[0].get('Id', '')):
                raise RuntimeError('Cached frozen image identity is unavailable')
            image_id = image[0]['Id']
            if ((self.spec.image.startswith('sha256:') and image_id != self.spec.image)
                    or not self.spec.image.startswith('sha256:') and not any(
                        ref.rsplit('@', 1)[-1] == self.spec.image.rsplit('@', 1)[-1]
                        for ref in image[0].get('RepoDigests') or [])):
                raise RuntimeError('Cached image digest differs from the frozen runtime spec')
            if image[0].get('Config', {}).get('Volumes'):
                raise RuntimeRejected('Images with implicit extra volume mounts are not supported')
            run['image_id'], run['phase'] = image_id, 'CREATING'
            self._save()
            cidfile = directory / 'container.cid'
            args = ['create', '--pull=never', '--name', run['planned_container_name'], '--cidfile', str(cidfile),
                '--label', _OWNER_LABEL + '=' + _OWNER, '--label', _TOKEN_LABEL + '=' + self.token,
                '--label', _RUN_LABEL + '=' + run_id, '--user', f'{self.spec.uid}:{self.spec.gid}',
                '--read-only', '--no-healthcheck', '--cap-drop=ALL', '--security-opt=no-new-privileges:true', '--network=none',
                '--ipc=private', '--cgroupns=private', '--cpus', str(self.spec.cpus),
                '--memory', str(self.spec.memory_bytes), '--memory-swap', str(self.spec.memory_bytes),
                '--pids-limit', str(self.spec.pids_limit), '--ulimit', 'nofile=256:256',
                '--tmpfs', f'/tmp:rw,nosuid,nodev,noexec,size={self.spec.tmpfs_bytes},mode=1777',
                '--workdir', '/source' if role == 'candidate' else '/tmp',
                '--entrypoint', self.spec.python_executable]
            for target, origin in mounts.items():
                if ',' in str(origin):
                    raise RuntimeRejected('Docker bind paths cannot contain mount-option separators')
                args += ['--mount', f'type=bind,source={origin},target={target},readonly,bind-propagation=rprivate']
            args += [self.spec.image, '-I', '-c', 'import time;time.sleep(86400)']
            creation = self._docker(args)
            if creation['reason'] != 'COMPLETE':
                run['creation_outcome'] = 'UNKNOWN'
            if cidfile.is_file():
                cid = cidfile.read_text().strip()
                if not re.fullmatch(r'[0-9a-f]{64}', cid):
                    raise RuntimeError('Docker CID file is invalid')
                run['container_id'] = cid
                self.containers.append(cid)
                run['phase'] = 'CREATED'
                self._save()
            self._success(creation)
            if run['container_id'] is None:
                raise RuntimeError('Docker creation returned without a durable container identity')
            inspected = self._inspect(cid)
            verified = self._validate_inspect(inspected, run, mounts, image_id)
            self._success(self._docker(['start', cid]))
            kernel = self._kernel_attestation(cid)
            run['attestation'] = {'status': 'VERIFIED', 'inspect': verified, 'kernel': kernel}
            run['phase'] = 'RUNNING'
            self._save()
            argv = [self.spec.docker_binary, 'exec', cid, self.spec.python_executable, '-I', '-c', _EXEC,
                json.dumps(list(invocation.argv)), json.dumps(_CANDIDATE_ENV)]
            captured = _capture(argv, env=self._client_env(), deadline=self.deadline,
                stdout_path=directory / 'stdout.bin', stderr_path=directory / 'stderr.bin',
                stdout_limit=self.spec.stdout_limit, stderr_limit=self.spec.stderr_limit,
                output_limit=self.spec.output_limit)
            run.update(captured)
            run['process_status'] = captured['reason']
            run['status'] = ('TIMEOUT' if captured['reason'] == 'TIMEOUT' else
                'OUTPUT_LIMIT' if captured['reason'] == 'OUTPUT_LIMIT' else
                'EXECUTION_PASS' if captured['exit_code'] == 0 else 'EXECUTION_FAILED')
            run['phase'] = 'PROCESS_FINISHED'
        except RuntimeRejected as exc:
            run['status'], run['error'] = 'REJECTED', str(exc)
        except TimeoutError as exc:
            run['status'], run['error'] = 'TIMEOUT', str(exc)
        except BaseException as exc:
            run['status'], run['error'] = 'INFRASTRUCTURE_ERROR', type(exc).__name__ + ': ' + str(exc)
            if not isinstance(exc, Exception):
                interrupted = exc
        finally:
            run['process_status'] = run.get('process_status', run['status'])
            if run.get('phase') == 'CREATING' and run.get('container_id') is None:
                run['creation_outcome'] = 'UNKNOWN'
                pending_id = directory / 'container.cid'
                if pending_id.is_file() and re.fullmatch(r'[0-9a-f]{64}', pending_id.read_text().strip()):
                    run['container_id'] = pending_id.read_text().strip()
            # Preserve the candidate process result before cleanup.  Cleanup
            # is a separate observation and must not erase a timeout, non-zero
            # exit, output limit or source-integrity failure.
            primary_status = run.get('status')
            primary_failure = None
            if primary_status not in {'EXECUTION_PASS', None}:
                primary_failure = make_failure(
                    'candidate_process' if role == 'candidate' else 'observer_process',
                    str(primary_status),
                    failure_class=('execution' if primary_status in {
                        'EXECUTION_FAILED', 'TIMEOUT', 'OUTPUT_LIMIT', 'INVALID_OBSERVATION'}
                        else 'infrastructure'),
                    dispatch_state='response_known', message=run.get('error'))
            run['cleanup'] = self._cleanup_run(run)
            run['phase'] = 'FINISHED'
            if role == 'candidate' and run.get('source_root'):
                try:
                    run['source_after_snapshot_id'] = compute_snapshot(run['source_root']).snapshot_id
                    run['source_unchanged'] = run['source_after_snapshot_id'] == run['snapshot_id']
                    if not run['source_unchanged']:
                        primary_failure = make_failure('source_integrity', 'SOURCE_CHANGED',
                            failure_class='infrastructure', dispatch_state='response_known',
                            message='Original source changed during isolated runtime')
                        run['status'], run['error'] = 'INFRASTRUCTURE_ERROR', primary_failure['message']
                except Exception as exc:
                    primary_failure = make_failure('source_integrity', 'SOURCE_INTEGRITY_UNVERIFIED',
                        failure_class='infrastructure', dispatch_state='response_known',
                        message='Original source integrity could not be verified: ' + str(exc))
                    run['status'], run['source_unchanged'] = 'INFRASTRUCTURE_ERROR', False
                    run['error'] = primary_failure['message']
            cleanup_failure = None
            if run['cleanup']['status'] != 'CONFIRMED':
                cleanup_failure = make_failure('cleanup', 'CLEANUP_UNCONFIRMED',
                    failure_class='infrastructure', dispatch_state='response_known',
                    message='; '.join(run['cleanup'].get('errors') or ()) or 'Runtime cleanup was not confirmed',
                    secondary=True)
            if primary_failure is None and run.get('status') not in {'EXECUTION_PASS', None}:
                primary_failure = make_failure('runtime', str(run['status']),
                    failure_class='infrastructure', dispatch_state='response_known', message=run.get('error'))
            run['primary_failure'] = primary_failure
            run['cleanup_failure'] = cleanup_failure
            run['terminal_status'] = project_terminal_status(primary_failure, cleanup_failure,
                default=str(run.get('status') or 'INFRASTRUCTURE_ERROR'))
            # A cleanup-only failure is infrastructure; an existing process
            # failure remains the terminal status and is never overwritten.
            run['status'] = run['terminal_status']
            for channel in ('stdout', 'stderr'):
                path = directory / (channel + '.bin')
                run[channel + '_bytes'] = path.stat().st_size
                run[channel + '_sha256'] = _hash(path)
            run.setdefault('exit_code', None)
            run.setdefault('truncated', False)
            _atomic_json(directory / 'receipt.json', run)
            self._save()
        if interrupted is not None:
            raise interrupted
        return run

    def _cleanup_run(self, run, *, deadline=None):
        deadline = time.monotonic() + self.spec.cleanup_seconds if deadline is None else deadline
        errors, identifiers = [], []
        if run.get('container_id'):
            identifiers.append(run['container_id'])
        try:
            listed = self._success(self._docker(['ps', '-aq', '--no-trunc', '--filter',
                'label=' + _TOKEN_LABEL + '=' + self.token, '--filter',
                'label=' + _RUN_LABEL + '=' + run['run_id']], deadline=deadline)).decode().split()
            identifiers.extend(cid for cid in listed if cid not in identifiers)
            if not identifiers and run.get('creation_outcome') == 'UNKNOWN':
                raise RuntimeError('Creation response unknown; container absence is not confirmed')
            for cid in identifiers:
                inspected = self._inspect(cid, deadline=deadline)
                if inspected is None:
                    continue
                self._verify_owner(inspected, run)
                if cid not in self.containers:
                    self.containers.append(cid)
                if run.get('container_id') is None:
                    run['container_id'] = cid
                self._save()
                self._success(self._docker(['rm', '-f', cid], deadline=deadline))
                if self._inspect(cid, deadline=deadline) is not None:
                    raise RuntimeError('Container remains after owned cleanup')
        except Exception as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))
        return {'status': 'FAILED' if errors else 'CONFIRMED', 'container_id': run.get('container_id'), 'errors': errors}

    def close(self):
        if not self.closed:
            deadline = time.monotonic() + self.spec.cleanup_seconds
            for run in self.runs:
                cleanup = self._cleanup_run(run, deadline=deadline)
                if cleanup['status'] != 'CONFIRMED' or run['cleanup']['status'] != 'CONFIRMED':
                    run['close_cleanup_failure'] = make_failure('close_cleanup', 'CLEANUP_UNCONFIRMED',
                        failure_class='infrastructure', dispatch_state='response_known',
                        message='; '.join(cleanup.get('errors') or ()) or 'Runtime close cleanup was not confirmed',
                        secondary=True)
                    # Keep any process root cause when one already exists;
                    # cleanup remains secondary when the process itself passed.
                    run['terminal_status'] = project_terminal_status(run.get('primary_failure'),
                        run.get('close_cleanup_failure'),
                        default=str(run.get('status') or 'INFRASTRUCTURE_ERROR'))
                    run['status'] = run['terminal_status']
                run['close_cleanup'] = cleanup
            self.closed = True
            self.manifest['status'] = ('CLOSED' if all(run['cleanup']['status'] == 'CONFIRMED'
                and run.get('close_cleanup', {}).get('status') == 'CONFIRMED' for run in self.runs)
                else 'INFRASTRUCTURE_ERROR')
            self.manifest['closed_at'] = datetime.now(timezone.utc).isoformat()
            self._save()
        return self.manifest

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
