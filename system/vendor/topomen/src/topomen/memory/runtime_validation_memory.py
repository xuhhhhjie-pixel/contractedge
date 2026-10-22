from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field

from topomen.memory.pattern_memory import (
    CROSS_PROJECT_PREFILL_MIN_PROJECTS,
    HIGH_CONFIDENCE_PREFILL_THRESHOLD,
    memory_staleness_score,
)

RUNTIME_VALIDATION_MEMORY_WRITE_LOCK = Lock()
RUNTIME_VALIDATION_MEMORY_INDEX_LOCK = Lock()
RUNTIME_VALIDATION_MEMORY_INDEX_CACHE: dict[Path, tuple[tuple[int, int], list[_IndexedRuntimeValidationRecord]]] = {}


class RuntimeValidationRecord(BaseModel):
    runtime_id: str = ""
    signals: list[str] = Field(default_factory=list)
    mode: str = "noop"
    container_command: list[str] = Field(default_factory=list)
    command_source: str = "runtime_validation_memory"
    selected_target: str = ""
    reason: str = ""
    expected_success_signal: str = "returncode == 0"
    support_count: int = Field(default=0, ge=0)
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    unique_project_count: int = Field(default=0, ge=0)
    source_projects: list[str] = Field(default_factory=list)
    source_project_keys: list[str] = Field(default_factory=list)
    failed_project_keys: list[str] = Field(default_factory=list)
    negative_evidence: list[str] = Field(default_factory=list)
    first_verified_at: str = ""
    last_verified_at: str = ""
    verification_count: int = Field(default=0, ge=0)
    staleness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    revalidation_status: str = "fresh"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RuntimeValidationMatch(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    record: RuntimeValidationRecord
    matched_signals: list[str] = Field(default_factory=list)
    prefill_ready: bool = False
    blocked_reason: str = ""


@dataclass(frozen=True)
class _IndexedRuntimeValidationRecord:
    record: RuntimeValidationRecord
    signals: frozenset[str]
    source_project_names: frozenset[str]
    source_project_keys: frozenset[str]


class RuntimeValidationMemory:
    def __init__(self, path: str | Path, *, create: bool = True):
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if create and not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def load_records(self) -> list[RuntimeValidationRecord]:
        if not self.path.exists():
            return []
        records: list[RuntimeValidationRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = RuntimeValidationRecord.model_validate_json(line)
            if not record.runtime_id:
                record = record.model_copy(update={"runtime_id": runtime_validation_record_id(record)})
            records.append(record)
        return records

    def write_records(self, records: list[RuntimeValidationRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with RUNTIME_VALIDATION_MEMORY_WRITE_LOCK:
            with self.path.open("w", encoding="utf-8") as handle:
                for record in records:
                    if not record.runtime_id:
                        record = record.model_copy(update={"runtime_id": runtime_validation_record_id(record)})
                    handle.write(record.model_dump_json())
                    handle.write("\n")
        with RUNTIME_VALIDATION_MEMORY_INDEX_LOCK:
            RUNTIME_VALIDATION_MEMORY_INDEX_CACHE.pop(_memory_path_key(self.path), None)

    def retrieve(
        self,
        signals: list[str],
        *,
        project_name: str = "",
        project_key: str = "",
        top_k: int = 3,
        min_confidence: float = 0.45,
        allow_same_project_memory: bool = False,
    ) -> list[RuntimeValidationMatch]:
        return match_runtime_validation_records(
            [entry.record for entry in self._load_index()],
            signals,
            project_name=project_name,
            project_key=project_key,
            top_k=top_k,
            min_confidence=min_confidence,
            allow_same_project_memory=allow_same_project_memory,
        )

    def _load_index(self) -> list[_IndexedRuntimeValidationRecord]:
        path_key = _memory_path_key(self.path)
        signature = _file_signature(self.path)
        with RUNTIME_VALIDATION_MEMORY_INDEX_LOCK:
            cached = RUNTIME_VALIDATION_MEMORY_INDEX_CACHE.get(path_key)
            if cached is not None and cached[0] == signature:
                return cached[1]
            indexed = [_indexed_record(record) for record in self.load_records()]
            RUNTIME_VALIDATION_MEMORY_INDEX_CACHE[path_key] = (signature, indexed)
            return indexed


def match_runtime_validation_records(
    records: list[RuntimeValidationRecord],
    signals: list[str],
    *,
    project_name: str = "",
    project_key: str = "",
    top_k: int = 3,
    min_confidence: float = 0.45,
    allow_same_project_memory: bool = False,
) -> list[RuntimeValidationMatch]:
    signal_set = _expand_signals(signals)
    current_project = normalize_runtime_signal(project_name)
    current_project_key = normalize_runtime_signal(project_key)
    matches: list[RuntimeValidationMatch] = []
    for record in records:
        indexed = _indexed_record(record)
        effective_confidence = effective_runtime_validation_confidence(record)
        if effective_confidence < min_confidence:
            continue
        if not allow_same_project_memory:
            if current_project_key and current_project_key in indexed.source_project_keys:
                continue
            if current_project and current_project in indexed.source_project_names:
                continue
        overlap = sorted(signal_set & indexed.signals)
        if not overlap:
            continue
        coverage = len(overlap) / max(len(indexed.signals), 1)
        diversity_bonus = min(_effective_unique_project_count(record), 4) * 0.015
        negative_penalty = 0.12 if _has_negative_evidence(record) else 0.0
        score = max(0.0, min(1.0, effective_confidence * 0.78 + coverage * 0.18 + diversity_bonus - negative_penalty))
        ready, reason = runtime_validation_prefill_readiness(record)
        matches.append(
            RuntimeValidationMatch(
                score=round(score, 4),
                record=record,
                matched_signals=overlap,
                prefill_ready=ready,
                blocked_reason=reason,
            )
        )
    matches.sort(
        key=lambda item: (
            item.prefill_ready,
            item.score,
            _effective_unique_project_count(item.record),
            effective_runtime_validation_confidence(item.record),
        ),
        reverse=True,
    )
    return matches[:top_k]


def runtime_validation_match_payload(matches: list[RuntimeValidationMatch]) -> list[dict[str, object]]:
    return [
        {
            "score": match.score,
            "runtime_id": match.record.runtime_id,
            "mode": match.record.mode,
            "container_command": match.record.container_command,
            "command_source": match.record.command_source,
            "selected_target": match.record.selected_target,
            "signals": match.record.signals,
            "matched_signals": match.matched_signals,
            "confidence": match.record.confidence,
            "effective_confidence": effective_runtime_validation_confidence(match.record),
            "support_count": match.record.support_count,
            "unique_project_count": _effective_unique_project_count(match.record),
            "success_rate": match.record.success_rate,
            "failed_project_keys": match.record.failed_project_keys,
            "negative_evidence": match.record.negative_evidence,
            "prefill_ready": match.prefill_ready,
            "blocked_reason": match.blocked_reason,
            "staleness_score": _effective_staleness_score(match.record),
            "revalidation_status": match.record.revalidation_status,
            "last_verified_at": _effective_last_verified_at(match.record),
        }
        for match in matches
    ]


def runtime_validation_record_id(record: RuntimeValidationRecord) -> str:
    payload = json.dumps(
        {
            "mode": record.mode,
            "container_command": record.container_command,
            "selected_target": record.selected_target,
            "signals": sorted(normalize_runtime_signal(signal) for signal in record.signals),
        },
        sort_keys=True,
    )
    return f"rvm-{hashlib.sha1(payload.encode()).hexdigest()[:12]}"


def runtime_validation_prefill_readiness(record: RuntimeValidationRecord) -> tuple[bool, str]:
    staleness = _effective_staleness_score(record)
    status = (record.revalidation_status or "").strip().lower()
    if status in {"expired", "failed", "blocked"} or staleness >= 1.0:
        return False, "stale_memory"
    if effective_runtime_validation_confidence(record) < HIGH_CONFIDENCE_PREFILL_THRESHOLD:
        return False, "low_confidence"
    if _effective_unique_project_count(record) < CROSS_PROJECT_PREFILL_MIN_PROJECTS:
        return False, "insufficient_cross_project_support"
    if _has_negative_evidence(record):
        return False, "negative_evidence"
    if not record.container_command:
        return False, "missing_command"
    return True, ""


def effective_runtime_validation_confidence(record: RuntimeValidationRecord) -> float:
    staleness = _effective_staleness_score(record)
    status = (record.revalidation_status or "").strip().lower()
    status_penalty = 0.0
    if status in {"needs_revalidation", "stale"}:
        status_penalty = 0.08
    elif status in {"expired", "failed", "blocked"}:
        status_penalty = 0.35
    return round(max(0.0, min(1.0, record.confidence - staleness * 0.18 - status_penalty)), 4)


def infer_runtime_validation_signals(
    *,
    imported_modules: list[str],
    python_packages: list[str],
    detected_files: list[str],
    entry_points: list[str],
    external_commands: list[str],
) -> list[str]:
    signals: list[str] = []
    signals.extend(f"import:{normalize_runtime_signal(item)}" for item in imported_modules)
    signals.extend(f"dep:{normalize_runtime_signal(item)}" for item in python_packages)
    signals.extend(f"entry:{normalize_runtime_signal(item)}" for item in entry_points)
    signals.extend(f"external:{normalize_runtime_signal(item)}" for item in external_commands)
    for file_path in detected_files:
        path = str(file_path).replace("\\", "/")
        name = Path(path).name.lower()
        if name in {"main.py", "app.py", "server.py", "cli.py"}:
            signals.append(f"file:{name}")
        if path.startswith("tests/") or "/tests/" in path or name.startswith("test_"):
            signals.append("layout:tests")
        if name == "pyproject.toml":
            signals.append("file:pyproject.toml")
    if any(normalize_runtime_signal(item) in {"flask", "fastapi", "django", "uvicorn"} for item in [*imported_modules, *python_packages]):
        signals.append("layout:web-service")
    return _dedupe(signals)


def normalize_runtime_signal(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    return re.sub(r"[^a-z0-9:./+-]+", "-", normalized).strip(".-")


def _expand_signals(signals: list[str]) -> set[str]:
    expanded: set[str] = set()
    for signal in signals:
        normalized = normalize_runtime_signal(signal)
        if not normalized:
            continue
        expanded.add(normalized)
        if ":" in normalized:
            _, value = normalized.split(":", 1)
            if value:
                expanded.add(value)
    return expanded


def _indexed_record(record: RuntimeValidationRecord) -> _IndexedRuntimeValidationRecord:
    return _IndexedRuntimeValidationRecord(
        record=record,
        signals=frozenset(_expand_signals(record.signals)),
        source_project_names=frozenset(normalize_runtime_signal(item) for item in record.source_projects if item),
        source_project_keys=frozenset(normalize_runtime_signal(item) for item in record.source_project_keys if item),
    )


def _memory_path_key(path: Path) -> Path:
    return path.expanduser().resolve()


def _file_signature(path: Path) -> tuple[int, int]:
    if not path.exists():
        return (0, 0)
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _effective_unique_project_count(record: RuntimeValidationRecord) -> int:
    if record.unique_project_count:
        return record.unique_project_count
    project_keys = {normalize_runtime_signal(item) for item in record.source_project_keys if item}
    if project_keys:
        return len(project_keys)
    project_names = {normalize_runtime_signal(item) for item in record.source_projects if item}
    if project_names:
        return len(project_names)
    return min(record.support_count, 1)


def _has_negative_evidence(record: RuntimeValidationRecord) -> bool:
    return bool(record.negative_evidence or record.failed_project_keys)


def _effective_last_verified_at(record: RuntimeValidationRecord) -> str:
    return record.last_verified_at or record.updated_at or record.created_at


def _effective_staleness_score(record: RuntimeValidationRecord) -> float:
    if record.staleness_score > 0:
        return record.staleness_score
    return memory_staleness_score(_effective_last_verified_at(record))


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_runtime_signal(str(value))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
