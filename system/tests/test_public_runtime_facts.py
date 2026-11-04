"""Offline counterexamples for independent benchmark process/cleanup facts."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from contractedge.execution import public


def observe_case(tmp_path, monkeypatch, *, failed=True, timed_out=False, cleanup_failed=True, with_probe=False,
                 diagnostic='main.go:2:13: cannot use "bad" as int value\n'):
    tools = tmp_path / 'tools'; tools.mkdir()
    for name in ('prepare_case.py', 'collect_patch.py', 'launch_public_case.py'):
        (tools / name).write_text('# offline public tool fixture\n')
    public_root = tmp_path / 'public'; public_root.mkdir()
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    source = workspace / 'source'; source.mkdir()
    (source / 'main.go').write_text('package main\nvar n int = "bad"\n')
    inputs = workspace / 'inputs'; inputs.mkdir()
    (inputs / 'public.txt').write_text('public input\n')
    (workspace / 'workspace.json').write_text(json.dumps({
        'case_id': 'CASE', 'source_root': str(source), 'inputs_root': str(inputs)}))

    def launch(argv, log, timeout):
        raw = Path(argv[argv.index('--output') + 1]); raw.mkdir()
        log.mkdir(); (log / 'tool.stdout').write_text('public launcher\n')
        (log / 'tool.stderr').write_text('')
        (raw / 'public_execution.json').write_text(json.dumps({
            'status': 'EXECUTION_FAILED' if failed else 'EXECUTION_PASS',
            'private_materials_loaded': False,
            'scenarios': [{'scenario': 'package-build', 'exit_code': 1 if failed else 0,
                           'timed_out': False, 'output_limited': False}]}))
        (raw / 'package-build.stderr').write_text(diagnostic if failed else '')
        return {'returncode': 1 if failed else 0, 'timed_out': timed_out}

    monkeypatch.setattr(public, 'run_tool', launch)
    monkeypatch.setattr(public, 'cleanup_owned_containers', lambda roots: {
        'status': 'CLEANUP_UNCONFIRMED' if cleanup_failed else 'OWNED_CONTAINERS_CLOSED',
        'removed': [], 'errors': ['offline inspect failure'] if cleanup_failed else []})
    runner = public.BenchmarkPublicRunner(public_root, tools, lock_root=tmp_path / 'locks',
        method_probe_profiles=('offline-selected-probe',) if with_probe else ())
    if with_probe:
        runner._method_probe_specs[str(workspace.resolve())] = [{'id': 'offline-selected-probe'}]
    return runner.observe(workspace, tmp_path / 'observed')


def test_cleanup_failure_does_not_hide_compiler_failure_or_its_diagnostic(tmp_path, monkeypatch):
    report = observe_case(tmp_path, monkeypatch)
    assert report['status'] == report['process_status'] == 'EXECUTION_FAILED'
    assert report['primary_failure']['code'] == 'EXECUTION_FAILED'
    assert report['cleanup_failure']['secondary'] is True
    assert report['failure_records'] == [report['primary_failure'], report['cleanup_failure']]
    assert public.public_failure_diagnostics(report)[0]['kind'] == 'CANDIDATE_BUILD_FAILURE'
    assert report['runtime_cleanup']['status'] == 'CLEANUP_UNCONFIRMED'
    assert report['launcher'] == {'returncode': 1, 'timed_out': False}
    assert json.loads((tmp_path / 'observed/observation.json').read_text()) == report


def test_cleanup_only_failure_preserves_successful_process_and_public_status_compatibility(tmp_path, monkeypatch):
    report = observe_case(tmp_path, monkeypatch, failed=False)
    assert report['status'] == 'INFRA_ERROR'
    assert report['process_status'] == 'EXECUTION_PASS'
    assert report['primary_failure'] is None
    assert report['failure_records'] == [report['cleanup_failure']]
    assert report['secondary_failures'] == [report['cleanup_failure']]
    assert not public.has_candidate_execution_failure(report)


@pytest.mark.parametrize('cleanup_failed', [True, False])
def test_method_probe_execution_requires_confirmed_launcher_cleanup(tmp_path, monkeypatch, cleanup_failed):
    from contractedge.execution import probes
    calls = []

    def run_probe(*args, **kwargs):
        calls.append((args, kwargs))
        return {'status': 'COMPLETE', 'probes': [], 'observations': []}

    monkeypatch.setattr(probes, 'run_method_probes', run_probe)
    monkeypatch.setattr(probes, 'behavior_records', lambda *args: {})
    monkeypatch.setattr(probes, 'candidate_runtime_failures', lambda *args: [])
    report = observe_case(tmp_path, monkeypatch, failed=False,
                          cleanup_failed=cleanup_failed, with_probe=True)
    assert len(calls) == (0 if cleanup_failed else 1)
    assert report['method_probe_status'] == ('NOT_RUN' if cleanup_failed else 'COMPLETE')
    assert report['process_status'] == 'EXECUTION_PASS'
    assert report['status'] == ('INFRA_ERROR' if cleanup_failed else 'EXECUTION_PASS')


def test_completed_compiler_fact_survives_later_launcher_timeout(tmp_path, monkeypatch):
    report = observe_case(tmp_path, monkeypatch, timed_out=True)
    assert report['status'] == report['process_status'] == 'BUDGET_EXCEEDED'
    assert report['primary_failure']['code'] == 'BUDGET_EXCEEDED'
    assert public.has_candidate_build_failure(report)
    assert public.public_failure_diagnostics(report)[0]['kind'] == 'CANDIDATE_BUILD_FAILURE'


def test_unrecognized_tool_error_does_not_create_source_blame(tmp_path, monkeypatch):
    report = observe_case(tmp_path, monkeypatch, diagnostic='go: module lookup disabled by GOPROXY=off\n')
    assert report['status'] == 'EXECUTION_FAILED'
    assert report['candidate_build_failures'] == []
    assert not public.has_candidate_execution_failure(report)
    assert public.public_failure_diagnostics(report) == []


def test_success_has_no_failure_records(tmp_path, monkeypatch):
    report = observe_case(tmp_path, monkeypatch, failed=False, cleanup_failed=False)
    assert report['status'] == report['process_status'] == 'EXECUTION_PASS'
    assert report['primary_failure'] is report['cleanup_failure'] is None
    assert report['failure_records'] == report['secondary_failures'] == []


def test_runtime_diagnostic_is_a_bound_fact_not_a_terminal_status_alias():
    failure = {'snapshot_id': 'current', 'status': 'FAILED',
               'operation': 'PRODUCTION_PROCESS_START',
               'source_locations': [{'path': 'main.go', 'line': 2}]}
    report = {'status': 'INFRA_ERROR', 'snapshot_id': 'current',
              'candidate_runtime_failures': [None, failure, {**failure, 'snapshot_id': 'old'}]}
    assert public.has_candidate_runtime_failure(report)
    assert public.public_failure_diagnostics(report) == [{'kind': 'CANDIDATE_RUNTIME_FAILURE', **failure}]
    report['snapshot_id'] = 'different'
    assert public.public_failure_diagnostics(report) == []


class OfflineDocker:
    """A deterministic container inventory; no Docker executable is invoked."""
    def __init__(self, containers, *, inspect_failure=(), sticky=(), remove_failure=(), final_list_failure=False):
        self.containers = dict(containers)
        self.inspect_failure = set(inspect_failure)
        self.sticky, self.remove_failure = set(sticky), set(remove_failure)
        self.final_list_failure = final_list_failure
        self.calls, self.listings = [], 0

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        assert argv[0] == 'docker'
        code, output = 0, ''
        if argv[1] == 'ps':
            self.listings += 1
            if self.final_list_failure and self.listings > 1:
                code = 1
            else:
                all_containers = '-aq' in argv or '-a' in argv
                output = '\n'.join(cid for cid, row in self.containers.items()
                                   if all_containers or row.get('running', True))
        elif argv[1] == 'inspect':
            cid = argv[-1]
            if cid in self.inspect_failure or cid not in self.containers:
                code = 1
            else:
                output = json.dumps(self.containers[cid]['mounts'])
        elif argv[1:3] == ['rm', '-f']:
            cid = argv[-1]
            if cid in self.remove_failure:
                code = 1
            elif cid not in self.sticky:
                self.containers.pop(cid)
        else:
            raise AssertionError('Unexpected offline Docker action: ' + repr(argv))
        return SimpleNamespace(returncode=code, stdout=output, stderr='offline failure' if code else '')


def container(path, *, running=True):
    return {'running': running, 'mounts': [{'Type': 'bind', 'Source': str(path)}]}


def test_cleanup_removes_stopped_owned_container_but_leaves_foreign_one(tmp_path, monkeypatch):
    owned, foreign = 'a' * 64, 'b' * 64
    docker = OfflineDocker({owned: container(tmp_path / 'owned/source', running=False),
                            foreign: container(tmp_path / 'foreign')})
    monkeypatch.setattr(public.subprocess, 'run', docker)
    report = public.cleanup_owned_containers([tmp_path / 'owned'])
    assert report['status'] == 'OWNED_CONTAINERS_CLOSED'
    assert report['removed'] == [owned]
    assert set(docker.containers) == {foreign}
    assert docker.listings >= 2
    assert all(foreign not in argv for argv in docker.calls if argv[1] == 'rm')


def test_failed_inspect_cannot_be_reported_as_closed_and_other_owned_is_cleaned(tmp_path, monkeypatch):
    unknown, owned = 'a' * 64, 'b' * 64
    docker = OfflineDocker({unknown: container(tmp_path / 'owned/unknown'),
                            owned: container(tmp_path / 'owned/source')}, inspect_failure={unknown})
    monkeypatch.setattr(public.subprocess, 'run', docker)
    report = public.cleanup_owned_containers([tmp_path / 'owned'])
    assert report['status'] == 'CLEANUP_UNCONFIRMED'
    assert report['removed'] == [owned]
    assert set(docker.containers) == {unknown}
    assert report['errors']


def test_successful_remove_command_is_not_proof_of_removal(tmp_path, monkeypatch):
    cid = 'a' * 64
    docker = OfflineDocker({cid: container(tmp_path / 'owned/source')}, sticky={cid})
    monkeypatch.setattr(public.subprocess, 'run', docker)
    report = public.cleanup_owned_containers([tmp_path / 'owned'])
    assert report['status'] == 'CLEANUP_UNCONFIRMED'
    assert report['removed'] == []
    assert cid in docker.containers


def test_remove_failure_does_not_abandon_other_proven_owned_containers(tmp_path, monkeypatch):
    first, second = 'a' * 64, 'b' * 64
    docker = OfflineDocker({first: container(tmp_path / 'owned/first'),
                            second: container(tmp_path / 'owned/second')}, remove_failure={first})
    monkeypatch.setattr(public.subprocess, 'run', docker)
    report = public.cleanup_owned_containers([tmp_path / 'owned'])
    assert report['status'] == 'OWNED_CLEANUP_FAILED'
    assert report['removed'] == [second]
    assert set(docker.containers) == {first}


def test_failed_final_inventory_cannot_confirm_removal(tmp_path, monkeypatch):
    cid = 'a' * 64
    docker = OfflineDocker({cid: container(tmp_path / 'owned/source')}, final_list_failure=True)
    monkeypatch.setattr(public.subprocess, 'run', docker)
    report = public.cleanup_owned_containers([tmp_path / 'owned'])
    assert report['status'] == 'CLEANUP_UNCONFIRMED'
    assert report['removed'] == []


@pytest.mark.parametrize('mounts', [None, [{}], [{'Type': None}],
                                  [{'Type': 'bind'}], [{'Type': 'bind', 'Source': 'relative/path'}]])
def test_malformed_mount_inventory_cannot_authorize_removal_or_closed(tmp_path, monkeypatch, mounts):
    cid = 'a' * 64
    docker = OfflineDocker({cid: {'mounts': mounts}})
    monkeypatch.setattr(public.subprocess, 'run', docker)
    report = public.cleanup_owned_containers([tmp_path / 'owned'])
    assert report['status'] == 'CLEANUP_UNCONFIRMED'
    assert not any(argv[1] == 'rm' for argv in docker.calls)
