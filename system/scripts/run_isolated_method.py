"""Linux-only method sandbox and typed clients for the trusted coordinator.

This module never forwards arbitrary filesystem or Docker operations. Model
credentials and the real public runner remain in separate trusted processes.
"""
from dataclasses import asdict, fields
from contextlib import ExitStack
import argparse
import fcntl
import importlib.util
import importlib
import json
import os
from pathlib import Path
import shutil
import math
import time
import hashlib
import uuid
import re
import subprocess
import signal
import sys
import secrets
import tempfile

from isolated_paths import SafeRoot, UnsafePath

from isolated_protocol import RpcClient, JsonRpcServer, Journal


SAFE_ENVIRONMENT = frozenset({
    'PATH', 'PYTHONPATH', 'PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE', 'TMPDIR',
    'CONTRACTEDGE_RPC_SOCKET', 'CONTRACTEDGE_RPC_TOKEN',
    'DOCKER_API_VERSION', 'DOCKER_HOST', 'DOCKER_CONFIG', 'CE_PUBLIC_SESSION',
})


def namespace_command(*, command, readonly, writable, cwd, environment):
    """Whitelist filesystem, PID, IPC and network; never inherit environment."""
    if not set(environment) <= SAFE_ENVIRONMENT:
        raise ValueError('Environment is not explicitly allowed')
    if not shutil.which('bwrap'):
        raise ValueError('Linux bwrap is required; no non-isolated fallback')
    result = ['bwrap', '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
              '--unshare-uts', '--die-with-parent', '--new-session', '--cap-drop', 'ALL', '--clearenv']
    for directory in ['/usr', '/bin', '/sbin', '/lib', '/lib64']:
        if Path(directory).exists():
            result += ['--ro-bind', directory, directory]
    result += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/run']
    for name in ['ld.so.cache', 'passwd', 'group', 'nsswitch.conf', 'localtime']:
        source = Path('/etc')/name
        if source.is_file():
            result += ['--ro-bind', str(source), str(source)]
    for flag, bindings in [('--ro-bind', readonly), ('--bind', writable)]:
        for source, destination in bindings.items():
            source, destination = Path(source), Path(destination)
            if not source.is_absolute() or not destination.is_absolute() or str(destination) == '/':
                raise ValueError('Explicit absolute mount paths are required')
            result += [flag, str(source), str(destination)]
    for key, value in environment.items():
        result += ['--setenv', key, str(value)]
    return result + ['--chdir', str(cwd), '--', *map(str, command)]


class ProxyMoney:
    def __init__(self, client):
        self.client = client

    def summary(self):
        return self.client.call('budget.summary', {})['summary']


class ProxyProvider:
    def __init__(self, client, *, timeout_seconds=240):
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.money_budget = ProxyMoney(client)

    def request_overhead(self, request):
        return self.client.call('model.overhead', {'request': asdict(request)})['text']

    def request_metadata(self, request):
        return self.client.call('model.metadata', {'request': asdict(request)})['metadata']

    def complete_json(self, request):
        types = importlib.import_module('contractedge.model.provider')
        result = self.client.call('model.complete', {'request': asdict(request),
                                                   'timeout_seconds': self.timeout_seconds})
        response = types.ModelResponse(result['content'], result['metadata'])
        if result.get('received_error'):
            error_type = getattr(types, 'ProviderResponseError', ReceivedResponseError)
            raise error_type('Received provider response rejected by frozen policy', response)
        return response


class ReceivedResponseError(RuntimeError):
    """Known received response on historical method APIs without the new type."""
    def __init__(self, message, response):
        super().__init__(message)
        self.response, self.provider_metadata = response, dict(response.metadata)


class ProxyPublicRunner:
    def __init__(self, client, *, timeout_seconds=2400):
        self.client = client
        self.timeout_seconds = timeout_seconds

    def prepare(self, case_id, workspace, log):
        return self.client.call('public.prepare', {'case_id': case_id, 'workspace': str(workspace),
                                                   'log': str(log), 'timeout_seconds': self.timeout_seconds})

    def _payload(self, workspace):
        from contractedge.source.snapshot import compute_snapshot
        source = json.loads((Path(workspace)/'workspace.json').read_text())['source_root']
        return {'workspace': str(workspace), 'snapshot_id': compute_snapshot(source).snapshot_id,
                'timeout_seconds': self.timeout_seconds}

    def observe(self, workspace, output):
        return self.client.call('public.observe', {**self._payload(workspace), 'output': str(output)})

    def export(self, workspace, patch, log):
        return self.client.call('public.export', {**self._payload(workspace), 'patch': str(patch), 'log': str(log)})


