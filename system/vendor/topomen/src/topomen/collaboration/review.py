from __future__ import annotations

from pydantic import BaseModel, Field

SYSTEM_DEPENDENCY_ERROR_TYPES = {
    "missing_build_tool",
    "missing_compiler",
    "missing_shared_library",
    "missing_system_binary",
    "missing_system_package",
}
PYTHON_DEPENDENCY_ERROR_TYPES = {"missing_python_module", "package_resolution"}


class VerificationDecision(BaseModel):
    accepted: bool = True
    reason: str = "accepted"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    expected_node_id: str = ""
    observed_node_id: str = ""


class CriticDecision(BaseModel):
    accepted: bool = True
    reason: str = "accepted"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class FailureVerifier:
    def verify(
        self,
        *,
        stage: str,
        error_type: str,
        observed_node_id: str,
    ) -> VerificationDecision:
        expected = self._expected_node(stage=stage, error_type=error_type)
        if not observed_node_id:
            return VerificationDecision(
                accepted=False,
                reason="missing_attribution_node",
                confidence=0.55,
                expected_node_id=expected,
                observed_node_id=observed_node_id,
            )
        if expected and observed_node_id != expected:
            return VerificationDecision(
                accepted=False,
                reason="attribution_node_mismatch",
                confidence=0.9,
                expected_node_id=expected,
                observed_node_id=observed_node_id,
            )
        return VerificationDecision(
            accepted=True,
            reason="attribution_consistent",
            confidence=0.85 if expected else 0.7,
            expected_node_id=expected,
            observed_node_id=observed_node_id,
        )

    def _expected_node(self, *, stage: str, error_type: str) -> str:
        if stage == "guard":
            return "dockerfile_generation"
        if error_type in SYSTEM_DEPENDENCY_ERROR_TYPES:
            return "system_dependency_plan"
        if error_type in PYTHON_DEPENDENCY_ERROR_TYPES:
            return "python_dependency_plan"
        if error_type == "dockerfile_syntax_error":
            return "dockerfile_generation"
        if error_type == "build_infra_error":
            return "build_validation"
        return ""


class MemoryWriteCritic:
    def review(
        self,
        *,
        allow_store: bool,
        fix_action: str,
        success: bool,
        duplicate_count: int,
    ) -> CriticDecision:
        if not allow_store:
            return CriticDecision(accepted=False, reason="noise_or_unreliable_failure", confidence=0.95)
        if not success:
            return CriticDecision(accepted=False, reason="unsuccessful_fix_not_persisted", confidence=0.8)
        if not fix_action or fix_action.strip() in {"(no explicit fix command captured)"}:
            return CriticDecision(accepted=False, reason="empty_fix_action", confidence=0.9)
        if duplicate_count > 1:
            return CriticDecision(accepted=False, reason="duplicate_fix_action_signature", confidence=0.85)
        return CriticDecision(accepted=True, reason="high_quality_memory_write", confidence=0.85)
