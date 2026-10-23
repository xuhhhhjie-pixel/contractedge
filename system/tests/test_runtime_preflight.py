"""Admission uses actual resource observations; mocked transport never proves lab readiness."""
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from contractedge.cli import main
from contractedge.execution.runtime_preflight import check_runtime, scoped_runtime_environment
from contractedge.orchestration.repair import repair_case
from tests.test_benchmark_current import make_public_fixture

IMAGE = 'golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d'


class DockerFixture:
    def __init__(self, *, cpu=True, memory=True, observed='cpu=200000 100000\nmemory=2147483648\n',
                 permission=False, mismatch_once=False, timeout=False, foreign=False, version='1.43'):
        self.cpu, self.memory, self.observed = cpu, memory, observed
        self.permission, self.mismatch_once, self.timeout, self.foreign, self.version = permission, mismatch_once, timeout, foreign, version
        self.calls = []
        self.token = None

    def __call__(self, arguments, *, env, **kwargs):
        self.calls.append((arguments, {key: env.get(key) for key in ['DOCKER_API_VERSION', 'DOCKER_CONTEXT']}))
        operation = arguments[1]
        def result(body='', code=0, error=''):
            return SimpleNamespace(stdout=json.dumps(body) if isinstance(body, dict) else body, returncode=code, stderr=error)
        if operation == 'context': return result('rootless\n')
        if operation == 'version':
            if self.permission: return result(code=1, error='permission denied at unix:///var/run/docker.sock')
            if self.mismatch_once and sum(call[0][1] == 'version' for call in self.calls) == 1:
                return result(code=1, error='client version 1.52 is too new. Maximum supported API version is 1.43')
            return result({'Server': {'ApiVersion': self.version}, 'Client': {'ApiVersion': '1.52'}})
        if operation == 'info':
            return result({'CpuCfsQuota': self.cpu, 'CpuCfsPeriod': self.cpu, 'MemoryLimit': self.memory, 'CgroupVersion': '2'})
        if operation == 'run':
            self.token = arguments[arguments.index('--label') + 1].split('=', 1)[1]
            if self.timeout: raise subprocess.TimeoutExpired(arguments, 1)
            return result(self.observed)
        if operation == 'inspect':
            return result({'contractedge.preflight.token': 'foreign' if self.foreign else self.token})
        if operation == 'rm': return result('removed')
        raise AssertionError('Unexpected Docker operation: ' + operation)


def test_actual_cgroup_limits_are_required_for_pass(monkeypatch):
    docker = DockerFixture()
    monkeypatch.setenv('DOCKER_API_VERSION', '1.52')
    with patch('subprocess.run', side_effect=docker):
        report = check_runtime(verify_image=IMAGE, context='rootless')
    assert report['status'] == 'PASS' and report['runtime_verified']
    assert report['environment_overrides'] == {'DOCKER_API_VERSION': '1.43', 'DOCKER_CONTEXT': 'rootless'}
    assert docker.calls[0][1]['DOCKER_API_VERSION'] is None
    command = next(call[0] for call in docker.calls if call[0][1] == 'run')
    assert '--cpus=2' in command and '--memory=2147483648' in command
    assert '--pull=never' in command and '--network=none' in command and '--read-only' in command
    assert os.environ['DOCKER_API_VERSION'] == '1.52'


def test_static_capabilities_alone_are_not_pass():
    with patch('subprocess.run', side_effect=DockerFixture()): report = check_runtime()
    assert report['status'] == 'CAPABILITY_CHECK_ONLY' and not report['runtime_verified']


@pytest.mark.parametrize('options,code', [
    ({'cpu': False}, 'CPU_LIMIT_UNAVAILABLE'), ({'cpu': None}, 'CPU_LIMIT_UNAVAILABLE'),
    ({'memory': False}, 'MEMORY_LIMIT_UNAVAILABLE'), ({'version': '1.24'}, 'API_TOO_OLD'),
])
def test_missing_controls_stop_before_container(options, code):
    docker = DockerFixture(**options)
    with patch('subprocess.run', side_effect=docker): report = check_runtime(verify_image=IMAGE)
    assert report['status'] == 'INFRA_ERROR'
    assert any(code in error for error in report['errors'])
    assert not any(call[0][1] == 'run' for call in docker.calls)


@pytest.mark.parametrize('observed', ['cpu=max 100000\nmemory=2147483648\n',
                                    'cpu=100000 100000\nmemory=2147483648\n',
                                    'cpu=200000 100000\nmemory=1073741824\n'])
def test_declared_but_ineffective_limits_fail(observed):
    with patch('subprocess.run', side_effect=DockerFixture(observed=observed)):
        report = check_runtime(verify_image=IMAGE)
    assert report['status'] == 'INFRA_ERROR' and not report['runtime_verified']


def test_api_mismatch_negotiates_only_task_environment():
    docker = DockerFixture(mismatch_once=True)
    with patch('subprocess.run', side_effect=docker): report = check_runtime(verify_image=IMAGE)
    assert report['status'] == 'PASS'
    assert sum(call[0][1] == 'version' for call in docker.calls) == 2
    assert report['environment_overrides']['DOCKER_API_VERSION'] == '1.43'


