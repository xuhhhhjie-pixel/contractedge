from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, Field

COMMON_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "during",
    "while",
    "line",
    "step",
    "error",
    "failed",
    "failure",
    "build",
    "docker",
    "running",
    "process",
    "returned",
    "code",
    "失败",
    "错误",
    "构建",
    "运行",
    "命令",
    "步骤",
    "行号",
}

SIGNATURE_PATTERNS = [
    r"ImportError:[^\n]+",
    r"ModuleNotFoundError:[^\n]+",
    r"fatal error:[^\n]+",
    r"error:[^\n]+",
    r"ERROR:[^\n]+",
    r"failed to solve:[^\n]+",
    r"executor failed running[^\n]+",
    r"unable to locate package[^\n]+",
    r"No matching distribution found for[^\n]+",
    r"Could not find a version that satisfies the requirement[^\n]+",
    r"cannot open shared object file[^\n]+",
    r"command not found[^\n]*",
    r"(导入错误|模块未找到|构建失败|运行失败|无法找到[^\n]*)",
    r"(错误|失败)[:：][^\n]+",
]

CHINESE_ERROR_KEYWORDS = ("错误", "失败", "未找到", "不存在", "无法", "缺少", "异常")
MOJIBAKE_MARKERS = ("�", "����", "锟斤拷", "���")
SHELL_NOISE_PATTERNS = (
    r"'grep' .*不是内部或外部命令",
    r"'grep' is not recognized",
    r"不是内部或外部命令",
    r"is not recognized as an internal or external command",
)
BUILD_INFRA_ERROR_PATTERNS = (
    r"failed to prepare extraction snapshot",
    r"parent snapshot .* does not exist",
    r"failed to compute cache key",
    r"failed to walk .* no such file or directory",
    r"failed to do request",
    r"failed to authorize",
    r"failed to fetch anonymous token",
    r"tls handshake timeout",
    r"i/o timeout",
    r"connection reset by peer",
    r"connectex: .*failed to respond",
    r"cannot connect to the docker daemon",
    r"error response from daemon",
    r"no space left on device",
)
DOCKERFILE_SYNTAX_ERROR_PATTERNS = (
    r"\\nrun\s+",
    r"\nrun\s+",
    r"/bin/sh:.*run: not found",
    r"\brun: not found",
    r"dockerfile parse error",
    r"unknown instruction",
)
APT_PACKAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
APT_PACKAGE_STOPWORDS = {
    "run",
    "apt",
    "apt-get",
    "aptitude",
    "install",
    "update",
    "upgrade",
    "rm",
    "rf",
    "var",
    "lib",
    "lists",
    "dockerfile",
    "echo",
    "sed",
}
PIP_PACKAGE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*(?:\[[a-zA-Z0-9_,.-]+\])?(?:[<>=!~]=?.*)?$")
PIP_PACKAGE_STOPWORDS = {
    ".",
    "pip",
    "install",
    "python",
    "run",
    "dockerfile",
    "requirements.txt",
}
PIP_REQUIREMENT_FLAGS = {"-r", "--requirement", "-c", "--constraint"}
CJK_UNIFIED_IDEOGRAPH_RANGES = (
    ("\u3400", "\u4dbf"),
    ("\u4e00", "\u9fff"),
    ("\uf900", "\ufaff"),
)
REPAIR_MEMORY_WRITE_LOCK = Lock()
REPAIR_MEMORY_INDEX_LOCK = Lock()
REPAIR_MEMORY_INDEX_CACHE: dict[Path, tuple[tuple[int, int], list[_IndexedRepairRecord]]] = {}
REPAIR_MEMORY_CANDIDATE_THRESHOLD = 0.2
REPAIR_MEMORY_PREFILL_THRESHOLD = 0.55

MEMORY_SIGNAL_ALIASES = {
    "libmagic1": ["magic", "python-magic", "libmagic"],
    "libzbar0": ["zbar", "pyzbar"],
    "pyyaml": ["yaml"],
    "python-magic": ["magic", "libmagic", "libmagic1"],
    "pillow": ["pil"],
    "opencv-python": ["cv2", "libgl"],
    "opencv-contrib-python": ["cv2", "libgl"],
    "psycopg2-binary": ["psycopg2", "pg-config", "libpq-dev"],
    "tesseract-ocr": ["tesseract", "pytesseract"],
    "ffmpeg": ["ffmpeg"],
    "libgl1": ["libgl", "libgl.so.1", "cv2"],
    "libgl1-mesa-glx": ["libgl", "libgl.so.1", "cv2"],
}


@dataclass(frozen=True)
class _IndexedRepairRecord:
    record: RepairRecord
    signature_tokens: tuple[str, ...]
    message_tokens: tuple[str, ...]
    context_tokens: tuple[str, ...]
    distinctive_tokens: frozenset[str]
    package_signals: frozenset[str]
    topology_node_id: str
    project_name: str
    project_key: str


