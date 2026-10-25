"""Isolated demo admission gates plus separately selected real CLI acceptance.

Offline tests replace only RuntimeSession/provider boundaries. The real_* tests
use the task's actual Docker daemon and cached image; they never call a model API.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from contractedge.contracts.validation import canonical_digest
from contractedge.dataset.benchmark import file_sha
from contractedge.development import public_demo
from contractedge.execution import public_observer
from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.money import MoneyBudget
from contractedge.orchestration.repair import repair_case


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'scripts/run_public_demo.py'
MOCK_RUNTIME = {'image': 'fixture.invalid/public@sha256:' + 'a' * 64,
                'docker_host': 'unix:///unconnected-test-only/docker.sock'}


def read(path):
    return json.loads(Path(path).read_text())


class NeverDispatch:
    def __init__(self):
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        raise AssertionError('The rejected observation must not reach a model provider')


class BoundarySessions:
    """A declared mock runtime boundary, not evidence of actual isolation."""
    def __init__(self, *, candidate_status='EXECUTION_PASS', close_status='CLOSED', check_sequences=None,
                 close_sequences=None):
        self.candidate_status, self.close_status = candidate_status, close_status
        self.check_sequences = check_sequences
        self.close_sequences = close_sequences
        self.deadlines, self.sessions = [], []
        self.candidate_calls, self.observer_calls = 0, 0

    def open(self, spec, *, deadline):
        self.deadlines.append(deadline)
        parent = self
        root = Path(spec.session_root)
        root.mkdir(parents=True)
        index = len(self.sessions)
        statuses = (self.check_sequences[min(index, len(self.check_sequences) - 1)]
                    if self.check_sequences else {'selected': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'})
        closed = (self.close_sequences[min(index, len(self.close_sequences) - 1)]
                  if self.close_sequences else self.close_status)

        class Session:
            def run_candidate(self, invocation, *, snapshot, public_inputs=None):
                parent.candidate_calls += 1
                self.snapshot = snapshot.snapshot_id
                self.candidate_invocation, self.public_inputs = invocation, Path(public_inputs)
                self.inputs_digest = canonical_digest({'public-inputs.json': file_sha(self.public_inputs)})
                path = root / 'mock-candidate.stdout'
                path.write_text('{"mock_boundary":true}')
                return {'status': parent.candidate_status, 'role': 'candidate',
                        'snapshot_id': self.snapshot, 'public_inputs_digest': self.inputs_digest,
                        'invocation_digest': invocation.digest,
                        'stdout_path': str(path), 'stderr_path': str(path),
                        'attestation': {'status': 'VERIFIED'},
                        'cleanup': {'status': 'CONFIRMED'}, 'mock_boundary': True}

            def run_observer(self, invocation, *, bounded_output, public_check_spec):
                parent.observer_calls += 1
                self.observer_invocation, self.public_check_spec = invocation, Path(public_check_spec)
                spec = read(public_check_spec)
                checks = []
                for scenario in spec['scenarios']:
                    identifier, expected = scenario['id'], scenario['expected']
                    status = statuses[identifier]
                    if identifier == 'stable':
                        actual = expected = {'modes': ['stable'] * len(spec['scenarios'])}
                        if status == 'FAIL':
                            actual = {'modes': ['regressed'] * len(spec['scenarios'])}
                    else:
                        actual = dict(expected) if status == 'PASS' else {key: 'broken-fixture-value' for key in expected}
                    checks.append({'id': identifier, 'status': status, 'actual': actual, 'expected': expected})
                path = root / 'mock-observer.stdout'
                path.write_text(json.dumps({'schema_version': 'ce.public_process_checks.v1',
                    'snapshot_id': spec['snapshot_id'], 'input_digest': spec['input_digest'], 'checks': checks}))
                return {'status': 'EXECUTION_PASS', 'role': 'observer', 'stdout_path': str(path),
                        'snapshot_id': self.snapshot, 'public_inputs_digest': self.inputs_digest,
                        'checks_digest': canonical_digest({'spec.json': file_sha(self.public_check_spec)}),
                        'invocation_digest': invocation.digest,
                        'attestation': {'status': 'VERIFIED'}, 'cleanup': {'status': 'CONFIRMED'},
                        'mock_boundary': True}

            def close(self):
                return {'status': closed, 'mock_boundary': True}

        session = Session()
        self.sessions.append(session)
        return session


def provider_config(root, **overrides):
    config = {'provider': 'openai_compatible', 'model': 'fixture-isolated',
        'base_url': 'https://fixture.invalid/v1', 'api_key_env': 'CE_ISOLATED_TEST_ONLY_KEY',
        'max_total_tokens': 1500000, 'max_elapsed_seconds': 180, 'max_model_calls': 16,
        'max_rounds': 2, 'public_runtime_config': MOCK_RUNTIME,
        'money_budget': {'path': str(root / 'mock-money.json'), 'limit_cny': '30',
                        'model': 'fixture-isolated', 'input_cny_per_million': '1',
                        'output_cny_per_million': '1'}}
    config.update(overrides)
    path = root / 'provider.json'
    public_demo.write_json(path, config)
    return path


def prepare_runner(root):
    public = public_demo.freeze_example('config-binding', root / 'frozen', runtime='isolated')
    runner = public_demo.IsolatedPublicExampleRunner(public, 'config-binding', MOCK_RUNTIME)
    workspace = root / 'workspace'
    runner.prepare('config-binding', workspace, root / 'prepare')
    return public, runner, workspace


def synthetic_api_fixture(settings, case, runner):
    # This is a new synthetic test allowance, never the real collaboration ledger.
    MoneyBudget(**settings['money_budget'])
    return public_demo.SyntheticExampleProvider(case, runner), public_demo.count_tokens


def make_mock_ready(root, monkeypatch):
    boundary = BoundarySessions()
    config = provider_config(root, observe_broken=True)
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    with patch.object(public_demo, '_provider', side_effect=synthetic_api_fixture):
        result = public_demo.run_demo('config-binding', root / 'saved', save_ready=True,
                                      provider_config=config, runtime='isolated')
    assert result['status'] == 'READY', result
    return root / 'saved'


@pytest.mark.parametrize('save_ready', [False, True])
def test_fresh_observe_broken_false_is_rejected_before_provider_creation(tmp_path, save_ready):
    config = provider_config(tmp_path, observe_broken=False)
    with patch.object(public_demo, '_provider') as factory, \
         patch.object(public_observer.RuntimeSession, 'open') as session:
        with pytest.raises(ValueError, match='Broken|observe_broken'):
            public_demo.run_demo('config-binding', tmp_path / 'rejected', save_ready=save_ready,
                                 provider_config=config, runtime='isolated')
    factory.assert_not_called()
    session.assert_not_called()


def test_inherited_observe_broken_false_is_rejected_before_claim_or_dispatch(tmp_path, monkeypatch):
    saved = make_mock_ready(tmp_path, monkeypatch)
    # A self-consistent legacy checkpoint is still subject to current admission.
    config = read(saved / 'run.config.json')
    config['observe_broken'] = False
    public_demo.write_json(saved / 'run.config.json', config)
    freeze = read(saved / 'run.freeze.json')
    freeze['config_digest'] = canonical_digest(config)
    public_demo.write_json(saved / 'run.freeze.json', freeze)
    checkpoint = read(saved / 'ready.checkpoint.json')
    for name in ('run.config.json', 'run.freeze.json'):
        checkpoint['files'][name] = file_sha(saved / name)
    public_demo.write_json(saved / 'ready.checkpoint.json', checkpoint)
    with patch.object(public_demo, '_provider') as factory:
        with pytest.raises(ValueError, match='Broken|observe_broken'):
            public_demo.run_demo(None, tmp_path / 'rejected', resume_ready=saved)
    factory.assert_not_called()
    assert not (saved / 'continuation.claim.json').exists()


def test_direct_repair_cannot_bypass_the_required_broken_observation(tmp_path):
    public, runner, _ = prepare_runner(tmp_path)
    provider = NeverDispatch()
    config = {**public_demo.default_config('config-binding'), 'observe_broken': False,
              'public_runtime': 'isolated', 'public_runtime_config': MOCK_RUNTIME}
    with patch.object(public_observer.RuntimeSession, 'open') as runtime:
        with pytest.raises(ValueError, match='Broken|observe_broken'):
            repair_case(public, tmp_path / 'frozen', 'config-binding', tmp_path / 'repair',
                        provider, public_demo.count_tokens, config, public_runner=runner)
    assert provider.requests == []
    runtime.assert_not_called()


@pytest.mark.parametrize('failure', ['process', 'missing-profile', 'cleanup'])
@pytest.mark.parametrize('save_ready', [False, True])
def test_unverified_initial_observation_never_dispatches_a_paid_request(tmp_path, monkeypatch, failure, save_ready):
    boundary = BoundarySessions(candidate_status='INFRASTRUCTURE_ERROR' if failure == 'process' else 'EXECUTION_PASS',
                                close_status='INFRASTRUCTURE_ERROR' if failure == 'cleanup' else 'CLOSED')
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    if failure == 'missing-profile':
        observe = public_observer.IsolatedPublicObserver.observe
        def missing(self, *args, **kwargs):
            report = observe(self, *args, **kwargs)
            report.pop('required_public_profile', None)
            return report
        monkeypatch.setattr(public_observer.IsolatedPublicObserver, 'observe', missing)
    provider = NeverDispatch()
    config = provider_config(tmp_path)
    with patch.object(public_demo, '_provider', return_value=(provider, public_demo.count_tokens)):
        if save_ready:
            with pytest.raises(ValueError, match='Broken|observation'):
                public_demo.run_demo('config-binding', tmp_path / 'run', save_ready=True,
                                     provider_config=config, runtime='isolated')
        else:
            result = public_demo.run_demo('config-binding', tmp_path / 'run',
                                          provider_config=config, runtime='isolated')
            assert result['status'] in {'INFRA_ERROR', 'INVALID_OBSERVATION', 'INFRASTRUCTURE_ERROR'}, result
    assert provider.requests == []
    assert not (tmp_path / 'run/ready.checkpoint.json').exists()
    if failure == 'process':
        assert boundary.observer_calls == 0
    if failure == 'cleanup':
        base = 'broken-public' if save_ready else 'repair/broken-public'
        observation = read(tmp_path / 'run' / base / 'observation.json')
        assert any('cleanup' in text.lower() for text in observation['errors'])


def test_every_isolated_observation_requires_its_profile_after_broken(tmp_path, monkeypatch):
    _, runner, workspace = prepare_runner(tmp_path)
    boundary = BoundarySessions()
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    first = runner.observe(workspace, tmp_path / 'first')
    assert first['status'] == 'EXECUTION_PASS', first
    observe = public_observer.IsolatedPublicObserver.observe
    def missing(self, *args, **kwargs):
        result = observe(self, *args, **kwargs)
        result.pop('required_public_profile')
        return result
    monkeypatch.setattr(public_observer.IsolatedPublicObserver, 'observe', missing)
    second = runner.observe(workspace, tmp_path / 'second')
    assert second['status'] == 'INVALID_OBSERVATION', second


@pytest.mark.parametrize('field', [
    'candidate.role', 'observer.role',
    'candidate.snapshot_id', 'observer.snapshot_id',
    'candidate.public_inputs_digest', 'observer.public_inputs_digest',
    'observer.checks_digest', 'candidate.invocation_digest', 'observer.invocation_digest',
    'candidate_inputs_digest', 'public_check_spec_digest',
])
@pytest.mark.parametrize('mutation', ['missing', 'null', 'mismatch'])
def test_isolated_observation_rejects_receipts_not_bound_to_frozen_execution(tmp_path, monkeypatch, field, mutation):
    _, runner, workspace = prepare_runner(tmp_path)
    boundary = BoundarySessions()
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    report = runner.observe(workspace, tmp_path / 'complete')
    assert report['status'] == 'EXECUTION_PASS', report
    session = boundary.sessions[0]
    runtime = report['runtime']
    # Derive every expected binding from actual files and calls received by the boundary.
    inputs_digest = canonical_digest({'public-inputs.json': file_sha(session.public_inputs)})
    checks_digest = canonical_digest({'spec.json': file_sha(session.public_check_spec)})
    assert runtime['candidate_inputs_digest'] == inputs_digest
    assert runtime['public_check_spec_digest'] == checks_digest
    for role in ('candidate', 'observer'):
        assert runtime[role]['snapshot_id'] == report['snapshot_id'] == session.snapshot
        assert runtime[role]['public_inputs_digest'] == inputs_digest
        assert runtime[role]['invocation_digest'] == getattr(session, role + '_invocation').digest
    assert runtime['observer']['checks_digest'] == checks_digest
    assert public_observer.isolated_observation_complete(report) is True

    parts = field.split('.')
    target = runtime
    for part in parts[:-1]:
        target = target[part]
    key = parts[-1]
    if mutation == 'missing':
        del target[key]
    elif mutation == 'null':
        target[key] = None
    elif key == 'role':
        target[key] = 'observer' if parts[0] == 'candidate' else 'candidate'
    elif key == 'snapshot_id':
        target[key] = 'stale-snapshot'
    elif key == 'invocation_digest':
        other_role = 'observer' if parts[0] == 'candidate' else 'candidate'
        target[key] = runtime[other_role]['invocation_digest']
    else:
        assert target[key] != '0' * 64
        target[key] = '0' * 64
    public_demo.write_json(tmp_path / 'tampered-observation.json', report)
    # Keep the profile intact: rejection must come from the altered runtime binding.
    validation = public_observer.enforce_public_profile({'decision': 'UNKNOWN'}, report, required=True)
    assert validation['required_public_profile']['binding_valid'] is True
    assert public_observer.isolated_observation_complete(report) is False


def test_deleting_all_model_postconditions_cannot_remove_a_failed_public_check(tmp_path, monkeypatch):
    _, runner, workspace = prepare_runner(tmp_path)
    boundary = BoundarySessions()
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    observation = runner.observe(workspace, tmp_path / 'broken')
    result = public_observer.enforce_public_profile({'decision': 'PASS', 'checks': []}, observation, required=True)
    assert result['decision'] == 'FAIL'
    assert result['required_public_profile']['binding_valid'] is True
    assert result['required_public_profile']['decision'] == 'FAIL'


def test_model_deleted_preservation_postconditions_cannot_hide_a_profile_failure_in_repair(tmp_path, monkeypatch):
    boundary = BoundarySessions(check_sequences=[
        {'selected': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'},
        {'selected': 'PASS', 'disabled': 'PASS', 'stable': 'FAIL'}])
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    class DropPreservationProvider(public_demo.SyntheticExampleProvider):
        def complete_json(self, request):
            response = super().complete_json(request)
            if request.prompt_version.startswith('changeset.'):
                body = json.loads(response.content)
                body['postconditions'] = [p for p in body['postconditions'] if p['id'] == 'P1']
                for change in body['changes']:
                    change['postcondition_refs'] = ['P1']
                return type(response)(json.dumps(body), response.metadata)
            return response
    def fixture_provider(settings, case, runner):
        return DropPreservationProvider(case, runner), public_demo.count_tokens
    settings = tmp_path / 'synthetic.json'
    public_demo.write_json(settings, {'provider': 'synthetic', 'max_rounds': 1,
                                     'public_runtime_config': MOCK_RUNTIME})
    with patch.object(public_demo, '_provider', side_effect=fixture_provider):
        result = public_demo.run_demo('config-binding', tmp_path / 'run', provider_config=settings, runtime='isolated')
    round_root = tmp_path / 'run/repair/rounds/0'
    plan = read(round_root / 'public-review-plan.json')
    review = read(round_root / 'public-review.json')
    assert [p['id'] for p in plan['postconditions']] == ['P1']
    assert review['required_public_profile']['binding_valid'] is True
    assert review['required_public_profile']['decision'] == 'FAIL' and review['decision'] == 'FAIL'
    assert result['status'] != 'PUBLIC_REVIEW_COMPLETE'
    trace = read(tmp_path / 'run/repair/repair.trace.json')
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id']
    decision = read(round_root / 'candidate-decision.json')
    selected = read(tmp_path / 'run/repair/selected-candidate.json')
    assert decision['decision'] == 'REJECT_REGRESSION'
    assert 'contractedge-public-process-v1:stable' in {r['check_id'] for r in decision['regressions']}
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id'] == selected['snapshot_id']
    assert selected['selection'] == 'INCUMBENT'
    assert Path(result['patch']).read_bytes() == b''


def test_candidate_cleanup_failure_skips_review_and_keeps_the_incumbent(tmp_path, monkeypatch):
    boundary = BoundarySessions(check_sequences=[
        {'selected': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'},
        {'selected': 'PASS', 'disabled': 'PASS', 'stable': 'PASS'}],
        close_sequences=['CLOSED', 'INFRASTRUCTURE_ERROR'])
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    dispatched, reviewed = [], []
    class RecordingProvider(public_demo.SyntheticExampleProvider):
        def complete_json(self, request):
            dispatched.append(request)
            return super().complete_json(request)
    def fixture_provider(settings, case, runner):
        return RecordingProvider(case, runner), public_demo.count_tokens
    review_method = ModelPublicReviewer.review
    def record_review(self, *args, **kwargs):
        reviewed.append(args)
        return review_method(self, *args, **kwargs)
    monkeypatch.setattr(ModelPublicReviewer, 'review', record_review)
    settings = provider_config(tmp_path, max_rounds=1)
    with patch.object(public_demo, '_provider', side_effect=fixture_provider):
        result = public_demo.run_demo('config-binding', tmp_path / 'run', provider_config=settings, runtime='isolated')
    trace = read(tmp_path / 'run/repair/repair.trace.json')
    candidate = read(tmp_path / 'run/repair/rounds/0/public/observation.json')
    assert candidate['runtime']['close']['status'] == 'INFRASTRUCTURE_ERROR'
    assert len(dispatched) == 3  # real diagnosis, plan and concrete edit fixture calls
    assert not any(r.prompt_version in {'public-postcondition-review.v1','public-postcondition-review.v2'} for r in dispatched)
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id']
    selected = read(tmp_path / 'run/repair/selected-candidate.json')
    assert reviewed == []
    assert trace['final_snapshot_id'] == trace['initial_snapshot_id'] == selected['snapshot_id']
    assert selected['selection'] == 'INCUMBENT'
    assert Path(result['patch']).read_bytes() == b''
    assert result['status'] != 'PUBLIC_REVIEW_COMPLETE'


def test_runtime_override_cannot_downgrade_an_isolated_ready_checkpoint(tmp_path, monkeypatch):
    saved = make_mock_ready(tmp_path, monkeypatch)
    with patch.object(public_demo, '_provider') as provider:
        with pytest.raises(ValueError, match='inherits|overrides'):
            public_demo.run_demo(None, tmp_path / 'downgraded', resume_ready=saved, runtime='host')
    provider.assert_not_called()
    assert not (saved / 'continuation.claim.json').exists()


def test_narrowed_runner_timeout_reaches_the_actual_session_deadline(tmp_path, monkeypatch):
    _, runner, workspace = prepare_runner(tmp_path)
    boundary = BoundarySessions()
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    runner.timeout_seconds = 0.25
    before = time.monotonic()
    result = runner.observe(workspace, tmp_path / 'observation')
    assert result['status'] == 'EXECUTION_PASS', result
    assert len(boundary.deadlines) == 1
    assert 0 < boundary.deadlines[0] - before <= 0.30
    assert runner.observer.timeout_seconds == 0.25


def test_preparation_does_not_rebase_an_expired_observation_deadline(tmp_path, monkeypatch):
    _, runner, workspace = prepare_runner(tmp_path)
    record = read(workspace / 'workspace.json')
    clock, deadlines = [0.0], []
    requests = public_observer._requests
    def slow_requests(*args):
        result = requests(*args)
        clock[0] = 2.0
        return result
    def expired_open(spec, *, deadline):
        deadlines.append(deadline)
        raise TimeoutError('Synthetic expired preparation deadline')
    monkeypatch.setattr(public_observer.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(public_observer, '_requests', slow_requests)
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', expired_open)
    observer = public_observer.IsolatedPublicObserver(**MOCK_RUNTIME, timeout_seconds=1)
    result = observer.observe(record['source_root'], workspace / 'inputs/runtime-inputs.json', tmp_path / 'expired')
    assert deadlines == [1.0]
    assert result['status'] != 'EXECUTION_PASS'


def test_save_ready_passes_only_the_remaining_case_time_to_observation(tmp_path, monkeypatch):
    boundary, clock = BoundarySessions(), [0.0]
    prepare = public_demo.PublicExampleRunner.prepare
    def slow_prepare(*args, **kwargs):
        result = prepare(*args, **kwargs)
        clock[0] = 4.0
        return result
    monkeypatch.setattr(public_demo.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(public_demo.PublicExampleRunner, 'prepare', slow_prepare)
    monkeypatch.setattr(public_observer.RuntimeSession, 'open', boundary.open)
    config = tmp_path / 'synthetic.json'
    public_demo.write_json(config, {'provider': 'synthetic', 'max_elapsed_seconds': 5,
                                   'public_runtime_config': MOCK_RUNTIME})
    result = public_demo.run_demo('config-binding', tmp_path / 'saved', save_ready=True,
                                  provider_config=config, runtime='isolated')
    assert result['status'] == 'READY', result
    assert boundary.deadlines == [5.0]


def test_legacy_host_synthetic_path_still_completes_without_opening_a_runtime(tmp_path):
    with patch.object(public_observer.RuntimeSession, 'open', side_effect=AssertionError('Host fixture must not open Docker')) as runtime, \
         patch('urllib.request.urlopen', side_effect=AssertionError('Host fixture must not call an API')) as http:
        result = public_demo.run_demo('config-binding', tmp_path / 'host')
    assert result['status'] == 'PUBLIC_REVIEW_COMPLETE', result
    assert read(tmp_path / 'host/run.config.json')['public_runtime'] == 'host'
    runtime.assert_not_called()
    http.assert_not_called()


@pytest.fixture
def actual_runtime():
    if os.environ.get('CONTRACTEDGE_RUN_ISOLATED_DEMOS') != '1':
        pytest.skip('Actual isolated demo execution was not requested; set CONTRACTEDGE_RUN_ISOLATED_DEMOS=1 in the Docker acceptance job')
    image, endpoint = os.environ.get('CONTRACTEDGE_RUNTIME_IMAGE'), os.environ.get('DOCKER_HOST')
    assert image and '@sha256:' in image and endpoint, 'Actual task Docker and a cached digest-pinned image are required'
    return image, endpoint


def run_cli(directory, *arguments):
    result = subprocess.run([sys.executable, str(ENTRY), *map(str, arguments)], cwd=ROOT,
                            capture_output=True, text=True, timeout=240)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'cli.stdout').write_text(result.stdout)
    (directory / 'cli.stderr').write_text(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def assert_actual_observation(path):
    observation = read(path)
    assert observation['status'] == 'EXECUTION_PASS', observation.get('errors')
    assert observation['required_public_profile']['status'] == 'COMPLETE'
    runtime = observation['runtime']
    assert runtime['close']['status'] == 'CLOSED'
    for role in ('candidate', 'observer'):
        assert runtime[role]['status'] == 'EXECUTION_PASS'
        assert runtime[role]['attestation']['status'] == 'VERIFIED'
        assert runtime[role]['cleanup']['status'] == 'CONFIRMED'
    candidate_mounts = {m['Destination'] for m in runtime['candidate']['attestation']['inspect']['mounts']}
    observer_mounts = {m['Destination'] for m in runtime['observer']['attestation']['inspect']['mounts']}
    assert candidate_mounts == {'/source', '/inputs'}
    assert observer_mounts == {'/observations', '/checks'}
    return observation


@pytest.mark.parametrize('case', ['config-binding', 'reload-state', 'two-consumers'])
def test_real_isolated_cli_repairs_each_published_case(tmp_path, actual_runtime, case):
    image, _ = actual_runtime
    output = tmp_path / 'run'
    run_cli(tmp_path, '--case', case, '--runtime', 'isolated', '--runtime-image', image, '--output', output)
    summary = read(output / 'summary.json')
    assert summary['status'] == 'PUBLIC_REVIEW_COMPLETE' and summary['zero_cost'] is True
    trace = read(output / 'repair/repair.trace.json')
    assert trace['initial_snapshot_id'] != trace['final_snapshot_id']
    assert Path(summary['patch']).read_text().startswith('diff --git')
    broken = assert_actual_observation(output / 'repair/broken-public/observation.json')
    assert {c['id']: c['status'] for c in broken['required_public_profile']['checks']} == {
        'selected': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'}
    final_round = output / 'repair/rounds' / str(summary['rounds'] - 1)
    fixed = assert_actual_observation(final_round / 'public/observation.json')
    assert all(c['status'] == 'PASS' for c in fixed['required_public_profile']['checks'])
    review = read(final_round / 'public-review.json')
    assert review['required_public_profile']['decision'] == 'PASS'
    assert trace['last_feedback']['pending_obligations'] == []
    inputs = read(output / 'repair/broken-public/candidate-inputs.json')
    assert all('expected' not in row for row in inputs['requests'])


def test_real_ready_new_process_inherits_the_isolated_runtime_and_budget(tmp_path, actual_runtime):
    image, _ = actual_runtime
    saved, continued = tmp_path / 'saved', tmp_path / 'continued'
    run_cli(tmp_path / 'save-cli', '--case', 'config-binding', '--runtime', 'isolated',
            '--runtime-image', image, '--save-ready', '--output', saved)
    checkpoint = read(saved / 'ready.checkpoint.json')
    previous = read(saved / 'model_calls.json')
    ledger_bytes = (saved / 'model_calls.json').read_bytes()
    settings = read(saved / 'run.config.json')
    assert settings['public_runtime'] == 'isolated'
    assert_actual_observation(saved / 'broken-public/observation.json')
    run_cli(tmp_path / 'resume-cli', '--resume-ready', saved, '--output', continued)
    summary = read(continued / 'summary.json')
    assert summary['status'] == 'PUBLIC_REVIEW_COMPLETE'
    assert (saved / 'model_calls.json').read_bytes() == ledger_bytes
    assert read(continued / 'run.config.json')['public_runtime_config'] == settings['public_runtime_config']
    combined = read(continued / 'combined.model_calls.json')
    assert combined[:len(previous)] == previous
    trace = read(continued / 'repair/repair.trace.json')
    assert trace['budget']['resumed_model_calls'] == len(previous)
    assert trace['budget']['reserved_tokens'] == sum(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] for c in combined)
    assert trace['budget']['max_calls'] == checkpoint['budget']['max_calls']
    assert trace['budget']['elapsed_seconds'] >= checkpoint['budget']['elapsed_seconds']
    assert_actual_observation(continued / 'repair/broken-public/observation.json')
    assert (saved / 'continuation.claim.json').is_file()