def write_json(path, value):
    """Trusted-side receipts; atomic replacement, independent of child paths."""
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class ModelBroker:
    """Enforce the original budget again outside the method's authority."""
    def __init__(self, provider, token_counter, config, calls_path, *, started=None):
        from contractedge.model.budget import BudgetedProvider
        self.provider, self.counter, self.config = provider, token_counter, config
        self.policy_failures = []
        self.budget = BudgetedProvider(provider, token_counter, max_tokens=config['max_total_tokens'],
            max_seconds=config['max_elapsed_seconds'], max_calls=config['max_model_calls'],
            on_update=lambda calls: write_json(calls_path, calls))
        if started is not None:
            self.budget.started = started

    def _request(self, payload):
        from contractedge.model.provider import ModelRequest
        value = payload.get('request')
        if not isinstance(value, dict) or set(value) - {f.name for f in fields(ModelRequest)}:
            raise ValueError('Invalid model request')
        if (value.get('model') != self.config['model']
                or type(value.get('max_output_tokens')) is not int
                or not 0 < value['max_output_tokens'] <= max(self.config.get(k, 0) for k in (
                    'max_output_tokens', 'execution_max_output_tokens', 'review_max_output_tokens'))
                or not isinstance(value.get('prompt_version'), str)
                or not isinstance(value.get('messages'), (list, tuple))
                or any(not isinstance(m, dict) or set(m) != {'role', 'content'}
                       or m.get('role') not in {'system', 'user', 'assistant'}
                       or not isinstance(m.get('content'), str) for m in value['messages'])):
            raise ValueError('Model identity, output budget or message contract mismatch')
        return ModelRequest(**{**value, 'messages': tuple(value['messages'])})

    def overhead(self, payload):
        if set(payload) != {'request'}:
            raise ValueError('Unexpected overhead parameters')
        request = self._request(payload)
        return {'text': self._overhead(request)}

    def _overhead(self, request):
        # Historical .5.5 budget/provider have no transport-overhead hook. Keep
        # their actual policy rather than importing a newer method silently.
        hook = getattr(self.provider, 'request_overhead', None)
        value = hook(request) if callable(hook) else ''
        if not isinstance(value, str):
            raise ValueError('Invalid transport overhead')
        return value

    def metadata(self, payload):
        if set(payload) != {'request'}:
            raise ValueError('Unexpected metadata parameters')
        request = self._request(payload)
        hook = getattr(self.provider, 'request_metadata', None)
        return {'metadata': dict(hook(request)) if callable(hook) else {}}

    def complete(self, payload):
        from contractedge.model.budget import BudgetExceeded
        try:
            return self._complete(payload)
        except Exception as error:
            self.policy_failures.append({'category':'BUDGET' if isinstance(error,BudgetExceeded) else 'COORDINATOR_ERROR',
                                         'error_type':type(error).__name__})
            raise

    def _complete(self, payload):
        from contractedge.model.provider import ProviderResponseError
        if set(payload) != {'request', 'timeout_seconds'}:
            raise ValueError('Unexpected model parameters')
        request = self._request(payload)
        timeout = payload['timeout_seconds']
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Invalid model timeout')
        prompt_tokens = sum(int(self.counter(m['content'])) for m in request.messages)
        overhead = self._overhead(request)
        prompt_tokens += int(self.counter(overhead)) if overhead else 0
        if prompt_tokens + request.max_output_tokens > self.config['max_context_tokens']:
            raise ValueError('Original context budget exceeded')
        old_timeout = getattr(self.provider, 'timeout_seconds', None)
        if old_timeout is not None:
            self.provider.timeout_seconds = min(old_timeout, timeout, self.config['timeout_seconds'])
        try:
            response = self.budget.complete_json(request)
            return {'content': response.content, 'metadata': dict(response.metadata)}
        except ProviderResponseError as error:
            self.policy_failures.append({'category':'MODEL_POLICY','error_type':type(error).__name__})
            # The service returned a known response. Preserve it and the
            # original strict policy failure, rather than label it network loss.
            return {'received_error': True, 'content': error.response.content,
                    'metadata': dict(error.response.metadata)}
        finally:
            if old_timeout is not None:
                self.provider.timeout_seconds = old_timeout


