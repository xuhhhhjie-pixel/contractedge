"""Public synthetic boundary counterexamples; no model service or hidden data."""
import json
from unittest.mock import patch

from contractedge.contracts.status import project_statuses
from contractedge.orchestration.runner import run_front_half
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import front_fixture


CONFIG = dict(model='fixture', max_context_tokens=24000, max_output_tokens=1024,
              max_total_tokens=100000, max_format_retries=1,
              max_evidence_extensions=2, gap_role_delivery_enabled=True)


def test_unsent_delivery_failure_is_not_a_model_evidence_gap(tmp_path):
    method, source = front_fixture(tmp_path)
    with patch('contractedge.retrieval.gap_delivery.audit_gap_delivery', return_value={
            'status': 'INCOMPLETE', 'reason': 'Required body absent in serialized request'}):
        trace = run_front_half(method, tmp_path, PipelineFixtureProvider(), CONFIG, len)
    assert not trace.model_calls
    assert trace.status == project_statuses(trace.to_wire())['method_status'] == 'EVIDENCE_DELIVERY_INCOMPLETE'
    assert trace.primary_failure['dispatch_state'] == 'not_sent'


def test_source_capacity_stop_keeps_actual_dimension_and_amount(tmp_path):
    method, source = front_fixture(tmp_path)
    trace = run_front_half(method, tmp_path, PipelineFixtureProvider(),
        {**CONFIG, 'max_execution_support_bytes': 1}, lambda t: len(t.split()))
    assert not trace.model_calls
    assert trace.status == 'BUDGET_EXHAUSTED'
    audits = trace.candidate_set.context_audit
    hit = next(r['admission_failure'] for r in reversed(audits) if 'admission_failure' in r)
    assert hit['dimension'] == 'execution_support_bytes'
    assert hit['actual'] > hit['limit'] == 1
    assert hit['message'] in str(trace.errors)


def test_exception_after_selection_preserves_retrieval_history(tmp_path):
    method, source = front_fixture(tmp_path)
    class Broken(PipelineFixtureProvider):
        def complete_json(self, request):
            raise RuntimeError('synthetic provider failure after selection')
    trace = run_front_half(method, tmp_path, Broken(), CONFIG, lambda t: len(t.split()))
    assert trace.errors and trace.retrieval_rounds
    assert trace.retrieval_rounds[0]['round_id'] == 1


def test_actual_benchmark_runner_enables_incumbent_protection(tmp_path, monkeypatch):
    from contractedge.orchestration import repair
    from contractedge.execution.public import BenchmarkPublicRunner
    from tests.test_candidate_regression_guard import test_real_loop_exports_incumbent_and_its_feedback_after_regression
    original = repair.repair_case
    def real_entry(public, tools, case, out, provider, counter, config, **kwargs):
        # Exercise the production runner capability and real repair loop; public
        # external commands are replaced with deterministic public fixture I/O.
        delegate = kwargs['public_runner']
        tool_root = tmp_path / 'public-tools'
        tool_root.mkdir()
        for name in ('prepare_case.py', 'collect_patch.py', 'launch_public_case.py'):
            (tool_root / name).write_text('# synthetic public tool\n')
        runner = BenchmarkPublicRunner(public, tool_root, lock_root=tmp_path / 'locks')
        for name in ('prepare', 'observe', 'export'):
            setattr(runner, name, getattr(delegate, name))
        kwargs['public_runner'] = runner
        return original(public, tools, case, out, provider, counter,
                        {k: v for k, v in config.items() if k != 'candidate_guard_enabled'}, **kwargs)
    monkeypatch.setattr(repair, 'repair_case', real_entry)
    test_real_loop_exports_incumbent_and_its_feedback_after_regression(tmp_path, False)
