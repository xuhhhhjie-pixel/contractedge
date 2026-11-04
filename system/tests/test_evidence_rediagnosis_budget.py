"""The initial-only cap cannot remove already acquired follow-up evidence."""
import hashlib

from contractedge.orchestration.runner import run_front_half
from contractedge.tokenizers import utf8_byte_token_bound
from tests.test_orchestration import OneExtensionProvider
from tests.test_planning_evidence_extension import PlanningGapProvider, front_fixture


class CapturingGapProvider(PlanningGapProvider):
    def __init__(self, template):
        super().__init__()
        self.requests = []
        self._snapshot_digests = {"templates/serviceaccount.yaml": hashlib.sha256(template.read_bytes()).hexdigest()}

    def complete_json(self, request):
        self.requests.append(request)
        return super().complete_json(request)


class CapturingDiagnosisGapProvider(OneExtensionProvider):
    def __init__(self, template):
        super().__init__()
        self.requests = []
        self._snapshot_digests = {"templates/serviceaccount.yaml": hashlib.sha256(template.read_bytes()).hexdigest()}

    def complete_json(self, request):
        self.requests.append(request)
        return super().complete_json(request)


def test_evidence_extension_uses_total_context_and_preserves_initial_cap(tmp_path):
    value, template = front_fixture(tmp_path)
    config = dict(model={"model": "fixture"}, max_context_tokens=220000,
        max_output_tokens=1024, max_total_tokens=4000000, gap_role_delivery_enabled=True)
    control = CapturingGapProvider(template)
    baseline = run_front_half(value, tmp_path, control, config, utf8_byte_token_bound)
    assert baseline.status == "READY", baseline.errors
    diag = [r for r in control.requests if str(r.prompt_version).startswith("diagnosis.")]
    counts = [r.metadata["context_audit"][-1]["request_tokens"] for r in diag]
    assert len(counts) == 2 and counts[1] > counts[0]
    initial_cap = (counts[0] + counts[1]) // 2

    provider = CapturingGapProvider(template)
    trace = run_front_half(value, tmp_path, provider,
        {**config, "initial_diagnosis_context_tokens": initial_cap}, utf8_byte_token_bound)
    assert trace.status == "READY", trace.errors
    diag = [r for r in provider.requests if str(r.prompt_version).startswith("diagnosis.")]
    first, followup = [r.metadata["context_audit"][-1] for r in diag]
    assert first["context_stage"] == "INITIAL_DIAGNOSIS"
    assert first["request_tokens"] <= initial_cap
    assert followup["context_stage"] == "EVIDENCE_REDIAGNOSIS"
    assert followup["request_tokens"] > initial_cap
    assert followup["request_tokens"] + diag[-1].max_output_tokens <= config["max_context_tokens"]


def test_followup_cannot_reset_existing_model_call_limit(tmp_path):
    value, template = front_fixture(tmp_path)
    provider = CapturingGapProvider(template)
    trace = run_front_half(value, tmp_path, provider,
        dict(model={"model": "fixture"}, max_context_tokens=220000,
            max_output_tokens=1024, max_total_tokens=4000000,
            max_model_calls=2, gap_role_delivery_enabled=True), utf8_byte_token_bound)
    assert trace.status == "BUDGET_EXHAUSTED"
    assert len(provider.requests) == 2


def test_followup_reserves_output_within_model_window(tmp_path):
    value, template = front_fixture(tmp_path)
    config = dict(model={"model": "fixture"}, max_context_tokens=220000,
        max_output_tokens=1024, max_total_tokens=4000000, gap_role_delivery_enabled=True)
    # Request the extension directly from diagnosis, so a larger planning
    # schema cannot stop the run before the boundary under test.
    control = CapturingDiagnosisGapProvider(template)
    baseline = run_front_half(value, tmp_path, control, config, utf8_byte_token_bound)
    assert baseline.status == "READY", baseline.errors
    diag = [r for r in control.requests if str(r.prompt_version).startswith("diagnosis.")]
    first, second = [r.metadata["context_audit"][-1]["request_tokens"] for r in diag]
    assert second - first > 128
    provider = CapturingDiagnosisGapProvider(template)
    trace = run_front_half(value, tmp_path, provider,
        {**config, "model_context_tokens": first + 1024 + 128}, utf8_byte_token_bound)
    assert trace.status in {"BUDGET_EXHAUSTED", "EVIDENCE_GAP"}, trace.errors
    assert len(provider.requests) == 1