class PublicBridge:
    """Translate three runner operations; never trust child input/Git metadata."""
    def __init__(self, child_root, visible_root, producer_root, transfer_root, case_id,
                 producer_client, *, max_timeout, expected_initial_snapshot=None, deadline=None):
        self.child_root, self.visible_root = Path(child_root), Path(visible_root)
        self.producer_root, self.transfer = Path(producer_root), Path(transfer_root)
        self.case_id, self.client, self.max_timeout = case_id, producer_client, max_timeout
        self.workspace = self.visible_root/case_id/'workspace'
        self.expected_initial_snapshot, self.deadline = expected_initial_snapshot, deadline
        self.record = None
        self.source_links = {}
        self.publication_limits = None
        self.public_infrastructure_failed = False
        self.exports = {}

    def _relative(self, value, *, output=False):
        if not isinstance(value, str) or str(Path(value)) != value:
            raise ValueError('Noncanonical method path')
        path = Path(value)
        if '..' in path.parts or not path.is_absolute():
            raise ValueError('Unsafe method path')
        relative = path.relative_to(self.visible_root)
        if len(relative.parts) < 2 or relative.parts[0] != self.case_id:
            raise ValueError('Path belongs to another case')
        if output and relative.parts[1] == 'workspace':
            raise ValueError('Public output cannot overwrite source or input workspace')
        return relative.as_posix()

    def _timeout(self, payload):
        timeout = payload.get('timeout_seconds')
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= self.max_timeout:
            raise ValueError('Invalid public timeout')
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - time.monotonic())
        if timeout <= 0:
            raise ValueError('Original global deadline expired')
        return timeout

    def _producer_relative(self, value):
        path = Path(value)
        relative = path.relative_to(self.producer_root)
        if '..' in relative.parts or not relative.parts:
            raise ValueError('Invalid producer output')
        return relative.as_posix()

    def prepare(self, payload):
        if (set(payload) != {'case_id', 'workspace', 'log', 'timeout_seconds'}
                or payload['case_id'] != self.case_id or payload['workspace'] != str(self.workspace)
                or self.record is not None):
            raise ValueError('Unregistered initial workspace')
        log = self._relative(payload['log'], output=True)
        timeout = self._timeout(payload)
        prepared = self.client.call('public.prepare', {'case_id': self.case_id, 'timeout_seconds': timeout})
        if self.expected_initial_snapshot is not None and prepared['snapshot_id'] != self.expected_initial_snapshot:
            raise ValueError('Initial public source identity mismatch')
        record = prepared['record']
        trusted_workspace = Path(prepared['workspace'])
        self._producer_relative(str(trusted_workspace))
        source_relative = Path(record['source_root']).relative_to(trusted_workspace)
        input_relative = Path(record['inputs_root']).relative_to(trusted_workspace)
        if record.get('private_materials_loaded') is not False or record.get('case_id') != self.case_id:
            raise ValueError('Public preparation identity mismatch')
        self.source_links = prepared['source_links']
        self.publication_limits = prepared['publication_limits']
        workspace_links = {str(source_relative/name): target for name, target in self.source_links.items()}
        stage_name = 'prepared-' + uuid.uuid4().hex
        stage = self.transfer/stage_name
        try:
            with SafeRoot(self.transfer) as transfer:
                transfer.publish_dir(trusted_workspace, stage_name, allowed_symlinks=workspace_links,
                                     limits=self.publication_limits)
            visible_record = dict(record, source_root=str(self.workspace/source_relative),
                                   inputs_root=str(self.workspace/input_relative))
            write_json(stage/'workspace.json', visible_record)
            with SafeRoot(self.child_root) as child:
                child.publish_dir(stage, self._relative(str(self.workspace)), allowed_symlinks=workspace_links,
                                  limits=self.publication_limits)
                child.publish_dir(prepared['prepare_log'], log)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
        self.record = visible_record
        return visible_record

    def _stage(self, payload):
        if self.record is None or payload['workspace'] != str(self.workspace):
            raise ValueError('Unregistered public workspace')
        timeout = self._timeout(payload)
        stage_id = uuid.uuid4().hex
        with SafeRoot(self.child_root) as child:
            manifest = child.clone_source(self._relative(self.record['source_root']), self.transfer/stage_id,
                                          allowed_symlinks=self.source_links)
        if manifest['snapshot_id'] != payload['snapshot_id']:
            shutil.rmtree(self.transfer/stage_id)
            raise ValueError('Method request has stale or incomplete source')
        return {'stage_id': stage_id, 'snapshot_id': manifest['snapshot_id'], 'timeout_seconds': timeout}

    def observe(self, payload):
        if set(payload) != {'workspace', 'snapshot_id', 'output', 'timeout_seconds'}:
            raise ValueError('Unexpected public observation parameters')
        output = self._relative(payload['output'], output=True)
        request = self._stage(payload)
        observed = self.client.call('public.observe', request)
        if observed['report'].get('status') in {'INFRA_ERROR', 'INVALID_OBSERVATION', 'BUDGET_EXCEEDED'}:
            self.public_infrastructure_failed = True
        self._producer_relative(observed['output'])
        with SafeRoot(self.child_root) as child:
            child.publish_dir(observed['output'], output)
        shutil.rmtree(self.transfer/request['stage_id'])
        return observed['report']

    def export(self, payload):
        if set(payload) != {'workspace', 'snapshot_id', 'patch', 'log', 'timeout_seconds'}:
            raise ValueError('Unexpected public export parameters')
        patch, log = self._relative(payload['patch'], output=True), self._relative(payload['log'], output=True)
        request = self._stage(payload)
        exported = self.client.call('public.export', request)
        with SafeRoot(self.producer_root) as producer:
            data = producer.read_bytes(self._producer_relative(exported['patch']), max_bytes=64*1024**2)
        self._producer_relative(exported['log'])
        with SafeRoot(self.child_root) as child:
            child.publish_bytes(data, patch)
            child.publish_dir(exported['log'], log)
        shutil.rmtree(self.transfer/request['stage_id'])
        result = {**exported['result'], 'patch_path': payload['patch'], 'patch_bytes': len(data),
                  'patch_sha256': hashlib.sha256(data).hexdigest()}
        self.exports[payload['patch']] = {'relative': patch, 'snapshot_id': request['snapshot_id'],
                                          'patch_sha256': result['patch_sha256'], 'patch_bytes': len(data)}
        return result


