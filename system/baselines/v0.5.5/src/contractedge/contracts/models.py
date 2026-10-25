from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


class ContractError(ValueError):
    """Raised when a wire object is malformed or violates identity constraints."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path or '<root>'}: {message}")


@dataclass(frozen=True)
class MethodInput:
    schema_version: str
    case_id: str
    release_id: str
    original_broken_revision: str
    archive_path: str
    archive_sha256: str
    source_root: str
    initial_snapshot_id: str
    requirement_text: str
    public_observable_text: str | None
    scope_hints: tuple[Mapping[str, Any], ...]
    public_evidence: tuple[Mapping[str, Any], ...]
    declared_artifacts: tuple[Mapping[str, Any], ...]
    input_digest: str


@dataclass(frozen=True)
class RunContext:
    run_id: str
    case_id: str
    round_id: int
    snapshot_id: str
    parent_snapshot_id: str | None
    input_digest: str
    prompt_version: str = "contractedge-front-half.v1_1"
    model_config: Mapping[str, Any] = field(default_factory=dict)
    retrieval_config: Mapping[str, Any] = field(default_factory=dict)
    budget: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    source_root: str
    file_digests: Mapping[str, str]


@dataclass(frozen=True)
class SemanticUnit:
    id: str
    role: str
    kind: str
    semantic_role_class: str
    existence: str
    locator_refs: tuple[str, ...]
    requirement_refs: tuple[str, ...]


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[ValidationIssue, ...] = ()
    value: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class DiagnosisPacket:
    schema_version: str
    run_id: str
    snapshot_id: str
    input_digest: str
    requirement_spec: Mapping[str, Any]
    obligation_atoms: tuple[Mapping[str, Any], ...]
    units: tuple[SemanticUnit, ...]
    expected_relations: tuple[Mapping[str, Any], ...]
    violations: tuple[Mapping[str, Any], ...]
    outcome_coverage: tuple[Mapping[str, Any], ...]
    evidence_refs: tuple[Mapping[str, Any], ...]
    evidence_gaps: tuple[Mapping[str, Any], ...]
    status: str
    requirement_horizon: tuple[str, ...]
    observed_boundary: str
    inferred_causal_path: tuple[str, ...]
    unverified_outcomes: tuple[str, ...]
    raw: Mapping[str, Any]
