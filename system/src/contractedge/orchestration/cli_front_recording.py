"""Durable standard-CLI front entry; one existing budget and one journal.

The TRACE.json filename remains a file. Its sibling artifacts directory owns a
one-shot claim, public source, inputs, actual call ledger and immutable receipts.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Mapping
from urllib.parse import urlsplit
import uuid

from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_run_trace
from contractedge.dataset.admission import check_case_admission
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.orchestration.recorder import ProviderJournal, RunRecorder
from contractedge.orchestration.runner import RunTrace
from contractedge.orchestration.repair import run_front_recorded
from contractedge.orchestration.semantic_delivery import SemanticDeliveryError
from contractedge.source.snapshot import compute_snapshot


class CliRunIdentityError(SemanticDeliveryError):
    pass


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _clone(value):
    return json.loads(_bytes(value))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_once(path, value):
    path = Path(path)
    content = _bytes(value)
    with path.open('xb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return content


def _write_atomic(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.calls-', dir=path.parent)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


_SECRET_KEYS = {'api_key', 'authorization', 'password', 'access_token', 'secret', 'bearer_token', 'headers'}


def _safe_config(value):
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            if key.lower() in _SECRET_KEYS:
                result[key] = '<REDACTED>'
            elif key in {'base_url', 'endpoint'} and isinstance(child, str):
                try:
                    url = urlsplit(child)
                    result[key] = '<REDACTED_ENDPOINT>' if url.username or url.password or url.query or url.fragment else child
                except ValueError:
                    # URL parser errors may contain the credential-bearing netloc.
                    result[key] = '<REDACTED_INVALID_ENDPOINT>'
            else:
                result[key] = _safe_config(child)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_config(child) for child in value]
    return value


def _validate_config(config):
    for key in ('provider', 'model', 'tokenizer'):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError('model configuration requires non-empty ' + repr(key))
    if _safe_config(config) != config:
        raise ValueError('CLI recording rejects literal credentials or authenticated/query endpoints; use an environment reference')
    for key in ('max_total_tokens', 'max_model_calls', 'max_context_tokens'):
        if type(config.get(key)) is not int or config[key] <= 0:
            raise ValueError('persistent CLI run requires explicit positive ' + key)
    seconds = config.get('max_elapsed_seconds')
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('persistent CLI run requires explicit positive max_elapsed_seconds')
    if config['provider'] == 'openai_compatible':
        money = config.get('money_budget')
        if not isinstance(money, Mapping) or not isinstance(money.get('path'), str) or not Path(money['path']).is_file():
            raise ValueError('persistent OpenAI CLI run requires its explicitly configured existing money ledger; no new allowance is created')


def _safe_path(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('CLI output and ancestors must not be symlinks')
    return path


class _Claim:
    def __init__(self, directory, output_path, identity):
        self.root = _safe_path(directory)
        self.output = _safe_path(output_path if output_path is not None else self.root / 'trace.json')
        if self.output.exists() or self.root.exists():
            raise FileExistsError('CLI trace or artifacts output already exists; no provider is created')
        self.root.parent.mkdir(parents=True, exist_ok=True)
        self.root.mkdir()  # Atomic, exclusive ownership; failure is never reset.
        self.root_identity = (self.root.stat().st_dev, self.root.stat().st_ino)
        self.claim_path = self.root / 'claim.json'
        claim = {'schema_version': 'ce.cli_front_claim.v1', **identity,
                 'trace_path': str(self.output), 'recording_dir': str(self.root), 'status': 'CLAIMED_ONCE'}
        _write_once(self.claim_path, claim)
        self.claim_digest = _sha(self.claim_path)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.output, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        self.output_identity = (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino)
        content = _bytes(claim)
        os.write(self.fd, content)
        os.fsync(self.fd)
        self.output_digest = hashlib.sha256(content).hexdigest()

    def check(self):
        for path in (self.root, self.output, self.claim_path):
            _safe_path(path)
        if (self.root.stat().st_dev, self.root.stat().st_ino) != self.root_identity:
            raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: recording directory identity changed')
        if (self.output.stat().st_dev, self.output.stat().st_ino) != self.output_identity or _sha(self.output) != self.output_digest:
            raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: trace claim changed')
        if _sha(self.claim_path) != self.claim_digest:
            raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: immutable claim changed')

    def publish(self, content):
        self.check()
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        with os.fdopen(os.dup(self.fd), 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        self.output_digest = hashlib.sha256(content).hexdigest()
        self.check()

    def close(self):
        if getattr(self, 'fd', None) is not None:
            os.close(self.fd)
            self.fd = None


def _front_config(config, run_id):
    result = {key: value for key, value in config.items()
              if key not in {'provider', 'base_url', 'api_key_env', 'timeout_seconds', 'tokenizer'}}
    result['model'] = {'model': config['model'], 'provider': config['provider'],
                       'schema_in_prompt': config.get('schema_in_prompt', True)}
    for key in ('model_revision', 'tokenizer_revision'):
        if isinstance(config.get(key), str) and config[key]:
            result['model'][key] = config[key]
    return {**result, 'run_id': run_id}


def run_recorded_public_case(public_root, case_id, config, *, recording_dir, output_path=None,
                             config_path=None, load_public_member, provider_factory, verify_model):
    started = time.monotonic()
    live_config, received = config, _clone(config)
    received_digest = canonical_digest(received)
    run_id = str(received.get('run_id') or uuid.uuid4())
    owner = _Claim(recording_dir, output_path, {'claim_id': str(uuid.uuid4()), 'run_id': run_id,
        'case_id': case_id, 'public_root': str(Path(public_root).absolute()), 'config_digest': received_digest})
    method, journal, budget, trace, provider, primary = None, None, None, None, None, None
    recording_error = None
    identity_guard = None
    config_source, config_source_digest = None, None
    try:
        config_source = Path(config_path).resolve(strict=True) if config_path is not None else None
        config_source_digest = _sha(config_source) if config_source is not None else None
        _write_once(owner.root / 'config.received.json', _safe_config(received))
        _validate_config(received)
        root = Path(public_root).resolve(strict=True)
        def load_inputs():
            check_case_admission(root, case_id, received)
            row, method_path = load_public_member(root, case_id)
            effective = _clone(received)
            if 'task_path' in row:
                value, projection = method_input_from_task(root, case_id)
                effective['public_execution_capabilities'] = projection.get('public_execution_capabilities')
            else:
                value = json.loads(method_path.read_bytes())
            parsed = parse_method_input(value)
            archive = root / parsed.archive_path
            if not archive.resolve().is_relative_to(root) or any(p.is_symlink() for p in (archive, *archive.parents)):
                raise ValueError('public Broken archive must remain inside the public release root')
            return parsed, effective, method_path, archive
        method, effective, method_path, archive = load_inputs()
        method_value = asdict(method)
        frozen_input = canonical_digest(method_value)
        archive_digest = _sha(archive)
        if archive_digest != method.archive_sha256:
            raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: Broken archive digest differs')
        source_archive = owner.root / 'source.tar.gz'
        with source_archive.open('xb') as stream:
            stream.write(archive.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        workspace = owner.root / 'workspace'
        safe_extract(source_archive, workspace)
        source = workspace / method.source_root
        if compute_snapshot(source).snapshot_id != method.initial_snapshot_id:
            raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: initial source snapshot differs')
        front_config = _front_config(effective, run_id)
        _write_once(owner.root / 'method_input.json', method_value)
        _write_once(owner.root / 'run.config.json', front_config)
        immutable = {name: _sha(owner.root / name) for name in
                     ('config.received.json', 'method_input.json', 'run.config.json', 'source.tar.gz')}
        def identity_guard():
            owner.check()
            if canonical_digest(live_config) != received_digest:
                raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: supplied configuration changed')
            if config_source is not None and _sha(config_source) != config_source_digest:
                raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: source configuration file changed')
            current, current_config, _, current_archive = load_inputs()
            if canonical_digest(asdict(current)) != frozen_input or canonical_digest(current_config) != canonical_digest(effective):
                raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: public input or effective configuration changed')
            if _sha(current_archive) != archive_digest:
                raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: public source archive changed')
            if any(_sha(owner.root / name) != digest for name, digest in immutable.items()):
                raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: saved input/config/archive changed')
            front_config_path = owner.root / 'front.config.json'
            if front_config_path.exists() and canonical_digest(json.loads(front_config_path.read_bytes())) != canonical_digest(front_config):
                raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: effective front configuration changed')
            if compute_snapshot(source).snapshot_id != method.initial_snapshot_id:
                raise CliRunIdentityError('CLI_RUN_IDENTITY_DRIFT: persistent source changed')
        identity_guard()
        journal = ProviderJournal(RunRecorder(owner.root, run_id=run_id, input_digest=method.input_digest))
        journal.record('cli_front_input', attempt_id='run', snapshot_id=method.initial_snapshot_id,
            status='PREPARED', input_refs=tuple(journal.capture(name, kind, snapshot_id=method.initial_snapshot_id)
                for name, kind in [('method_input.json', 'METHOD_INPUT'), ('run.config.json', 'FRONT_CONFIG'),
                                   ('source.tar.gz', 'BROKEN_ARCHIVE')]))
        if time.monotonic() - started >= effective['max_elapsed_seconds']:
            raise BudgetExceeded('Global CLI deadline expired during persistent preparation')
        provider, counter = provider_factory(effective)
        if effective.get('ollama_model_digest'):
            verify_model(effective)
        identity_guard()
        def update(calls):
            active = sys.exc_info()[1] if calls and calls[-1].get('error') else None
            try:
                identity_guard()
                journal.callback(calls, lambda actual: _write_atomic(owner.root / 'model_calls.json', actual))
                identity_guard()
            except Exception as exc:
                if active is not None:
                    active.recording_errors = tuple(getattr(active, 'recording_errors', ())) + (type(exc).__name__ + ': ' + str(exc),)
                    return
                if calls:
                    calls[-1]['error'] = type(exc).__name__ + ': ' + str(exc)
                    calls[-1]['provider_error_metadata'] = {
                        'dispatch': 'RESPONSE_RECEIVED' if 'response_content' in calls[-1] else 'NOT_STARTED',
                        'source': 'CLI_IDENTITY_OR_RECORDING_ADMISSION'}
                raise
        budget = BudgetedProvider(provider, counter, max_tokens=effective['max_total_tokens'],
            max_seconds=effective['max_elapsed_seconds'], max_calls=effective['max_model_calls'], on_update=update)
        budget.started = started
        journal.attach(budget)
        original_complete = budget.complete_quoted
        def guarded_complete(request, quote):
            identity_guard()  # Before the existing budget reserves anything.
            return original_complete(request, quote)
        budget.complete_quoted = guarded_complete
        trace = run_front_recorded(method, workspace, budget, front_config, counter,
            directory=owner.root, journal=journal, attempt_id='cli-run')
        primary = journal.primary_error
        try:
            identity_guard()
        except Exception as exc:
            primary = primary or exc
            trace = replace(trace, status='EVIDENCE_DELIVERY_INCOMPLETE', errors=trace.errors + (str(exc),))
    except Exception as exc:
        primary = exc
        if method is not None:
            status = ('EVIDENCE_DELIVERY_INCOMPLETE' if isinstance(exc, CliRunIdentityError) else
                      'BUDGET_EXHAUSTED' if isinstance(exc, BudgetExceeded) else 'INFRASTRUCTURE_ERROR')
            trace = RunTrace(run_id, case_id, method.input_digest, method.initial_snapshot_id, status,
                             None, None, None, None, None, (), (type(exc).__name__ + ': ' + str(exc),))
    try:
        calls = _clone(budget.calls) if budget is not None else []
        if trace is None:
            failure = {'schema_version': 'ce.cli_front_start_failure.v1', 'run_id': run_id, 'case_id': case_id,
                'status': 'NOT_ADMITTED', 'error_type': type(primary).__name__, 'error': str(primary), 'model_calls': calls}
            content = _write_once(owner.root / 'failure.json', failure)
            owner.publish(content)
        else:
            trace = replace(trace, model_calls=tuple(calls))
            def record_final(name):
                if journal is not None:
                    journal.update(calls)
                    journal.record('cli_front_final', attempt_id='run', snapshot_id=trace.snapshot_id,
                        status=trace.status, call_start=0,
                        input_refs=tuple(journal.latest[key] for key in
                            ('cli_front_input', 'front', 'front_delivery', 'semantic_delivery') if key in journal.latest),
                        output_refs=(journal.capture(name, 'FINAL_CLI_TRACE', snapshot_id=trace.snapshot_id),))
            payload = validate_run_trace(trace.to_wire())
            content = _write_once(owner.root / 'final.trace.json', payload)
            _write_atomic(owner.root / 'model_calls.json', calls)
            if budget is not None:
                _write_once(owner.root / 'budget.json', budget.summary())
            try:
                record_final('final.trace.json')
            except Exception as exc:
                recording_error = exc
                if primary is None:
                    trace = replace(trace, status='INFRASTRUCTURE_ERROR', errors=trace.errors + ('CLI_RECORDING_FAILED: ' + str(exc),))
                    content = _write_once(owner.root / 'failed.final.trace.json', validate_run_trace(trace.to_wire()))
            def reject_final_drift():
                nonlocal trace, primary, content, recording_error
                if trace.status not in {'READY', 'READY_PARTIAL'} or identity_guard is None:
                    return False
                try:
                    identity_guard()
                    return False
                except Exception as exc:
                    primary = primary or exc
                    trace = replace(trace, status='EVIDENCE_DELIVERY_INCOMPLETE',
                        errors=trace.errors + ('CLI_FINAL_IDENTITY_REJECTED: ' + str(exc),))
                    content = _write_once(owner.root / 'identity.failed.trace.json', validate_run_trace(trace.to_wire()))
                    try:
                        record_final('identity.failed.trace.json')
                    except Exception as error:
                        recording_error = recording_error or error
                    return True
            reject_final_drift()  # Includes all writes and the final immutable seal.
            owner.publish(content)
            if reject_final_drift():
                owner.publish(content)
        _write_once(owner.root / 'completion.json', {'schema_version': 'ce.cli_front_completion.v1',
            'run_id': run_id, 'case_id': case_id, 'status': trace.status if trace is not None else 'NOT_ADMITTED',
            'recording_status': ('FAILED' if recording_error is not None or getattr(primary, 'recording_errors', ())
                or getattr(primary, 'delivery_recording_error', None) else 'SEALED' if journal is not None else 'NOT_STARTED'),
            'trace_sha256': hashlib.sha256(content).hexdigest(), 'trace_path': str(owner.output),
            'actual_budget_calls': len(calls), 'recording_errors': list(getattr(primary, 'recording_errors', ())),
            'final_recording_error': str(recording_error) if recording_error is not None else None})
    except Exception as exc:
        if primary is None:
            recording_error = recording_error or exc
            if trace is not None:
                # Even completion failure must not leave a published READY trace.
                trace = replace(trace, status='INFRASTRUCTURE_ERROR',
                    errors=trace.errors + ('CLI_RECORDING_FAILED: ' + str(recording_error),))
                content = _bytes(validate_run_trace(trace.to_wire()))
                try:
                    _write_once(owner.root / 'failed.recording.trace.json', trace.to_wire())
                    if journal is not None:
                        record_final('failed.recording.trace.json')
                except Exception as error:
                    recording_error.recording_errors = tuple(getattr(recording_error, 'recording_errors', ())) + (
                        type(error).__name__ + ': ' + str(error),)
                try:
                    owner.publish(content)
                except Exception as error:
                    recording_error.recording_errors = tuple(getattr(recording_error, 'recording_errors', ())) + (
                        type(error).__name__ + ': ' + str(error),)
            raise recording_error
        primary.recording_errors = tuple(getattr(primary, 'recording_errors', ())) + (type(exc).__name__ + ': ' + str(exc),)
    finally:
        owner.close()
    if trace is None:
        raise primary
    if recording_error is not None and primary is None:
        raise recording_error
    return trace
