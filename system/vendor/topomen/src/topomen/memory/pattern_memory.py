from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field

from topomen.memory.repair_memory import (
    RepairRecord,
    extract_apt_packages,
    extract_python_packages,
    tokenize_error_text,
)

PATTERN_MEMORY_WRITE_LOCK = Lock()
PATTERN_MEMORY_INDEX_LOCK = Lock()
PATTERN_MEMORY_INDEX_CACHE: dict[Path, tuple[tuple[int, int], list[_IndexedPatternRecord]]] = {}

HIGH_CONFIDENCE_PREFILL_THRESHOLD = 0.75
PROMPT_HINT_CONFIDENCE_THRESHOLD = 0.55
CROSS_PROJECT_PREFILL_MIN_PROJECTS = 2
MEMORY_STALE_AFTER_DAYS = 180
MEMORY_EXPIRED_AFTER_DAYS = 365

PACKAGE_SIGNAL_ALIASES = {
    "libmagic1": ["magic", "python-magic"],
    "libzbar0": ["zbar", "pyzbar"],
    "pyyaml": ["yaml"],
    "python-magic": ["magic", "libmagic"],
    "pillow": ["pil"],
    "opencv-python": ["cv2"],
    "opencv-contrib-python": ["cv2"],
    "psycopg2-binary": ["psycopg2", "pg_config"],
    "tesseract-ocr": ["tesseract", "pytesseract"],
}

GENERIC_PATTERN_TOKENS = {
    "apt-get",
    "build",
    "code",
    "command",
    "docker",
    "dockerfile",
    "error",
    "failed",
    "file",
    "install",
    "line",
    "module",
    "not",
    "package",
    "python",
    "returned",
    "run",
    "runtime",
    "step",
}


