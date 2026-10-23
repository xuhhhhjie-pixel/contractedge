"""Six-case process observer with a distinct, strictly bound isolated profile.

Candidate and verifier run only through the existing trusted RuntimeSession.
Host fixtures cannot satisfy this profile, and runtime failure never falls back.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import time
from typing import Mapping
from jsonschema import Draft202012Validator

from contractedge.contracts.runtime import FrozenInvocation, RuntimeSpec
from contractedge.contracts.validation import canonical_digest
from contractedge.development.method_panel_fixtures import (CANDIDATE, OBSERVER, SCOPE,
    candidate_inputs, runtime_inputs, _public_spec, case_definition, requirement_observation,
    enforce_requirement_profile)
from contractedge.execution.runtime_session import RuntimeSession
from contractedge.source.snapshot import compute_snapshot

PROFILE = 'ce.d2_isolated_requirement_profile.v1'
BOUNDARY = 'Actual RuntimeSession candidate and independent six-case checker; expected assertions are absent from candidate mounts. Finite public outputs only; no hidden evaluation.'
SCHEMA = Path(__file__).resolve().parents[1] / 'contracts/schema/d2_isolated_requirement_profile.v1.json'
LIMIT_FIELDS = ('cpus', 'memory_bytes', 'pids_limit', 'tmpfs_bytes', 'stdout_limit', 'stderr_limit', 'output_limit', 'cleanup_seconds')


def _write(path, value):
    from contractedge.orchestration.repair import write_json
    write_json(Path(path), value)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate isolated panel JSON key')
        result[key] = value
    return result


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique)


def candidate_invocation():
    return FrozenInvocation.freeze(('/usr/bin/python3', '-I', '-B', '-c', CANDIDATE,
        '/source', '/inputs/public-inputs.json'))


def observer_invocation():
    return FrozenInvocation.freeze(('/usr/bin/python3', '-I', '-B', '-c', OBSERVER,
        '/checks/spec.json', '/observations/stdout.bin'))


def _run_complete(run, *, role, snapshot, inputs_digest, invocation, checks_digest=None):
    if (not isinstance(run, Mapping) or run.get('role') != role
            or run.get('status') != 'EXECUTION_PASS' or run.get('exit_code') != 0
            or run.get('snapshot_id') != snapshot or run.get('public_inputs_digest') != inputs_digest
            or run.get('invocation_digest') != invocation or run.get('truncated') is not False
            or run.get('process_status') != 'COMPLETE'
            or run.get('cleanup', {}).get('status') != 'CONFIRMED'
            or run.get('close_cleanup', {}).get('status') != 'CONFIRMED'
            or run.get('attestation', {}).get('status') != 'VERIFIED'):
        return False
    expected_mounts = {'/source', '/inputs'} if role == 'candidate' else {'/observations', '/checks'}
    inspect = run['attestation'].get('inspect')
    if not isinstance(inspect, Mapping):
        return False
    mounts = inspect.get('mounts')
    if (inspect.get('network_mode') != 'none' or inspect.get('user') != '10001:10001'
            or not isinstance(mounts, list) or len(mounts) != len(expected_mounts)
            or {row.get('Destination') for row in mounts} != expected_mounts
            or any(row.get('RW') is not False or row.get('Type') != 'bind' for row in mounts)):
        return False
    if role == 'candidate' and (run.get('source_unchanged') is not True or run.get('source_after_snapshot_id') != snapshot):
        return False
    if role == 'observer' and run.get('checks_digest') != checks_digest:
        return False
    for channel in ('stdout', 'stderr'):
        if (type(run.get(channel + '_bytes')) is not int or run[channel + '_bytes'] < 0
                or not isinstance(run.get(channel + '_sha256'), str) or len(run[channel + '_sha256']) != 64):
            return False
    return True


def isolated_panel_observation_complete(observation):
    """Validate this observer's profile AND actual runtime/source/input authority."""
    try:
        if not isinstance(observation, Mapping) or observation.get('status') != 'EXECUTION_PASS':
            return False
        profile = observation.get('required_public_profile')
        if not isinstance(profile, Mapping) or list(Draft202012Validator(_load(SCHEMA)).iter_errors(profile)):
            return False
        finite = enforce_requirement_profile({'decision': 'UNKNOWN'}, observation)
        if not finite['requirement_profile_valid']:
            return False
        semantic = observation['requirement_profile']; runtime = observation.get('runtime')
        if (not isinstance(runtime, Mapping) or runtime.get('close', {}).get('status') != 'CLOSED'
                or runtime['close'].get('schema_version') != 'ce.runtime_session.v1'
                or runtime['close'].get('spec_digest') != canonical_digest(runtime['spec'])
                or canonical_digest(runtime['close'].get('spec')) != canonical_digest(runtime['spec'])):
            return False
        snapshot, inputs = observation['snapshot_id'], profile['candidate_inputs_digest']
        if (profile['status'] != 'COMPLETE' or profile['fixture_id'] != semantic['fixture_id']
                or profile['snapshot_id'] != snapshot or profile['input_digest'] != observation['input_digest']
                or profile['requirement_profile_digest'] != canonical_digest(semantic)
                or profile['candidate_invocation_digest'] != candidate_invocation().digest
                or profile['observer_invocation_digest'] != observer_invocation().digest
                or profile['observer_script_sha256'] != hashlib.sha256(OBSERVER.encode()).hexdigest()
                or profile['runtime_spec_digest'] != canonical_digest(runtime['spec'])
                or profile['public_check_spec_digest'] != runtime.get('public_check_spec_digest')
                or inputs != runtime.get('candidate_inputs_digest')
                or observation.get('isolated') is not True):
            return False
        runs = runtime['close'].get('runs')
        if not isinstance(runs, list) or len(runs) != 2:
            return False
        checker_body = {'schema_version': 'ce.d2_requirement_checks.v1', 'fixture_id': semantic['fixture_id'],
            'snapshot_id': snapshot, 'input_digest': observation['input_digest'],
            'spec_digest': semantic['spec_digest'], 'checks': semantic['checks']}
        checker_bytes = (json.dumps(checker_body, sort_keys=True) + '\n').encode()
        if (runtime['observer'].get('stdout_sha256') != hashlib.sha256(checker_bytes).hexdigest()
                or runtime['observer'].get('stdout_bytes') != len(checker_bytes)):
            return False
        for index, role in enumerate(('candidate', 'observer')):
            run = runtime.get(role)
            if (not _run_complete(run, role=role, snapshot=snapshot, inputs_digest=inputs,
                    invocation=profile[role + '_invocation_digest'], checks_digest=profile['public_check_spec_digest'])
                    or canonical_digest(run) != canonical_digest(runs[index])):
                return False
            host = run['attestation']['inspect'].get('host_config', {})
            spec = runtime['spec']
            if (run['stdout_bytes'] > spec['stdout_limit'] or run['stderr_bytes'] > spec['stderr_limit']
                    or run['stdout_bytes'] + run['stderr_bytes'] > spec['output_limit']):
                return False
            if (host.get('ReadonlyRootfs') is not True or host.get('Privileged') is not False
                    or host.get('NetworkMode') != 'none' or host.get('Memory') != spec['memory_bytes']
                    or host.get('MemorySwap') != spec['memory_bytes'] or host.get('PidsLimit') != spec['pids_limit']
                    or host.get('NanoCpus') != round(spec['cpus'] * 1e9)
                    or set(host.get('CapDrop') or []) != {'ALL'} or host.get('CapAdd')
                    or not any(v in ('no-new-privileges', 'no-new-privileges:true') for v in host.get('SecurityOpt') or [])):
                return False
            inspected, kernel = run['attestation']['inspect'], run['attestation'].get('kernel')
            if (not re.fullmatch(r'[a-f0-9]{64}', str(run.get('container_id', '')))
                    or inspected.get('container_id') != run['container_id']
                    or inspected.get('image_id') != run.get('image_id')
                    or not isinstance(kernel, Mapping)):
                return False
            quota, period = kernel['cpu_max'].split()
            if (kernel['uid'] != spec['uid'] or kernel['gid'] != spec['gid']
                    or not 0 < int(quota) / int(period) <= spec['cpus'] + 0.001
                    or not 0 < int(kernel['memory_max']) <= spec['memory_bytes']
                    or not 0 < int(kernel['pids_max']) <= spec['pids_limit']
                    or int(kernel['cap_eff'], 16) != 0 or kernel['no_new_privs'] != '1'
                    or kernel['seccomp'] != '2' or not 0 < kernel['tmpfs_bytes'] <= spec['tmpfs_bytes']):
                return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError, OSError, ZeroDivisionError):
        return False


