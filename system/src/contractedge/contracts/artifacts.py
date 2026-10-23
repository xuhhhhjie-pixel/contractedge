"""Content-bound references; these objects do not assert semantic correctness."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
import re
from typing import Any, Mapping


def relative_path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError("Artifact path must be a restricted relative POSIX path")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value
            or any(part in {".", ".."} for part in path.parts)):
        raise ValueError("Artifact path must not be absolute, traversing or ambiguous")
    return value


def digest(value: str | None, *, optional=False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Expected a lowercase SHA256 digest")


def identity(value: str) -> None:
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(char) < 32 for char in value)):
        raise ValueError("Expected a nonempty bounded identity")


def index(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative integer index")


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    relative_path: str
    sha256: str
    snapshot_id: str | None
    input_digest: str
    schema_version: str = field(default="ce.artifact_ref.v1", init=False)

    def __post_init__(self):
        identity(self.kind)
        relative_path(self.relative_path)
        digest(self.sha256)
        digest(self.input_digest)
        if self.snapshot_id is not None:
            identity(self.snapshot_id)


@dataclass(frozen=True)
class CallRef:
    run_id: str
    segment_index: int
    absolute_call_index: int
    request_digest: str | None
    transport_digest: str | None
    ledger_segment_digest: str | None = None
    ledger_segment_path: str | None = None
    ledger_revision: int | None = None
    schema_version: str = field(default="ce.call_ref.v1", init=False)

    def __post_init__(self):
        identity(self.run_id)
        index(self.segment_index)
        index(self.absolute_call_index)
        digest(self.request_digest, optional=True)
        digest(self.transport_digest, optional=True)
        bound = (self.ledger_segment_digest, self.ledger_segment_path, self.ledger_revision)
        if any(value is not None for value in bound):
            if any(value is None for value in bound):
                raise ValueError("Sealed CallRef requires digest, path and revision together")
            digest(self.ledger_segment_digest)
            relative_path(self.ledger_segment_path)
            index(self.ledger_revision)


@dataclass(frozen=True)
class LedgerSeal:
    run_id: str
    input_digest: str
    segment_index: int
    revision: int
    first_call_index: int
    artifact_ref: ArtifactRef
    call_refs: tuple[CallRef, ...]
    schema_version: str = field(default="ce.ledger_seal.v1", init=False)

    def __post_init__(self):
        identity(self.run_id)
        digest(self.input_digest)
        for value in (self.segment_index, self.revision, self.first_call_index):
            index(value)


@dataclass(frozen=True)
class StageReceipt:
    run_id: str
    input_digest: str
    stage: str
    attempt_id: str
    snapshot_id: str | None
    status: str
    input_refs: tuple[ArtifactRef, ...]
    output_refs: tuple[ArtifactRef, ...]
    projection_refs: tuple[ArtifactRef, ...]
    call_refs: tuple[CallRef, ...]
    issues: tuple[Mapping[str, Any], ...]
    revision: int
    relative_path: str
    receipt_digest: str | None = None
    schema_version: str = field(default="ce.stage_receipt.v1", init=False)

    def __post_init__(self):
        for value in (self.run_id, self.stage, self.attempt_id, self.status):
            identity(value)
        if self.snapshot_id is not None:
            identity(self.snapshot_id)
        digest(self.input_digest)
        digest(self.receipt_digest, optional=True)
        index(self.revision)
        relative_path(self.relative_path)