class MemoryCard(BaseModel):
    memory_id: str
    ecosystem: str = ""
    topology_node_id: str = ""
    stage: str = ""
    error_type: str = ""
    symptoms: list[str] = Field(default_factory=list)
    root_cause: str = ""
    fix_strategy: str = "generic"
    apt_packages: list[str] = Field(default_factory=list)
    python_packages: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    anti_conditions: list[str] = Field(default_factory=list)
    validation: list[str] = Field(default_factory=list)
    support_project_keys: list[str] = Field(default_factory=list)
    failed_project_keys: list[str] = Field(default_factory=list)
    verified_base_images: list[str] = Field(default_factory=list)
    package_manager: str = ""
    first_verified_at: str = ""
    last_verified_at: str = ""
    verification_count: int = Field(default=0, ge=0)
    staleness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    revalidation_status: str = "fresh"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PatternRecord(BaseModel):
    pattern_id: str
    signals: list[str] = Field(default_factory=list)
    target_node_id: str = ""
    stage: str = ""
    error_type: str = ""
    ecosystem: str = ""
    root_cause: str = ""
    symptom_signatures: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    anti_conditions: list[str] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    fix_strategy: str = "generic"
    apt_packages: list[str] = Field(default_factory=list)
    python_packages: list[str] = Field(default_factory=list)
    support_count: int = Field(default=0, ge=0)
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    unique_project_count: int = Field(default=0, ge=0)
    failed_project_count: int = Field(default=0, ge=0)
    source_projects: list[str] = Field(default_factory=list)
    source_project_keys: list[str] = Field(default_factory=list)
    failed_project_keys: list[str] = Field(default_factory=list)
    negative_evidence: list[str] = Field(default_factory=list)
    verified_base_images: list[str] = Field(default_factory=list)
    package_manager: str = ""
    first_verified_at: str = ""
    last_verified_at: str = ""
    verification_count: int = Field(default=0, ge=0)
    staleness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    revalidation_status: str = "fresh"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    memory_card: MemoryCard | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PatternMatch(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    record: PatternRecord
    matched_signals: list[str] = Field(default_factory=list)
    prefill_ready: bool = False
    blocked_reason: str = ""


@dataclass(frozen=True)
class _IndexedPatternRecord:
    record: PatternRecord
    signals: frozenset[str]
    source_project_names: frozenset[str]
    source_project_keys: frozenset[str]


class PatternMemory:
    def __init__(self, path: str | Path, *, create: bool = True):
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if create and not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def load_records(self) -> list[PatternRecord]:
        if not self.path.exists():
            return []
        records: list[PatternRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(PatternRecord.model_validate_json(line))
        return records

    def write_records(self, records: list[PatternRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with PATTERN_MEMORY_WRITE_LOCK:
            with self.path.open("w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(record.model_dump_json())
                    handle.write("\n")
        with PATTERN_MEMORY_INDEX_LOCK:
            PATTERN_MEMORY_INDEX_CACHE.pop(_memory_path_key(self.path), None)

    def retrieve(
        self,
        signals: list[str],
        *,
        project_name: str = "",
        project_key: str = "",
        top_k: int = 5,
        min_confidence: float = PROMPT_HINT_CONFIDENCE_THRESHOLD,
        allow_same_project_memory: bool = False,
    ) -> list[PatternMatch]:
        return _match_indexed_pattern_records(
            self._load_index(),
            signals,
            project_name=project_name,
            project_key=project_key,
            top_k=top_k,
            min_confidence=min_confidence,
            allow_same_project_memory=allow_same_project_memory,
        )

    def _load_index(self) -> list[_IndexedPatternRecord]:
        path_key = _memory_path_key(self.path)
        signature = _file_signature(self.path)
        with PATTERN_MEMORY_INDEX_LOCK:
            cached = PATTERN_MEMORY_INDEX_CACHE.get(path_key)
            if cached is not None and cached[0] == signature:
                return cached[1]
            indexed = [_indexed_pattern_record(record) for record in self.load_records()]
            PATTERN_MEMORY_INDEX_CACHE[path_key] = (signature, indexed)
            return indexed


def build_patterns_from_repair_records(records: list[RepairRecord]) -> list[PatternRecord]:
    grouped: dict[tuple[str, str, tuple[str, ...], tuple[str, ...]], dict[str, list[RepairRecord]]] = defaultdict(
        lambda: {"success": [], "failure": []}
    )
    for record in records:
        apt_packages, python_packages = _record_packages(record)
        if not apt_packages and not python_packages:
            continue
        if record.fix_strategy not in {"install_apt_packages", "install_python_package"}:
            continue
        target_node_id = _record_target_node(record)
        key = (
            target_node_id,
            record.fix_strategy,
            tuple(apt_packages),
            tuple(python_packages),
        )
        grouped[key]["success" if record.success else "failure"].append(record)

    patterns: list[PatternRecord] = []
    for (target_node_id, fix_strategy, apt_packages, python_packages), items in grouped.items():
        success_records = items["success"]
        if not success_records:
            continue
        failed_records = items["failure"]
        source_projects = _dedupe(record.project for record in success_records if record.project)
        source_project_keys = _dedupe(_record_project_identity(record) for record in success_records)
        failed_project_keys = _dedupe(_record_project_identity(record) for record in failed_records)
        negative_evidence = _dedupe(
            f"{record.project}: {record.error_signature}" for record in failed_records if record.error_signature
        )
        support_count = len(success_records)
        success_rate = support_count / max(support_count + len(failed_records), 1)
        signals = _record_group_signals(success_records, list(apt_packages), list(python_packages))
        unique_project_count = len(set(source_project_keys))
        failed_project_count = len(set(failed_project_keys))
        confidence = _pattern_confidence(
            support_count=support_count,
            success_rate=success_rate,
            unique_project_count=unique_project_count,
            failed_project_count=failed_project_count,
        )
        pattern_id = _pattern_id(
            target_node_id=target_node_id,
            fix_strategy=fix_strategy,
            apt_packages=list(apt_packages),
            python_packages=list(python_packages),
        )
        error_type = _common_record_value(success_records, "error_type")
        stage = _common_record_value(success_records, "stage")
        ecosystem = _infer_pattern_ecosystem(
            fix_strategy=fix_strategy,
            apt_packages=list(apt_packages),
            python_packages=list(python_packages),
        )
        root_cause = _infer_root_cause(
            error_type=error_type,
            apt_packages=list(apt_packages),
            python_packages=list(python_packages),
            signals=signals,
        )
        preconditions = _pattern_preconditions(
            fix_strategy=fix_strategy,
            signals=signals,
            apt_packages=list(apt_packages),
            python_packages=list(python_packages),
        )
        anti_conditions = _pattern_anti_conditions(fix_strategy=fix_strategy)
        validation_commands = _record_validation_commands(success_records)
        symptom_signatures = _dedupe(record.error_signature for record in success_records if record.error_signature)[:5]
        verified_base_images = _record_base_images(success_records)
        package_manager = _infer_package_manager(fix_strategy=fix_strategy)
        first_verified_at, last_verified_at = _record_verification_window(success_records)
        verification_count = len(success_records)
        staleness_score = memory_staleness_score(last_verified_at)
        revalidation_status = _revalidation_status(staleness_score)
        memory_card = MemoryCard(
            memory_id=pattern_id,
            ecosystem=ecosystem,
            topology_node_id=target_node_id,
            stage=stage,
            error_type=error_type,
            symptoms=symptom_signatures,
            root_cause=root_cause,
            fix_strategy=fix_strategy,
            apt_packages=list(apt_packages),
            python_packages=list(python_packages),
            preconditions=preconditions,
            anti_conditions=anti_conditions,
            validation=validation_commands,
            support_project_keys=source_project_keys,
            failed_project_keys=failed_project_keys,
            verified_base_images=verified_base_images,
            package_manager=package_manager,
            first_verified_at=first_verified_at,
            last_verified_at=last_verified_at,
            verification_count=verification_count,
            staleness_score=staleness_score,
            revalidation_status=revalidation_status,
            confidence=confidence,
        )
        patterns.append(
            PatternRecord(
                pattern_id=pattern_id,
                signals=signals,
                target_node_id=target_node_id,
                stage=stage,
                error_type=error_type,
                ecosystem=ecosystem,
                root_cause=root_cause,
                symptom_signatures=symptom_signatures,
                preconditions=preconditions,
                anti_conditions=anti_conditions,
                validation_commands=validation_commands,
                fix_strategy=fix_strategy,
                apt_packages=list(apt_packages),
                python_packages=list(python_packages),
                support_count=support_count,
                success_rate=round(success_rate, 4),
                unique_project_count=unique_project_count,
                failed_project_count=failed_project_count,
                source_projects=source_projects,
                source_project_keys=source_project_keys,
                failed_project_keys=failed_project_keys,
                negative_evidence=negative_evidence[:10],
                verified_base_images=verified_base_images,
                package_manager=package_manager,
                first_verified_at=first_verified_at,
                last_verified_at=last_verified_at,
                verification_count=verification_count,
                staleness_score=staleness_score,
                revalidation_status=revalidation_status,
                confidence=confidence,
                memory_card=memory_card,
            )
        )
    patterns.sort(
        key=lambda item: (item.confidence, _effective_unique_project_count(item), item.support_count, item.pattern_id),
        reverse=True,
    )
    return patterns


def rebuild_pattern_memory_from_repair_records(
    records: list[RepairRecord],
    pattern_memory_path: str | Path,
) -> list[PatternRecord]:
    patterns = build_patterns_from_repair_records(records)
    PatternMemory(pattern_memory_path).write_records(patterns)
    return patterns


def match_pattern_records(
    records: list[PatternRecord],
    signals: list[str],
    *,
    project_name: str = "",
    project_key: str = "",
    top_k: int = 5,
    min_confidence: float = PROMPT_HINT_CONFIDENCE_THRESHOLD,
    allow_same_project_memory: bool = False,
) -> list[PatternMatch]:
    return _match_indexed_pattern_records(
        [_indexed_pattern_record(record) for record in records],
        signals,
        project_name=project_name,
        project_key=project_key,
        top_k=top_k,
        min_confidence=min_confidence,
        allow_same_project_memory=allow_same_project_memory,
    )


def _match_indexed_pattern_records(
    records: list[_IndexedPatternRecord],
    signals: list[str],
    *,
    project_name: str = "",
    project_key: str = "",
    top_k: int = 5,
    min_confidence: float = PROMPT_HINT_CONFIDENCE_THRESHOLD,
    allow_same_project_memory: bool = False,
) -> list[PatternMatch]:
    signal_set = _expand_signals(signals)
    current_project = normalize_pattern_signal(project_name)
    current_project_key = normalize_pattern_signal(project_key)
    matches: list[PatternMatch] = []
    for entry in records:
        record = entry.record
        effective_confidence = effective_pattern_confidence(record)
        if effective_confidence < min_confidence:
            continue
        if not allow_same_project_memory:
            if current_project_key and current_project_key in entry.source_project_keys:
                continue
            if current_project and current_project in entry.source_project_names:
                continue
        record_signals = set(entry.signals)
        overlap = sorted(signal_set & record_signals)
        if not overlap:
            continue
        coverage = len(overlap) / max(len(record_signals), 1)
        diversity_bonus = min(_effective_unique_project_count(record), 4) * 0.015
        negative_penalty = 0.12 if _pattern_has_negative_evidence(record) else 0.0
        score = max(0.0, min(1.0, effective_confidence * 0.78 + coverage * 0.18 + diversity_bonus - negative_penalty))
        ready, blocked_reason = _prefill_readiness(record)
        matches.append(
            PatternMatch(
                score=round(score, 4),
                record=record,
                matched_signals=overlap,
                prefill_ready=ready,
                blocked_reason=blocked_reason,
            )
        )
    matches.sort(
        key=lambda item: (
            item.prefill_ready,
            item.score,
            _effective_unique_project_count(item.record),
            effective_pattern_confidence(item.record),
            item.record.support_count,
        ),
        reverse=True,
    )
    return matches[:top_k]


def pattern_prefill_packages(matches: list[PatternMatch]) -> tuple[list[str], list[str]]:
    apt_packages: list[str] = []
    python_packages: list[str] = []
    for match in matches:
        ready = getattr(match, "prefill_ready", False)
        if not ready and hasattr(match, "record"):
            ready, _ = _prefill_readiness(match.record)
        if effective_pattern_confidence(match.record) < HIGH_CONFIDENCE_PREFILL_THRESHOLD or not ready:
            continue
        if match.record.fix_strategy == "install_apt_packages":
            apt_packages.extend(match.record.apt_packages)
        elif match.record.fix_strategy == "install_python_package":
            python_packages.extend(match.record.python_packages)
    return _dedupe(apt_packages), _dedupe(python_packages)


def format_pattern_hints(matches: list[PatternMatch]) -> list[str]:
    hints: list[str] = []
    for match in matches:
        record = match.record
        package_text = ", ".join([*record.apt_packages, *record.python_packages])
        source_text = ", ".join(record.source_projects[:3]) or "unknown"
        readiness = "prefill-ready" if match.prefill_ready else f"hint-only:{match.blocked_reason or 'low-confidence'}"
        hints.append(
            f"Pattern {record.pattern_id} matched {', '.join(match.matched_signals)} "
            f"-> {record.fix_strategy}({package_text}); confidence={record.confidence:.2f}; "
            f"support={record.support_count}; unique_projects={_effective_unique_project_count(record)}; "
            f"{readiness}; sources={source_text}"
        )
    return hints


def pattern_match_payload(matches: list[PatternMatch]) -> list[dict[str, object]]:
    return [
        {
            "score": match.score,
            "pattern_id": match.record.pattern_id,
            "signals": match.record.signals,
            "matched_signals": match.matched_signals,
            "target_node_id": match.record.target_node_id,
            "fix_strategy": match.record.fix_strategy,
            "apt_packages": match.record.apt_packages,
            "python_packages": match.record.python_packages,
            "source_projects": match.record.source_projects,
            "source_project_keys": match.record.source_project_keys,
            "failed_project_keys": match.record.failed_project_keys,
            "confidence": match.record.confidence,
            "effective_confidence": effective_pattern_confidence(match.record),
            "support_count": match.record.support_count,
            "unique_project_count": _effective_unique_project_count(match.record),
            "failed_project_count": _effective_failed_project_count(match.record),
            "success_rate": match.record.success_rate,
            "prefill_ready": match.prefill_ready,
            "blocked_reason": match.blocked_reason,
            "staleness_score": _effective_staleness_score(match.record),
            "revalidation_status": match.record.revalidation_status,
            "last_verified_at": _effective_last_verified_at(match.record),
            "memory_card": match.record.memory_card.model_dump(mode="json") if match.record.memory_card else None,
        }
        for match in matches
    ]


def normalize_pattern_signal(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    return re.sub(r"[^a-z0-9.+-]+", "-", normalized).strip(".-")


def _memory_path_key(path: Path) -> Path:
    return path.expanduser().resolve()


def _file_signature(path: Path) -> tuple[int, int]:
    if not path.exists():
        return (0, 0)
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _indexed_pattern_record(record: PatternRecord) -> _IndexedPatternRecord:
    return _IndexedPatternRecord(
        record=record,
        signals=frozenset(_expand_signals(record.signals)),
        source_project_names=frozenset(normalize_pattern_signal(item) for item in record.source_projects if item),
        source_project_keys=frozenset(normalize_pattern_signal(item) for item in record.source_project_keys if item),
    )


def _effective_unique_project_count(record: PatternRecord) -> int:
    if record.unique_project_count:
        return record.unique_project_count
    explicit_keys = {normalize_pattern_signal(item) for item in record.source_project_keys if item}
    project_names = {normalize_pattern_signal(item) for item in record.source_projects if item}
    if explicit_keys:
        return len(explicit_keys)
    if project_names:
        return len(project_names)
    return min(record.support_count, 1)


def _effective_failed_project_count(record: PatternRecord) -> int:
    if record.failed_project_count:
        return record.failed_project_count
    failed_keys = {normalize_pattern_signal(item) for item in record.failed_project_keys if item}
    if failed_keys:
        return len(failed_keys)
    return len(record.negative_evidence)


def _pattern_has_negative_evidence(record: PatternRecord) -> bool:
    return bool(record.negative_evidence or _effective_failed_project_count(record) > 0)


def _prefill_readiness(record: PatternRecord) -> tuple[bool, str]:
    unique_projects = _effective_unique_project_count(record)
    staleness = _effective_staleness_score(record)
    status = (record.revalidation_status or "").strip().lower()
    if status in {"expired", "failed", "blocked"} or staleness >= 1.0:
        return False, "stale_memory"
    if effective_pattern_confidence(record) < HIGH_CONFIDENCE_PREFILL_THRESHOLD:
        return False, "low_confidence"
    if unique_projects < CROSS_PROJECT_PREFILL_MIN_PROJECTS:
        return False, "insufficient_cross_project_support"
    if _pattern_has_negative_evidence(record):
        return False, "negative_evidence"
    return True, ""


def _record_project_identity(record: RepairRecord) -> str:
    explicit_key = normalize_pattern_signal(str(record.metadata.get("source_project_key", "")))
    if explicit_key:
        return explicit_key
    return normalize_pattern_signal(record.project)


def effective_pattern_confidence(record: PatternRecord) -> float:
    staleness = _effective_staleness_score(record)
    status = (record.revalidation_status or "").strip().lower()
    status_penalty = 0.0
    if status in {"needs_revalidation", "stale"}:
        status_penalty = 0.08
    elif status in {"expired", "failed", "blocked"}:
        status_penalty = 0.35
    return round(max(0.0, min(1.0, record.confidence - staleness * 0.18 - status_penalty)), 4)


def memory_staleness_score(
    last_verified_at: str,
    *,
    stale_after_days: int = MEMORY_STALE_AFTER_DAYS,
    expired_after_days: int = MEMORY_EXPIRED_AFTER_DAYS,
    now: datetime | None = None,
) -> float:
    verified_at = _parse_datetime(last_verified_at)
    if verified_at is None:
        return 1.0
    now_value = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now_value - verified_at).total_seconds() / 86400)
    if age_days <= stale_after_days:
        return 0.0
    denominator = max(1, expired_after_days - stale_after_days)
    return round(min(1.0, (age_days - stale_after_days) / denominator), 4)


def _effective_staleness_score(record: PatternRecord) -> float:
    if record.staleness_score > 0:
        return record.staleness_score
    return memory_staleness_score(_effective_last_verified_at(record))


def _effective_last_verified_at(record: PatternRecord) -> str:
    if record.last_verified_at:
        return record.last_verified_at
    if record.memory_card and record.memory_card.last_verified_at:
        return record.memory_card.last_verified_at
    return record.updated_at or record.created_at


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _revalidation_status(staleness_score: float) -> str:
    if staleness_score >= 1.0:
        return "expired"
    if staleness_score >= 0.7:
        return "needs_revalidation"
    return "fresh"


def _record_packages(record: RepairRecord) -> tuple[list[str], list[str]]:
    apt_packages = _split_metadata_packages(str(record.metadata.get("apt_packages", "")))
    python_packages = _split_metadata_packages(str(record.metadata.get("python_packages", "")))
    if not apt_packages and record.fix_strategy == "install_apt_packages":
        apt_packages = extract_apt_packages(record.fix_action)
        if record.fix_action.lower().startswith("install apt packages:"):
            apt_packages = _split_metadata_packages(record.fix_action.split(":", 1)[1])
    if not python_packages and record.fix_strategy == "install_python_package":
        python_packages = extract_python_packages(record.fix_action)
        if record.fix_action.lower().startswith("install python packages:"):
            python_packages = _split_metadata_packages(record.fix_action.split(":", 1)[1])
    return _dedupe(apt_packages), _dedupe(python_packages)


def _split_metadata_packages(value: str) -> list[str]:
    return _dedupe(normalize_pattern_signal(item) for item in re.split(r"[\s,]+", value) if item.strip())


def _record_target_node(record: RepairRecord) -> str:
    metadata_node = str(record.metadata.get("topology_node_id", "")).strip()
    if metadata_node:
        return metadata_node
    if record.fix_strategy == "install_apt_packages":
        return "system_dependency_plan"
    if record.fix_strategy == "install_python_package":
        return "python_dependency_plan"
    return ""


def _record_group_signals(
    records: list[RepairRecord],
    apt_packages: list[str],
    python_packages: list[str],
) -> list[str]:
    signals: list[str] = []
    for package in [*apt_packages, *python_packages]:
        signals.extend(_package_signal_variants(package))
    for record in records:
        signals.extend(_extract_record_signals(record))
    return _dedupe(signal for signal in signals if signal and signal not in GENERIC_PATTERN_TOKENS)


def _extract_record_signals(record: RepairRecord) -> list[str]:
    text = "\n".join([record.error_signature, record.error_message, record.fix_action])
    signals: list[str] = []
    regexes = [
        r"no module named ['\"](?P<signal>[a-zA-Z0-9_.-]+)['\"]",
        r"filenotfounderror:.*['\"](?P<signal>[a-zA-Z0-9_.-]+)['\"]",
        r"command ['\"]?(?P<signal>[a-zA-Z0-9_.-]+)['\"]? not found",
        r"(?P<signal>lib[a-zA-Z0-9_.+-]+)\.so",
    ]
    normalized_text = text.lower()
    for pattern in regexes:
        for match in re.finditer(pattern, normalized_text, flags=re.IGNORECASE):
            signals.append(match.group("signal"))
    for token in tokenize_error_text(text):
        normalized = normalize_pattern_signal(token)
        if len(normalized) >= 3 and normalized not in GENERIC_PATTERN_TOKENS:
            signals.append(normalized)
    return _dedupe(_expand_signals(signals))


def _expand_signals(signals: list[str]) -> set[str]:
    expanded: set[str] = set()
    for signal in signals:
        normalized = normalize_pattern_signal(signal)
        if not normalized:
            continue
        expanded.add(normalized)
        expanded.update(_package_signal_variants(normalized))
    return expanded


def _package_signal_variants(package: str) -> list[str]:
    normalized = normalize_pattern_signal(package)
    if not normalized:
        return []
    variants = {normalized, normalized.replace("-", "_")}
    if normalized.startswith("lib") and len(normalized) > 3:
        variants.add(normalized[3:])
        variants.add(re.sub(r"\d+$", "", normalized[3:]))
    variants.update(PACKAGE_SIGNAL_ALIASES.get(normalized, []))
    return [normalize_pattern_signal(item) for item in variants if normalize_pattern_signal(item)]


def _common_record_value(records: list[RepairRecord], field_name: str) -> str:
    values = _dedupe(str(getattr(record, field_name, "")) for record in records if str(getattr(record, field_name, "")).strip())
    return values[0] if len(values) == 1 else ""


def _infer_pattern_ecosystem(*, fix_strategy: str, apt_packages: list[str], python_packages: list[str]) -> str:
    if fix_strategy == "install_python_package" or python_packages:
        return "python"
    if fix_strategy == "install_apt_packages" or apt_packages:
        return "system"
    return "deployment"


def _infer_root_cause(
    *,
    error_type: str,
    apt_packages: list[str],
    python_packages: list[str],
    signals: list[str],
) -> str:
    packages = ", ".join([*apt_packages, *python_packages]) or "unknown package"
    signal_text = ", ".join(signals[:4]) or "matching runtime signal"
    if error_type == "missing_python_module":
        return f"Python import signal ({signal_text}) is resolved by installing {packages}."
    if error_type in {"missing_shared_library", "missing_system_binary", "missing_system_package", "missing_build_tool"}:
        return f"System/runtime signal ({signal_text}) is resolved by installing {packages}."
    return f"Repeated deployment failure signal ({signal_text}) is resolved by {packages}."


def _pattern_preconditions(
    *,
    fix_strategy: str,
    signals: list[str],
    apt_packages: list[str],
    python_packages: list[str],
) -> list[str]:
    preconditions: list[str] = []
    if fix_strategy == "install_apt_packages" or apt_packages:
        preconditions.append("Debian/Ubuntu-like base image with apt available")
    if fix_strategy == "install_python_package" or python_packages:
        preconditions.append("Python project with pip-compatible dependency installation")
    if signals:
        preconditions.append(f"Observed one of these dependency signals: {', '.join(signals[:6])}")
    return preconditions


def _pattern_anti_conditions(*, fix_strategy: str) -> list[str]:
    if fix_strategy == "install_apt_packages":
        return ["Non-Debian base images without apt need package-manager-specific translation"]
    return []


def _record_validation_commands(records: list[RepairRecord]) -> list[str]:
    commands: list[str] = []
    for record in records:
        for key in ("validation_command", "validation_commands", "runtime_command", "trigger_command"):
            commands.extend(_split_metadata_values(str(record.metadata.get(key, ""))))
    return _dedupe_text(commands)[:5]


def _record_base_images(records: list[RepairRecord]) -> list[str]:
    images: list[str] = []
    for record in records:
        for key in ("base_image", "base_image_source", "verified_base_image", "verified_base_images"):
            images.extend(_split_metadata_values(str(record.metadata.get(key, ""))))
    return _dedupe_text(images)[:5]


def _infer_package_manager(*, fix_strategy: str) -> str:
    if fix_strategy == "install_apt_packages":
        return "apt"
    if fix_strategy == "install_python_package":
        return "pip"
    return ""


def _record_verification_window(records: list[RepairRecord]) -> tuple[str, str]:
    values: list[datetime] = []
    for record in records:
        for value in (
            str(record.metadata.get("last_verified_at", "")),
            str(record.metadata.get("verified_at", "")),
            record.created_at,
        ):
            parsed = _parse_datetime(value)
            if parsed is not None:
                values.append(parsed)
                break
    if not values:
        now_value = datetime.now(timezone.utc).isoformat()
        return now_value, now_value
    values.sort()
    return values[0].isoformat(), values[-1].isoformat()


def _split_metadata_values(value: str) -> list[str]:
    return [item for item in re.split(r"[\n,]+", value) if item.strip()]


def _pattern_confidence(
    *,
    support_count: int,
    success_rate: float,
    unique_project_count: int = 0,
    failed_project_count: int = 0,
) -> float:
    confidence = (
        0.45
        + min(support_count, 5) * 0.03
        + min(unique_project_count, 4) * 0.08
        + success_rate * 0.12
        - failed_project_count * 0.12
        - (1.0 - success_rate) * 0.25
    )
    return round(max(0.0, min(confidence, 0.95)), 4)


def _pattern_id(
    *,
    target_node_id: str,
    fix_strategy: str,
    apt_packages: list[str],
    python_packages: list[str],
) -> str:
    payload = json.dumps(
        {
            "target_node_id": target_node_id,
            "fix_strategy": fix_strategy,
            "apt_packages": sorted(apt_packages),
            "python_packages": sorted(python_packages),
        },
        sort_keys=True,
    )
    return f"pm-{hashlib.sha1(payload.encode()).hexdigest()[:12]}"


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = normalize_pattern_signal(str(value))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _dedupe_text(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        key = re.sub(r"\s+", " ", text).lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
