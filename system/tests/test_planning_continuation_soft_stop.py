"""Failed local acquisition retains the existing planning correction owner."""
import hashlib
import json
from unittest.mock import patch

import pytest

from contractedge.orchestration import runner
from tests.test_planning_correction_accounting import SequenceProvider
from tests.test_planning_evidence_extension import front_fixture
from tests.test_planning_evidence_extension import PlanningGapProvider
from contractedge.model.provider import ModelResponse


def run(root, steps):
    method, source = front_fixture(root)
    provider = SequenceProvider(steps)
    provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(source.read_bytes()).hexdigest()}
    config = dict(model={'model': 'fixture'}, max_context_tokens=24000, max_output_tokens=1024,
        max_total_tokens=100000, max_model_calls=12, max_format_retries=1, max_evidence_extensions=2)
    trace = runner.run_front_half(method, root, provider, config, lambda s: len(s.split()))
    return trace, provider


@pytest.mark.parametrize('failure', ['no_evidence', 'selection_error'])
def test_two_local_failures_do_not_mint_free_plan_retries(tmp_path, failure):
    if failure == 'no_evidence':
        with patch.object(runner, 'extract_gap_extension_units', return_value=([], {})) as lookup, \
                patch.object(runner, 'collect_absence_search_evidence', return_value=([], {})):
            trace, provider = run(tmp_path, ['invalid_gap', 'invalid_gap', 'valid'])
    else:
        with patch.object(runner, 'extend_once', side_effect=ValueError('capacity')) as lookup:
            trace, provider = run(tmp_path, ['invalid_gap', 'invalid_gap', 'valid'])
    assert trace.status == 'INVALID_PLAN', trace.errors
    assert any('PRESERVE_CONSTRAINT_MISSING' in error for error in trace.errors)
    assert provider.plan_calls == 2 and provider.diagnosis_calls == 1
    assert lookup.call_count == 2
    assert not provider.requests[0]['validation_feedback']
    assert provider.requests[1]['validation_feedback']


def test_failed_lookup_cannot_reset_a_previously_used_plan_correction(tmp_path):
    with patch.object(runner, 'extract_gap_extension_units', return_value=([], {})), \
            patch.object(runner, 'collect_absence_search_evidence', return_value=([], {})):
        trace, provider = run(tmp_path, ['invalid', 'invalid_gap', 'valid'])
    assert trace.status == 'INVALID_PLAN', trace.errors
    assert provider.plan_calls == 2 and provider.diagnosis_calls == 1
    assert provider.requests[1]['validation_feedback']


def test_second_selection_failure_after_real_rediagnosis_keeps_plan_errors(tmp_path):
    original = runner.extend_once
    count = [0]
    def select(*args, **kwargs):
        count[0] += 1
        if count[0] == 2:
            raise ValueError('second acquisition capacity')
        return original(*args, **kwargs)
    with patch.object(runner, 'extend_once', side_effect=select):
        trace, provider = run(tmp_path, ['gap', 'invalid_gap', 'invalid_gap'])
    assert trace.status == 'INVALID_PLAN', trace.errors
    assert provider.diagnosis_calls == 2 and provider.plan_calls == 3
    assert sum(bool(body['validation_feedback']) for body in provider.requests) == 1
    assert len(trace.model_calls) == 5


@pytest.mark.parametrize('stop', ['budget', 'delivery'])
def test_withdrawn_diagnosis_hard_stop_cannot_export_old_plan(tmp_path, monkeypatch, stop):
    method, source = front_fixture(tmp_path)
    class Withdraw(PlanningGapProvider):
        def complete_json(self, request):
            response = super().complete_json(request)
            if request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls == 2:
                body = json.loads(response.content)
                body.update(status='EVIDENCE_GAP', violations=[], unverified_outcomes=['O1'],
                    evidence_gaps=[dict(id='G2', summary='Old claim withdrawn; check a different file.',
                        locator_hint={'path': 'templates/another-optional.yaml'})],
                    outcome_coverage=[dict(outcome_id='O1', repair_supported=[], preserve_required=[],
                        preserve_current_satisfaction=[], unresolved=['O1'])])
                return ModelResponse(json.dumps(body), {})
            return response
    provider = Withdraw()
    provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(source.read_bytes()).hexdigest()}
    config = dict(model={'model': 'fixture'}, max_context_tokens=24000, max_output_tokens=1024,
        max_total_tokens=100000, max_model_calls=3 if stop == 'budget' else 12,
        max_format_retries=1, max_evidence_extensions=2, gap_role_delivery_enabled=True)
    if stop == 'delivery':
        from contractedge.retrieval import gap_delivery
        original = gap_delivery.audit_gap_delivery
        def missing_final_source(*args, **kwargs):
            if provider.diagnosis_calls >= 2:
                return {'status': 'INCOMPLETE', 'reason': 'Required second-stage source was not delivered'}
            return original(*args, **kwargs)
        monkeypatch.setattr(gap_delivery, 'audit_gap_delivery', missing_final_source)
    trace = runner.run_front_half(method, tmp_path, provider, config, lambda s: len(s.split()))
    assert trace.status == ('BUDGET_EXHAUSTED' if stop == 'budget' else 'EVIDENCE_DELIVERY_INCOMPLETE')
    if stop == 'delivery':
        assert any('REQUIRED_SOURCE_NOT_DELIVERED' in error for error in trace.errors)
    assert provider.diagnosis_calls == 2 and provider.plan_calls == 1
    assert trace.diagnosis['violations'] == []
    assert trace.changeset is None