def test_permission_error_does_not_switch_or_modify_context():
    docker = DockerFixture(permission=True)
    with patch('subprocess.run', side_effect=docker): report = check_runtime(context='default', verify_image=IMAGE)
    assert report['status'] == 'INFRA_ERROR' and 'permission denied' in report['errors'][0]
    assert len(docker.calls) == 1


@pytest.mark.parametrize('foreign', [False, True])
def test_timeout_cleanup_only_removes_its_exact_owned_probe(foreign):
    docker = DockerFixture(timeout=True, foreign=foreign)
    with patch('subprocess.run', side_effect=docker): report = check_runtime(verify_image=IMAGE)
    removals = [call[0] for call in docker.calls if call[0][1] == 'rm']
    assert report['status'] == 'INFRA_ERROR' and not report['runtime_verified']
    assert len(removals) == (0 if foreign else 1)
    if removals: assert removals[0][-1] == 'ce-preflight-' + docker.token


@pytest.mark.parametrize('options', [{'cpus': 0}, {'cpus': 'NaN'}, {'memory_mib': True},
    {'verify_image': 'golang:latest'}, {'verify_image': {}}, {'timeout_seconds': True}])
def test_invalid_settings_do_not_start_docker(options):
    with patch('subprocess.run') as run, pytest.raises(ValueError): check_runtime(**options)
    run.assert_not_called()


def test_environment_restores_after_failure(monkeypatch):
    monkeypatch.setenv('DOCKER_API_VERSION', '1.52')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    with pytest.raises(RuntimeError):
        with scoped_runtime_environment({'DOCKER_API_VERSION': '1.43', 'DOCKER_CONTEXT': 'rootless'}):
            assert os.environ['DOCKER_CONTEXT'] == 'rootless'
            raise RuntimeError('synthetic task failure')
    assert os.environ['DOCKER_API_VERSION'] == '1.52' and 'DOCKER_CONTEXT' not in os.environ


@pytest.mark.parametrize('runtime_verified,status', [(False, 'PASS'), (False, 'CAPABILITY_CHECK_ONLY'), (False, 'INFRA_ERROR')])
def test_repair_admission_blocks_before_prepare_or_model(tmp_path, runtime_verified, status):
    public, _ = make_public_fixture(tmp_path/'benchmark')
    provider = SimpleNamespace(complete_json=Mock(side_effect=AssertionError('no model call')))
    runner = SimpleNamespace(prepare=Mock(side_effect=AssertionError('no workspace preparation')))
    result = {'status': status, 'runtime_verified': runtime_verified, 'errors': ['CPU_LIMIT_UNAVAILABLE'], 'environment_overrides': {}}
    with patch('contractedge.orchestration.repair.check_runtime', return_value=result):
        report = repair_case(public, tmp_path, 'CASE-1', tmp_path/'run', provider, len,
                             {'model': 'fixture', 'max_context_tokens': 24000, 'runtime_preflight': {'verify_image': IMAGE}},
                             public_runner=runner)
    assert report['status'] == 'INFRASTRUCTURE_ERROR'
    assert report['budget']['model_calls'] == 0 and report['candidate'] is None
    assert any('CPU_LIMIT_UNAVAILABLE' in error for error in report['errors'])
    assert json.loads((tmp_path/'run/runtime-preflight.json').read_text()) == result
    provider.complete_json.assert_not_called()
    runner.prepare.assert_not_called()


def test_admitted_task_receives_scoped_api_and_restores_parent(monkeypatch):
    monkeypatch.setenv('DOCKER_API_VERSION', '1.52')
    result = {'status': 'PASS', 'runtime_verified': True, 'errors': [],
              'environment_overrides': {'DOCKER_API_VERSION': '1.43'}}
    def implementation(*args, **kwargs):
        assert os.environ['DOCKER_API_VERSION'] == '1.43'
        assert kwargs['_runtime_preflight'] == result
        return {'status': 'fixture'}
    with patch('contractedge.orchestration.repair.check_runtime', return_value=result), patch(
            'contractedge.orchestration.repair._repair_case_impl', side_effect=implementation):
        assert repair_case('unused', 'unused', 'fixture', 'unused', None, len,
                           {'runtime_preflight': {'verify_image': IMAGE}})['status'] == 'fixture'
    assert os.environ['DOCKER_API_VERSION'] == '1.52'


def test_cli_reports_static_only_as_nonpass(capsys):
    with patch('contractedge.execution.runtime_preflight.check_runtime', return_value={'status': 'CAPABILITY_CHECK_ONLY'}):
        assert main(['runtime-check']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'CAPABILITY_CHECK_ONLY'



def test_explicit_host_is_not_replaced_by_synthetic_default_context(monkeypatch):
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setenv('DOCKER_HOST', 'unix:///tmp/owned-runtime-fixture.sock')
    docker = DockerFixture()
    with patch('subprocess.run', side_effect=docker): report = check_runtime(verify_image=IMAGE)
    assert report['status'] == 'PASS'
    assert report['endpoint_source'] == 'INHERITED_DOCKER_HOST'
    assert 'DOCKER_CONTEXT' not in report['environment_overrides']
    assert not any(call[0][1] == 'context' for call in docker.calls)
    assert os.environ['DOCKER_HOST'] == 'unix:///tmp/owned-runtime-fixture.sock'
