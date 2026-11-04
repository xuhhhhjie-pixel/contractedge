"""Process diagnostics never grant another call when cleanup is unconfirmed."""
import json
from pathlib import Path

import pytest

from contractedge.contracts.failures import make_failure
from contractedge.dataset.benchmark import file_sha
from contractedge.orchestration.repair import repair_case
from tests.test_benchmark_current import make_public_fixture
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_repair_execution import PublicLoopRunner, TwoStepExecutor


@pytest.mark.parametrize('failed_observation', [1, 2])
def test_unconfirmed_cleanup_stops_before_next_model_or_review_and_keeps_process_failure(tmp_path, failed_observation):
    public, _ = make_public_fixture(tmp_path / 'benchmark')
    provider = PipelineFixtureProvider()

    class Runner(PublicLoopRunner):
        observations = 0

        def prepare(self, *args):
            record = super().prepare(*args)
            path = Path(record['source_root']) / 'templates/serviceaccount.yaml'
            self.initial_content = path.read_text()
            provider._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(path)}
            return record

        def observe(self, workspace, output):
            report = super().observe(workspace, output)
            self.observations += 1
            if self.observations != failed_observation:
                return report
            primary = make_failure('public_execution', 'EXECUTION_FAILED', failure_class='execution',
                snapshot_id=report['snapshot_id'], message='Source compilation failed')
            cleanup = make_failure('cleanup', 'CLEANUP_UNCONFIRMED', failure_class='infrastructure',
                snapshot_id=report['snapshot_id'], message='Container removal was not confirmed', secondary=True)
            return {**report, 'status': 'EXECUTION_FAILED', 'process_status': 'EXECUTION_FAILED',
                'runtime_cleanup': {'status': 'FAILED'},
                'primary_failure': primary, 'cleanup_failure': cleanup,
                'failure_records': [primary, cleanup]}

    class Reviewer:
        calls = 0

        def review(self, *args):
            self.calls += 1
            raise AssertionError('Cleanup failure cannot be sent to review')

    runner, reviewer, executor = Runner(public), Reviewer(), TwoStepExecutor()
    out = tmp_path / 'run'
    report = repair_case(public, tmp_path, 'CASE-1', out, provider,
        lambda text: len(text.split()), dict(model='fixture', max_context_tokens=12000,
            max_output_tokens=2048, max_rounds=2),
        public_runner=runner, executor=executor, reviewer=reviewer)
    assert report['status'] == 'INFRASTRUCTURE_ERROR', report['errors']
    assert runner.observations == failed_observation
    assert reviewer.calls == 0
    assert executor.calls == failed_observation - 1
    assert report['primary_failure']['code'] == 'EXECUTION_FAILED'
    assert any(f['code'] == 'CLEANUP_UNCONFIRMED' for f in report['failure_records'])
    source = Path(json.loads((out / 'workspace/workspace.json').read_text())['source_root'])
    assert (source / 'templates/serviceaccount.yaml').read_text() == runner.initial_content
    if failed_observation == 1:
        assert report['budget']['model_calls'] == 0
    else:
        assert (out / 'emergency-rollback.json').is_file()
