"""No Docker: trusted checker subprocesses and explicit runtime-port doubles.

Candidates are never executed in the host process by these tests. Doubles test
control/receipt rejection only and are not actual OS isolation evidence.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from contractedge.contracts.validation import canonical_digest
from contractedge.development.method_panel import PanelProvider, panel_config, run_cell
from contractedge.development.method_panel_fixtures import (CASES, MODES, OBSERVER, CANDIDATE,
    case_definition, freeze_panel_case, runtime_inputs)
from contractedge.development.isolated_panel import IsolatedPanelRunner, validate_runtime_config
from contractedge.development.public_demo import count_tokens
from contractedge.execution import panel_observer as module
from contractedge.execution.public_observer import isolated_observation_complete, enforce_public_profile
from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot

RUNTIME = {'image': 'sha256:' + 'a' * 64, 'docker_host': 'unix:///not-used-by-unit-tests.sock'}


def output_fixture(case_id, *, broken=False):
    case = case_definition(case_id)
    rows = [{'scenario_id': scenario['id'], 'step_id': step['id'],
        'actual': {**step['expected'], 'mode': 'stable'}}
        for scenario in case['scenarios'] for step in scenario['steps']]
    if broken:
        by_id = {(r['scenario_id'], r['step_id']): r for r in rows}
        for check in case['checks']:
            if check['kind'] == 'REPAIR':
                for assertion in check['assertions']:
                    row = by_id[(assertion['scenario_id'], assertion['step_id'])]
                    row['actual'].update({key: 'unit-broken-value' for key in assertion['expected']})
    return {'schema_version': 'ce.d2_panel_outputs.v1', 'results': rows}


class RuntimeDouble:
    """Only the trusted checker runs; the candidate response is explicitly canned."""
    def __init__(self, spec, deadline, actual, *, close_status='CLOSED', mutate=None):
        self.spec, self.deadline, self.actual = spec, deadline, actual
        self.root = Path(spec.session_root); self.root.mkdir(parents=True)
        self.runs, self.close_status, self.mutate = [], close_status, mutate
        self.calls = []

    def receipt(self, role, invocation, snapshot, inputs_digest, raw, *, checks_digest=None):
        directory = self.root / role; directory.mkdir()
        stdout, stderr = directory / 'stdout.bin', directory / 'stderr.bin'
        stdout.write_bytes(raw); stderr.write_bytes(b'')
        limits = self.spec
        mount_names = ['/source', '/inputs'] if role == 'candidate' else ['/observations', '/checks']
        cid = ('b' if role == 'candidate' else 'c') * 64
        row = {'role': role, 'status': 'EXECUTION_PASS', 'process_status': 'COMPLETE', 'exit_code': 0,
            'snapshot_id': snapshot, 'public_inputs_digest': inputs_digest,
            'invocation_digest': invocation.digest, 'container_id': cid, 'image_id': self.spec.image,
            'truncated': False, 'cleanup': {'status': 'CONFIRMED'},
            'attestation': {'status': 'VERIFIED', 'inspect': {'container_id': cid, 'image_id': self.spec.image,
                'user': '10001:10001', 'network_mode': 'none',
                'mounts': [{'Destination': x, 'RW': False, 'Type': 'bind'} for x in mount_names],
                'host_config': {'ReadonlyRootfs': True, 'Privileged': False, 'NetworkMode': 'none',
                    'Memory': limits.memory_bytes, 'MemorySwap': limits.memory_bytes,
                    'PidsLimit': limits.pids_limit, 'NanoCpus': round(limits.cpus * 1e9),
                    'CapDrop': ['ALL'], 'CapAdd': [], 'SecurityOpt': ['no-new-privileges:true']}},
                'kernel': {'uid': 10001, 'gid': 10001, 'cpu_max': '200000 100000',
                    'memory_max': str(limits.memory_bytes), 'pids_max': str(limits.pids_limit),
                    'cap_eff': '00000000', 'no_new_privs': '1', 'seccomp': '2', 'tmpfs_bytes': limits.tmpfs_bytes}},
            'stdout_path': str(stdout), 'stderr_path': str(stderr),
            'stdout_bytes': len(raw), 'stderr_bytes': 0,
            'stdout_sha256': hashlib.sha256(raw).hexdigest(), 'stderr_sha256': hashlib.sha256(b'').hexdigest(),
            'UNIT_TEST_RUNTIME_DOUBLE_NOT_OS_ISOLATION': True}
        if role == 'candidate':
            row.update(source_unchanged=True, source_after_snapshot_id=snapshot)
        else:
            row['checks_digest'] = checks_digest
        self.runs.append(row)
        return row

    def run_candidate(self, invocation, *, snapshot, public_inputs):
        self.calls.append('candidate')
        assert invocation == module.candidate_invocation()
        body = json.loads(Path(public_inputs).read_text())
        assert set(body) == {'scenarios'}
        assert all(set(step) == {'id', 'config'} for s in body['scenarios'] for step in s['steps'])
        digest = canonical_digest({'public-inputs.json': module._sha(public_inputs)})
        raw = self.actual if isinstance(self.actual, bytes) else json.dumps(self.actual).encode()
        return self.receipt('candidate', invocation, snapshot.snapshot_id, digest, raw)

    def run_observer(self, invocation, *, bounded_output, public_check_spec):
        self.calls.append('observer')
        assert invocation == module.observer_invocation()
        # Execute the exact immutable independent checker; only path arguments differ.
        result = subprocess.run([sys.executable, '-I', '-B', '-c', OBSERVER,
            str(public_check_spec), bounded_output['stdout_path']], capture_output=True, timeout=10)
        row = self.receipt('observer', invocation, bounded_output['snapshot_id'], bounded_output['public_inputs_digest'],
            result.stdout, checks_digest=canonical_digest({'spec.json': module._sha(public_check_spec)}))
        if result.returncode:
            row.update(status='EXECUTION_FAILED', exit_code=result.returncode)
        return row

    def close(self):
        self.calls.append('close')
        for row in self.runs:
            row['close_cleanup'] = {'status': 'CONFIRMED'}
        if self.mutate:
            self.mutate(self.runs)
        return {'schema_version': 'ce.runtime_session.v1', 'status': self.close_status,
            'spec': self.spec.to_wire(), 'spec_digest': canonical_digest(self.spec.to_wire()),
            'runs': self.runs, 'UNIT_TEST_RUNTIME_DOUBLE_NOT_OS_ISOLATION': True}


def observe(tmp_path, case_id='config-binding/base', *, broken=False, actual=None, close_status='CLOSED', mutate=None):
    source = tmp_path / 'source'; source.mkdir()
    (source / 'app.py').write_text('raise AssertionError("candidate must never execute on host")\n')
    inputs = tmp_path / 'runtime-inputs.json'; inputs.write_text(json.dumps(runtime_inputs(case_id)))
    doubles = []
    def factory(spec, deadline):
        item = RuntimeDouble(spec, deadline, actual if actual is not None else output_fixture(case_id, broken=broken),
            close_status=close_status, mutate=mutate)
        doubles.append(item); return item
    with patch.object(module.RuntimeSession, 'open', side_effect=factory):
        result = module.IsolatedPanelObserver(**RUNTIME).observe(source, inputs, tmp_path / 'observe', fixture_id=case_id)
    return result, doubles


@pytest.mark.parametrize('case_id', tuple(CASES))
def test_all_six_profiles_use_exact_checker_and_two_distinct_runtime_roles(tmp_path, case_id):
    report, doubles = observe(tmp_path, case_id)
    assert report['status'] == 'EXECUTION_PASS', report['errors']
    assert isolated_observation_complete(report)
    assert doubles[0].calls == ['candidate', 'observer', 'close']
    assert report['required_public_profile']['schema_version'] == module.PROFILE
    assert set(report['required_public_profile']) != {'selected', 'disabled', 'stable'}
    assert enforce_public_profile({'decision': 'PASS'}, report)['decision'] == 'PASS'


@pytest.mark.parametrize('case_id', tuple(CASES))
def test_broken_target_fail_and_all_fixed_preservation_states_are_checked(tmp_path, case_id):
    report, _ = observe(tmp_path, case_id, broken=True)
    assert isolated_observation_complete(report)
    assert {r['id']:r['status'] for r in report['requirement_profile']['checks']} == case_definition(case_id)['broken_profile']
    assert enforce_public_profile({'decision': 'PASS'}, report)['decision'] == 'FAIL'


@pytest.mark.parametrize('name', ['drop-check', 'stale', 'wrong-input', 'old-profile', 'wrong-invocation',
    'truncated', 'unverified', 'missing-kernel', 'bad-mount', 'candidate-cleanup', 'observer-cleanup',
    'close-failed', 'changed-close-run', 'changed-resources', 'wrong-checker-sha', 'oversized-output'])
def test_profile_runtime_and_identity_corruption_cannot_certify_isolation(tmp_path, name):
    report, _ = observe(tmp_path)
    assert isolated_observation_complete(report)
    if name == 'drop-check': report['requirement_profile']['checks'].pop()
    elif name == 'stale': report['snapshot_id'] = 'STALE'
    elif name == 'wrong-input': report['required_public_profile']['candidate_inputs_digest'] = '0' * 64
    elif name == 'old-profile': report['required_public_profile']['schema_version'] = 'ce.required_public_profile.v1'
    elif name == 'wrong-invocation': report['runtime']['candidate']['invocation_digest'] = '0' * 64
    elif name == 'truncated': report['runtime']['candidate']['truncated'] = True
    elif name == 'unverified': report['runtime']['observer']['attestation']['status'] = 'UNKNOWN'
    elif name == 'missing-kernel': report['runtime']['candidate']['attestation'].pop('kernel')
    elif name == 'bad-mount': report['runtime']['candidate']['attestation']['inspect']['mounts'].append({'Destination':'/checks','RW':False,'Type':'bind'})
    elif name == 'candidate-cleanup': report['runtime']['candidate']['cleanup']['status'] = 'PENDING'
    elif name == 'observer-cleanup': report['runtime']['observer']['close_cleanup']['status'] = 'PENDING'
    elif name == 'close-failed': report['runtime']['close']['status'] = 'INFRASTRUCTURE_ERROR'
    elif name == 'changed-close-run': report['runtime']['close']['runs'] = []
    elif name == 'changed-resources': report['runtime']['spec']['memory_bytes'] = 1
    elif name == 'wrong-checker-sha': report['runtime']['observer']['stdout_sha256'] = '0' * 64
    elif name == 'oversized-output': report['runtime']['candidate']['stdout_bytes'] = 99999999
    assert not isolated_observation_complete(report)
    assert enforce_public_profile({'decision': 'PASS'}, report)['decision'] != 'PASS'


@pytest.mark.parametrize('raw', [b'{"status":"PASS","checks":[]}', b'{"schema_version":"ce.d2_panel_outputs.v1","results":[]}',
    b'{"schema_version":"ce.d2_panel_outputs.v1","results":[],"results":[]}'])
def test_candidate_verdicts_missing_outputs_and_duplicate_keys_fail_closed(tmp_path, raw):
    report, doubles = observe(tmp_path, actual=raw)
    assert report['status'] == 'INVALID_OBSERVATION'
    assert not isolated_observation_complete(report)
    assert doubles[0].calls[-1] == 'close'


def test_cleanup_failure_rejects_complete_checker_output(tmp_path):
    report, _ = observe(tmp_path, close_status='INFRASTRUCTURE_ERROR')
    assert report['status'] == 'INVALID_OBSERVATION'
    assert not isolated_observation_complete(report)


@pytest.mark.parametrize('target', ['source', 'public-input', 'candidate-input', 'check-spec'])
def test_mutation_during_observation_cannot_reuse_a_completed_runtime_receipt(tmp_path, target):
    def mutate(_):
        path = {'source': tmp_path / 'source/app.py', 'public-input': tmp_path / 'runtime-inputs.json',
            'candidate-input': tmp_path / 'observe/candidate-inputs.json', 'check-spec': tmp_path / 'observe/checks.json'}[target]
        path.write_text(path.read_text() + '\n')
    report, _ = observe(tmp_path, mutate=mutate)
    assert report['status'] == 'INVALID_OBSERVATION'
    assert not isolated_observation_complete(report)


def test_runtime_open_failure_does_not_call_a_host_candidate_or_provider(tmp_path):
    public = freeze_panel_case('config-binding/base', tmp_path, runtime='isolated')
    runner = IsolatedPanelRunner(public, 'config-binding/base', RUNTIME)
    provider = PanelProvider('config-binding/base', 'direct', runner)
    config = {**panel_config('direct'), 'public_runtime': 'isolated'}
    with patch.object(module.RuntimeSession, 'open', side_effect=RuntimeError('unit fixture: runtime unavailable')):
        report = repair_case(public, tmp_path / 'unused', runner.case, tmp_path / 'repair', provider,
            count_tokens, config, public_runner=runner)
    assert report['status'] == 'INVALID_OBSERVATION'
    assert provider.requests == []
    assert not json.loads((tmp_path / 'repair/initial-public-observation.json').read_text())['isolated']


def test_host_profile_cannot_be_promoted_by_attaching_isolated_label(tmp_path):
    from contractedge.development.method_panel_fixtures import requirement_observation
    report, _ = observe(tmp_path)
    report.pop('runtime'); report['required_public_profile'] = report['requirement_profile']
    report['isolated'] = True
    assert not isolated_observation_complete(report)


@pytest.mark.parametrize('mode', MODES)
def test_production_four_mode_bridge_with_runtime_double_never_uses_host_runner(tmp_path, mode):
    counter = 0
    def factory(spec, deadline):
        nonlocal counter
        counter += 1
        return RuntimeDouble(spec, deadline, output_fixture('config-binding/base', broken=counter == 1))
    with patch.object(module.RuntimeSession, 'open', side_effect=factory), patch(
            'contractedge.development.method_panel_fixtures.PanelRunner.observe', side_effect=AssertionError('host fallback forbidden')):
        row = run_cell('config-binding/base', mode, tmp_path / mode, runtime_config=RUNTIME)
    assert counter == 2
    assert row['status'] == 'PUBLIC_REVIEW_COMPLETE'
    assert row['removed_stage_calls_zero'] and row['broken_target_fail_proven']
    assert row['broken_isolation_complete'] and row['candidate_isolation_complete']


def test_postedit_incomplete_runtime_restores_incumbent_in_production_entry(tmp_path):
    counter = 0
    def factory(spec, deadline):
        nonlocal counter
        counter += 1
        return RuntimeDouble(spec, deadline, output_fixture('config-binding/base', broken=counter == 1),
            close_status='CLOSED' if counter == 1 else 'INFRASTRUCTURE_ERROR')
    with patch.object(module.RuntimeSession, 'open', side_effect=factory):
        row = run_cell('config-binding/base', 'direct', tmp_path / 'failure', runtime_config=RUNTIME)
    assert row['broken_isolation_complete']
    assert not row['candidate_isolation_complete']
    assert row['final_snapshot_id'] == row['initial_snapshot_id']
    assert row['candidate_export']['patch_bytes'] == 0
    assert row['status'] != 'PUBLIC_REVIEW_COMPLETE'


def test_public_runtime_configuration_is_explicit_and_digest_pinned():
    assert validate_runtime_config(RUNTIME) == RUNTIME
    for config in ({'image':'latest','docker_host':RUNTIME['docker_host']}, {'image':RUNTIME['image'],'docker_host':'tcp://x'},
            {**RUNTIME,'api_key':'forbidden'}, {}):
        with pytest.raises(ValueError): validate_runtime_config(config)