def enforce_isolated_panel_profile(review, observation):
    finite = enforce_requirement_profile(review, observation)
    valid = isolated_panel_observation_complete(observation)
    if not valid:
        return {**finite, 'decision': 'UNKNOWN', 'requirement_profile_valid': False,
            'requirement_profile_decision': 'UNKNOWN', 'public_requirement_checks': [],
            'required_public_profile': {'binding_valid': False, 'decision': 'UNKNOWN'},
            'rationale': 'Six-case isolated profile or verified runtime/cleanup identity is incomplete.'}
    return {**finite, 'required_public_profile': {'binding_valid': True,
        'decision': finite['requirement_profile_decision'],
        'profile_digest': canonical_digest(observation['required_public_profile'])}}


class IsolatedPanelObserver:
    def __init__(self, *, image, docker_host, docker_api_version=None, timeout_seconds=30, **limits):
        if set(limits) - set(LIMIT_FIELDS):
            raise ValueError('Unsupported isolated panel resource limit')
        self.image, self.docker_host, self.docker_api_version = image, docker_host, docker_api_version
        self.timeout_seconds, self.limits = timeout_seconds, limits

    def observe(self, source, inputs, output, *, fixture_id):
        source, inputs, output = Path(source).resolve(), Path(inputs).resolve(), Path(output).resolve()
        output.mkdir(parents=True, exist_ok=False)
        deadline = time.monotonic() + self.timeout_seconds
        case = case_definition(fixture_id)
        if _load(inputs) != runtime_inputs(fixture_id):
            raise ValueError('Frozen six-case public inputs differ from the registered fixture')
        snapshot, input_digest = compute_snapshot(source), _sha(inputs)
        request_file, check_file = output / 'candidate-inputs.json', output / 'checks.json'
        _write(request_file, candidate_inputs(fixture_id))
        public = _public_spec(fixture_id)
        _write(check_file, {**public, 'snapshot_id': snapshot.snapshot_id, 'input_digest': input_digest,
            'spec_digest': canonical_digest(public)})
        input_projection = canonical_digest({'public-inputs.json': _sha(request_file)})
        check_projection = canonical_digest({'spec.json': _sha(check_file)})
        candidate, checker = candidate_invocation(), observer_invocation()
        workspace = Path(os.path.commonpath((source, inputs, output)))
        spec = RuntimeSpec(image=self.image, docker_host=self.docker_host, docker_api_version=self.docker_api_version,
            workspace_root=workspace, session_root=output / 'runtime',
            allowed_invocation_digests=(candidate.digest, checker.digest), **self.limits)
        session = None; candidate_result = checked_result = None; closed = {'status': 'NOT_OPENED'}
        status, errors, checks = 'INFRA_ERROR', [], []
        try:
            session = RuntimeSession.open(spec, deadline=deadline)
            candidate_result = session.run_candidate(candidate, snapshot=snapshot, public_inputs=request_file)
            if candidate_result.get('status') == 'EXECUTION_PASS':
                checked_result = session.run_observer(checker, bounded_output=candidate_result, public_check_spec=check_file)
                if checked_result.get('status') == 'EXECUTION_PASS':
                    raw = Path(checked_result['stdout_path']).read_bytes()
                    if (len(raw) != checked_result.get('stdout_bytes')
                            or hashlib.sha256(raw).hexdigest() != checked_result.get('stdout_sha256')):
                        raise ValueError('Independent checker output differs from its trusted runtime receipt')
                    body = _load(checked_result['stdout_path'])
                    if (set(body) != {'schema_version', 'fixture_id', 'snapshot_id', 'input_digest', 'spec_digest', 'checks'}
                            or body['schema_version'] != 'ce.d2_requirement_checks.v1' or body['fixture_id'] != fixture_id
                            or body['snapshot_id'] != snapshot.snapshot_id or body['input_digest'] != input_digest
                            or body['spec_digest'] != canonical_digest(public)):
                        raise ValueError('Six-case independent observer returned a stale or unsupported identity')
                    checks = body['checks']; status = 'EXECUTION_PASS'
                else:
                    status = 'INVALID_OBSERVATION'
            else:
                status = 'EXECUTION_FAILED' if candidate_result.get('status') == 'EXECUTION_FAILED' else 'INFRA_ERROR'
        except Exception as exc:
            status = 'INVALID_OBSERVATION'; errors.append(type(exc).__name__ + ': ' + str(exc))
        finally:
            if session is not None:
                try:
                    closed = session.close()
                except Exception as exc:
                    closed = {'status': 'INFRASTRUCTURE_ERROR', 'error': type(exc).__name__ + ': ' + str(exc)}
        if (closed.get('status') != 'CLOSED' or time.monotonic() > deadline
                or compute_snapshot(source).snapshot_id != snapshot.snapshot_id or _sha(inputs) != input_digest
                or canonical_digest({'public-inputs.json': _sha(request_file)}) != input_projection
                or canonical_digest({'spec.json': _sha(check_file)}) != check_projection):
            status = 'INVALID_OBSERVATION'; errors.append('Runtime deadline/cleanup or frozen source/input/spec integrity did not complete')
        report = {'schema_version': 'ce.d2_isolated_public_observation.v1', 'status': status,
            'snapshot_id': snapshot.snapshot_id, 'input_digest': input_digest, 'observations': [],
            'requirement_profile': None, 'errors': errors, 'isolated': False, 'boundary': BOUNDARY}
        if status == 'EXECUTION_PASS':
            finite = requirement_observation(fixture_id, snapshot.snapshot_id, input_digest, checks,
                locator_path=(output / 'trusted-checks.json').relative_to(workspace))
            # Build new provenance from actual runtime receipts; the finite
            # profile only describes checker predicates and cannot certify OS isolation.
            report.update(observations=finite['observations'], requirement_profile=finite['requirement_profile'], isolated=True)
            for record in report['observations']:
                record['origin'] = 'Independent frozen six-case checker in verified RuntimeSession'
            report['requirement_profile']['observations_digest'] = canonical_digest(report['observations'])
        report['environment_digest'] = canonical_digest({'image': self.image, 'observer': hashlib.sha256(OBSERVER.encode()).hexdigest(),
            'candidate_invocation': candidate.digest, 'observer_invocation': checker.digest,
            'resources': {key: getattr(spec, key) for key in LIMIT_FIELDS}, 'runtime_kind': 'isolated-public-process'})
        report['runtime'] = {'candidate': candidate_result, 'observer': checked_result, 'close': closed,
            'candidate_inputs_digest': input_projection, 'public_check_spec_digest': check_projection, 'spec': spec.to_wire()}
        report['required_public_profile'] = {'schema_version': PROFILE, 'fixture_id': fixture_id,
            'snapshot_id': snapshot.snapshot_id, 'input_digest': input_digest,
            'candidate_inputs_digest': input_projection, 'public_check_spec_digest': check_projection,
            'requirement_profile_digest': canonical_digest(report['requirement_profile']),
            'candidate_invocation_digest': candidate.digest, 'observer_invocation_digest': checker.digest,
            'observer_script_sha256': hashlib.sha256(OBSERVER.encode()).hexdigest(),
            'runtime_spec_digest': canonical_digest(spec.to_wire()), 'status': 'COMPLETE' if status == 'EXECUTION_PASS' else 'INCOMPLETE'}
        if status == 'EXECUTION_PASS' and not isolated_panel_observation_complete(report):
            report.update(status='INVALID_OBSERVATION', isolated=False)
            report['required_public_profile']['status'] = 'INCOMPLETE'
            report['errors'].append('Strict six-case runtime/profile completeness validation failed')
        _write(output / 'trusted-checks.json', {'checks': checks})
        _write(output / 'observation.json', report)
        return report
