"""Explicit public failure selection and request-checked front-half replay.

This developer tool never discovers run roots or executes source from a package.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

from jsonschema import Draft202012Validator

VERSION = 'ce.public_failure.v1'
ROLES = {'method_input', 'model_calls', 'front_trace', 'front_config',
         'public_observations', 'execution_feedback'}
REQUIRED = ROLES - {'execution_feedback'}
REQUEST_FIELDS = {'model', 'prompt_version', 'messages', 'max_output_tokens',
                  'temperature', 'metadata', 'output_schema', 'thinking'}
TOKENIZERS = {'utf8_byte_bound', 'whitespace_fixture', 'qwen35_local'}
LIMIT = 64 * 1024 * 1024
FORBIDDEN = {'.git', 'gt', 'fixed', 'evaluation', 'hidden', 'graders', 'credentials', 'secrets'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def relative(value, *, public_only=True):
    if not isinstance(value, str) or '\\' in value:
        raise ValueError('Expected a portable relative path')
    p = PurePosixPath(value)
    if p.is_absolute() or not p.parts or any(x in {'..', '.'} or ':' in x for x in p.parts):
        raise ValueError('Path traversal or absolute path rejected')
    if public_only and any(x.casefold() in FORBIDDEN or x.casefold().startswith('.env') for x in p.parts):
        raise ValueError('Non-public path rejected: ' + value)
    return p.as_posix()


def regular(root, name, *, public_only=True):
    name = relative(name, public_only=public_only)
    path = root / name
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink selection rejected: ' + name)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Missing selected file: ' + name)
    if path.stat().st_size > LIMIT:
        raise ValueError('Selected file exceeds public package size bound')
    return path


def public_bytes(data):
    if re.search(rb'BEGIN [^\n]*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}', data):
        raise ValueError('Possible credential in selected material; export refused')
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return
    def visit(obj):
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key.casefold() in {'api_key', 'authorization', 'access_token', 'password', 'private_key'} and val:
                    raise ValueError('Credential field in selected material; export refused')
                visit(val)
        elif isinstance(obj, list):
            for val in obj:
                visit(val)
    visit(value)


def method_identity(method_root):
    src = Path(method_root).resolve(strict=True) / 'src'
    files = {}
    for p in sorted(src.rglob('*')):
        if p.is_file() and p.suffix in {'.py', '.json'}:
            name = p.relative_to(src).as_posix()
            # This is the trusted runtime implementation, including its public
            # evaluation adapter code; it is not benchmark evaluation material.
            files[name] = sha(regular(src, name, public_only=False).read_bytes())
    if not files:
        raise ValueError('Trusted method root has no runtime source')
    return sha(canonical(files))


def export_package(run_root, source_root, recipe, output):
    run_root, source_root, output = Path(run_root).resolve(strict=True), Path(source_root).resolve(strict=True), Path(output).resolve()
    if output.exists() or output.is_relative_to(source_root) or output.is_relative_to(run_root):
        raise ValueError('Output must be a new independent directory')
    if set(recipe.get('files', {})) - ROLES:
        raise ValueError('Unknown artifact role')
    if recipe.get('scope') != 'front_semantic_failure' or recipe.get('semantic_reviewer') != 'disabled':
        raise ValueError('Only explicit front semantic replay with disabled reviewer is supported')
    if recipe.get('tokenizer') not in TOKENIZERS:
        raise ValueError('Explicit supported tokenizer required')
    selected, missing = {}, []
    for role in sorted(REQUIRED | set(recipe['files'])):
        name = recipe['files'].get(role)
        if name is None:
            missing.append(role)
            continue
        try:
            path = regular(run_root, name)
        except ValueError:
            missing.append(role)
            continue
        selected['artifacts/' + role + '.json'] = (path.read_bytes(), role)
    source_names = recipe.get('source_files', [])
    if not isinstance(source_names, list) or len(source_names) != len(set(source_names)):
        raise ValueError('source_files must be a unique explicit list')
    for name in source_names:
        name = relative(name)
        selected['broken/' + name] = (regular(source_root, name).read_bytes(), 'broken_source')
    if not source_names:
        missing.append('broken_source')
    if sum(len(data) for data, _ in selected.values()) > LIMIT:
        raise ValueError('Public package exceeds 64 MiB bound')
    for data, _ in selected.values():
        public_bytes(data)
    identity = {key: recipe.get(key) for key in ['method_source_sha256', 'broken_snapshot_id', 'input_digest', 'run_id',
                                               'tokenizer', 'scope', 'semantic_reviewer', 'model_call_range']}
    manifest = {'schema_version': VERSION, 'identity': identity, 'missing_roles': missing,
                'files': {name: {'role': role, 'sha256': sha(data), 'bytes': len(data)} for name, (data, role) in selected.items()}}
    output.mkdir(parents=True)
    for name, (data, _) in selected.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (output / 'manifest.json').write_bytes(canonical(manifest) + b'\n')
    return check_package(output)


def contract_errors(value, filename, role, errors):
    # Schemas belong to this trusted developer tool, never to the selected package.
    schema = load(Path(__file__).resolve().parents[1] / 'src/contractedge/contracts/schema' / filename)
    invalid = sorted(Draft202012Validator(schema).iter_errors(value),
                     key=lambda e: tuple(map(str, e.absolute_path)))
    for error in invalid[:12]:
        errors.append('INVALID_CONTRACT:' + role + ':' + '.'.join(map(str, error.absolute_path)) + ':' + error.validator)


def positive_int(value, minimum=1):
    return type(value) is int and value >= minimum


def finite_number(value, minimum=0):
    return type(value) in {int, float} and math.isfinite(value) and value >= minimum


def package_result(package, identity, errors, missing=(), cfg=None, calls=(), bounds=None):
    bounds = bounds if bounds is not None else [0, 0]
    return {'schema_version': VERSION, 'status': 'INVALID' if errors else ('INCOMPLETE' if missing else 'COMPLETE'),
            'errors': errors, 'missing': sorted(set(missing)), 'identity': identity,
            'configuration_sha256': sha(canonical(cfg)), 'manifest_sha256': sha((package/'manifest.json').read_bytes()),
            'recorded_calls': bounds[1] - bounds[0], 'model_call_range': bounds,
            'total_ledger_calls': len(calls), 'real_model_http_calls': 0}


def check_package(package):
    package = Path(package).resolve(strict=True)
    try:
        manifest = load(regular(package, 'manifest.json'))
    except (ValueError, UnicodeDecodeError) as exc:
        # Path/credential selection remains refused by regular/public_bytes.
        if not isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
            raise
        return package_result(package, {}, ['INVALID_MANIFEST_JSON'])
    if not isinstance(manifest, dict):
        return package_result(package, {}, ['INVALID_MANIFEST:object_required'])
    if manifest.get('schema_version') != VERSION:
        return package_result(package, {}, ['INVALID_MANIFEST:schema_version'])
    if (not isinstance(manifest.get('files'), dict) or not isinstance(manifest.get('identity'), dict)
            or not isinstance(manifest.get('missing_roles', []), list)
            or any(not isinstance(role, str) or role not in ROLES | {'broken_source'}
                   for role in manifest.get('missing_roles', []))):
        return package_result(package, {}, ['INVALID_MANIFEST:fields'])
    errors, missing = [], list(manifest.get('missing_roles', []))
    roles, sources, total = {}, {}, 0
    for name, item in manifest['files'].items():
        if (not isinstance(item, dict) or not isinstance(item.get('role'), str)
                or not isinstance(item.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])
                or not positive_int(item.get('bytes'), 0)):
            errors.append('INVALID_MANIFEST_ENTRY:' + name)
            continue
        path = regular(package, name)
        data = path.read_bytes()
        public_bytes(data)
        total += len(data)
        if sha(data) != item['sha256'] or len(data) != item['bytes']:
            errors.append('FILE_IDENTITY_MISMATCH:' + name)
        role = item['role']
        if role == 'broken_source':
            if not name.startswith('broken/'):
                errors.append('INVALID_SOURCE_LOCATION:' + name)
            sources[name.removeprefix('broken/')] = sha(data)
        elif role in ROLES and name == 'artifacts/' + role + '.json' and role not in roles:
            try:
                roles[role] = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                errors.append('INVALID_ARTIFACT_JSON:' + role)
                roles[role] = None
        else:
            errors.append('INVALID_ARTIFACT_ROLE:' + name)
    if total > LIMIT:
        errors.append('PACKAGE_TOO_LARGE')
    # Extra files are never imported or read as input; reject rather than silently copy them.
    actual = set()
    for directory, dirs, names in os.walk(package, followlinks=False):
        for child in dirs + names:
            if (Path(directory)/child).is_symlink():
                errors.append('UNREGISTERED_SYMLINK')
        actual.update((Path(directory)/name).relative_to(package).as_posix() for name in names)
    if actual != set(manifest['files']) | {'manifest.json'}:
        errors.append('UNREGISTERED_PACKAGE_FILES')
    missing.extend(sorted(REQUIRED - set(roles)))
    identity = manifest['identity']
    for key in ['method_source_sha256', 'broken_snapshot_id', 'input_digest']:
        if not isinstance(identity.get(key), str) or not re.fullmatch('[0-9a-f]{64}', identity[key]):
            errors.append('INVALID_IDENTITY:' + key)
    if not isinstance(identity.get('run_id'), str) or not identity['run_id'].strip():
        errors.append('INVALID_IDENTITY:run_id')
    if not sources:
        missing.append('broken_source')
    elif sha(canonical(sources)) != identity.get('broken_snapshot_id'):
        errors.append('BROKEN_SNAPSHOT_MISMATCH')
    for role, filename in [('method_input', 'method_input.v1_1.json'), ('front_trace', 'run_trace.v1_1.json'),
                           ('execution_feedback', 'execution_feedback.v1.json')]:
        if role in roles and not (role == 'execution_feedback' and roles[role] is None):
            contract_errors(roles[role], filename, role, errors)
    value, trace, cfg = [roles.get(role) if isinstance(roles.get(role), dict) else {}
                         for role in ['method_input', 'front_trace', 'front_config']]
    if 'method_input' in roles:
        body = {k:v for k,v in value.items() if k != 'input_digest'}
        if sha(canonical(body)) != value.get('input_digest') or value.get('input_digest') != identity.get('input_digest'):
            errors.append('METHOD_INPUT_DIGEST_MISMATCH')
        if value.get('initial_snapshot_id') != identity.get('broken_snapshot_id'):
            errors.append('METHOD_INPUT_SNAPSHOT_MISMATCH')
    if 'front_trace' in roles:
        for key, actual_value in [('run_id', identity.get('run_id')), ('input_digest', identity.get('input_digest')),
                                  ('snapshot_id', identity.get('broken_snapshot_id'))]:
            if trace.get(key) != actual_value:
                errors.append('TRACE_IDENTITY_MISMATCH:' + key)
        if value and trace.get('case_id') != value.get('case_id'):
            errors.append('TRACE_CASE_MISMATCH')
        context = trace.get('run_context')
        if isinstance(context, dict):
            for key in ['run_id', 'case_id', 'input_digest', 'snapshot_id']:
                if context.get(key) != trace.get(key):
                    errors.append('TRACE_CONTEXT_MISMATCH:' + key)
    if 'front_config' in roles:
        model = cfg.get('model')
        if (cfg.get('run_id') != identity.get('run_id') or not isinstance(model, dict)
                or not isinstance(model.get('model'), str) or not model['model'].strip()
                or not positive_int(cfg.get('max_context_tokens'))):
            errors.append('EFFECTIVE_FRONT_CONFIG_REQUIRED:front_config')
        for key in ['max_output_tokens', 'max_total_tokens', 'max_model_calls', 'model_context_tokens',
                    'initial_diagnosis_context_tokens', 'max_format_retries', 'top_n']:
            if key in cfg and not positive_int(cfg[key], 0 if key == 'max_format_retries' else 1):
                errors.append('INVALID_FRONT_CONFIG:front_config:' + key)
        if 'max_seconds' in cfg and not finite_number(cfg['max_seconds'], 0.001):
            errors.append('INVALID_FRONT_CONFIG:front_config:max_seconds')
    if 'public_observations' in roles:
        observations = roles['public_observations']
        if not isinstance(observations, list):
            errors.append('INVALID_PUBLIC_OBSERVATIONS:list_required')
        else:
            schema = load(Path(__file__).resolve().parents[1] / 'src/contractedge/contracts/schema/method_input.v1_1.json')
            evidence = {**schema['$defs']['evidence'], '$defs': schema['$defs'], 'additionalProperties': True}
            validator = Draft202012Validator(evidence)
            for index, row in enumerate(observations):
                if not validator.is_valid(row):
                    errors.append(f'INVALID_PUBLIC_OBSERVATION:{index}')
                elif row['snapshot_id'] != identity.get('broken_snapshot_id'):
                    errors.append(f'OBSERVATION_SNAPSHOT_MISMATCH:{index}')
    feedback = roles.get('execution_feedback')
    if isinstance(feedback, dict):
        for key in ['run_id', 'input_digest']:
            if feedback.get(key) != identity.get(key):
                errors.append('FEEDBACK_IDENTITY_MISMATCH:' + key)
        if value and feedback.get('case_id') != value.get('case_id'):
            errors.append('FEEDBACK_CASE_MISMATCH')
        if feedback.get('to_snapshot') != identity.get('broken_snapshot_id'):
            errors.append('FEEDBACK_SNAPSHOT_MISMATCH')
    if identity.get('scope') != 'front_semantic_failure' or identity.get('semantic_reviewer') != 'disabled' or identity.get('tokenizer') not in TOKENIZERS:
        errors.append('UNSUPPORTED_REPLAY_SCOPE')
    calls = roles.get('model_calls', [])
    if not isinstance(calls, list):
        errors.append('INVALID_MODEL_CALLS:list_required')
        calls = []
    if not calls:
        missing.append('complete_model_calls')
        calls = []
    bounds = identity.get('model_call_range')
    if not calls:
        bounds = [0, 0]
    elif bounds is None:
        bounds = [0, len(calls)]
    if (not isinstance(bounds, list) or len(bounds) != 2 or any(type(v) is not int for v in bounds)
            or not 0 <= bounds[0] <= bounds[1] <= len(calls)):
        errors.append('INVALID_MODEL_CALL_RANGE')
        bounds = [0, 0]
    selected_calls = calls[bounds[0]:bounds[1]]
    if not selected_calls:
        missing.append('complete_model_calls')
    front_calls = trace.get('model_calls') if isinstance(trace.get('model_calls'), list) else []
    if trace and calls and len(front_calls) != len(selected_calls):
        errors.append('FRONT_CALL_RANGE_MISMATCH')
    for offset, call in enumerate(selected_calls):
        index = offset + bounds[0]
        if not isinstance(call, dict):
            errors.append(f'INVALID_MODEL_CALL:call_{index}')
            continue
        request = call.get('request')
        if not isinstance(request, dict) or REQUEST_FIELDS - set(request):
            missing.append(f'complete_request_parameters:call_{index}')
            continue
        if (set(request) != REQUEST_FIELDS
                or any(not isinstance(request[key], str) or not request[key].strip() for key in ['model', 'prompt_version'])
                or not isinstance(request['messages'], list) or not request['messages']
                or any(not isinstance(m, dict) or not isinstance(m.get('role'), str) or not m['role']
                       or not isinstance(m.get('content'), str) for m in request['messages'])
                or not isinstance(request['metadata'], dict) or not positive_int(request['max_output_tokens'])
                or not finite_number(request['temperature'])
                or not (request['output_schema'] is None or isinstance(request['output_schema'], dict))
                or request['thinking'] not in (None, 'enabled', 'disabled')):
            errors.append(f'INVALID_REQUEST_PARAMETERS:call_{index}')
        # Bind exact request to the durable ledger, including every saved public request field.
        for key in ['model', 'prompt_version', 'messages', 'output_schema']:
            if request[key] != call.get(key):
                errors.append(f'LEDGER_REQUEST_MISMATCH:call_{index}:{key}')
        if request['metadata'] != call.get('request_metadata') or request['max_output_tokens'] != call.get('output_tokens_reserved'):
            errors.append(f'LEDGER_REQUEST_MISMATCH:call_{index}:parameters')
        if not isinstance(call.get('response_content'), str) or not isinstance(call.get('response_metadata'), dict) or call.get('error'):
            missing.append(f'completed_response:call_{index}')
        if trace and offset < len(front_calls):
            front_call = front_calls[offset]
            if not isinstance(front_call, dict):
                errors.append(f'INVALID_FRONT_CALL:call_{index}')
                continue
            for key in ['prompt_version', 'response_content', 'response_metadata']:
                if front_call.get(key) != call.get(key):
                    errors.append(f'FRONT_LEDGER_MISMATCH:call_{index}:{key}')
    return package_result(package, identity, errors, missing, cfg, calls, bounds)


class ReplayMismatch(RuntimeError):
    pass


def worker(package, method_root, expected_method_sha256):
    report = check_package(package)
    if report['status'] != 'COMPLETE':
        return report
    identity = report['identity']
    if method_identity(method_root) != expected_method_sha256 or expected_method_sha256 != identity['method_source_sha256']:
        raise ValueError('TRUSTED_METHOD_IDENTITY_MISMATCH')
    # The package contains data only. Import only the independently supplied, hash-checked method.
    sys.path.insert(0, str(Path(method_root).resolve()/'src'))
    import socket
    import urllib.request
    def denied(*args, **kwargs):
        raise RuntimeError('OFFLINE_REPLAY_EXTERNAL_IO_DENIED')
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.socket.sendto = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    urllib.request.urlopen = denied
    subprocess.Popen = denied
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.runner import run_front_half
    from contractedge.contracts.validation import parse_method_input
    from contractedge.source.snapshot import compute_snapshot
    p = Path(package)
    roles = {role: load(p/'artifacts'/f'{role}.json') for role in REQUIRED}
    value = parse_method_input(roles['method_input'])
    if compute_snapshot(p/'broken').snapshot_id != value.initial_snapshot_id:
        raise ValueError('FROZEN_METHOD_SNAPSHOT_MISMATCH')
    if identity['tokenizer'] == 'utf8_byte_bound':
        counter = lambda text: len(text.encode('utf-8'))
    elif identity['tokenizer'] == 'whitespace_fixture':
        counter = lambda text: len(text.split())
    else:
        from contractedge.tokenizers import qwen35_token_count
        counter = qwen35_token_count
    start, stop = report['model_call_range']
    calls = roles['model_calls'][start:stop]
    class Tape:
        index = 0
        mismatch = None
        def complete_json(self, request):
            actual = json.loads(canonical(dataclasses.asdict(request)))
            if self.index >= len(calls):
                self.mismatch = {'call_index': self.index, 'reason': 'UNRECORDED_REQUEST'}
            elif actual != calls[self.index]['request']:
                expected = calls[self.index]['request']
                self.mismatch = {'call_index': self.index, 'reason': 'FINAL_REQUEST_CHANGED',
                                 'fields': sorted(k for k in REQUEST_FIELDS if actual.get(k) != expected.get(k)),
                                 'expected_sha256': sha(canonical(expected)), 'actual_sha256': sha(canonical(actual))}
            if self.mismatch:
                raise ReplayMismatch(str(self.mismatch))
            saved = calls[self.index]
            self.index += 1
            return ModelResponse(saved['response_content'], saved['response_metadata'])
    tape = Tape()
    feedback = p/'artifacts/execution_feedback.json'
    import tempfile
    import shutil
    with tempfile.TemporaryDirectory(prefix='ce-public-replay-') as scratch:
        workspace = Path(scratch)/'workspace'
        source = workspace if value.source_root == '.' else workspace/relative(value.source_root)
        shutil.copytree(p/'broken', source)
        trace = run_front_half(roles['method_input'], workspace, tape, roles['front_config'], counter,
                               public_observations=tuple(roles['public_observations']),
                               execution_feedback=load(feedback) if feedback.exists() else None).to_wire()
        if compute_snapshot(source).snapshot_id != value.initial_snapshot_id:
            raise ValueError('BROKEN_SOURCE_CHANGED_DURING_REPLAY')
    terminal = ['status', 'diagnosis', 'changeset', 'errors', 'structural_validation']
    differences = [key for key in terminal if trace.get(key) != roles['front_trace'].get(key)]
    if method_identity(method_root) != expected_method_sha256 or check_package(package)['status'] != 'COMPLETE':
        raise ValueError('REPLAY_INPUT_CHANGED_DURING_RUN')
    matched = tape.mismatch is None and tape.index == len(calls) and not differences
    return {**report, 'status': 'REPLAY_MATCH' if matched else 'REPLAY_DIVERGED', 'matched_calls': tape.index,
            'first_request_divergence': tape.mismatch, 'terminal_differences': differences,
            'replayed_status': trace['status'], 'replayed_errors': trace.get('errors', []),
            'scope': 'front semantic request/validation replay; no HTTP, latency, execution or repair-effectiveness replay'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    export = sub.add_parser('export')
    for key in ['run-root', 'source-root', 'recipe', 'output']:
        export.add_argument('--'+key, required=True)
    check = sub.add_parser('check');check.add_argument('--package', required=True)
    for command in ['replay', '_worker']:
        replay = sub.add_parser(command)
        for key in ['package', 'method-root', 'expected-method-sha256']:
            replay.add_argument('--'+key, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            result = export_package(args.run_root, args.source_root, load(args.recipe), args.output)
        elif args.command == 'check':
            result = check_package(args.package)
        elif args.command == '_worker':
            result = worker(args.package, args.method_root, args.expected_method_sha256)
        else:
            command = [sys.executable, '-I', str(Path(__file__).resolve()), '_worker', '--package', str(Path(args.package).resolve()),
                       '--method-root', str(Path(args.method_root).resolve()), '--expected-method-sha256', args.expected_method_sha256]
            env = {k:v for k,v in os.environ.items() if k in {'PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'HOME'}}
            completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
            if not completed.stdout.strip():
                raise ValueError('REPLAY_WORKER_FAILED:' + completed.stderr[-1200:])
            result = json.loads(completed.stdout)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result['status'] in {'COMPLETE', 'REPLAY_MATCH'} else 1
    except Exception as exc:
        print(json.dumps({'schema_version': VERSION, 'status': 'ERROR', 'errors': [type(exc).__name__+': '+str(exc)], 'real_model_http_calls': 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