class RepairRecord(BaseModel):
    project: str
    group: str = Field(description="Experiment cohort, e.g. zero-shot or rag")
    iteration: int = Field(ge=0)
    stage: str = Field(default="build", description="build or run")
    error_signature: str
    error_message: str
    fix_action: str
    error_type: str = "generic"
    fix_strategy: str = "generic"
    success: bool
    dockerfile_before: str | None = None
    dockerfile_after: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RetrievalMatch(BaseModel):
    score: float
    record: RepairRecord
    score_components: dict[str, float] = Field(default_factory=dict)
    matched_tokens: list[str] = Field(default_factory=list)
    matched_packages: list[str] = Field(default_factory=list)
    matched_stage: bool = False
    matched_error_type: bool = False
    matched_node: bool = False
    evidence_level: str = "hint"


class ExperimentRunResult(BaseModel):
    group: str
    project: str
    success: bool
    iterations: int = Field(ge=0)
    retrieved_contexts: int = Field(default=0, ge=0)
    memory_retrieval_events: int = Field(default=0, ge=0)
    memory_retrieved_matches: int = Field(default=0, ge=0)
    memory_precise_matches: int = Field(default=0, ge=0)
    topology_memory_matches: int = Field(default=0, ge=0)
    memory_prefill_candidates: int = Field(default=0, ge=0)
    memory_prefill_applied_packages: int = Field(default=0, ge=0)
    pattern_memory_matches: int = Field(default=0, ge=0)
    pattern_cross_project_matches: int = Field(default=0, ge=0)
    pattern_low_support_matches: int = Field(default=0, ge=0)
    pattern_negative_evidence_matches: int = Field(default=0, ge=0)
    pattern_prefill_applied_packages: int = Field(default=0, ge=0)
    runtime_validation_memory_matches: int = Field(default=0, ge=0)
    runtime_validation_memory_prefill_candidates: int = Field(default=0, ge=0)
    node_failures: dict[str, int] = Field(default_factory=dict)
    total_seconds: float = Field(default=0.0, ge=0.0)
    docker_build_count: int = Field(default=0, ge=0)
    docker_run_count: int = Field(default=0, ge=0)
    docker_build_seconds: float = Field(default=0.0, ge=0.0)
    docker_run_seconds: float = Field(default=0.0, ge=0.0)
    cache_hit_steps: int = Field(default=0, ge=0)
    cache_total_steps: int = Field(default=0, ge=0)
    node_retries: dict[str, int] = Field(default_factory=dict)
    guardrail_violations: int = Field(default=0, ge=0)
    prefill_validated_first: bool = False
    verifier_rejections: int = Field(default=0, ge=0)
    critic_rejections: int = Field(default=0, ge=0)
    accepted_memory_writes: int = Field(default=0, ge=0)
    multistage_used: bool = False
    threaded_build_used: bool = False
    parallel_build_branches: int = Field(default=0, ge=0)
    parallel_repair_used: bool = False
    repair_branches_tried: int = Field(default=0, ge=0)
    build_cache_enabled: bool = False
    build_cache_namespace: str = ""
    build_cache_reuse_existing: bool = False
    build_cache_id_scope: str = ""
    build_cache_ids: dict[str, str] = Field(default_factory=dict)
    final_image: str = ""
    internal_branch_image_count: int = Field(default=0, ge=0)


