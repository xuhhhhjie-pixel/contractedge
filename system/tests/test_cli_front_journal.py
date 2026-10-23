"""Standard CLI front journaling, using public-only controlled responses."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from contractedge import cli
from contractedge.contracts.validation import canonical_digest
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.development import public_demo
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.recorder import RunRecorder


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


@pytest.fixture
def public_case(tmp_path):
    case = 'reload-state'
    frozen = public_demo.freeze_example(case, tmp_path / 'fixture')
    method, _ = method_input_from_task(frozen, case)
    # Collect actual public process observations, without Git or Docker.
    workspace = tmp_path / 'observe-workspace'
    source = Path(safe_extract(frozen / method['archive_path'], workspace / 'source').source_root)
    inputs = workspace / 'inputs'
    inputs.mkdir()
    shutil.copyfile(frozen / 'tasks' / case / 'runtime-inputs.json', inputs / 'runtime-inputs.json')
    write(workspace / 'workspace.json', {'source_root': str(source)})
    runner = public_demo.PublicExampleRunner(frozen, case)
    runner.source = source
    observed = runner.observe(workspace, tmp_path / 'observed')
    assert observed['status'] == 'EXECUTION_PASS'
    public = tmp_path / 'public'
    public.mkdir()
    shutil.copyfile(frozen / method['archive_path'], public / 'source.tar.gz')
    method.update(archive_path='source.tar.gz', public_evidence=observed['observations'])
    method.pop('input_digest')
    method['input_digest'] = canonical_digest(method)
    write(public / 'method_input.json', method)
    (public / 'manifest.jsonl').write_text(json.dumps({'case_id': case, 'method_input': 'method_input.json'}) + '\n')
    config = {**public_demo.default_config(case), 'provider': 'ollama_native', 'model': 'controlled',
              'tokenizer': 'builtins:len', 'base_url': 'http://127.0.0.1:11434', 'run_id': 'cli-front-fixture'}
    config_path = tmp_path / 'config.json'
    write(config_path, config)
    return SimpleNamespace(case=case, public=public, method=method, runner=runner, config=config,
                           config_path=config_path, output=tmp_path / 'trace.json',
                           artifacts=tmp_path / 'trace.json.artifacts')


class ControlledProvider(public_demo.SyntheticExampleProvider):
    def __init__(self, fixture, *, invalid_diagnosis=False, invalid_plan=False, error=None, mutate=None):
        super().__init__(fixture.case, fixture.runner)
        self.requests, self.invalid_diagnosis, self.invalid_plan = [], invalid_diagnosis, invalid_plan
        self.error, self.mutate = error, mutate

    def complete_json(self, request):
        self.requests.append(json.loads(json.dumps(asdict(request))))
        if self.mutate is not None:
            mutate, self.mutate = self.mutate, None
            mutate()
        if self.error is not None:
            raise self.error
        if request.prompt_version.startswith('diagnosis.') and self.invalid_diagnosis:
            self.invalid_diagnosis = False
            return ModelResponse('{}', {'controlled_invalid': 'diagnosis'})
        if request.prompt_version.startswith('changeset.') and self.invalid_plan:
            self.invalid_plan = False
            return ModelResponse('{}', {'controlled_invalid': 'planning'})
        return super().complete_json(request)


def install_provider(monkeypatch, fixture, **kwargs):
    provider = ControlledProvider(fixture, **kwargs)
    factories = []
    def build(config):
        factories.append(config)
        return provider, public_demo.count_tokens
    monkeypatch.setattr(cli, '_build_provider', build)
    return provider, factories


def run(fixture, **kwargs):
    return cli.run_public_case(fixture.public, fixture.case, fixture.config,
        recording_dir=fixture.artifacts, output_path=fixture.output, config_path=fixture.config_path, **kwargs)


def assert_actual_journal(fixture, provider, trace):
    actual = read(fixture.artifacts / 'model_calls.json')
    payload = trace.to_wire() if hasattr(trace, 'to_wire') else trace
    assert payload['model_calls'] == actual
    assert [row['request'] for row in actual] == provider.requests
    assert len(actual) == len(provider.requests)
    budget = read(fixture.artifacts / 'budget.json')
    assert budget['model_calls'] == len(actual)
    assert budget['reserved_tokens'] == sum(row['prompt_tokens_reserved'] + row['output_tokens_reserved'] for row in actual)
    final = read(next((fixture.artifacts / 'journal/stages/cli_front_final').rglob('*.json')))
    recorder = RunRecorder(fixture.artifacts, run_id=payload['run_id'], input_digest=payload['input_digest'])
    assert len(final['call_refs']) == len(actual)
    for index, reference in enumerate(final['call_refs']):
        recorder.verify_call(reference)
        assert reference['absolute_call_index'] == index
        sealed = read(fixture.artifacts / reference['ledger_segment_path'])
        assert sealed[index] == actual[index]
        assert sealed[index]['request_digest'] == actual[index]['request_digest']
    final_output = next(ref for ref in final['output_refs'] if ref['kind'] == 'FINAL_CLI_TRACE')
    recorder.verify_artifact(final_output)
    assert (fixture.artifacts / final_output['relative_path']).read_bytes() == fixture.output.read_bytes()
    assert any('/front/' in ref['relative_path'] for ref in final['input_refs'])
    assert any('/semantic_delivery/' in ref['relative_path'] for ref in final['input_refs'])
    return actual


def test_standard_main_keeps_trace_file_and_persists_public_source_and_actual_journal(public_case, monkeypatch, capsys):
    fixture = public_case
    provider, factories = install_provider(monkeypatch, fixture)
    code = cli.main(['run', '--public-root', str(fixture.public), '--case-id', fixture.case,
                     '--config', str(fixture.config_path), '--output', str(fixture.output)])
    assert code == 0, capsys.readouterr()
    assert fixture.output.is_file()
    assert fixture.artifacts.is_dir()
    payload = read(fixture.output)
    assert payload['status'] == 'READY', payload['errors']
    assert len(factories) == 1
    assert (fixture.artifacts / 'workspace/repo/app.py').read_bytes() == (fixture.runner.source / 'app.py').read_bytes()
    assert (fixture.artifacts / 'source.tar.gz').read_bytes() == (fixture.public / 'source.tar.gz').read_bytes()
    for name in ('claim.json', 'front.replay.json', 'front.delivery.json', 'completion.json'):
        assert (fixture.artifacts / name).is_file()
    assert read(fixture.artifacts / 'completion.json')['recording_status'] == 'SEALED'
    assert_actual_journal(fixture, provider, payload)


@pytest.mark.parametrize('invalid', ['diagnosis', 'planning', 'both'])
def test_format_corrections_are_separate_actual_calls_in_one_budget(public_case, monkeypatch, invalid):
    fixture = public_case
    fixture.config['max_format_retries'] = 1
    write(fixture.config_path, fixture.config)
    provider, _ = install_provider(monkeypatch, fixture,
        invalid_diagnosis=invalid in {'diagnosis', 'both'}, invalid_plan=invalid in {'planning', 'both'})
    trace = run(fixture)
    assert trace.status == 'READY', trace.errors
    actual = assert_actual_journal(fixture, provider, trace)
    assert len(actual) == (4 if invalid == 'both' else 3)
    if invalid in {'diagnosis', 'both'}:
        assert any(row['request_metadata'].get('format_retry_index') == 1 for row in actual)
    if invalid in {'planning', 'both'}:
        assert any(row['request_metadata'].get('planning_format_retry_index') == 1 for row in actual)


def test_unknown_dispatch_keeps_one_reservation_and_never_resends(public_case, monkeypatch):
    fixture = public_case
    error = TimeoutError('controlled unknown dispatch')
    provider, _ = install_provider(monkeypatch, fixture, error=error)
    trace = run(fixture)
    assert trace.status == 'INFRASTRUCTURE_ERROR'
    actual = assert_actual_journal(fixture, provider, trace)
    assert len(actual) == 1 and actual[0]['error'] == 'TimeoutError: controlled unknown dispatch'
    assert 'response_content' not in actual[0]
    with pytest.raises(FileExistsError):
        run(fixture)
    assert len(provider.requests) == 1


@pytest.mark.parametrize('existing', ['trace', 'artifacts'])
def test_existing_destination_is_rejected_before_provider_creation(public_case, monkeypatch, existing):
    fixture = public_case
    _, factories = install_provider(monkeypatch, fixture)
    if existing == 'trace':
        fixture.output.write_text('existing')
    else:
        fixture.artifacts.mkdir()
    with pytest.raises(FileExistsError):
        run(fixture)
    assert not factories
    if existing == 'trace':
        assert fixture.output.read_text() == 'existing'


def test_concurrent_claim_has_one_provider_and_one_final_trace(public_case, monkeypatch):
    fixture = public_case
    provider, factories = install_provider(monkeypatch, fixture)
    def attempt():
        try:
            return run(fixture).status
        except FileExistsError:
            return 'CLAIM_REFUSED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ['CLAIM_REFUSED', 'READY']
    assert len(factories) == 1 and len(provider.requests) == 2
    assert read(fixture.output)['status'] == 'READY'


def test_failed_claim_is_not_reused_and_has_no_fabricated_trace(public_case, monkeypatch):
    fixture = public_case
    _, factories = install_provider(monkeypatch, fixture)
    del fixture.config['max_model_calls']
    write(fixture.config_path, fixture.config)
    with pytest.raises(ValueError, match='max_model_calls'):
        run(fixture)
    assert read(fixture.output)['schema_version'] == 'ce.cli_front_start_failure.v1'
    assert read(fixture.output)['model_calls'] == []
    assert not factories
    with pytest.raises(FileExistsError):
        run(fixture)


@pytest.mark.parametrize('target', ['mapping', 'config-file', 'input', 'archive', 'saved-input', 'saved-archive', 'source'])
def test_drift_after_dispatch_stops_before_another_model_call(public_case, monkeypatch, target):
    fixture = public_case
    def mutate():
        if target == 'mapping':
            fixture.config['max_model_calls'] += 1
        elif target == 'config-file':
            fixture.config_path.write_text(fixture.config_path.read_text() + '\n')
        elif target in {'input', 'saved-input'}:
            path = (fixture.public / 'method_input.json' if target == 'input' else fixture.artifacts / 'method_input.json')
            value = read(path)
            value['requirement_text'] += ' changed'
            value.pop('input_digest')
            value['input_digest'] = canonical_digest(value)
            write(path, value)
        elif target in {'archive', 'saved-archive'}:
            path = (fixture.public if target == 'archive' else fixture.artifacts) / 'source.tar.gz'
            with path.open('ab') as stream:
                stream.write(b'changed')
        else:
            with (fixture.artifacts / 'workspace/repo/app.py').open('a') as stream:
                stream.write('\n# changed\n')
    provider, _ = install_provider(monkeypatch, fixture, mutate=mutate)
    trace = run(fixture)
    assert trace.status == 'EVIDENCE_DELIVERY_INCOMPLETE', trace.errors
    assert len(provider.requests) == 1
    assert 'CLI_RUN_IDENTITY_DRIFT' in ' '.join(trace.errors)
    assert read(fixture.output)['status'] != 'READY'


def test_recording_failure_preserves_original_provider_error(public_case, monkeypatch):
    from contractedge.orchestration import cli_front_recording as recording
    fixture = public_case
    original = recording._write_atomic
    failed = []
    def fail_settlement(path, value):
        if Path(path).name == 'model_calls.json' and value and value[-1].get('error') and not failed:
            failed.append(True)
            raise OSError('controlled journal disk failure')
        return original(path, value)
    monkeypatch.setattr(recording, '_write_atomic', fail_settlement)
    error = TimeoutError('original provider error')
    provider, _ = install_provider(monkeypatch, fixture, error=error)
    trace = run(fixture)
    assert len(provider.requests) == 1
    assert trace.status == 'INFRASTRUCTURE_ERROR'
    assert trace.errors == ('TimeoutError: original provider error',)
    assert error.recording_errors
    assert 'controlled journal disk failure' in ' '.join(error.recording_errors)
    assert read(fixture.artifacts / 'completion.json')['recording_status'] == 'FAILED'


def test_failed_reservation_recording_does_not_claim_a_dispatch(public_case, monkeypatch):
    from contractedge.orchestration import cli_front_recording as recording
    fixture = public_case
    original = recording._write_atomic
    failed = []
    def fail_admission(path, value):
        if Path(path).name == 'model_calls.json' and value and not failed:
            failed.append(True)
            raise OSError('controlled reservation persistence failure')
        return original(path, value)
    monkeypatch.setattr(recording, '_write_atomic', fail_admission)
    provider, _ = install_provider(monkeypatch, fixture)
    trace = run(fixture)
    assert trace.status == 'INFRASTRUCTURE_ERROR'
    assert provider.requests == []
    actual = read(fixture.artifacts / 'model_calls.json')
    assert len(actual) == 1
    assert actual[0]['provider_error_metadata']['dispatch'] == 'NOT_STARTED'
    assert 'response_content' not in actual[0]
    assert read(fixture.artifacts / 'budget.json')['model_calls'] == 1


def test_preparation_time_uses_the_global_deadline_before_provider_creation(public_case, monkeypatch):
    from contractedge.orchestration import cli_front_recording as recording
    fixture = public_case
    fixture.config['max_elapsed_seconds'] = 1
    write(fixture.config_path, fixture.config)
    clock = iter([10.0, 12.0])
    monkeypatch.setattr(recording, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    _, factories = install_provider(monkeypatch, fixture)
    trace = run(fixture)
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert not factories and trace.model_calls == ()


@pytest.mark.parametrize('field', ['max_total_tokens', 'max_model_calls', 'max_context_tokens', 'max_elapsed_seconds'])
def test_explicit_global_budgets_are_required_before_provider(public_case, monkeypatch, field):
    fixture = public_case
    del fixture.config[field]
    write(fixture.config_path, fixture.config)
    _, factories = install_provider(monkeypatch, fixture)
    with pytest.raises(ValueError, match=field):
        run(fixture)
    assert factories == []


def test_openai_requires_existing_explicit_money_path_without_reading_ledger(public_case, monkeypatch):
    fixture = public_case
    fixture.config['provider'] = 'openai_compatible'
    fixture.config['money_budget'] = {'path': str(fixture.public / 'missing-ledger.json')}
    write(fixture.config_path, fixture.config)
    _, factories = install_provider(monkeypatch, fixture)
    with pytest.raises(ValueError, match='existing money ledger'):
        run(fixture)
    assert not factories and not Path(fixture.config['money_budget']['path']).exists()


def test_existing_money_object_is_preserved_without_inspecting_or_resetting_ledger(public_case, monkeypatch):
    from contractedge.orchestration import cli_front_recording as recording
    fixture = public_case
    ledger = fixture.public / 'opaque-money-ledger'
    ledger.write_bytes(b'OPAQUE_LEDGER_CONTENT_NOT_TO_READ')
    fixture.config.update(provider='openai_compatible', money_budget={'path': str(ledger)})
    write(fixture.config_path, fixture.config)
    provider, _ = install_provider(monkeypatch, fixture)
    provider.money_budget = object()
    original_budget, budgets = recording.BudgetedProvider, []
    class CapturedBudget(original_budget):
        def __init__(self, actual, *args, **kwargs):
            super().__init__(actual, *args, **kwargs)
            budgets.append(self)
    monkeypatch.setattr(recording, 'BudgetedProvider', CapturedBudget)
    trace = run(fixture)
    assert trace.status == 'READY', trace.errors
    assert len(budgets) == 1 and budgets[0].provider is provider
    assert budgets[0].provider.money_budget is provider.money_budget
    assert ledger.read_bytes() == b'OPAQUE_LEDGER_CONTENT_NOT_TO_READ'
    for path in fixture.artifacts.rglob('*.json'):
        assert 'OPAQUE_LEDGER_CONTENT_NOT_TO_READ' not in path.read_text()


@pytest.mark.parametrize('key,value', [('api_key', 'DO_NOT_PERSIST'), ('headers', {'Authorization': 'DO_NOT_PERSIST'}),
                                     ('base_url', 'https://example.invalid/v1?key=DO_NOT_PERSIST'),
                                     ('base_url', 'https://user:DO_NOT_PERSIST@example.com\uff0fx')])
def test_literal_credentials_are_refused_and_redacted_before_provider(public_case, monkeypatch, key, value):
    fixture = public_case
    fixture.config[key] = value
    write(fixture.config_path, fixture.config)
    _, factories = install_provider(monkeypatch, fixture)
    with pytest.raises(ValueError, match='literal credentials'):
        run(fixture)
    assert not factories
    for path in fixture.artifacts.rglob('*.json'):
        assert 'DO_NOT_PERSIST' not in path.read_text()
    assert 'DO_NOT_PERSIST' not in fixture.output.read_text()


@pytest.mark.parametrize('boundary', ['final-trace', 'final-stage', 'publication'])
def test_final_identity_is_rechecked_after_sealing_and_publication(public_case, monkeypatch, boundary):
    from contractedge.orchestration import cli_front_recording as recording
    fixture = public_case
    mutated = []
    def mutate():
        if not mutated:
            mutated.append(True)
            with (fixture.artifacts / 'workspace/repo/app.py').open('a') as stream:
                stream.write('\n# final boundary drift\n')
    if boundary == 'final-trace':
        original = recording._write_once
        def write_then_mutate(path, value):
            result = original(path, value)
            if Path(path).name == 'final.trace.json':
                mutate()
            return result
        monkeypatch.setattr(recording, '_write_once', write_then_mutate)
    elif boundary == 'final-stage':
        original = recording.ProviderJournal.record
        def record_then_mutate(self, stage, **kwargs):
            result = original(self, stage, **kwargs)
            if stage == 'cli_front_final':
                mutate()
            return result
        monkeypatch.setattr(recording.ProviderJournal, 'record', record_then_mutate)
    else:
        original = recording._Claim.publish
        def publish_then_mutate(self, content):
            result = original(self, content)
            mutate()
            return result
        monkeypatch.setattr(recording._Claim, 'publish', publish_then_mutate)
    provider, _ = install_provider(monkeypatch, fixture)
    trace = run(fixture)
    assert trace.status == 'EVIDENCE_DELIVERY_INCOMPLETE'
    assert len(provider.requests) == 2
    assert read(fixture.output)['status'] == trace.status
    completion = read(fixture.artifacts / 'completion.json')
    assert completion['status'] == trace.status
    latest = sorted((fixture.artifacts / 'journal/stages/cli_front_final/run').glob('*.json'))[-1]
    receipt = read(latest)
    assert receipt['status'] == trace.status
    final = next(ref for ref in receipt['output_refs'] if ref['kind'] == 'FINAL_CLI_TRACE')
    assert (fixture.artifacts / final['relative_path']).read_bytes() == fixture.output.read_bytes()


def test_completion_failure_downgrades_published_ready_and_raises_recording_error(public_case, monkeypatch):
    from contractedge.orchestration import cli_front_recording as recording
    fixture = public_case
    original = recording._write_once
    def fail_completion(path, value):
        if Path(path).name == 'completion.json':
            raise OSError('controlled completion failure')
        return original(path, value)
    monkeypatch.setattr(recording, '_write_once', fail_completion)
    provider, _ = install_provider(monkeypatch, fixture)
    with pytest.raises(OSError, match='controlled completion failure'):
        run(fixture)
    assert len(provider.requests) == 2
    payload = read(fixture.output)
    assert payload['status'] == 'INFRASTRUCTURE_ERROR'
    assert any('controlled completion failure' in error for error in payload['errors'])
    assert not (fixture.artifacts / 'completion.json').exists()


def test_programmatic_legacy_front_path_remains_available_without_artifacts(public_case, monkeypatch):
    fixture = public_case
    provider, factories = install_provider(monkeypatch, fixture)
    trace = cli.run_public_case(fixture.public, fixture.case, fixture.config)
    assert trace.status == 'READY', trace.errors
    assert len(provider.requests) == 2 and len(factories) == 1
    assert not fixture.output.exists() and not fixture.artifacts.exists()