def validate_method_result(child_root, case_id, bridge, journal_root, *, broker=None):
    """Bind final candidate to trusted export and keep queue stop reasons explicit."""
    with SafeRoot(child_root) as child:
        trace = json.loads(child.read_bytes(case_id+'/repair.trace.json', max_bytes=8*1024**2))
        if trace.get('case_id') != case_id:
            raise ValueError('Method trace belongs to another case')
        candidate = trace.get('candidate')
        if candidate:
            exported = bridge.exports.get(candidate.get('patch_path'))
            if (not exported or candidate.get('patch_sha256') != exported['patch_sha256']
                    or candidate.get('patch_bytes') != exported['patch_bytes']
                    or trace.get('final_snapshot_id') != exported['snapshot_id']):
                raise ValueError('Candidate is not bound to a trusted source export')
            data = child.read_bytes(exported['relative'], max_bytes=64*1024**2)
            if len(data) != exported['patch_bytes'] or hashlib.sha256(data).hexdigest() != exported['patch_sha256']:
                raise ValueError('Final patch changed after trusted export')
    pending, not_dispatched = 0, 0
    with SafeRoot(journal_root) as journal:
        for path in Path(journal_root).glob('*.json'):
            status = json.loads(journal.read_bytes(path.name, max_bytes=8*1024**2)).get('status')
            if status == 'NOT_DISPATCHED':
                not_dispatched += 1
            elif status != 'DONE':
                pending += 1
    method_statuses = {'PUBLIC_EXECUTION_COMPLETE', 'PUBLIC_REVIEW_COMPLETE', 'INVALID_PLAN', 'INVALID_OUTPUT',
        'INVALID_DIAGNOSIS', 'INVALID_CHANGESET', 'NEEDS_EVIDENCE', 'INSUFFICIENT_EVIDENCE', 'ROUND_LIMIT',
        'STOPPED_UNCERTAIN', 'CANDIDATE_REGRESSION_REJECTED', 'CANDIDATE_COMPARISON_UNCERTAIN',
        'RECOVERY_PLAN_UNSUPPORTED', 'RECOVERY_NOT_ROUTED', 'RECOVERY_REGRESSION_REJECTED',
        'RECOVERY_COMPARISON_UNAVAILABLE','CONCRETE_EDIT_REJECTED'}
    policy_failures = list(broker.policy_failures) if broker is not None else []
    stopped = (pending > 0 or not_dispatched > 0 or bridge.public_infrastructure_failed
               or bool(policy_failures) or trace.get('recovery_failure') is not None
               or trace.get('source_integrity_verified') is False or trace.get('status') not in method_statuses)
    return {'classification': 'INFRASTRUCTURE_STOP' if stopped else 'METHOD_RESULT',
            'method_status': trace.get('status'), 'unknown_requests': pending,
            'not_dispatched_requests': not_dispatched, 'candidate_verified': bool(candidate),
            'coordinator_policy_failures':policy_failures,'recovery_failure':trace.get('recovery_failure'),
            'semantic_outcome_upgraded': False}


def check_registration(path, identity):
    registration = json.loads(Path(path).read_text())
    if (not isinstance(registration, dict) or registration.get('role') != 'ISOLATED_DEVELOPMENT_VALIDATION'
            or registration.get('allow_paid') is not True or registration.get('counts_in_46_slot_panel') is not False
            or any(registration.get(key) != value for key, value in identity.items())):
        raise ValueError('Paid development registration does not match this exact run')


