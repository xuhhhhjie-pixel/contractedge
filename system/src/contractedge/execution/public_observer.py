"""Frozen public process-output checks, separated from untrusted candidate code.

The candidate bridge is untrusted process I/O. Only the separate observer may
assign public check statuses. This does not establish internal function facts
or hidden correctness and never imports a candidate in the control process.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from typing import Mapping

import yaml

from contractedge.contracts.runtime import FrozenInvocation, RuntimeSpec
from contractedge.contracts.validation import canonical_digest
from contractedge.execution.runtime_session import RuntimeSession
from contractedge.source.snapshot import compute_snapshot


_CANDIDATE = r'''import importlib.util,json,pathlib,sys
source=pathlib.Path('/source')
requests=json.loads(pathlib.Path('/inputs/public-inputs.json').read_text())['requests']
spec=importlib.util.spec_from_file_location('candidate',source/'app.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
results=[]
for request in requests:
    state=None
    if request.get('warm_config') is not None:
        state=module.render(request['warm_config'],None).get('state')
    actual=module.render(request['config'],state)
    results.append({'id':request['id'],'actual':actual})
print(json.dumps({'schema_version':'ce.public_process_outputs.v1','results':results},sort_keys=True))
'''

_OBSERVER = r'''import json,pathlib
def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON key')
        result[key]=value
    return result
spec=json.loads(pathlib.Path('/checks/spec.json').read_text(),object_pairs_hook=unique)
raw=pathlib.Path('/observations/stdout.bin').read_bytes()
body=json.loads(raw.decode('utf-8'),object_pairs_hook=unique)
if not isinstance(body,dict) or set(body)!={'schema_version','results'} or body['schema_version']!='ce.public_process_outputs.v1':
    raise ValueError('Candidate must provide actual process values, not check verdicts')
rows=body['results'];expected=spec['scenarios']
if not isinstance(rows,list) or len(rows)!=len(expected):raise ValueError('Missing or extra result')
by_id={}
for row in rows:
    if not isinstance(row,dict) or set(row)!={'id','actual'} or not isinstance(row['actual'],dict):raise ValueError('Invalid actual result')
    if row['id'] in by_id:raise ValueError('Duplicate result ID')
    by_id[row['id']]=row['actual']
if set(by_id)!={s['id'] for s in expected}:raise ValueError('Wrong result IDs')
checks=[]
for scenario in expected:
    actual=by_id[scenario['id']];wanted=scenario['expected']
    if scenario['id']=='stable':
        selected={'modes':[by_id[s['id']].get('mode') for s in expected]}
        wanted={'modes':['stable']*len(expected)}
    else:
        if any(key not in actual for key in wanted):raise ValueError('Missing expected public output field')
        selected={key:actual[key] for key in wanted}
    checks.append({'id':scenario['id'],'status':'PASS' if selected==wanted else 'FAIL','actual':selected,'expected':wanted})
print(json.dumps({'schema_version':'ce.public_process_checks.v1','snapshot_id':spec['snapshot_id'],
    'input_digest':spec['input_digest'],'checks':checks},sort_keys=True))
'''

_STATEMENTS = {
    'selected': 'Enabled production outputs consume the published selected configuration, including declared reload/consumer scenarios.',
    'disabled': 'Disabled production outputs preserve the published default configuration.',
    'stable': 'Every published scenario preserves stable mode.',
}
_PROBE_ID = 'contractedge-public-process-v1'


def public_check_capabilities():
    """Published assertions are input capability descriptions, never results."""
    return {'schema_version': 'ce.public_process_check_capabilities.v1', 'probe_id': _PROBE_ID,
        'observer_sha256': hashlib.sha256(_OBSERVER.encode()).hexdigest(),
        'checks': [{'id': _PROBE_ID + ':' + key, 'statement': value,
                    'scope': 'Published finite process-output scenarios only.'} for key, value in _STATEMENTS.items()]}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate public JSON key')
        result[key] = value
    return result


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique)


def _requests(source, inputs):
    """Read public data only; expected values stay outside the candidate input."""
    body = _load(inputs)
    scenarios = body.get('scenarios') if isinstance(body, dict) else None
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 16:
        raise ValueError('A finite public scenario list is required')
    identifiers = [row.get('id') for row in scenarios if isinstance(row, dict)]
    if (len(identifiers) != len(scenarios) or any(not isinstance(value, str) for value in identifiers)
            or set(identifiers) != set(_STATEMENTS) or len(set(identifiers)) != len(identifiers)):
        raise ValueError('Published selected/disabled/stable checks must appear exactly once')
    base = yaml.safe_load((Path(source) / 'config.yaml').read_text(encoding='utf-8'))
    if not isinstance(base, dict):
        raise ValueError('Public base configuration must be an object')
    requests = []
    for row in scenarios:
        if not isinstance(row.get('config'), dict) or not isinstance(row.get('expected'), dict) or not row['expected']:
            raise ValueError('Each public scenario requires configuration and a published assertion')
        warm = row.get('warm_config')
        if warm is not None and not isinstance(warm, dict):
            raise ValueError('Public warm configuration must be an object')
        requests.append({'id': row['id'], 'config': {**base, **row['config']},
                         'warm_config': {**base, **warm} if warm is not None else None})
    return body, {'requests': requests}


class IsolatedPublicObserver:
    """Observer for the three published developer cases, using actual sessions."""
    def __init__(self, *, image, docker_host, docker_api_version=None, timeout_seconds=30):
        self.image, self.docker_host = image, docker_host
        self.docker_api_version, self.timeout_seconds = docker_api_version, timeout_seconds

    def observe(self, source, inputs, output):
        deadline = time.monotonic() + self.timeout_seconds
        source, inputs, output = Path(source), Path(inputs), Path(output)
        output.mkdir(parents=True, exist_ok=False)
        snapshot = compute_snapshot(source)
        public, requests = _requests(source, inputs)
        input_digest = _sha(inputs)
        request_file = output / 'candidate-inputs.json'
        _write(request_file, requests)
        check_file = output / 'checks.json'
        _write(check_file, {**public, 'snapshot_id': snapshot.snapshot_id, 'input_digest': input_digest})
        candidate_inputs_digest = canonical_digest({'public-inputs.json': _sha(request_file)})
        public_check_spec_digest = canonical_digest({'spec.json': _sha(check_file)})
        candidate = FrozenInvocation.freeze(('/usr/bin/python3', '-I', '-B', '-c', _CANDIDATE))
        observer = FrozenInvocation.freeze(('/usr/bin/python3', '-I', '-B', '-c', _OBSERVER))
        workspace_root = Path(os.path.commonpath((source.resolve(), inputs.resolve(), output.resolve())))
        spec = RuntimeSpec(image=self.image, docker_host=self.docker_host,
            workspace_root=workspace_root, session_root=output / 'runtime',
            allowed_invocation_digests=(candidate.digest, observer.digest),
            docker_api_version=self.docker_api_version)
        session = None
        candidate_result, checked_result, closed, checks = None, None, None, []
        errors = []
        status = 'INFRA_ERROR'
        try:
            session = RuntimeSession.open(spec, deadline=deadline)
            candidate_result = session.run_candidate(candidate, snapshot=snapshot, public_inputs=request_file)
            if candidate_result['status'] == 'EXECUTION_PASS':
                checked_result = session.run_observer(observer, bounded_output=candidate_result, public_check_spec=check_file)
                if checked_result['status'] == 'EXECUTION_PASS':
                    body = _load(checked_result['stdout_path'])
                    if (body.get('schema_version') != 'ce.public_process_checks.v1'
                            or body.get('snapshot_id') != snapshot.snapshot_id or body.get('input_digest') != input_digest):
                        raise ValueError('Observer identity differs from the frozen public invocation')
                    checks = body['checks']
                    if (not isinstance(checks, list) or len(checks) != len(_STATEMENTS)
                            or {row['id'] for row in checks} != set(_STATEMENTS)
                            or any(row.get('status') not in {'PASS', 'FAIL'} for row in checks)):
                        raise ValueError('Observer did not cover every registered public check')
                    status = 'EXECUTION_PASS'
                else:
                    status = 'INVALID_OBSERVATION'
            else:
                status = 'EXECUTION_FAILED' if candidate_result['status'] == 'EXECUTION_FAILED' else 'INFRA_ERROR'
        except Exception as exc:
            errors.append(type(exc).__name__ + ': ' + str(exc))
            status = 'INVALID_OBSERVATION'
        finally:
            try:
                closed = session.close() if session is not None else {'status': 'NOT_OPENED'}
            except Exception as exc:
                closed = {'status': 'INFRASTRUCTURE_ERROR', 'error': type(exc).__name__ + ': ' + str(exc)}
        if compute_snapshot(source).snapshot_id != snapshot.snapshot_id or _sha(inputs) != input_digest:
            status = 'INVALID_OBSERVATION'
            errors.append('Source or public input changed during the observation')
        # The close receipt, like each run result, must be confirmed by runtime.
        if closed.get('status') not in {'CLOSED', 'CLEANED', 'CLEANUP_CONFIRMED'}:
            status = 'INFRA_ERROR'
            errors.append('Runtime session cleanup was not confirmed')
        observations = []
        if status == 'EXECUTION_PASS':
            for check in checks:
                value = {'probe_id': _PROBE_ID, 'id': check['id'], 'status': check['status'],
                    'statement': _STATEMENTS[check['id']],
                    'observed': {'actual': check['actual'], 'expected': check['expected'],
                        'scope': 'Bounded public candidate process output; no claim about hidden or internal function state.'}}
                excerpt = json.dumps(value, ensure_ascii=False, sort_keys=True)
                observations.append({'id': 'OBS-' + check['id'], 'snapshot_id': snapshot.snapshot_id,
                    'kind': 'PUBLIC_OBSERVATION', 'evidence_kind': 'PUBLIC_BEHAVIOR',
                    'locator': {'path': str((output / 'trusted-checks.json').relative_to(workspace_root)), 'field': check['id'],
                        'truncated': False, 'whole_file_read': True},
                    'content_digest': hashlib.sha256(excerpt.encode()).hexdigest(), 'excerpt': excerpt,
                    'structured_complete': True,
                    'behavior_check': {'id': _PROBE_ID + ':' + check['id'], 'status': check['status'],
                        'probe_sha256': hashlib.sha256(_OBSERVER.encode()).hexdigest()},
                    'origin': 'independent frozen public process-output observer'})
        _write(output / 'trusted-checks.json', {'checks': checks})
        profile = {'schema_version': 'ce.required_public_profile.v1', 'snapshot_id': snapshot.snapshot_id,
            'input_digest': input_digest, 'check_ids': sorted(_STATEMENTS), 'checks': checks,
            'observations_digest': canonical_digest(observations),
            'observer_sha256': hashlib.sha256(_OBSERVER.encode()).hexdigest(),
            'status': 'COMPLETE' if status == 'EXECUTION_PASS' else 'INCOMPLETE'}
        report = {'schema_version': 'ce.public_observation.v1', 'status': status,
            'snapshot_id': snapshot.snapshot_id, 'observations': observations, 'input_digest': input_digest,
            'environment_digest': canonical_digest({'image': self.image, 'observer': profile['observer_sha256'],
                'network': 'none', 'runtime_kind': 'isolated-public-process'}),
            'required_public_profile': profile, 'errors': errors,
            'runtime': {'candidate': candidate_result, 'observer': checked_result, 'close': closed,
                'candidate_inputs_digest': candidate_inputs_digest,
                'public_check_spec_digest': public_check_spec_digest},
            'observation_boundary': 'Isolated candidate process output and independent frozen public assertions; not hidden evaluation.'}
        _write(output / 'observation.json', report)
        return report


def enforce_public_profile(review, observation, *, required=False):
    """A model's own postconditions cannot delete the registered public checks."""
    profile = observation.get('required_public_profile')
    if isinstance(profile, Mapping) and profile.get('schema_version') == 'ce.d2_isolated_requirement_profile.v1':
        from contractedge.execution.panel_observer import enforce_isolated_panel_profile
        return enforce_isolated_panel_profile(review, observation)
    if profile is None and not required:
        return review
    valid = (isinstance(profile, Mapping)
        and observation.get('status') == 'EXECUTION_PASS'
        and profile.get('schema_version') == 'ce.required_public_profile.v1'
        and profile.get('snapshot_id') == observation.get('snapshot_id')
        and profile.get('input_digest') == observation.get('input_digest')
        and profile.get('observations_digest') == canonical_digest(observation.get('observations', []))
        and profile.get('observer_sha256') == hashlib.sha256(_OBSERVER.encode()).hexdigest()
        and profile.get('status') == 'COMPLETE'
        and profile.get('check_ids') == sorted(_STATEMENTS))
    checks = profile.get('checks') if isinstance(profile, Mapping) else None
    valid = (valid and isinstance(checks, list) and len(checks) == len(_STATEMENTS)
        and all(isinstance(check, Mapping) and isinstance(check.get('id'), str) for check in checks)
        and {check['id'] for check in checks} == set(_STATEMENTS))
    # A profile is a view over independent check records, not authority to
    # replace those records' actual verdicts or drop a failed observation.
    if valid:
        records = observation.get('observations')
        valid = isinstance(records, list) and len(records) == len(_STATEMENTS)
        by_id = {}
        if valid:
            for record in records:
                if not isinstance(record, Mapping) or not isinstance(record.get('id'), str) or record['id'] in by_id:
                    valid = False
                    break
                by_id[record['id']] = record
        if valid:
            for check in checks:
                record = by_id.get('OBS-' + check['id'])
                if record is None:
                    valid = False
                    break
                excerpt = record.get('excerpt')
                try:
                    body = json.loads(excerpt, object_pairs_hook=_unique)
                except (TypeError, ValueError):
                    valid = False
                    break
                expected = {'probe_id': _PROBE_ID, 'id': check['id'], 'status': check.get('status'),
                    'statement': _STATEMENTS[check['id']], 'observed': {
                        'actual': check.get('actual'), 'expected': check.get('expected'),
                        'scope': 'Bounded public candidate process output; no claim about hidden or internal function state.'}}
                locator = record.get('locator') or {}
                valid = (record.get('snapshot_id') == observation.get('snapshot_id')
                    and record.get('evidence_kind') == 'PUBLIC_BEHAVIOR'
                    and record.get('structured_complete') is True
                    and isinstance(excerpt, str) and hashlib.sha256(excerpt.encode()).hexdigest() == record.get('content_digest')
                    and isinstance(locator, Mapping) and locator.get('truncated') is False and locator.get('whole_file_read') is True
                    and body == expected and record.get('behavior_check') == {
                        'id': _PROBE_ID + ':' + check['id'], 'status': check.get('status'),
                        'probe_sha256': hashlib.sha256(_OBSERVER.encode()).hexdigest()})
                if not valid:
                    break
    decision = 'UNKNOWN'
    if valid:
        statuses = [c.get('status') for c in checks]
        decision = 'FAIL' if 'FAIL' in statuses else 'PASS' if all(s == 'PASS' for s in statuses) else 'UNKNOWN'
    final = review.get('decision', 'UNKNOWN')
    if decision == 'FAIL':
        final = 'FAIL'
    elif decision != 'PASS' and final == 'PASS':
        final = 'UNKNOWN'
    return {**review, 'decision': final, 'required_public_profile': {
        'decision': decision, 'profile_digest': canonical_digest(profile), 'binding_valid': bool(valid)},
        'rationale': str(review.get('rationale', '')) + ' Registered requirement-level public checks remain mandatory.'}