def normalize_error_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"\x1b\[[0-9;]*m", " ", normalized)
    normalized = re.sub(r"dockerfile:\d+", "dockerfile", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _is_cjk(char: str) -> bool:
    return any(start <= char <= end for start, end in CJK_UNIFIED_IDEOGRAPH_RANGES)


def _tokenize_cjk(text: str) -> list[str]:
    cjk_chars = [char for char in text if _is_cjk(char)]
    tokens: list[str] = []
    for size in (2, 3):
        tokens.extend("".join(cjk_chars[index : index + size]) for index in range(len(cjk_chars) - size + 1))
    return tokens


def tokenize_error_text(text: str) -> list[str]:
    normalized = normalize_error_text(text)
    latin_tokens = re.findall(r"[a-z0-9_.:+/-]+", normalized)
    cjk_tokens = _tokenize_cjk(normalized)
    tokens = latin_tokens + cjk_tokens
    return [token for token in tokens if len(token) > 1 and token not in COMMON_STOPWORDS]


def extract_error_signature(log_text: str) -> str:
    normalized = log_text.strip()
    if not normalized:
        return "unknown error"

    for pattern in SIGNATURE_PATTERNS:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    priority_lines = [
        line
        for line in lines
        if any(keyword in line.lower() for keyword in ("error", "failed", "not found"))
        or any(keyword in line for keyword in CHINESE_ERROR_KEYWORDS)
    ]
    if priority_lines:
        return priority_lines[-1]
    return lines[-1][:240]


def is_garbled_text(text: str) -> bool:
    if not text:
        return False
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return True
    replacement_ratio = text.count("�") / max(len(text), 1)
    return replacement_ratio > 0.05


def is_shell_noise(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in SHELL_NOISE_PATTERNS)


def is_build_infra_error(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in BUILD_INFRA_ERROR_PATTERNS)


def is_dockerfile_syntax_error(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in DOCKERFILE_SYNTAX_ERROR_PATTERNS)


def _missing_file_target(text: str) -> str:
    normalized = normalize_error_text(text)
    match = re.search(r"no such file or directory: ['\"](?P<target>[^'\"]+)['\"]", normalized)
    if not match:
        return ""
    return match.group("target").strip()


def _looks_like_missing_executable(text: str) -> bool:
    normalized = normalize_error_text(text)
    target = _missing_file_target(text)
    if target and "/" not in target and "\\" not in target and "." not in target:
        return True
    executable_markers = (
        "ffmpeg",
        "pdftoppm",
        "tesseract",
        "mysql_config",
        "pg_config",
        "pkg-config",
        "curl-config",
    )
    if any(marker in normalized for marker in executable_markers) and (
        "not found" in normalized or "no such file or directory" in normalized
    ):
        return True
    return bool(re.search(r"\bcommand ['\"]?[a-z0-9_.-]+['\"]? not found\b", normalized))


def classify_error_type(error_signature: str, error_message: str = "") -> str:
    text = f"{error_signature}\n{error_message}"
    normalized = normalize_error_text(text)
    if is_garbled_text(text):
        return "encoding_noise"
    if is_shell_noise(text):
        return "shell_tool_failure"
    if is_build_infra_error(text):
        return "build_infra_error"
    if is_dockerfile_syntax_error(text):
        return "dockerfile_syntax_error"
    if any(tool in normalized for tool in ("curl-config", "pkg-config", "mysql_config", "pg_config", "cmake")):
        return "missing_build_tool"
    if "tesseract" in normalized and ("not installed" in normalized or "no such file" in normalized or "not found" in normalized):
        return "missing_system_binary"
    if _looks_like_missing_executable(text):
        return "missing_system_binary"
    if "g++" in normalized or "gcc" in normalized or "build wheel" in normalized:
        return "missing_compiler"
    if (
        "cannot open shared object file" in normalized
        or "importerror: lib" in normalized
        or "failed to find libmagic" in normalized
        or "shared library" in normalized
        or "unable to find zbar" in normalized
        or "could not find lib" in normalized
        or "could not load shared library" in normalized
    ):
        return "missing_shared_library"
    if "no matching distribution found" in normalized or "could not find a version that satisfies" in normalized:
        return "package_resolution"
    if "unable to locate package" in normalized or "no installation candidate" in normalized:
        return "missing_system_package"
    if "modulenotfounderror" in normalized or "no module named" in normalized:
        return "missing_python_module"
    return "generic"


def compress_error_message(log_text: str, *, max_lines: int = 12, max_chars: int = 1200) -> str:
    lines = [line.strip() for line in log_text.splitlines() if line.strip()]
    if not lines:
        return ""
    priority_lines = [
        line
        for line in lines
        if any(keyword in line.lower() for keyword in ("error", "failed", "not found"))
        or any(keyword in line for keyword in CHINESE_ERROR_KEYWORDS)
    ]
    selected = priority_lines[-max_lines:] if priority_lines else lines[-max_lines:]
    compressed = "\n".join(selected)
    return compressed[:max_chars]


def extract_apt_packages(action_text: str) -> list[str]:
    packages: list[str] = []
    for match in re.finditer(r"apt-get install -y(?: --no-install-recommends)? ([^\n&|;]+)", action_text):
        segment = match.group(1)
        candidates = re.split(r"[\s,]+", segment.strip())
        for candidate in candidates:
            cleaned = candidate.strip().strip("\"'`(){}[]:,;")
            cleaned = cleaned.replace("\\+", "+")
            if cleaned.endswith("\\"):
                cleaned = cleaned[:-1]
            if cleaned.lower() in APT_PACKAGE_STOPWORDS:
                continue
            if not APT_PACKAGE_PATTERN.fullmatch(cleaned):
                continue
            if cleaned and not cleaned.startswith("-"):
                packages.append(cleaned)
    seen: set[str] = set()
    deduped: list[str] = []
    for package in packages:
        if package not in seen:
            seen.add(package)
            deduped.append(package)
    return deduped


def extract_python_packages(action_text: str) -> list[str]:
    packages: list[str] = []
    for match in re.finditer(r"(?:python\s+-m\s+)?pip\s+install\s+(?P<args>[^\n&|;]+)", action_text, flags=re.IGNORECASE):
        segment = match.group("args")
        candidates = re.split(r"[\s,]+", segment.strip())
        skip_next = False
        for candidate in candidates:
            cleaned = candidate.strip().strip("\"'`(){}[]:,;")
            if not cleaned:
                continue
            lowered = cleaned.lower()
            if skip_next:
                skip_next = False
                continue
            if lowered in PIP_REQUIREMENT_FLAGS or lowered in {"-e", "--editable"}:
                skip_next = True
                continue
            if lowered.startswith("-"):
                continue
            if lowered in PIP_PACKAGE_STOPWORDS:
                continue
            if lowered.endswith((".txt", ".in", ".lock")):
                continue
            if not PIP_PACKAGE_PATTERN.fullmatch(cleaned):
                continue
            package = re.split(r"[<>=!~;\[]", cleaned, maxsplit=1)[0].strip().lower().replace("_", "-")
            if package:
                packages.append(package)
    seen: set[str] = set()
    deduped: list[str] = []
    for package in packages:
        if package not in seen:
            seen.add(package)
            deduped.append(package)
    return deduped


def normalize_memory_signal(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower().replace("_", "-")
    return re.sub(r"[^a-z0-9.+-]+", "-", normalized).strip(".-")


def expand_memory_signals(values: list[str]) -> set[str]:
    expanded: set[str] = set()
    for value in values:
        normalized = normalize_memory_signal(str(value))
        if not normalized:
            continue
        expanded.add(normalized)
        expanded.add(normalized.replace("-", "_"))
        expanded.update(normalize_memory_signal(alias) for alias in MEMORY_SIGNAL_ALIASES.get(normalized, []))
        if normalized.startswith("lib") and len(normalized) > 3:
            without_prefix = re.sub(r"\d+$", "", normalized[3:])
            if without_prefix:
                expanded.add(without_prefix)
    return {item for item in expanded if item}


def _split_metadata_values(value: str) -> list[str]:
    return [item for item in re.split(r"[\s,]+", value) if item.strip()]


def _record_package_signals(record: RepairRecord) -> frozenset[str]:
    values: list[str] = []
    for key in ("apt_packages", "python_packages", "package", "python_package", "import_name"):
        values.extend(_split_metadata_values(str(record.metadata.get(key, ""))))
    values.extend(extract_apt_packages(record.fix_action))
    values.extend(extract_python_packages(record.fix_action))
    if record.fix_action.lower().startswith("install apt packages:"):
        values.extend(_split_metadata_values(record.fix_action.split(":", 1)[1]))
    if record.fix_action.lower().startswith("install python packages:"):
        values.extend(_split_metadata_values(record.fix_action.split(":", 1)[1]))
    values.extend(token for token in tokenize_error_text(record.error_signature) if len(token) >= 3)
    return frozenset(expand_memory_signals(values))


def _indexed_record(record: RepairRecord) -> _IndexedRepairRecord:
    context_text = " ".join(
        [
            record.project,
            record.error_signature,
            record.error_message,
            record.fix_action,
            record.error_type,
            record.fix_strategy,
            " ".join(str(value) for value in record.metadata.values()),
        ]
    )
    signature_tokens = tuple(tokenize_error_text(record.error_signature))
    message_tokens = tuple(tokenize_error_text(record.error_message))
    context_tokens = tuple(tokenize_error_text(context_text))
    return _IndexedRepairRecord(
        record=record,
        signature_tokens=signature_tokens,
        message_tokens=message_tokens,
        context_tokens=context_tokens,
        distinctive_tokens=frozenset(_distinctive_tokens(list(context_tokens))),
        package_signals=_record_package_signals(record),
        topology_node_id=normalize_memory_signal(str(record.metadata.get("topology_node_id", ""))),
        project_name=normalize_error_text(record.project),
        project_key=normalize_error_text(str(record.metadata.get("source_project_key", ""))),
    )


def summarize_fix_action(actions: list[str]) -> tuple[str, str, dict[str, str]]:
    filtered_actions = []
    for action in actions:
        stripped = action.strip()
        if not stripped:
            continue
        if re.fullmatch(r'python -c "print\(\'[^\']*\'\)"', stripped):
            continue
        if stripped.startswith("docker build") or stripped.startswith("docker run"):
            continue
        filtered_actions.append(stripped)
    joined = " && ".join(filtered_actions)
    apt_packages = extract_apt_packages(joined)
    if apt_packages:
        return (
            f"Install apt packages: {', '.join(apt_packages)}",
            "install_apt_packages",
            {"apt_packages": ", ".join(apt_packages)},
        )
    python_packages = extract_python_packages(joined)
    if python_packages:
        return (
            f"Install Python packages: {', '.join(python_packages)}",
            "install_python_package",
            {"python_packages": ", ".join(python_packages)},
        )
    if "sed -i" in joined:
        return ("Patch Dockerfile with sed and rebuild", "patch_dockerfile", {})
    if "docker build" in joined and "docker run" not in joined:
        return ("Rebuild Docker image", "rebuild_image", {})
    if joined:
        return (joined[:240], "generic", {})
    return ("(no explicit fix command captured)", "generic", {})


def should_store_repair_record(error_signature: str, error_message: str, trigger_command: str) -> bool:
    error_type = classify_error_type(error_signature, error_message)
    if error_type in {"encoding_noise", "shell_tool_failure", "build_infra_error", "dockerfile_syntax_error"}:
        return False
    if not error_signature or error_signature == "unknown error":
        return False
    if "grep" in trigger_command.lower() and is_shell_noise(error_message):
        return False
    return True


def _cosine_similarity(left_tokens: list[str], right_tokens: list[str]) -> float:
    if not left_tokens or not right_tokens:
        return 0.0
    left_counts = Counter(left_tokens)
    right_counts = Counter(right_tokens)
    overlap = sum(left_counts[token] * right_counts[token] for token in left_counts.keys() & right_counts.keys())
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return overlap / (left_norm * right_norm)


def _distinctive_tokens(tokens: list[str]) -> set[str]:
    return {
        token
        for token in tokens
        if len(token) >= 6
        or "." in token
        or any(char.isdigit() for char in token)
        or any(_is_cjk(char) for char in token)
    }


def _signature_match_bonus(query_signature: str, record_signature: str) -> float:
    normalized_query = normalize_error_text(query_signature)
    normalized_record = normalize_error_text(record_signature)
    if normalized_query == normalized_record:
        return 0.3
    if normalized_query in normalized_record or normalized_record in normalized_query:
        return 0.2
    return 0.0


def summarize_experiment_runs(runs: list[ExperimentRunResult]) -> dict[str, dict[str, float | int | dict[str, int]]]:
    grouped_runs: dict[str, list[ExperimentRunResult]] = defaultdict(list)
    for run in runs:
        grouped_runs[run.group].append(run)

    summary: dict[str, dict[str, float | int | dict[str, int]]] = {}
    for group, items in grouped_runs.items():
        success_count = sum(1 for item in items if item.success)
        iteration_total = sum(item.iterations for item in items)
        context_total = sum(item.retrieved_contexts for item in items)
        memory_retrieval_events_total = sum(item.memory_retrieval_events for item in items)
        memory_retrieved_matches_total = sum(item.memory_retrieved_matches for item in items)
        memory_precise_matches_total = sum(item.memory_precise_matches for item in items)
        topology_memory_matches_total = sum(item.topology_memory_matches for item in items)
        memory_prefill_candidates_total = sum(item.memory_prefill_candidates for item in items)
        memory_prefill_applied_packages_total = sum(item.memory_prefill_applied_packages for item in items)
        pattern_memory_matches_total = sum(item.pattern_memory_matches for item in items)
        pattern_cross_project_matches_total = sum(item.pattern_cross_project_matches for item in items)
        pattern_low_support_matches_total = sum(item.pattern_low_support_matches for item in items)
        pattern_negative_evidence_matches_total = sum(item.pattern_negative_evidence_matches for item in items)
        pattern_prefill_applied_packages_total = sum(item.pattern_prefill_applied_packages for item in items)
        runtime_validation_memory_matches_total = sum(item.runtime_validation_memory_matches for item in items)
        runtime_validation_memory_prefill_candidates_total = sum(
            item.runtime_validation_memory_prefill_candidates for item in items
        )
        seconds_total = sum(item.total_seconds for item in items)
        build_count_total = sum(item.docker_build_count for item in items)
        run_count_total = sum(item.docker_run_count for item in items)
        build_seconds_total = sum(item.docker_build_seconds for item in items)
        run_seconds_total = sum(item.docker_run_seconds for item in items)
        cache_hit_total = sum(item.cache_hit_steps for item in items)
        cache_step_total = sum(item.cache_total_steps for item in items)
        guardrail_violations_total = sum(item.guardrail_violations for item in items)
        prefill_validated_first_count = sum(1 for item in items if item.prefill_validated_first)
        verifier_rejections_total = sum(item.verifier_rejections for item in items)
        critic_rejections_total = sum(item.critic_rejections for item in items)
        accepted_memory_writes_total = sum(item.accepted_memory_writes for item in items)
        multistage_used_count = sum(1 for item in items if item.multistage_used)
        threaded_build_used_count = sum(1 for item in items if item.threaded_build_used)
        parallel_build_branches_total = sum(item.parallel_build_branches for item in items)
        parallel_repair_used_count = sum(1 for item in items if item.parallel_repair_used)
        repair_branches_total = sum(item.repair_branches_tried for item in items)
        build_cache_enabled_count = sum(1 for item in items if item.build_cache_enabled)
        build_cache_reuse_existing_count = sum(
            1 for item in items if item.build_cache_enabled and item.build_cache_reuse_existing
        )
        build_cache_id_scopes = Counter(item.build_cache_id_scope for item in items if item.build_cache_id_scope)
        final_images = Counter(item.final_image for item in items if item.final_image)
        internal_branch_image_total = sum(item.internal_branch_image_count for item in items)
        node_failures: Counter[str] = Counter()
        node_retries: Counter[str] = Counter()
        for item in items:
            node_failures.update(item.node_failures)
            node_retries.update(item.node_retries)
        summary[group] = {
            "runs": len(items),
            "successes": success_count,
            "success_rate": success_count / len(items) if items else 0.0,
            "avg_iterations": iteration_total / len(items) if items else 0.0,
            "avg_retrieved_contexts": context_total / len(items) if items else 0.0,
            "memory_retrieval_events": memory_retrieval_events_total,
            "memory_retrieved_matches": memory_retrieved_matches_total,
            "memory_precise_matches": memory_precise_matches_total,
            "topology_memory_matches": topology_memory_matches_total,
            "memory_prefill_candidates": memory_prefill_candidates_total,
            "memory_prefill_applied_packages": memory_prefill_applied_packages_total,
            "pattern_memory_matches": pattern_memory_matches_total,
            "pattern_cross_project_matches": pattern_cross_project_matches_total,
            "pattern_low_support_matches": pattern_low_support_matches_total,
            "pattern_negative_evidence_matches": pattern_negative_evidence_matches_total,
            "pattern_prefill_applied_packages": pattern_prefill_applied_packages_total,
            "runtime_validation_memory_matches": runtime_validation_memory_matches_total,
            "runtime_validation_memory_prefill_candidates": runtime_validation_memory_prefill_candidates_total,
            "avg_memory_retrieved_matches": memory_retrieved_matches_total / len(items) if items else 0.0,
            "avg_memory_precise_matches": memory_precise_matches_total / len(items) if items else 0.0,
            "avg_topology_memory_matches": topology_memory_matches_total / len(items) if items else 0.0,
            "avg_memory_prefill_candidates": memory_prefill_candidates_total / len(items) if items else 0.0,
            "avg_memory_prefill_applied_packages": memory_prefill_applied_packages_total / len(items) if items else 0.0,
            "avg_pattern_memory_matches": pattern_memory_matches_total / len(items) if items else 0.0,
            "avg_pattern_cross_project_matches": pattern_cross_project_matches_total / len(items) if items else 0.0,
            "avg_pattern_low_support_matches": pattern_low_support_matches_total / len(items) if items else 0.0,
            "avg_pattern_negative_evidence_matches": pattern_negative_evidence_matches_total / len(items) if items else 0.0,
            "avg_pattern_prefill_applied_packages": pattern_prefill_applied_packages_total / len(items) if items else 0.0,
            "avg_runtime_validation_memory_matches": runtime_validation_memory_matches_total / len(items) if items else 0.0,
            "avg_runtime_validation_memory_prefill_candidates": runtime_validation_memory_prefill_candidates_total / len(items) if items else 0.0,
            "avg_total_seconds": seconds_total / len(items) if items else 0.0,
            "avg_docker_build_count": build_count_total / len(items) if items else 0.0,
            "avg_docker_run_count": run_count_total / len(items) if items else 0.0,
            "avg_docker_build_seconds": build_seconds_total / len(items) if items else 0.0,
            "avg_docker_run_seconds": run_seconds_total / len(items) if items else 0.0,
            "cache_hit_rate": cache_hit_total / cache_step_total if cache_step_total else 0.0,
            "cache_hit_steps": cache_hit_total,
            "cache_total_steps": cache_step_total,
            "node_failures": dict(sorted(node_failures.items())),
            "node_retries": dict(sorted(node_retries.items())),
            "guardrail_violations": guardrail_violations_total,
            "avg_guardrail_violations": guardrail_violations_total / len(items) if items else 0.0,
            "prefill_validated_first_count": prefill_validated_first_count,
            "verifier_rejections": verifier_rejections_total,
            "critic_rejections": critic_rejections_total,
            "accepted_memory_writes": accepted_memory_writes_total,
            "multistage_used_count": multistage_used_count,
            "threaded_build_used_count": threaded_build_used_count,
            "avg_parallel_build_branches": parallel_build_branches_total / len(items) if items else 0.0,
            "parallel_repair_used_count": parallel_repair_used_count,
            "avg_repair_branches_tried": repair_branches_total / len(items) if items else 0.0,
            "build_cache_enabled_count": build_cache_enabled_count,
            "build_cache_reuse_existing_count": build_cache_reuse_existing_count,
            "build_cache_id_scopes": dict(sorted(build_cache_id_scopes.items())),
            "final_images": dict(sorted(final_images.items())),
            "internal_branch_image_total": internal_branch_image_total,
        }
    for group, metrics in summary.items():
        if "multistage" not in group:
            continue
        baseline_group = group.replace("topology-multistage", "topology-scheduler", 1)
        baseline_metrics = summary.get(baseline_group)
        current_build_seconds = float(metrics.get("avg_docker_build_seconds", 0.0) or 0.0)
        baseline_build_seconds = float(baseline_metrics.get("avg_docker_build_seconds", 0.0) or 0.0) if baseline_metrics else 0.0
        metrics["multistage_speedup"] = (
            baseline_build_seconds / current_build_seconds if baseline_build_seconds > 0 and current_build_seconds > 0 else 0.0
        )
    return summary


def repair_match_payload(matches: list[RetrievalMatch]) -> list[dict[str, object]]:
    return [
        {
            "score": match.score,
            "evidence_level": match.evidence_level,
            "error_signature": match.record.error_signature,
            "error_type": match.record.error_type,
            "fix_strategy": match.record.fix_strategy,
            "fix_action": match.record.fix_action,
            "matched_packages": match.matched_packages,
            "matched_tokens": match.matched_tokens,
            "matched_stage": match.matched_stage,
            "matched_error_type": match.matched_error_type,
            "matched_node": match.matched_node,
            "score_components": match.score_components,
            "metadata": match.record.metadata,
        }
        for match in matches
    ]


def _memory_path_key(path: Path) -> Path:
    return path.resolve(strict=False)


def _file_signature(path: Path) -> tuple[int, int]:
    if not path.exists():
        return (0, 0)
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _project_names_match(project_name: str, record_project: str) -> bool:
    if not project_name or not record_project:
        return False
    if project_name in record_project or record_project in project_name:
        return True
    project_tokens = {token for token in re.split(r"[-_.\s]+", project_name) if token and not re.fullmatch(r"[0-9a-f]{6,}", token)}
    record_tokens = {token for token in re.split(r"[-_.\s]+", record_project) if token}
    if not project_tokens or not record_tokens:
        return False
    overlap = project_tokens & record_tokens
    return len(overlap) >= 4 and len(overlap) / len(record_tokens) >= 0.6


def _score_evidence_level(
    score: float,
    *,
    package_overlap: float,
    exact_bonus: float,
    matched_error_type: bool,
    project_match: bool,
) -> str:
    if (
        score >= REPAIR_MEMORY_PREFILL_THRESHOLD
        or (score >= 0.23 and package_overlap >= 0.3)
        or (score >= 0.40 and project_match)
    ) and (package_overlap >= 0.3 or exact_bonus > 0 or matched_error_type or project_match):
        return "prefill"
    if score >= REPAIR_MEMORY_CANDIDATE_THRESHOLD:
        return "candidate"
    return "hint"


def _strategy_matches_error_type(error_type: str, fix_strategy: str) -> bool:
    if error_type in {
        "missing_build_tool",
        "missing_compiler",
        "missing_shared_library",
        "missing_system_binary",
        "missing_system_package",
    }:
        return fix_strategy == "install_apt_packages"
    if error_type in {"missing_python_module", "package_resolution"}:
        return fix_strategy == "install_python_package"
    return False


def _score_indexed_record(
    entry: _IndexedRepairRecord,
    *,
    query_signature: str,
    query_tokens: list[str],
    query_distinctive: set[str],
    query_package_signals: set[str],
    query_stage: str,
    query_error_type: str,
    query_node_id: str,
    project_match: bool,
) -> RetrievalMatch:
    signature_score = _cosine_similarity(query_tokens, list(entry.signature_tokens))
    message_score = _cosine_similarity(query_tokens, list(entry.message_tokens))
    context_score = _cosine_similarity(query_tokens, list(entry.context_tokens))
    distinctive_overlap = (
        len(query_distinctive & entry.distinctive_tokens) / len(query_distinctive) if query_distinctive else 0.0
    )
    package_overlap = 0.0
    matched_packages: list[str] = []
    if query_package_signals and entry.package_signals:
        matched_packages = sorted(query_package_signals & entry.package_signals)
        package_overlap = len(matched_packages) / max(min(len(query_package_signals), len(entry.package_signals)), 1)
    exact_bonus = _signature_match_bonus(query_signature, entry.record.error_signature)
    matched_stage = bool(query_stage and query_stage == entry.record.stage)
    matched_error_type = bool(query_error_type and query_error_type == entry.record.error_type and query_error_type != "generic")
    matched_node = bool(query_node_id and query_node_id == entry.topology_node_id)
    strategy_match = _strategy_matches_error_type(query_error_type, entry.record.fix_strategy)
    components = {
        "signature": round(signature_score, 4),
        "message": round(message_score, 4),
        "context": round(context_score, 4),
        "distinctive": round(distinctive_overlap, 4),
        "package": round(package_overlap, 4),
        "exact": round(exact_bonus, 4),
        "stage": 0.05 if matched_stage else 0.0,
        "error_type": 0.12 if matched_error_type else 0.0,
        "node": 0.12 if matched_node else 0.0,
        "project": 0.42 if project_match else 0.0,
        "strategy": 0.06 if strategy_match else 0.0,
    }
    score = min(
        1.0,
        exact_bonus
        + (0.34 * signature_score)
        + (0.18 * message_score)
        + (0.18 * context_score)
        + (0.12 * distinctive_overlap)
        + (0.32 * package_overlap)
        + components["stage"]
        + components["error_type"]
        + components["node"]
        + components["project"]
        + components["strategy"],
    )
    matched_tokens = sorted(query_distinctive & entry.distinctive_tokens)[:12]
    rounded_score = round(score, 4)
    return RetrievalMatch(
        score=rounded_score,
        record=entry.record,
        score_components=components,
        matched_tokens=matched_tokens,
        matched_packages=matched_packages[:12],
        matched_stage=matched_stage,
        matched_error_type=matched_error_type,
        matched_node=matched_node,
        evidence_level=_score_evidence_level(
            rounded_score,
            package_overlap=package_overlap,
            exact_bonus=exact_bonus,
            matched_error_type=matched_error_type,
            project_match=project_match,
        ),
    )


class RepairMemory:
    def __init__(self, path: str | Path, *, create: bool = True):
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if create and not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def add_record(self, record: RepairRecord) -> None:
        with REPAIR_MEMORY_WRITE_LOCK:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json())
                handle.write("\n")
        with REPAIR_MEMORY_INDEX_LOCK:
            REPAIR_MEMORY_INDEX_CACHE.pop(_memory_path_key(self.path), None)

    def load_records(self) -> list[RepairRecord]:
        records: list[RepairRecord] = []
        if not self.path.exists():
            return records
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                records.append(RepairRecord.model_validate(json.loads(line)))
        return records

    def retrieve(
        self,
        error_text: str,
        *,
        top_k: int = 3,
        min_score: float = 0.15,
        success_only: bool = True,
        project_name: str = "",
        project_key: str = "",
        allow_same_project_memory: bool = True,
        stage: str = "",
        error_type: str = "",
        topology_node_id: str = "",
        package_signals: list[str] | None = None,
    ) -> list[RetrievalMatch]:
        query_signature = extract_error_signature(error_text)
        normalized_package_signals = expand_memory_signals(package_signals or [])
        query_tokens = tokenize_error_text(query_signature + " " + error_text + " " + " ".join(normalized_package_signals))
        query_distinctive = _distinctive_tokens(query_tokens)
        query_error_type = error_type or classify_error_type(query_signature, error_text)
        query_stage = stage.strip().lower()
        query_node_id = normalize_memory_signal(topology_node_id)
        normalized_project_name = normalize_error_text(project_name)
        normalized_project_key = normalize_error_text(project_key)
        matches: list[RetrievalMatch] = []

        for entry in self._load_index():
            record = entry.record
            if success_only and not record.success:
                continue
            if not allow_same_project_memory:
                if normalized_project_key and entry.project_key and normalized_project_key == entry.project_key:
                    continue
                if normalized_project_name and entry.project_name and (
                    normalized_project_name in entry.project_name or entry.project_name in normalized_project_name
                ):
                    continue
            match = _score_indexed_record(
                entry,
                query_signature=query_signature,
                query_tokens=query_tokens,
                query_distinctive=query_distinctive,
                query_package_signals=normalized_package_signals,
                query_stage=query_stage,
                query_error_type=query_error_type,
                query_node_id=query_node_id,
                project_match=allow_same_project_memory
                and (
                    _project_names_match(normalized_project_name, entry.project_name)
                    or bool(normalized_project_key and entry.project_key and normalized_project_key == entry.project_key)
                ),
            )
            if match.score >= min_score:
                matches.append(match)

        matches.sort(
            key=lambda item: (
                item.score,
                item.evidence_level == "prefill",
                item.matched_error_type,
                len(item.matched_packages),
            ),
            reverse=True,
        )
        return matches[:top_k]

    def _load_index(self) -> list[_IndexedRepairRecord]:
        path_key = _memory_path_key(self.path)
        signature = _file_signature(self.path)
        with REPAIR_MEMORY_INDEX_LOCK:
            cached = REPAIR_MEMORY_INDEX_CACHE.get(path_key)
            if cached is not None and cached[0] == signature:
                return cached[1]
        records = self.load_records()
        indexed = [_indexed_record(record) for record in records]
        with REPAIR_MEMORY_INDEX_LOCK:
            REPAIR_MEMORY_INDEX_CACHE[path_key] = (signature, indexed)
        return indexed