def claim_attempt(root, *, attempt_id, output, state, identity):
    """Irreversible admission marker: process death never renews a case budget.

    Two exclusive, fsynced claims bind both attempt and canonical output. A crash
    between claims is conservatively blocked; operators retain the records and
    preregister a distinct attempt/output rather than deleting or resuming them.
    """
    if not isinstance(attempt_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}',attempt_id):
        raise ValueError('Paid execution requires an explicit registered attempt ID')
    root=Path(root);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.is_symlink():raise ValueError('Invalid attempt claim directory')
    output=str(Path(output).resolve())
    record={'schema_version':'ce.isolated_attempt.v1','attempt_id':attempt_id,'output_path':output,
            'state_root':str(state),'identity_sha256':hashlib.sha256(
                json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'automatic_resume':False,'budget_reset':False}
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for kind,value in [('output',output),('attempt',attempt_id)]:
            name=kind+'-'+hashlib.sha256(value.encode()).hexdigest()+'.json'
            try:fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
            except FileExistsError:raise ValueError('Attempt or output already claimed; retain prior journal, no automatic restart') from None
            with os.fdopen(fd,'w') as stream:
                json.dump(record,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            os.fsync(directory)
    finally:os.close(directory)
    return record


def write_docker_wrapper(path, executable, session):
    if not re.fullmatch('[a-f0-9]{32}', session) or not Path(executable).is_absolute():
        raise ValueError('Invalid Docker session identity')
    Path(path).write_text('#!/usr/bin/python3\nimport os,sys\n'
        'args=sys.argv[1:]\n'
        "if args and args[0] in {'run','create'}:\n"
        ' args=[args[0],"--label",' + repr('contractedge.isolated.session=' + session) + ']+args[1:]\n'
        'os.execv(' + repr(executable) + ',[' + repr(executable) + ']+args)\n')
    Path(path).chmod(0o755)


def cleanup_session(executable, session, *, environment):
    if not re.fullmatch('[a-f0-9]{32}', session):
        raise ValueError('Invalid cleanup session')
    removed, confirmed = [], True
    def invoke(arguments):
        return subprocess.run([executable, *arguments], env=environment,
                              capture_output=True, text=True, timeout=15)
    try:
        listed = invoke(['ps', '-aq', '--filter', 'label=contractedge.isolated.session=' + session])
        if listed.returncode:
            return {'status': 'CLEANUP_UNCONFIRMED', 'removed': removed}
        for cid in listed.stdout.split():
            if not re.fullmatch('[a-f0-9]{12,64}', cid):
                confirmed = False
                continue
            inspected = invoke(['inspect', '--format', '{{json .Config.Labels}}', cid])
            if inspected.returncode or json.loads(inspected.stdout).get('contractedge.isolated.session') != session:
                confirmed = False
                continue
            result = invoke(['rm', '-f', cid])
            if result.returncode:
                confirmed = False
            else:
                removed.append(cid)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        confirmed = False
    return {'status': 'OWNED_CONTAINERS_CLOSED' if confirmed else 'CLEANUP_UNCONFIRMED', 'removed': removed}


HARNESS_NAMES = ('isolated_protocol.py', 'isolated_paths.py', 'run_isolated_method.py',
                 'isolated_public_worker.py', 'run_reliability_worker_v10.py', 'run_reliability_worker_v9.py')


def source_files(root):
    return {p.relative_to(Path(root)/'src').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((Path(root)/'src').rglob('*')) if p.is_file() and p.suffix in {'.py', '.json'}}


def execution_identity(args):
    scripts = Path(__file__).resolve().parent
    def sha(path):
        path = Path(path)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    benchmark = Path(args.benchmark_root)
    dependencies = Path(args.dependency_root)
    venv = Path(args.python).absolute().parent.parent
    metadata = [*venv.glob('lib/python*/site-packages/*.dist-info/METADATA'),
                *venv.glob('lib/python*/site-packages/*.pth')]
    installed = {}
    links = {}
    if (venv/'pyvenv.cfg').is_file():
        for path in sorted(venv.rglob('*')):
            relative=path.relative_to(venv).as_posix()
            if path.is_symlink():
                links[relative]=os.readlink(path)
                if path.is_dir() and not path.resolve().is_relative_to(venv.resolve()):
                    raise ValueError('External virtualenv directory links are not registered')
            if path.is_file():installed[relative]=sha(path)
    identity = {'case_id': args.case, 'config_sha256': sha(args.config),
        'output_path':str(Path(args.output).resolve()),'attempt_id':getattr(args,'attempt_id',None),
        'method_files': source_files(args.method_root),
        'coordinator_code_files': source_files(scripts.parent),
        'harness_files': {**{name: sha(scripts/name) for name in HARNESS_NAMES},
                          'runtime_preflight.py': sha(scripts.parent/'src/contractedge/execution/runtime_preflight.py')},
        'public_release_files': {name: sha(benchmark/name) for name in
                                 ['public/manifest.jsonl', 'partition/member_groups.jsonl', 'RELEASE_ENTRY.json']},
        'public_tool_files': {p.relative_to(benchmark/'tools').as_posix(): sha(p)
                             for p in sorted((benchmark/'tools').rglob('*'))
                             if p.is_file() and p.suffix in {'.py', '.json'}},
        'dependency_files': {p.relative_to(dependencies).as_posix(): sha(p)
                             for p in sorted(dependencies.rglob('*'))
                             if p.is_file() and p.suffix in {'.py', '.json', '.so'}},
        'python_environment': {'interpreter_sha256': sha(args.python), 'pyvenv_cfg_sha256': sha(venv/'pyvenv.cfg'),
                               'installed_files':installed,'links':links,
                               'distribution_metadata': {p.relative_to(venv).as_posix(): sha(p) for p in sorted(metadata)}}}
    if args.seed:
        identity['seed_sha256'] = sha(args.seed)
    if args.runtime_admission:
        identity['runtime_admission_sha256'] = sha(args.runtime_admission)
    return identity


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=10)


def make_channel_root():
    """Linux sockaddr_un has a short pathname limit, unlike audit directories."""
    return Path(tempfile.mkdtemp(prefix='ce-rpc-', dir='/tmp'))


def _runtime_admission(config, settings_path):
    # This harness capability must also exist when running the historical
    # baseline. Load only the stdlib-only admission implementation, independently
    # of whichever frozen contractedge package the coordinator has selected.
    path = Path(__file__).resolve().parents[1]/'src/contractedge/execution/runtime_preflight.py'
    spec = importlib.util.spec_from_file_location('isolated_runtime_admission', path)
    admission = importlib.util.module_from_spec(spec); spec.loader.exec_module(admission)
    settings = config.get('runtime_preflight')
    if settings is None:
        if not settings_path:
            raise ValueError('Historical method requires the preregistered common runtime admission settings')
        settings = json.loads(Path(settings_path).read_text())
    return admission.check_runtime(**settings)


def _docker_endpoint(preflight):
    environment = {'PATH': '/usr/bin:/bin'}
    overrides = preflight.get('environment_overrides', {})
    if overrides.get('DOCKER_API_VERSION'):
        environment['DOCKER_API_VERSION'] = overrides['DOCKER_API_VERSION']
    context = preflight.get('context')
    if context:
        command = ['/usr/bin/docker', 'context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}']
        inspected = subprocess.run(command, capture_output=True, text=True, timeout=10)
        if inspected.returncode:
            raise ValueError('Verified Docker context endpoint is unavailable')
        endpoint = inspected.stdout.strip()
    else:
        endpoint = os.environ.get('DOCKER_HOST', 'unix:///var/run/docker.sock')
    if not endpoint.startswith('unix:///'):
        raise ValueError('Only the verified local Unix Docker endpoint is supported')
    path = Path(endpoint[len('unix://'):])
    if not path.is_socket():
        raise ValueError('Verified Docker Unix socket is unavailable')
    environment['DOCKER_HOST'] = endpoint
    return path, environment


def run_session(args, config, identity):
    """One development run; prior output is never overwritten or auto-retried."""
    sys.dont_write_bytecode = True
    output, method, benchmark = Path(args.output).absolute(), Path(args.method_root).absolute(), Path(args.benchmark_root).absolute()
    if output.name != args.case or output.exists():
        raise ValueError('Fresh output named exactly after this case is required')
    if args.execute:
        if not args.registration or not args.api_key_file:
            raise ValueError('Paid execution requires exact registration and parent-only key file')
        check_registration(args.registration, identity)
        if identity.get('output_path') != str(output.resolve()) or identity.get('attempt_id') != getattr(args,'attempt_id',None):
            raise ValueError('Registered attempt/output mismatch')
    session = uuid.uuid4().hex
    output.parent.mkdir(parents=True, exist_ok=True)
    state = output.parent/('.isolated-' + session)
    if args.execute:
        claim_attempt(Path(config['money_budget']['path']).resolve().parent/'isolated-attempts',
            attempt_id=getattr(args,'attempt_id',None),output=output,state=state,identity=identity)
    coordinator = Path(__file__).resolve().parents[1]
    # The trusted API coordinator is common infrastructure for both variants;
    # only the child and public producer import the selected frozen method.
    # This keeps strict actual-model identity checks available for .5.5 too.
    sys.path.insert(0, str(coordinator/'src'))
    import contractedge
    if not Path(contractedge.__file__).resolve().is_relative_to(coordinator/'src'):
        raise ValueError('Coordinator imported another method package; use a fresh process')
    from contractedge.dataset.admission import check_case_admission
    from contractedge.dataset.benchmark import method_input_from_task
    from contractedge.execution.topomen_agent import source_identity
    check_case_admission(benchmark/'public', args.case, config)
    native_identity = source_identity(config['topomen_source_root'])
    if native_identity['source_sha256'] != config['topomen_source_sha256']:
        raise ValueError('Audited native TopoMen source changed before model dispatch')
    method_input, _ = method_input_from_task(benchmark/'public', args.case)
    state.mkdir(mode=0o700)
    if shutil.disk_usage(state).free < config.get('min_free_work_disk_bytes', 0):
        raise ValueError('Configured disk admission failed before workspace/model dispatch')
    producer_root, transfer, child_slot = state/'producer', state/'transfer', state/'child'
    for path in [producer_root, transfer, child_slot, producer_root/'tmp', state/'tmp']:
        path.mkdir(mode=0o700)
    started = time.monotonic()
    receipt = {'schema_version': 'ce.isolated_run.v1', 'session': session, 'case_id': args.case,
               'identity': identity, 'status': 'ADMISSION', 'paid_enabled': args.execute,
               'counts_in_46_slot_panel': False, 'formal_ready': False, 'state_root': str(state)}
    receipt['native_topomen'] = native_identity
    receipt['model_coordinator_policy'] = {'model_identity_policy': 'require_match',
        'scope': 'Common frozen API transport for both variants; selected method still owns all prompts, schemas, retries and edits',
        'wire_model_parameters_changed': False}
    write_json(state/'receipt.json', receipt)
    preflight = _runtime_admission(config, args.runtime_admission)
    write_json(state/'runtime-preflight.json', preflight)
    if preflight.get('status') != 'PASS' or preflight.get('runtime_verified') is not True:
        receipt.update(status='RUNTIME_ADMISSION_FAILED')
        write_json(state/'receipt.json', receipt)
        raise ValueError('Actual runtime admission failed; preserved receipt, no method/model dispatch')
    socket_path, docker_env = _docker_endpoint(preflight)
    # The original file remains unchanged. No model, token, probe or edit limit
    # changes: only local financial/runtime responsibilities move to the parent.
    effective = {**config, 'money_budget': None, 'runtime_preflight': None}
    write_json(state/'effective-config.json', effective)
    write_json(state/'original-config.json', config)
    write_json(state/'control.json', {'started_monotonic': started})
    receipt['effective_config_sha256'] = hashlib.sha256((state/'effective-config.json').read_bytes()).hexdigest()
    receipt['delegated_config_keys'] = [k for k in sorted(set(config)|set(effective))
                                       if config.get(k, '__MISSING') != effective.get(k, '__MISSING')]
    python = Path(args.python).absolute()
    venv = python.parent.parent
    if not (venv/'pyvenv.cfg').is_file():
        raise ValueError('Explicit installed virtualenv Python is required')
    dependencies = Path(args.dependency_root).resolve(strict=True)
    scripts = Path(__file__).resolve().parent
    harness_mounts = {scripts/name: Path('/run/ce-harness')/name for name in HARNESS_NAMES}
    common = {method/'src': method/'src', venv: venv, dependencies: dependencies,
              benchmark/'public': benchmark/'public', benchmark/'partition': benchmark/'partition',
              benchmark/'RELEASE_ENTRY.json': benchmark/'RELEASE_ENTRY.json',
              state/'effective-config.json': Path('/run/ce-config.json'),
              state/'runtime-preflight.json': Path('/run/ce-preflight.json'),
              state/'control.json': Path('/run/ce-control.json'), **harness_mounts}
    native = Path(config['topomen_source_root'])/'src'
    common[native] = native
    lock_root = Path(config['lock_root']); lock_root.mkdir(parents=True, exist_ok=True)
    wrapper = state/'docker-wrapper'; write_docker_wrapper(wrapper, '/usr/bin/docker', session)
    channel_root = make_channel_root()
    receipt['channel_root'] = str(channel_root)
    method_socket = channel_root/'method.sock'
    producer_token, method_token = secrets.token_hex(32), secrets.token_hex(32)
    base_env = {'PYTHONPATH': os.pathsep.join([str(method/'src'), str(dependencies), '/run/ce-harness']),
                'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
    producer_env = {**base_env, **docker_env, 'PATH': '/run/ce-bin:/usr/bin:/bin',
                    'TMPDIR': str(producer_root/'tmp'),
                    'CONTRACTEDGE_RPC_TOKEN': producer_token, 'CE_PUBLIC_SESSION': session}
    producer_mounts = {**common, benchmark/'tools': benchmark/'tools',
                       socket_path: socket_path, wrapper: Path('/run/ce-bin/docker')}
    # Put producer socket in its own mounted directory; the method never sees it.
    channel = channel_root/'producer'; channel.mkdir()
    producer_socket = channel/'producer.sock'
    producer_env['CONTRACTEDGE_RPC_SOCKET'] = '/run/ce-public-channel/producer.sock'
    # Rebuild after pinning the socket alias rather than bind a whole parent dir.
    producer_command = namespace_command(readonly=producer_mounts,
        writable={producer_root: producer_root, transfer: transfer, lock_root: lock_root,
                  channel: Path('/run/ce-public-channel')},
        command=[python, '/run/ce-harness/isolated_public_worker.py', '--root', producer_root,
                 '--transfer-root', transfer, '--public-root', benchmark/'public', '--tools-root', benchmark/'tools',
                 '--config', '/run/ce-config.json', '--case', args.case], cwd=producer_root, environment=producer_env)
    producer_process, method_process, key = None, None, None
    try:
        with (state/'producer.log').open('wb') as producer_log, ExitStack() as stack:
            producer_process = subprocess.Popen(producer_command, stdout=producer_log, stderr=subprocess.STDOUT,
                                                start_new_session=True, env={})
            deadline = time.monotonic() + 20
            while not producer_socket.exists():
                if producer_process.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('Isolated public producer startup failed; no model dispatch')
                time.sleep(.05)
            bridge = PublicBridge(child_slot, output.parent, producer_root, transfer, args.case,
                RpcClient(producer_socket, producer_token, timeout=config['public_timeout_seconds']+60),
                max_timeout=config['public_timeout_seconds'], expected_initial_snapshot=method_input['initial_snapshot_id'],
                deadline=started+config['max_elapsed_seconds'])
            handlers = {'public.prepare': bridge.prepare, 'public.observe': bridge.observe, 'public.export': bridge.export}
            if args.execute:
                from contractedge.cli import _build_provider
                key = Path(args.api_key_file).read_text().strip()
                if not key:
                    raise ValueError('Empty parent API credential')
                os.environ[config['api_key_env']] = key
                provider, counter = _build_provider({**config, 'model_identity_policy': 'require_match'})
                broker = ModelBroker(provider, counter, config, state/'parent-model-calls.json', started=started)
                handlers.update({'model.complete': broker.complete, 'model.overhead': broker.overhead,
                                 'model.metadata': broker.metadata,
                                 'budget.summary': lambda p: {'summary': provider.money_budget.summary()}})
                receipt['money_before'] = provider.money_budget.summary()
            server = stack.enter_context(JsonRpcServer(method_socket, method_token, Journal(state/'method-journal'),
                handlers, sensitive_values=[key, producer_token]))
            stack.callback(stop_process, producer_process)
            child_env = {**base_env, 'PATH': str(venv/'bin')+':/usr/bin:/bin', 'TMPDIR': str(state/'tmp'),
                         'CONTRACTEDGE_RPC_SOCKET': '/run/ce-method.sock', 'CONTRACTEDGE_RPC_TOKEN': method_token}
            method_mounts = {**common, method_socket: Path('/run/ce-method.sock')}
            if args.seed:
                method_mounts[Path(args.seed).absolute()] = Path('/run/ce-seed.patch')
            if args.execute:
                command = [python, '/run/ce-harness/run_reliability_worker_v10.py', '--method-root', method,
                    '--benchmark-root', benchmark, '--config', '/run/ce-config.json', '--case', args.case,
                    '--output', output, '--preflight', '/run/ce-preflight.json', '--control', '/run/ce-control.json']
                if args.seed:
                    command += ['--seed', '/run/ce-seed.patch']
            else:
                # Real public prepare/build/export under the method namespace;
                # no model handler or API key is installed in this mode.
                smoke = """import json,os
from pathlib import Path
from run_isolated_method import ProxyPublicRunner
from isolated_protocol import RpcClient
out=Path(OUTPUT);out.mkdir()
r=ProxyPublicRunner(RpcClient('/run/ce-method.sock',os.environ['CONTRACTEDGE_RPC_TOKEN'],timeout=TIMEOUT+60),timeout_seconds=TIMEOUT)
w=out/'workspace';prepared=r.prepare(CASE,w,out/'prepare')
observed=r.observe(w,out/'public');patch=r.export(w,out/'candidate.patch',out/'export')
result=dict(case_id=CASE,public_status=observed['status'],snapshot_id=observed['snapshot_id'],patch=patch,
 api_key_present=bool(os.environ.get('CONTRACTEDGE_API_KEY')),docker_socket_visible=Path('/var/run/docker.sock').exists(),
 private_state_visible=Path(STATE).joinpath('method-journal').exists(),paid_calls=0,counts_in_46_slot_panel=False)
(out/'isolated-smoke.json').write_text(json.dumps(result,indent=2)+'\\n')
print(json.dumps(result))
""".replace('OUTPUT', repr(str(output))).replace('TIMEOUT', str(config['public_timeout_seconds'])).replace(
                     'CASE', repr(args.case)).replace('STATE', repr(str(state)))
                command = [python, '-c', smoke]
            wrapped = namespace_command(readonly=method_mounts,
                writable={child_slot: output.parent, state/'tmp': state/'tmp'},
                command=command, cwd=output.parent, environment=child_env)
            receipt.update(status='METHOD_RUNNING', method_command_kind='worker_v10' if args.execute else 'public-only-smoke')
            write_json(state/'receipt.json', receipt)
            with (state/'method.log').open('wb') as method_log:
                method_process = subprocess.Popen(wrapped, stdout=method_log, stderr=subprocess.STDOUT,
                                                  start_new_session=True, env={})
                stack.callback(stop_process, method_process)
                stack.callback(server.cancel_pending)
                remaining = started+config['max_elapsed_seconds']-time.monotonic()
                code = method_process.wait(timeout=max(.01, remaining))
            receipt['method_returncode'] = code
            if code:
                raise ValueError('Isolated method failed; artifacts retained, no automatic retry')
            if args.execute:
                receipt['money_after'] = provider.money_budget.summary()
                receipt['parent_budget'] = broker.budget.summary()
                receipt['terminal_validation'] = validate_method_result(child_slot, args.case, bridge, state/'method-journal',broker=broker)
            else:
                with SafeRoot(child_slot) as child:
                    smoke_result = json.loads(child.read_bytes(args.case+'/isolated-smoke.json', max_bytes=1024*1024))
                receipt['public_execution_status'] = smoke_result['public_status']
                receipt['public_execution_qualified'] = smoke_result['public_status'] == 'EXECUTION_PASS'
            if source_files(method) != identity['method_files']:
                raise ValueError('Frozen source changed during isolated execution')
            if execution_identity(args) != identity or source_identity(config['topomen_source_root']) != native_identity:
                raise ValueError('Frozen harness/data/runtime/native identity changed during isolated execution')
            with SafeRoot(output.parent) as destination:
                child_links = {}
                # Real runs retire their workspaces. Public-only smoke keeps
                # the exact known S0 links; no new output links are accepted.
                if (child_slot/args.case/'workspace').exists():
                    source_relative = Path(bridge.record['source_root']).relative_to(output)
                    child_links = {str(source_relative/name): target for name,target in bridge.source_links.items()}
                destination.publish_dir(child_slot/args.case, output.name, allowed_symlinks=child_links,
                                        limits=bridge.publication_limits)
            receipt.update(status='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING' if args.execute else 'PUBLIC_ONLY_SMOKE_COMPLETE',
                           method_source_unchanged=True)
            if args.execute and receipt['terminal_validation']['classification'] == 'INFRASTRUCTURE_STOP':
                receipt['status'] = 'INFRASTRUCTURE_STOP'
    except BaseException as error:
        receipt.update(status='INTERRUPTED' if isinstance(error, (KeyboardInterrupt, subprocess.TimeoutExpired)) else 'FAILED',
                       error_type=type(error).__name__)
        raise
    finally:
        stop_process(method_process); stop_process(producer_process)
        receipt['cleanup'] = cleanup_session('/usr/bin/docker', session, environment=docker_env)
        write_json(state/'receipt.json', receipt)
        shutil.rmtree(channel_root)
    return receipt


def main():
    # Registration includes executable bytecode too. Imports during the run must
    # not mutate the environment being checked at both admission and completion.
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--inspect', action='store_true')
    mode.add_argument('--dry-public', action='store_true')
    mode.add_argument('--execute', action='store_true')
    for name in ['method-root', 'benchmark-root', 'config', 'output', 'case', 'python', 'dependency-root']:
        parser.add_argument('--' + name, required=True)
    for name in ['registration', 'api-key-file', 'runtime-admission', 'seed', 'attempt-id']:
        parser.add_argument('--' + name)
    args = parser.parse_args()
    identity = execution_identity(args)
    if args.inspect:
        print(json.dumps({**identity, 'role': 'ISOLATED_DEVELOPMENT_VALIDATION', 'allow_paid': False,
                          'counts_in_46_slot_panel': False}, ensure_ascii=False, indent=2))
        return
    config = json.loads(Path(args.config).read_text())
    lock_parent = Path(config['money_budget']['path']).resolve(strict=True).parent
    with (lock_parent/'isolated-method-run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def interrupted(*_):
            raise KeyboardInterrupt('Owned isolated session interrupted')
        signal.signal(signal.SIGTERM, interrupted)
        receipt = run_session(args, config, identity)
        print(json.dumps({key: receipt[key] for key in ['case_id', 'status', 'state_root', 'paid_enabled',
                                                       'counts_in_46_slot_panel', 'formal_ready', 'cleanup']}, ensure_ascii=False))
        if receipt['cleanup']['status'] != 'OWNED_CONTAINERS_CLOSED':
            raise SystemExit(2)
        if receipt['status'] == 'INFRASTRUCTURE_STOP' or receipt.get('public_execution_qualified') is False:
            raise SystemExit(2)


if __name__ == '__main__':
    main()
