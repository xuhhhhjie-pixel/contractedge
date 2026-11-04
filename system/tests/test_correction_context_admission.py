"""A known invalid response does not authorize dropping context for correction."""
from pathlib import Path

from contractedge.dataset.benchmark import file_sha
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case
from tests.test_benchmark_current import make_public_fixture
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_repair_execution import PublicLoopRunner


def test_execution_correction_overflow_is_budget_stop_without_new_call_or_correction_charge(tmp_path):
    public, _ = make_public_fixture(tmp_path / 'benchmark')

    class Provider(PipelineFixtureProvider):
        executions = 0

        def complete_json(self, request):
            if request.prompt_version == 'topomen-source-execution.v1':
                self.executions += 1
                return ModelResponse('invalid ' * 20000, {'finish_reason': 'stop'})
            return super().complete_json(request)

    provider = Provider()

    class Runner(PublicLoopRunner):
        def prepare(self, *args):
            record = super().prepare(*args)
            path = Path(record['source_root']) / 'templates/serviceaccount.yaml'
            provider._snapshot_digests = {'templates/serviceaccount.yaml': file_sha(path)}
            return record

    result = repair_case(public, tmp_path, 'CASE-1', tmp_path / 'run', provider,
        lambda text: len(text.split()), dict(model='fixture', max_context_tokens=12000,
            max_output_tokens=2048, max_rounds=2, execution_format_retry_in_round=True,
            max_format_retries=1), public_runner=Runner(public))
    assert result['status'] == 'BUDGET_EXHAUSTED', result['errors']
    assert result['primary_failure']['failure_class'] == 'infrastructure'
    assert result['primary_failure']['dispatch_state'] == 'not_sent'
    assert result['execution_format_corrections_used'] == 0
    assert provider.executions == 1
