"""Task-scoped Docker admission; a static capability claim is not runtime PASS."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import json
import os
import re
import subprocess
import threading
import time
import uuid

_TASK_ENVIRONMENT_LOCK = threading.RLock()
_PROBE = '''set -eu
if [ -f /sys/fs/cgroup/cpu.max ]; then
  cpu=$(cat /sys/fs/cgroup/cpu.max)
  memory=$(cat /sys/fs/cgroup/memory.max)
else
  cpu_path=/sys/fs/cgroup/cpu
  if [ ! -f "$cpu_path/cpu.cfs_quota_us" ]; then cpu_path=/sys/fs/cgroup/cpu,cpuacct; fi
  cpu="$(cat "$cpu_path/cpu.cfs_quota_us") $(cat "$cpu_path/cpu.cfs_period_us")"
  memory=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)
fi
printf 'cpu=%s\\nmemory=%s\\n' "$cpu" "$memory"
'''


@contextmanager
def scoped_runtime_environment(overrides=None):
    """Serialize repair calls in this CLI process while restoring task overrides.

    Existing public runtime helpers inherit environment rather than accept an
    env argument. Other processes and Docker configuration are never changed.
    """
    with _TASK_ENVIRONMENT_LOCK:
        values = dict(overrides or {})
        if set(values) - {'DOCKER_API_VERSION', 'DOCKER_CONTEXT'}:
            raise ValueError('Only task Docker API/context overrides are allowed')
        previous = {key: os.environ.get(key) for key in values}
        try:
            os.environ.update(values)
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def _field(mapping, name):
    if not isinstance(mapping, dict):
        return None
    return next((value for key, value in mapping.items() if key.lower() == name.lower()), None)


def check_runtime(*, cpus=2, memory_mib=2048, context=None, verify_image=None, timeout_seconds=20):
    """Use an existing context and cached trusted image; never pull or reconfigure."""
    try:
        cpu = Decimal(str(cpus))
    except InvalidOperation as exc:
        raise ValueError('cpus must be a positive finite number') from exc
    if not cpu.is_finite() or cpu <= 0 or type(memory_mib) is not int or memory_mib <= 0:
        raise ValueError('CPU and memory limits must be positive')
    if type(timeout_seconds) not in {int, float} or not 0 < timeout_seconds <= 120:
        raise ValueError('timeout_seconds must be within (0,120]')
    if context is not None and (not isinstance(context, str) or not context.strip()):
        raise ValueError('context must be a nonempty existing context name')
    if verify_image is not None and (not isinstance(verify_image, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./_:-]*@sha256:[a-fA-F0-9]{64}', verify_image)):
        raise ValueError('verify_image must be an immutable image reference with a SHA-256 digest')
    started = time.monotonic()
    deadline = started + timeout_seconds
    environment = dict(os.environ)
    # An inherited API override disables negotiation; inspect the actual server.
    environment.pop('DOCKER_API_VERSION', None)
    if context is not None:
        environment['DOCKER_CONTEXT'] = context
    report = {'schema_version': 'ce.runtime_preflight.v1', 'status': 'INFRA_ERROR',
              'context': context or environment.get('DOCKER_CONTEXT'),
              'required_cpus': str(cpu), 'required_memory_bytes': memory_mib * 1024 * 1024,
              'environment_overrides': {}, 'errors': [], 'runtime_verified': False,
              'image': verify_image, 'attempts': [], 'global_configuration_changed': False}

    def invoke(arguments, *, cleanup=False):
        remaining = 5 if cleanup else deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(['docker', *arguments], timeout_seconds)
        result = subprocess.run(['docker', *arguments], env=environment,
                                capture_output=True, text=True, timeout=remaining)
        report['attempts'].append({'operation': arguments[0], 'exit_code': result.returncode})
        return result

    container = None
    token = None
    try:
        if context is None and environment.get('DOCKER_CONTEXT'):
            report['context'] = environment['DOCKER_CONTEXT']
        elif context is None and environment.get('DOCKER_HOST'):
            # Pinning the synthetic 'default' context here would override the
            # caller's explicit host. Keep that task endpoint untouched.
            report['endpoint_source'] = 'INHERITED_DOCKER_HOST'
        elif context is None:
            current = invoke(['context', 'show'])
            name = current.stdout.rstrip('\r\n')
            if current.returncode or not name:
                report['errors'].append('DOCKER_CONTEXT_UNRESOLVED: ' + current.stderr.strip()[:1500])
                return report
            environment['DOCKER_CONTEXT'] = name
            report['context'] = name
        version = invoke(['version', '--format', '{{json .}}'])
        if version.returncode:
            maximum = re.search(r'maximum supported API version is\s+(\d+\.\d+)', version.stderr, re.I)
            if not maximum:
                report['errors'].append('DOCKER_UNAVAILABLE: ' + version.stderr.strip()[:1500])
                return report
            environment['DOCKER_API_VERSION'] = maximum.group(1)
            version = invoke(['version', '--format', '{{json .}}'])
        if version.returncode:
            report['errors'].append('API_NEGOTIATION_FAILED: ' + version.stderr.strip()[:1500])
            return report
        versions = json.loads(version.stdout)
        if not isinstance(versions, dict):
            report['errors'].append('DOCKER_VERSION_INVALID')
            return report
        server, client = versions.get('Server') or {}, versions.get('Client') or {}
        server_api, client_api = _field(server, 'ApiVersion'), _field(client, 'ApiVersion')
        if not all(isinstance(value, str) and re.fullmatch(r'\d+\.\d+', value) for value in [server_api, client_api]):
            report['errors'].append('API_VERSION_UNREPORTED')
            return report
        selected = min([server_api, client_api], key=lambda value: tuple(map(int, value.split('.'))))
        environment['DOCKER_API_VERSION'] = selected
        report.update(server_api=server_api, client_api=client_api)
        report['environment_overrides']['DOCKER_API_VERSION'] = selected
        if report['context'] is not None:
            report['environment_overrides']['DOCKER_CONTEXT'] = report['context']
        info = invoke(['info', '--format', '{{json .}}'])
        if info.returncode:
            report['errors'].append('DOCKER_INFO_FAILED: ' + info.stderr.strip()[:1500])
            return report
        values = json.loads(info.stdout)
        capabilities = {key: _field(values, key) for key in ['CpuCfsQuota', 'CpuCfsPeriod', 'MemoryLimit', 'CgroupVersion']}
        report['capabilities'] = capabilities
        if capabilities['CpuCfsQuota'] is not True or capabilities['CpuCfsPeriod'] is not True:
            report['errors'].append('CPU_LIMIT_UNAVAILABLE_OR_UNREPORTED')
        if capabilities['MemoryLimit'] is not True:
            report['errors'].append('MEMORY_LIMIT_UNAVAILABLE_OR_UNREPORTED')
        if tuple(map(int, selected.split('.'))) < (1, 25):
            report['errors'].append('API_TOO_OLD_FOR_NANOCPUS')
        if report['errors']:
            return report
        if verify_image is None:
            report['status'] = 'CAPABILITY_CHECK_ONLY'
            report['errors'].append('RUNTIME_LIMITS_NOT_VERIFIED: supply a trusted cached digest-pinned probe image')
            return report
        token = uuid.uuid4().hex
        container = 'ce-preflight-' + token
        command = ['run', '--rm', '--pull=never', '--network=none', '--read-only',
                   '--name', container, '--label', 'contractedge.preflight.token=' + token,
                   '--user', f'{os.getuid()}:{os.getgid()}', '--cpus=' + str(cpu),
                   '--memory=' + str(report['required_memory_bytes']), '--pids-limit=64',
                   '--entrypoint=/bin/sh', verify_image, '-c', _PROBE]
        result = invoke(command)
        if result.returncode:
            report['errors'].append('LIMIT_PROBE_FAILED: ' + result.stderr.strip()[:1500])
            return report
        # Successful --rm exits removed this owned probe; never enumerate peers.
        container = None
        observed = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        quota, period = observed.get('cpu', '').split()
        memory = int(observed.get('memory', ''))
        if quota == 'max' or Decimal(quota) <= 0 or Decimal(period) <= 0 or Decimal(quota) / Decimal(period) != cpu:
            report['errors'].append('CPU_LIMIT_NOT_EFFECTIVE')
        if memory != report['required_memory_bytes']:
            report['errors'].append('MEMORY_LIMIT_NOT_EFFECTIVE')
        report['observed_limits'] = observed
        if not report['errors']:
            report.update(status='PASS', runtime_verified=True)
        return report
    except (OSError, ValueError, TypeError, InvalidOperation, subprocess.TimeoutExpired) as exc:
        report['errors'].append(type(exc).__name__ + ': ' + str(exc)[:1500])
        return report
    finally:
        if container is not None:
            try:
                inspected = invoke(['inspect', '--format', '{{json .Config.Labels}}', container], cleanup=True)
                if inspected.returncode == 0:
                    labels = json.loads(inspected.stdout)
                    if isinstance(labels, dict) and labels.get('contractedge.preflight.token') == token:
                        removed = invoke(['rm', '-f', container], cleanup=True)
                        if removed.returncode:
                            report['errors'].append('OWNED_PROBE_CLEANUP_FAILED')
                    else:
                        report['errors'].append('PROBE_OWNERSHIP_MISMATCH_NO_DELETE')
                elif not re.search(r'No such (object|container)', inspected.stderr, re.I):
                    report['errors'].append('OWNED_PROBE_CLEANUP_UNCONFIRMED')
            except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
                report['errors'].append('OWNED_PROBE_CLEANUP_UNCONFIRMED')
        if report['errors'] and report['status'] == 'PASS':
            report.update(status='INFRA_ERROR', runtime_verified=False)
        report['elapsed_seconds'] = time.monotonic() - started
