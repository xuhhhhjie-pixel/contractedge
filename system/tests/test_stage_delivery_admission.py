"""Cross-stage delivery gates on production entry points, without live services."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_run_trace
from contractedge.dataset.benchmark import file_sha, method_input_from_task
from contractedge.execution.topomen import ModelPlanExecutor, PlanRejected
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.provider import OpenAICompatibleProvider
from contractedge.orchestration.evidence_delivery import select_observations_delivery
from contractedge.orchestration.repair import repair_case, revise_plan, run_front_recorded
from contractedge.source.snapshot import compute_snapshot
from tests.test_benchmark_current import make_public_fixture
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_repair_execution import PublicLoopRunner, TwoStepExecutor, bound_plan


def config(**overrides):
    return dict(model='fixture', provider='configured', max_context_tokens=100000,
                max_output_tokens=1024, execution_max_output_tokens=256,
                max_total_tokens=2000000, max_elapsed_seconds=30, max_model_calls=8,
                max_rounds=1, schema_in_prompt=False, **overrides)


def front_config():
    return {**config(), 'model': {'model': 'fixture', 'provider': 'configured', 'schema_in_prompt': False}}


class RecordingPipeline(PipelineFixtureProvider):
    def __init__(self, source):
        super().__init__()
        self.requests = []
        self.source = source

    def complete_json(self, request):
        self.requests.append(request)
        self._snapshot_digests = compute_snapshot(self.source).file_digests
        return super().complete_json(request)


def prepared_case(tmp_path):
    public, _ = make_public_fixture(tmp_path / 'benchmark')
    runner = PublicLoopRunner(public, raw_only=True)
    workspace = tmp_path / 'prepared'
    record = runner.prepare('CASE-1', workspace, tmp_path / 'prepare')
    source = Path(record['source_root'])
    method, _ = method_input_from_task(public, 'CASE-1')
    parsed = parse_method_input(method)
    front_workspace = source if parsed.source_root == '.' else source.parent
    return public, runner, parsed, source, front_workspace


def budgeted(provider, counter=len, on_update=None):
    return BudgetedProvider(provider, counter, max_tokens=2000000, max_seconds=30,
                            max_calls=8, on_update=on_update)


def ready_front(tmp_path):
    public, runner, method, source, workspace = prepared_case(tmp_path)
    port = RecordingPipeline(source)
    budget = budgeted(port)
    observation = runner.observe(tmp_path / 'prepared', tmp_path / 'ready-broken')
    trace = run_front_recorded(method, workspace, budget, front_config(), len,
                               directory=tmp_path / 'ready-front',
                               public_observations=tuple(observation['observations'])).to_wire()
    assert trace['status'] in {'READY', 'READY_PARTIAL'}, trace.get('errors')
    return method, source, trace


def behavior_record(snapshot, *, failed=True, stale=False, large=False):
    status = 'FAIL' if failed else 'PASS'
    text = json.dumps({'probe_id': 'public', 'id': 'retain-current-output', 'status': status,
                       'statement': 'Current output retains the declared selection.',
                       'observed': {'actual': 'default', 'detail': 'x' * (10000 if large else 1)}})
    return dict(id='OLD-PASS' if stale else 'CURRENT-FAIL', snapshot_id='OLD' if stale else snapshot,
                kind='PUBLIC_OBSERVATION', evidence_kind='PUBLIC_BEHAVIOR',
                locator={'path': 'registered-public-check.json', 'truncated': False,
                         'whole_file_read': True},
                content_digest=hashlib.sha256(text.encode()).hexdigest(), excerpt=text,
                origin='Frozen method behavior probe', scenario='selected',
                behavior_check={'id': 'public:retain-current-output', 'status': status,
                                'probe_sha256': 'a' * 64})


class DeliveryRunner(PublicLoopRunner):
    def __init__(self, public, *, oversized=False, with_stale=False):
        super().__init__(public, raw_only=True)
        self.oversized, self.with_stale = oversized, with_stale

    def observe(self, workspace, output):
        report = super().observe(workspace, output)
        current = behavior_record(report['snapshot_id'], large=self.oversized)
        report['observations'].append(current)
        if self.with_stale:
            report['observations'].append(behavior_record(report['snapshot_id'], failed=False, stale=True))
        return report


def test_mandatory_failure_overflow_stops_repair_before_any_model_dispatch(tmp_path):
    public, _ = make_public_fixture(tmp_path / 'benchmark')
    class NeverDispatch:
        calls = 0
        def complete_json(self, request):
            self.calls += 1
            raise AssertionError('Incomplete public evidence must not reach a model')
    port = NeverDispatch()
    output = tmp_path / 'run'
    report = repair_case(public, tmp_path, 'CASE-1', output, port, len,
                         config(max_public_evidence_tokens=64),
                         public_runner=DeliveryRunner(public, oversized=True))
    assert report['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', report
    assert port.calls == 0 and report['budget']['model_calls'] == 0
    receipt = json.loads((output / 'rounds/0/public-evidence.delivery.json').read_text())
    assert receipt['status'] == 'INCOMPLETE'
    assert receipt['mandatory_coverage']['missing']
    assert not (output / 'rounds/0/front.trace.json').exists()


def test_stale_preservation_evidence_is_not_delivered_as_current(tmp_path):
    public, _ = make_public_fixture(tmp_path / 'benchmark')
    seen = []
    class InspectInitialRequest:
        def complete_json(self, request):
            seen.append(request)
            raise RuntimeError('Stop after inspecting the real initial request')
    output = tmp_path / 'run'
    repair_case(public, tmp_path, 'CASE-1', output, InspectInitialRequest(), len,
                config(), public_runner=DeliveryRunner(public, with_stale=True))
    assert len(seen) == 1
    text = json.dumps(asdict(seen[0]))
    assert 'CURRENT-FAIL' in text and 'OLD-PASS' not in text
    receipt = json.loads((output / 'rounds/0/public-evidence.delivery.json').read_text())
    assert receipt['status'] == 'COMPLETE'
    assert any(item['ref']['id'] == 'OLD-PASS' and 'STALE' in item['reason']
               for item in receipt['omitted'])
    assert all(item['ref']['snapshot_id'] == receipt['snapshot_id'] for item in receipt['selected'])


def test_required_review_failure_overflow_stays_unknown_without_review_dispatch(tmp_path):
    public, _ = make_public_fixture(tmp_path / 'benchmark')
    class ReviewOverflowRunner(DeliveryRunner):
        observations = 0
        def observe(self, workspace, output):
            self.oversized = self.observations > 0
            self.observations += 1
            return super().observe(workspace, output)
    class NeverReview:
        calls = 0
        def review(self, requirement, plan, observation):
            self.calls += 1
            raise AssertionError('Incomplete review evidence must not reach the reviewer')
    output = tmp_path / 'run'
    provider = RecordingPipeline(output / 'workspace/source/repo')
    reviewer = NeverReview()
    report = repair_case(public, tmp_path, 'CASE-1', output, provider, len,
                         config(max_review_evidence_tokens=64),
                         public_runner=ReviewOverflowRunner(public),
                         executor=TwoStepExecutor(), reviewer=reviewer)
    assert len(provider.requests) == 2, report
    assert reviewer.calls == 0
    receipt = json.loads((output / 'rounds/0/review-evidence.delivery.json').read_text())
    review = json.loads((output / 'rounds/0/public-review.json').read_text())
    assert receipt['status'] == 'INCOMPLETE' and receipt['mandatory_coverage']['missing']
    assert review['decision'] == 'UNKNOWN' and review['method'] == 'EVIDENCE_DELIVERY_INCOMPLETE'
    assert report['status'] != 'PUBLIC_REVIEW_COMPLETE'


def schema_overhead_counter(text):
    # A deterministic test tokenizer stresses a transport-only representation;
    # schema_in_prompt=False keeps this marker out of all message bodies.
    return len(text) + (200000 if text.startswith('{"type": "json_schema"') else 0)


@pytest.mark.parametrize('stage', ['initial', 'revision', 'execution'])
def test_every_stage_rejects_transport_schema_overhead_before_dispatch(tmp_path, stage):
    if stage == 'initial':
        _, _, method, source, workspace = prepared_case(tmp_path)
    elif stage == 'revision':
        method, source, trace = ready_front(tmp_path)
    else:
        source = tmp_path / 'source'
        source.mkdir()
        (source / 'templates').mkdir()
        (source / 'templates/serviceaccount.yaml').write_text('name: default\n')
        plan = bound_plan(source)
    port = OpenAICompatibleProvider('https://fixture.invalid/v1', 'CE_STAGE_FIXTURE_KEY',
                                    output_mode='json_schema', supports_json_schema=True)
    budget = budgeted(port, schema_overhead_counter)
    before = compute_snapshot(source).snapshot_id
    with patch('urllib.request.urlopen', side_effect=AssertionError('No admitted dispatch expected')) as transport:
        if stage == 'initial':
            result = run_front_recorded(method, workspace, budget, front_config(), schema_overhead_counter,
                                        directory=tmp_path / 'blocked-front')
            assert result.status == 'BUDGET_EXHAUSTED', result.errors
        elif stage == 'revision':
            with pytest.raises((BudgetExceeded, PlanRejected), match='[Cc]ontext|[Bb]udget'):
                revise_plan(trace, method, source, {'rationale': 'Recheck current requirement'},
                            budget, schema_overhead_counter, config())
        else:
            executor = ModelPlanExecutor(budget, schema_overhead_counter, config())
            with pytest.raises((BudgetExceeded, PlanRejected), match='[Cc]ontext|[Bb]udget'):
                executor.execute(plan, source, {'allowed_paths': ['templates/**']}, requirement='Repair')
    transport.assert_not_called()
    assert budget.calls == [] and budget.reserved_tokens == 0
    assert compute_snapshot(source).snapshot_id == before


def test_sidecar_binds_final_dispatched_requests_and_persisted_inputs_without_wire_change(tmp_path, monkeypatch):
    _, runner, method, source, workspace = prepared_case(tmp_path)
    observations = runner.observe(tmp_path / 'prepared', tmp_path / 'broken')['observations']
    delivery = select_observations_delivery(observations, method.requirement_text, len,
                                            snapshot_id=compute_snapshot(source).snapshot_id)
    pipeline = RecordingPipeline(source)
    class RecordingHTTPProvider(OpenAICompatibleProvider):
        def __init__(self):
            super().__init__('https://fixture.invalid/v1', 'CE_STAGE_FIXTURE_KEY')
            self.requests = []
        def complete_json(self, request):
            self.requests.append(request)
            return super().complete_json(request)
    port = RecordingHTTPProvider()
    monkeypatch.setenv('CE_STAGE_FIXTURE_KEY', 'stage-test-placeholder')
    ledger = tmp_path / 'calls.json'
    budget = budgeted(port, on_update=lambda calls: ledger.write_text(json.dumps(calls)))
    directory = tmp_path / 'front'
    actual_transport = []
    class Response:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps(self.body).encode()
    def transport(http_request, timeout):
        # Observe the production ledger at dispatch, before any response exists.
        call = json.loads(ledger.read_text())[-1]
        assert 'response_content' not in call
        assert call['transport_request']['body_sha256'] == hashlib.sha256(http_request.data).hexdigest()
        assert json.loads(http_request.data)['messages'] == list(port.requests[-1].messages)
        actual_transport.append(hashlib.sha256(http_request.data).hexdigest())
        response = pipeline.complete_json(port.requests[-1])
        return Response({'model': 'fixture', 'usage': {'prompt_tokens': 2, 'completion_tokens': 2},
                         'choices': [{'message': {'content': response.content}, 'finish_reason': 'stop'}]})
    with patch.object(port, 'prepare_request', wraps=port.prepare_request) as prepare, \
         patch('urllib.request.urlopen', side_effect=transport):
        trace = run_front_recorded(method, workspace, budget, front_config(), len,
                                   directory=directory, public_observations=delivery.records,
                                   evidence_delivery=delivery.receipt).to_wire()
    assert trace['status'] in {'READY', 'READY_PARTIAL'}, trace.get('errors')
    validate_run_trace(trace)
    assert len(actual_transport) == len(port.requests) == 2
    for request in port.requests:
        assert sum(call.args[0] is request for call in prepare.call_args_list) == 1
    receipt = json.loads((directory / 'front.delivery.json').read_text())
    assert receipt['schema_version'] == 'ce.front_delivery.v1'
    assert receipt['model_call_range'] == [0, 2]
    assert receipt['evidence_delivery_receipt_digest'] == delivery.receipt['receipt_digest']
    assert [r['request_digest'] for r in receipt['requests']] == [
        canonical_digest(asdict(request)) for request in port.requests]
    assert [r['transport_body_sha256'] for r in receipt['requests']] == actual_transport
    assert [r['request_digest'] for r in receipt['requests']] == [c['request_digest'] for c in budget.calls]
    artifacts = receipt['artifacts']
    for name in ['front.method_input.json', 'front.config.json', 'front.public_observations.json',
                 'front.execution_feedback.json', 'front.replay.json']:
        expected = artifacts[name]
        if isinstance(expected, dict):
            expected = expected['sha256']
        assert expected == file_sha(directory / name)
    expected_input = {key: json.loads((directory / name).read_text()) for key, name in {
        'method_input': 'front.method_input.json', 'config': 'front.config.json',
        'observations': 'front.public_observations.json', 'feedback': 'front.execution_feedback.json'}.items()}
    assert receipt['effective_front_input_sha256'] == canonical_digest(expected_input)
    assert all(key not in trace for key in ('delivery', 'delivery_receipt', 'request_quote'))