def isolated_observation_complete(observation):
    """A valid profile cannot substitute for completed runs and confirmed cleanup."""
    if not isinstance(observation, Mapping):
        return False
    if isinstance(observation.get('required_public_profile'), Mapping) and observation['required_public_profile'].get('schema_version') == 'ce.d2_isolated_requirement_profile.v1':
        from contractedge.execution.panel_observer import isolated_panel_observation_complete
        return isolated_panel_observation_complete(observation)
    validation = enforce_public_profile({'decision': 'UNKNOWN'}, observation, required=True)
    if validation.get('required_public_profile', {}).get('binding_valid') is not True:
        return False
    runtime = observation.get('runtime')
    if not isinstance(runtime, Mapping) or not isinstance(runtime.get('close'), Mapping):
        return False
    if runtime['close'].get('status') != 'CLOSED':
        return False
    snapshot = observation.get('snapshot_id')
    inputs_digest = runtime.get('candidate_inputs_digest')
    checks_digest = runtime.get('public_check_spec_digest')
    if (not isinstance(snapshot, str) or not snapshot
            or not isinstance(inputs_digest, str) or len(inputs_digest) != 64
            or not isinstance(checks_digest, str) or len(checks_digest) != 64):
        return False
    invocations = {
        'candidate': FrozenInvocation.freeze(('/usr/bin/python3', '-I', '-B', '-c', _CANDIDATE)).digest,
        'observer': FrozenInvocation.freeze(('/usr/bin/python3', '-I', '-B', '-c', _OBSERVER)).digest,
    }
    for role in ('candidate', 'observer'):
        run = runtime.get(role)
        if (not isinstance(run, Mapping) or run.get('status') != 'EXECUTION_PASS' or run.get('role') != role
                or run.get('snapshot_id') != snapshot or run.get('public_inputs_digest') != inputs_digest
                or run.get('invocation_digest') != invocations[role]
                or not isinstance(run.get('attestation'), Mapping)
                or run['attestation'].get('status') != 'VERIFIED'
                or not isinstance(run.get('cleanup'), Mapping)
                or run['cleanup'].get('status') != 'CONFIRMED'):
            return False
        if role == 'observer' and run.get('checks_digest') != checks_digest:
            return False
    return True
