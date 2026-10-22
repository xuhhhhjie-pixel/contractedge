from __future__ import annotations

import ast
import hashlib
import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from topomen.memory.pattern_memory import (
    HIGH_CONFIDENCE_PREFILL_THRESHOLD,
    PatternMemory,
    format_pattern_hints,
    pattern_match_payload,
    pattern_prefill_packages,
)
from topomen.memory.repair_memory import (
    REPAIR_MEMORY_CANDIDATE_THRESHOLD,
    REPAIR_MEMORY_PREFILL_THRESHOLD,
    RepairMemory,
    RetrievalMatch,
    classify_error_type,
    extract_apt_packages,
    extract_error_signature,
    extract_python_packages,
    normalize_error_text,
    repair_match_payload,
)
from topomen.memory.runtime_validation_memory import (
    RuntimeValidationMatch,
    RuntimeValidationMemory,
    infer_runtime_validation_signals,
    runtime_validation_match_payload,
)

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "experiments",
    "build",
    "dist",
    "node_modules",
    "venv",
}

IGNORED_IMPORT_MODULES = {
    "__future__",
    "argparse",
    "ast",
    "collections",
    "csv",
    "datetime",
    "functools",
    "hashlib",
    "importlib",
    "io",
    "itertools",
    "json",
    "logging",
    "math",
    "os",
    "pathlib",
    "re",
    "shutil",
    "sqlite3",
    "subprocess",
    "sys",
    "tempfile",
    "threading",
    "time",
    "typing",
    "unittest",
}

DEPENDENCY_TO_IMPORT = {
    "opencv-contrib-python": "cv2",
    "opencv-python": "cv2",
    "pillow": "pil",
    "psycopg2-binary": "psycopg2",
    "python-dateutil": "dateutil",
    "python-magic": "magic",
    "pyyaml": "yaml",
}
IMPORT_TO_DEPENDENCY = {import_name: dependency for dependency, import_name in DEPENDENCY_TO_IMPORT.items()}

SYSTEM_PACKAGE_HINTS = {
    "cv2": ["libgl1", "libglib2.0-0"],
    "cffi": ["gcc", "build-essential", "libffi-dev"],
    "curl": ["libcurl4-openssl-dev"],
    "ffmpeg": ["ffmpeg"],
    "librosa": ["libsndfile1"],
    "lxml": ["libxml2-dev", "libxslt1-dev"],
    "magic": ["libmagic1"],
    "mysqlclient": ["default-libmysqlclient-dev"],
    "numpy": ["libgomp1"],
    "pil": ["libjpeg-dev", "zlib1g-dev"],
    "psycopg2": ["libpq-dev", "gcc", "build-essential"],
    "pycurl": ["libcurl4-openssl-dev"],
    "pytesseract": ["tesseract-ocr"],
    "sklearn": ["libgomp1"],
    "soundfile": ["libsndfile1"],
    "ujson": ["gcc", "build-essential", "g++"],
}
LEGACY_SYSTEM_PACKAGE_ALIASES = {
    "libgl1-mesa-glx": "libgl1",
}

ENTRYPOINT_NAMES = {"app.py", "main.py", "server.py", "run.py"}
DEPENDENCY_FILE_PATTERNS = ("requirements*.txt", "requirements/*.txt", "pyproject.toml", "setup.cfg", "setup.py")
SYSTEM_DEPENDENCY_ERROR_TYPES = {
    "missing_build_tool",
    "missing_compiler",
    "missing_shared_library",
    "missing_system_binary",
    "missing_system_package",
}
PYTHON_DEPENDENCY_ERROR_TYPES = {"missing_python_module", "package_resolution"}
EXECUTABLE_DOCKERFILE_NODE_ORDER = [
    "base_image_selection",
    "system_dependency_plan",
    "python_dependency_plan",
    "file_copy_plan",
    "runtime_validation",
]

RUNTIME_VALIDATION_MODES = {"script", "package_import", "web_service", "test", "noop"}
WEB_FRAMEWORK_IMPORTS = {"django", "fastapi", "flask", "sanic", "starlette", "uvicorn"}


class TopologyEvidenceItem(BaseModel):
    source_type: str
    raw_signal: str
    inferred_target: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict[str, str] = Field(default_factory=dict)


class RuntimeValidationPlan(BaseModel):
    mode: str = "noop"
    container_command: list[str] = Field(default_factory=lambda: ["python", "-c", "print('container ready')"])
    host_command: str = "docker run --rm {image_name} python -c \"print('container ready')\" 2>&1"
    command_source: str = "fallback"
    confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    reason: str = "No reliable runtime entrypoint was detected."
    expected_success_signal: str = "returncode == 0"
    selected_target: str = ""
    candidate_modes: list[dict[str, str]] = Field(default_factory=list)
    rejected_candidates: list[dict[str, str]] = Field(default_factory=list)
    planner_signals: list[str] = Field(default_factory=list)
    fallback_candidates: list[dict[str, str | list[str] | float]] = Field(default_factory=list)
    max_attempts: int = Field(default=1, ge=1, le=6)
    planner_version: str = "runtime-planner-v2"


class BuildCachePlan(BaseModel):
    enabled: bool = False
    strategy: str = "layer_order"
    namespace: str = "topomen"
    dependency_hash: str = ""
    reuse_existing: bool = True
    cache_id_scope: str = "shared"
    requires_buildkit: bool = False
    buildkit_frontend_image: str = "docker/dockerfile:1.7"
    apt_cache_id: str = ""
    apt_lists_cache_id: str = ""
    pip_cache_id: str = ""
    cache_targets: dict[str, str] = Field(default_factory=dict)


class ProjectAnalysis(BaseModel):
    project_path: str
    detected_files: list[str] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    python_packages: list[str] = Field(default_factory=list)
    missing_python_packages: list[str] = Field(default_factory=list)
    external_commands: list[str] = Field(default_factory=list)
    pattern_apt_packages: list[str] = Field(default_factory=list)
    pattern_python_packages: list[str] = Field(default_factory=list)
    pattern_memory_hints: list[str] = Field(default_factory=list)
    pattern_memory_matches: list[dict] = Field(default_factory=list)
    repair_memory_matches: list[dict] = Field(default_factory=list)
    runtime_validation_memory_matches: list[dict] = Field(default_factory=list)
    memory_apt_packages: list[str] = Field(default_factory=list)
    memory_python_packages: list[str] = Field(default_factory=list)
    imported_modules: list[str] = Field(default_factory=list)
    local_modules: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    base_image: str = "python:3.11-slim"
    base_image_source: str = "python:3.11-slim"
    docker_registry_mirror: str = ""
    system_packages: list[str] = Field(default_factory=list)
    repair_memory_hints: list[str] = Field(default_factory=list)
    dependency_evidence: list[TopologyEvidenceItem] = Field(default_factory=list)
    parallel_tasks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    runtime_validation: RuntimeValidationPlan = Field(default_factory=RuntimeValidationPlan)
    build_cache: BuildCachePlan = Field(default_factory=BuildCachePlan)
    scan_strategy: str = "legacy_rglob"
    python_files_scanned: int = 0
    python_scan_budget: int = 80
    python_scan_max_bytes: int = 262144
    topology_planning_seconds: float = Field(default=0.0, ge=0.0)


class TopologyNode(BaseModel):
    node_id: str
    title: str
    node_type: str
    depends_on: list[str] = Field(default_factory=list)
    description: str = ""
    evidence: list[str] = Field(default_factory=list)
    outputs: dict[str, list[str] | str] = Field(default_factory=dict)
    status: str = "planned"
    parallel_group: str = "serial"
    dockerfile_block: str = ""
    cache_scope: str = "none"
    validation_hint: str = ""
    evidence_items: list[TopologyEvidenceItem] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    owned_artifacts: list[str] = Field(default_factory=list)
    validation_command: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    scheduler_role: str = ""
    parallel_safe: bool = False
    resource_keys: list[str] = Field(default_factory=list)
    estimated_cost_weight: float = Field(default=1.0, ge=0.0)
    checkpoint_scope: str = ""
    repair_scope: str = ""


class TopologyBuildStep(BaseModel):
    step_id: str
    node_id: str
    scheduler_role: str
    depends_on: list[str] = Field(default_factory=list)
    resource_keys: list[str] = Field(default_factory=list)
    parallel_safe: bool = False
    estimated_cost_weight: float = Field(default=1.0, ge=0.0)
    checkpoint_scope: str = ""
    repair_scope: str = ""


class TopologyBuildPlan(BaseModel):
    strategy: str = "topology_dag"
    steps: list[TopologyBuildStep] = Field(default_factory=list)
    resource_limits: dict[str, int] = Field(default_factory=dict)
    final_step_id: str = "merge-final"
    runtime_step_id: str = "runtime-validation"


class FailureAttribution(BaseModel):
    stage: str
    error_signature: str
    error_type: str
    topology_node_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class DockerfileGuardResult(BaseModel):
    valid: bool
    violations: list[str] = Field(default_factory=list)


class BuildTopology(BaseModel):
    project_path: str
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    analysis: ProjectAnalysis
    nodes: list[TopologyNode]
    execution_layers: list[list[str]]
    multistage: bool = False
    parallel_build_stages: list[str] = Field(default_factory=list)

    def node_map(self) -> dict[str, TopologyNode]:
        return {node.node_id: node for node in self.nodes}

    def to_mermaid(self) -> str:
        lines = ["flowchart TD"]
        for node in self.nodes:
            label = _escape_mermaid_label(f"{node.title}\\n({node.node_type})")
            lines.append(f'    {node.node_id}["{label}"]')
        for node in self.nodes:
            for dependency in node.depends_on:
                lines.append(f"    {dependency} --> {node.node_id}")
        return "\n".join(lines)


def plan_project_topology(
    project_path: str | Path,
    *,
    repair_memory_path: str | Path | None = None,
    pattern_memory_path: str | Path | None = None,
    runtime_validation_memory_path: str | Path | None = None,
    project_key: str = "",
    allow_same_project_memory: bool = True,
    allow_same_project_pattern_memory: bool = False,
    allow_same_project_runtime_memory: bool = False,
    max_workers: int = 4,
    python_scan_budget: int = 80,
    python_scan_max_bytes: int = 262144,
    python_scan_priority_dirs: list[str] | None = None,
    build_cache_enabled: bool = False,
    build_cache_namespace: str = "topomen",
    build_cache_reuse_existing: bool = True,
    docker_registry_mirror_enabled: bool = False,
    docker_registry_mirror: str = "",
    docker_buildkit_frontend_image: str = "docker/dockerfile:1.7",
) -> BuildTopology:
    analysis = analyze_project(
        project_path,
        repair_memory_path=repair_memory_path,
        pattern_memory_path=pattern_memory_path,
        runtime_validation_memory_path=runtime_validation_memory_path,
        project_key=project_key,
        allow_same_project_memory=allow_same_project_memory,
        allow_same_project_pattern_memory=allow_same_project_pattern_memory,
        allow_same_project_runtime_memory=allow_same_project_runtime_memory,
        max_workers=max_workers,
        python_scan_budget=python_scan_budget,
        python_scan_max_bytes=python_scan_max_bytes,
        python_scan_priority_dirs=python_scan_priority_dirs,
        build_cache_enabled=build_cache_enabled,
        build_cache_namespace=build_cache_namespace,
        build_cache_reuse_existing=build_cache_reuse_existing,
        docker_registry_mirror_enabled=docker_registry_mirror_enabled,
        docker_registry_mirror=docker_registry_mirror,
        docker_buildkit_frontend_image=docker_buildkit_frontend_image,
    )
    return create_build_topology(analysis)


def analyze_project(
    project_path: str | Path,
    *,
    repair_memory_path: str | Path | None = None,
    pattern_memory_path: str | Path | None = None,
    runtime_validation_memory_path: str | Path | None = None,
    project_key: str = "",
    allow_same_project_memory: bool = True,
    allow_same_project_pattern_memory: bool = False,
    allow_same_project_runtime_memory: bool = False,
    max_workers: int = 4,
    python_scan_budget: int = 80,
    python_scan_max_bytes: int = 262144,
    python_scan_priority_dirs: list[str] | None = None,
    build_cache_enabled: bool = False,
    build_cache_namespace: str = "topomen",
    build_cache_reuse_existing: bool = True,
    docker_registry_mirror_enabled: bool = False,
    docker_registry_mirror: str = "",
    docker_buildkit_frontend_image: str = "docker/dockerfile:1.7",
) -> ProjectAnalysis:
    started_at = datetime.now(timezone.utc)
    project_path = Path(project_path)
    workers = max(1, max_workers)
    warnings: list[str] = []
    if not project_path.exists():
        warnings.append(f"Project path does not exist: {project_path}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            "project_scan": executor.submit(_collect_project_files, project_path),
            "dependency_scan": executor.submit(_collect_dependency_info, project_path),
            "entrypoint_scan": executor.submit(
                _collect_python_signals,
                project_path,
                max_python_files=max(1, int(python_scan_budget)),
                max_bytes_per_file=max(4096, int(python_scan_max_bytes)),
                priority_dirs=python_scan_priority_dirs,
            ),
        }
        detected_files = futures["project_scan"].result()
        dependency_files, python_packages, requires_python = futures["dependency_scan"].result()
        imported_modules, entry_points, local_modules, external_commands, scan_stats = futures["entrypoint_scan"].result()

    dependency_signals = _dedupe([*python_packages, *imported_modules])
    matching_signals = _dedupe([*dependency_signals, *external_commands])
    base_image_source = infer_base_image(requires_python)
    normalized_registry_mirror = _normalize_registry_mirror(
        docker_registry_mirror if docker_registry_mirror_enabled else ""
    )
    base_image = resolve_docker_registry_mirror(
        base_image_source,
        registry_mirror=normalized_registry_mirror,
    )
    buildkit_frontend_image = resolve_docker_registry_mirror(
        docker_buildkit_frontend_image,
        registry_mirror=normalized_registry_mirror,
    )
    static_system_packages, static_system_warnings = normalize_system_packages(
        infer_system_packages(dependency_signals),
        base_image=base_image,
    )
    missing_python_packages = infer_missing_python_packages(python_packages, imported_modules, local_modules=local_modules)
    normalized_project_key = _normalize_package_name(project_key)
    pattern_matches = _collect_pattern_memory_matches(
        pattern_memory_path,
        matching_signals,
        project_name=project_path.name,
        project_key=normalized_project_key,
        allow_same_project_memory=allow_same_project_pattern_memory,
    )
    pattern_apt_packages, pattern_python_packages = pattern_prefill_packages(pattern_matches)
    pattern_apt_packages, pattern_system_warnings = normalize_system_packages(
        pattern_apt_packages,
        base_image=base_image,
    )
    pattern_hints = format_pattern_hints(pattern_matches)
    repair_matches = _collect_repair_memory_matches(
        repair_memory_path,
        [*matching_signals, *static_system_packages, *pattern_apt_packages, *pattern_python_packages],
        project_name=project_path.name,
        project_key=normalized_project_key,
        allow_same_project_memory=allow_same_project_memory,
    )
    memory_apt_packages, memory_python_packages = _repair_memory_prefill_packages(repair_matches)
    memory_apt_packages, memory_system_warnings = normalize_system_packages(
        memory_apt_packages,
        base_image=base_image,
    )
    system_packages = _dedupe([*static_system_packages, *pattern_apt_packages, *memory_apt_packages])
    build_cache = plan_build_cache(
        project_path,
        dependency_files=dependency_files,
        base_image=base_image,
        system_packages=system_packages,
        python_packages=python_packages,
        missing_python_packages=missing_python_packages,
        pattern_python_packages=pattern_python_packages,
        memory_python_packages=memory_python_packages,
        enabled=build_cache_enabled,
        namespace=build_cache_namespace,
        reuse_existing=build_cache_reuse_existing,
        buildkit_frontend_image=buildkit_frontend_image,
    )
    repair_hints = _format_repair_memory_hints(repair_matches)
    warnings.extend([*static_system_warnings, *pattern_system_warnings, *memory_system_warnings])
    dependency_evidence = build_dependency_evidence(
        python_packages=python_packages,
        imported_modules=imported_modules,
        local_modules=local_modules,
        external_commands=external_commands,
        missing_python_packages=missing_python_packages,
        pattern_python_packages=pattern_python_packages,
        memory_python_packages=memory_python_packages,
        system_packages=system_packages,
        pattern_memory_matches=pattern_matches,
        pattern_memory_hints=pattern_hints,
        repair_memory_hints=repair_hints,
        repair_memory_matches=repair_matches,
    )

    analysis = ProjectAnalysis(
        project_path=str(project_path),
        detected_files=detected_files,
        dependency_files=dependency_files,
        python_packages=python_packages,
        missing_python_packages=missing_python_packages,
        external_commands=external_commands,
        pattern_apt_packages=pattern_apt_packages,
        pattern_python_packages=pattern_python_packages,
        pattern_memory_hints=pattern_hints,
        pattern_memory_matches=pattern_match_payload(pattern_matches),
        repair_memory_matches=repair_match_payload(repair_matches),
        memory_apt_packages=memory_apt_packages,
        memory_python_packages=memory_python_packages,
        imported_modules=imported_modules,
        local_modules=local_modules,
        entry_points=entry_points,
        base_image=base_image,
        base_image_source=base_image_source,
        docker_registry_mirror=normalized_registry_mirror,
        system_packages=system_packages,
        repair_memory_hints=repair_hints,
        dependency_evidence=dependency_evidence,
        parallel_tasks=sorted(futures),
        warnings=warnings,
        build_cache=build_cache,
        scan_strategy=str(scan_stats.get("strategy", "legacy_rglob")),
        python_files_scanned=int(scan_stats.get("python_files_scanned", 0)),
        python_scan_budget=max(1, int(python_scan_budget)),
        python_scan_max_bytes=max(4096, int(python_scan_max_bytes)),
        topology_planning_seconds=max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds()),
    )
    runtime_matches = _collect_runtime_validation_memory_matches(
        runtime_validation_memory_path,
        _runtime_validation_memory_signals(analysis),
        project_name=project_path.name,
        project_key=normalized_project_key,
        allow_same_project_memory=allow_same_project_runtime_memory,
    )
    return analysis.model_copy(
        update={
            "runtime_validation": plan_runtime_validation(analysis, runtime_validation_matches=runtime_matches),
            "runtime_validation_memory_matches": runtime_validation_match_payload(runtime_matches),
        }
    )


def infer_base_image(requires_python: str = "") -> str:
    version_match = re.search(r"3\.(1[0-9]|[8-9])", requires_python)
    if not version_match:
        return "python:3.11-slim"
    minor = version_match.group(0)
    if minor in {"3.8", "3.9", "3.10", "3.11", "3.12"}:
        return f"python:{minor}-slim"
    return "python:3.11-slim"


def resolve_docker_registry_mirror(image: str, *, registry_mirror: str = "") -> str:
    """Resolve Docker Hub style images through an optional registry mirror."""
    image = image.strip()
    mirror = _normalize_registry_mirror(registry_mirror)
    if not image or not mirror or _image_has_explicit_registry(image):
        return image
    if "/" not in _image_name_without_tag(image):
        return f"{mirror}/library/{image}"
    return f"{mirror}/{image}"


def _normalize_registry_mirror(registry_mirror: str) -> str:
    mirror = registry_mirror.strip().rstrip("/")
    mirror = re.sub(r"^https?://", "", mirror)
    return mirror.strip("/")


def _image_has_explicit_registry(image: str) -> bool:
    if "/" not in image:
        return False
    first_component = image.split("/", 1)[0]
    return "." in first_component or ":" in first_component or first_component == "localhost"


def _image_name_without_tag(image: str) -> str:
    image = image.split("@", 1)[0]
    if "/" not in image:
        return image.split(":", 1)[0]
    repository, _, tail = image.rpartition("/")
    return f"{repository}/{tail.split(':', 1)[0]}"


def infer_system_packages(signals: list[str]) -> list[str]:
    normalized_signals = set()
    for signal in signals:
        normalized = _normalize_package_name(signal)
        normalized_signals.add(normalized)
        normalized_signals.add(DEPENDENCY_TO_IMPORT.get(normalized, normalized))

    packages: list[str] = []
    for signal in sorted(normalized_signals):
        packages.extend(SYSTEM_PACKAGE_HINTS.get(signal, []))
    return _dedupe(normalize_system_package_name(package) for package in packages)


def normalize_system_package_name(name: str) -> str:
    normalized = _normalize_package_name(name)
    return LEGACY_SYSTEM_PACKAGE_ALIASES.get(normalized, normalized)


def normalize_system_packages(
    packages: list[str],
    *,
    base_image: str = "",
) -> tuple[list[str], list[str]]:
    normalized_packages: list[str] = []
    warnings: list[str] = []
    for package in packages:
        original = _normalize_package_name(package)
        normalized = normalize_system_package_name(package)
        if normalized != original:
            warning = f"Normalized legacy apt package '{original}' -> '{normalized}' for base image {base_image or 'unknown'}."
            warnings.append(warning)
        normalized_packages.append(normalized)
    return _dedupe(normalized_packages), _dedupe(warnings)


def plan_build_cache(
    project_path: str | Path,
    *,
    dependency_files: list[str],
    base_image: str,
    system_packages: list[str],
    python_packages: list[str],
    missing_python_packages: list[str],
    pattern_python_packages: list[str],
    memory_python_packages: list[str],
    enabled: bool = False,
    namespace: str = "topomen",
    reuse_existing: bool = True,
    buildkit_frontend_image: str = "docker/dockerfile:1.7",
) -> BuildCachePlan:
    project_path = Path(project_path)
    normalized_namespace = _safe_cache_id(namespace or "topomen")
    dependency_hash = _build_dependency_hash(
        project_path,
        dependency_files=dependency_files,
        base_image=base_image,
        system_packages=system_packages,
        python_packages=python_packages,
        missing_python_packages=missing_python_packages,
        pattern_python_packages=pattern_python_packages,
        memory_python_packages=memory_python_packages,
    )
    cache_id_scope = "shared" if reuse_existing else "workspace"
    cache_key_hash = dependency_hash if reuse_existing else f"{dependency_hash}-{_build_workspace_hash(project_path)}"
    if not enabled:
        return BuildCachePlan(
            enabled=False,
            strategy="layer_order",
            namespace=normalized_namespace,
            dependency_hash=dependency_hash,
            reuse_existing=reuse_existing,
            cache_id_scope=cache_id_scope,
            buildkit_frontend_image=buildkit_frontend_image,
        )

    return BuildCachePlan(
        enabled=True,
        strategy="buildkit_cache_mount",
        namespace=normalized_namespace,
        dependency_hash=dependency_hash,
        reuse_existing=reuse_existing,
        cache_id_scope=cache_id_scope,
        requires_buildkit=True,
        buildkit_frontend_image=buildkit_frontend_image,
        apt_cache_id=f"{normalized_namespace}-apt-{cache_key_hash}",
        apt_lists_cache_id=f"{normalized_namespace}-apt-lists-{cache_key_hash}",
        pip_cache_id=f"{normalized_namespace}-pip-{cache_key_hash}",
        cache_targets={
            "apt_cache": "/var/cache/apt",
            "apt_lists": "/var/lib/apt/lists",
            "pip": "/root/.cache/pip",
        },
    )


def infer_missing_python_packages(
    python_packages: list[str],
    imported_modules: list[str],
    *,
    local_modules: list[str] | None = None,
) -> list[str]:
    installed_signals: set[str] = set()
    for package in python_packages:
        normalized = _normalize_package_name(package)
        installed_signals.add(normalized)
        installed_signals.add(DEPENDENCY_TO_IMPORT.get(normalized, normalized))

    local_module_signals = {_normalize_package_name(module) for module in local_modules or []}
    missing: list[str] = []
    for module in imported_modules:
        normalized_module = _normalize_package_name(module)
        if normalized_module in local_module_signals:
            continue
        if normalized_module in installed_signals:
            continue
        candidate = IMPORT_TO_DEPENDENCY.get(normalized_module, normalized_module)
        if candidate in installed_signals:
            continue
        missing.append(candidate)
        installed_signals.add(candidate)
        installed_signals.add(DEPENDENCY_TO_IMPORT.get(candidate, candidate))
    return _dedupe(missing)


def plan_runtime_validation(
    analysis: ProjectAnalysis,
    *,
    runtime_validation_matches: list[RuntimeValidationMatch] | None = None,
) -> RuntimeValidationPlan:
    project_path = Path(analysis.project_path)
    imported_modules = {_normalize_package_name(module) for module in analysis.imported_modules}
    python_packages = {_normalize_package_name(package) for package in analysis.python_packages}
    planner_signals: list[str] = []
    rejected_candidates: list[dict[str, str]] = []
    candidate_modes: list[dict[str, str]] = []
    if imported_modules:
        planner_signals.append(f"imports={','.join(sorted(imported_modules)[:8])}")
    if python_packages:
        planner_signals.append(f"dependencies={','.join(sorted(python_packages)[:8])}")
    if runtime_validation_matches:
        best_memory_match = next((match for match in runtime_validation_matches if match.prefill_ready), None)
        if best_memory_match is not None:
            record = best_memory_match.record
            candidate_modes.append(
                {
                    "mode": record.mode,
                    "selected": "true",
                    "reason": f"runtime validation memory matched {record.runtime_id}",
                }
            )
            command = record.container_command or ["python", "-c", "print('container ready')"]
            return _runtime_plan(
                mode=record.mode,
                command=command,
                command_source="runtime_validation_memory",
                confidence=min(0.95, max(record.confidence, best_memory_match.score)),
                reason=record.reason or f"Runtime validation memory matched {record.runtime_id}.",
                expected_success_signal=record.expected_success_signal,
                selected_target=record.selected_target,
                candidate_modes=candidate_modes,
                rejected_candidates=rejected_candidates,
                planner_signals=[*planner_signals, f"runtime_memory:{record.runtime_id}"],
                fallback_candidates=_build_runtime_fallback_candidates(
                    analysis,
                    primary_mode=record.mode,
                    primary_command=command,
                ),
            )

    def _reject(mode: str, reason: str) -> None:
        rejected_candidates.append({"mode": mode, "reason": reason})

    if imported_modules & WEB_FRAMEWORK_IMPORTS or python_packages & WEB_FRAMEWORK_IMPORTS:
        candidate_modes.append({"mode": "web_service", "selected": "true", "reason": "web framework detected"})
        command = ["python", "-m", "compileall", "-q", "."]
        return _runtime_plan(
            mode="web_service",
            command=command,
            command_source="web_framework_static_scan",
            confidence=0.75,
            reason="Web framework imports were detected; use compile smoke instead of starting a long-running service.",
            expected_success_signal="Python sources compile successfully without starting a server.",
            selected_target="compileall",
            candidate_modes=candidate_modes,
            rejected_candidates=rejected_candidates,
            planner_signals=planner_signals,
            fallback_candidates=_build_runtime_fallback_candidates(
                analysis,
                primary_mode="web_service",
                primary_command=command,
            ),
        )
    _reject("web_service", "no web framework imports detected")

    script_entrypoint = _select_script_entrypoint(analysis)
    if script_entrypoint:
        candidate_modes.append({"mode": "script", "selected": "true", "reason": "entrypoint file detected"})
        command = ["python", script_entrypoint]
        return _runtime_plan(
            mode="script",
            command=command,
            command_source="script_entrypoint_static_scan",
            confidence=0.85,
            reason=f"Script entrypoint detected: {script_entrypoint}.",
            expected_success_signal="Script exits with returncode 0.",
            selected_target=script_entrypoint,
            candidate_modes=candidate_modes,
            rejected_candidates=rejected_candidates,
            planner_signals=planner_signals,
            fallback_candidates=_build_runtime_fallback_candidates(
                analysis,
                primary_mode="script",
                primary_command=command,
            ),
        )
    _reject("script", "no script entrypoint file detected")

    pyproject_script_module = _select_pyproject_script_module(project_path)
    if pyproject_script_module:
        candidate_modes.append({"mode": "script", "selected": "true", "reason": "pyproject script target detected"})
        command = ["python", "-m", pyproject_script_module]
        return _runtime_plan(
            mode="script",
            command=command,
            command_source="pyproject_script_static_scan",
            confidence=0.8,
            reason=f"pyproject script target detected: {pyproject_script_module}.",
            expected_success_signal=f"Module {pyproject_script_module} runs with returncode 0.",
            selected_target=pyproject_script_module,
            candidate_modes=candidate_modes,
            rejected_candidates=rejected_candidates,
            planner_signals=planner_signals,
            fallback_candidates=_build_runtime_fallback_candidates(
                analysis,
                primary_mode="script",
                primary_command=command,
            ),
        )
    _reject("script", "no [project.scripts] target detected")

    if _has_tests(project_path, analysis.detected_files):
        command = ["python", "-m", "pytest", "-q"] if _tests_use_pytest(project_path, python_packages) else ["python", "-m", "unittest", "discover", "-s", "tests"]
        selected_test_mode = "pytest" if command[2] == "pytest" else "unittest"
        candidate_modes.append({"mode": "test", "selected": "true", "reason": f"{selected_test_mode} test layout detected"})
        return _runtime_plan(
            mode="test",
            command=command,
            command_source="test_layout_static_scan",
            confidence=0.8,
            reason="Tests were detected without a script entrypoint; use the test runner as runtime smoke.",
            expected_success_signal="Selected test command exits with returncode 0.",
            selected_target=selected_test_mode,
            candidate_modes=candidate_modes,
            rejected_candidates=rejected_candidates,
            planner_signals=planner_signals,
            fallback_candidates=_build_runtime_fallback_candidates(
                analysis,
                primary_mode="test",
                primary_command=command,
            ),
        )
    _reject("test", "no tests directory or test files detected")

    import_module = _select_import_smoke_module(analysis)
    if import_module:
        code = f"import {import_module}; print('runtime validation ok')"
        command = ["python", "-c", code]
        candidate_modes.append({"mode": "package_import", "selected": "true", "reason": "importable module detected"})
        return _runtime_plan(
            mode="package_import",
            command=command,
            command_source="package_import_static_scan",
            confidence=0.7,
            reason=f"No executable entrypoint was detected; validate importability of {import_module}.",
            expected_success_signal=f"Module {import_module} imports successfully.",
            selected_target=import_module,
            candidate_modes=candidate_modes,
            rejected_candidates=rejected_candidates,
            planner_signals=planner_signals,
            fallback_candidates=_build_runtime_fallback_candidates(
                analysis,
                primary_mode="package_import",
                primary_command=command,
            ),
        )
    _reject("package_import", "no importable local or dependency module detected")
    candidate_modes.append({"mode": "noop", "selected": "true", "reason": "fallback"})

    command = ["python", "-c", "print('container ready')"]
    return _runtime_plan(
        mode="noop",
        command=command,
        command_source="fallback_noop",
        confidence=0.25,
        reason="No script entrypoint, tests, web framework, or importable package signal was detected.",
        expected_success_signal="Container starts and Python exits with returncode 0.",
        selected_target="container_ready",
        candidate_modes=candidate_modes,
        rejected_candidates=rejected_candidates,
        planner_signals=planner_signals,
        fallback_candidates=[],
    )


def build_dependency_evidence(
    *,
    python_packages: list[str],
    imported_modules: list[str],
    local_modules: list[str],
    external_commands: list[str] | None = None,
    missing_python_packages: list[str],
    pattern_python_packages: list[str] | None = None,
    system_packages: list[str],
    pattern_memory_matches: list | None = None,
    pattern_memory_hints: list[str] | None = None,
    repair_memory_hints: list[str],
    repair_memory_matches: list[RetrievalMatch] | None = None,
    memory_python_packages: list[str] | None = None,
) -> list[TopologyEvidenceItem]:
    evidence: list[TopologyEvidenceItem] = []
    for package in python_packages:
        evidence.append(
            TopologyEvidenceItem(
                source_type="dependency_file",
                raw_signal=package,
                inferred_target=package,
                confidence=0.95,
            )
        )
    for module in imported_modules:
        evidence.append(
            TopologyEvidenceItem(
                source_type="source_import",
                raw_signal=module,
                inferred_target=IMPORT_TO_DEPENDENCY.get(module, module),
                confidence=0.75,
            )
        )
    for module in local_modules:
        evidence.append(
            TopologyEvidenceItem(
                source_type="local_module",
                raw_signal=module,
                inferred_target=module,
                confidence=0.9,
            )
        )
    for command in external_commands or []:
        evidence.append(
            TopologyEvidenceItem(
                source_type="external_command",
                raw_signal=command,
                inferred_target=command,
                confidence=0.65,
            )
        )
    for package in missing_python_packages:
        import_name = DEPENDENCY_TO_IMPORT.get(package, package)
        evidence.append(
            TopologyEvidenceItem(
                source_type="inferred_python_dependency",
                raw_signal=import_name,
                inferred_target=package,
                confidence=0.7,
            )
        )
    for package in pattern_python_packages or []:
        evidence.append(
            TopologyEvidenceItem(
                source_type="pattern_memory_prefill_python",
                raw_signal=package,
                inferred_target=package,
                confidence=HIGH_CONFIDENCE_PREFILL_THRESHOLD,
            )
        )
    for package in memory_python_packages or []:
        evidence.append(
            TopologyEvidenceItem(
                source_type="repair_memory_prefill_python",
                raw_signal=package,
                inferred_target=package,
                confidence=0.65,
            )
        )
    for match in pattern_memory_matches or []:
        record = match.record
        evidence.append(
            TopologyEvidenceItem(
                source_type="pattern_memory",
                raw_signal=", ".join(match.matched_signals),
                inferred_target=", ".join([*record.apt_packages, *record.python_packages]),
                confidence=record.confidence,
                metadata={
                    "pattern_id": record.pattern_id,
                    "target_node_id": record.target_node_id,
                    "fix_strategy": record.fix_strategy,
                    "support_count": str(record.support_count),
                    "success_rate": f"{record.success_rate:.4f}",
                    "source_projects": ", ".join(record.source_projects),
                },
            )
        )

    signals = _dedupe([*python_packages, *imported_modules])
    for signal in signals:
        normalized = _normalize_package_name(signal)
        normalized_import = DEPENDENCY_TO_IMPORT.get(normalized, normalized)
        for package in SYSTEM_PACKAGE_HINTS.get(normalized_import, []):
            if package in system_packages:
                evidence.append(
                    TopologyEvidenceItem(
                        source_type="system_package_rule",
                        raw_signal=signal,
                        inferred_target=package,
                        confidence=0.75,
                    )
                )
    for match in repair_memory_matches or []:
        record = match.record
        evidence.append(
            TopologyEvidenceItem(
                source_type="repair_memory",
                raw_signal=record.error_signature,
                inferred_target=record.fix_action,
                confidence=match.score,
                metadata={
                    "error_type": record.error_type,
                    "fix_strategy": record.fix_strategy,
                    "stage": record.stage,
                    "score": f"{match.score:.4f}",
                    "evidence_level": match.evidence_level,
                    "matched_packages": ", ".join(match.matched_packages),
                    "matched_tokens": ", ".join(match.matched_tokens),
                    "matched_error_type": str(match.matched_error_type),
                    "matched_node": str(match.matched_node),
                    **_repair_memory_package_metadata(record),
                    **{key: str(value) for key, value in record.metadata.items()},
                },
            )
        )
    return _dedupe_evidence(evidence)


def create_build_topology(analysis: ProjectAnalysis) -> BuildTopology:
    dockerfile_blocks = _build_dockerfile_blocks(analysis)
    dependency_file_evidence = _evidence_by_source(analysis, "dependency_file")
    source_import_evidence = _evidence_by_source(analysis, "source_import")
    local_module_evidence = _evidence_by_source(analysis, "local_module")
    external_command_evidence = _evidence_by_source(analysis, "external_command")
    repair_memory_evidence = _evidence_by_source(analysis, "repair_memory")
    pattern_memory_evidence = _evidence_by_source(analysis, "pattern_memory")
    system_pattern_memory_evidence = _pattern_memory_evidence_for_node(pattern_memory_evidence, "system_dependency_plan")
    python_pattern_memory_evidence = _pattern_memory_evidence_for_node(pattern_memory_evidence, "python_dependency_plan")
    system_repair_memory_evidence = _repair_memory_evidence_for_node(repair_memory_evidence, "system_dependency_plan")
    python_repair_memory_evidence = _repair_memory_evidence_for_node(repair_memory_evidence, "python_dependency_plan")
    system_package_evidence = _evidence_by_source(analysis, "system_package_rule")
    inferred_python_evidence = _evidence_by_source(analysis, "inferred_python_dependency")
    nodes = [
        TopologyNode(
            node_id="project_scan",
            title="Project structure scan",
            node_type="evidence",
            description="Collect repository files that affect Docker environment construction.",
            evidence=analysis.detected_files[:12],
            evidence_items=_file_evidence_items(analysis.detected_files),
            outputs={"detected_files": analysis.detected_files},
            parallel_group="static_analysis",
            risk_tags=["file_copy"],
            owned_artifacts=[f"file:{item}" for item in analysis.detected_files],
            validation_command="Inspect project files before Dockerfile generation.",
        ),
        TopologyNode(
            node_id="dependency_scan",
            title="Dependency scan",
            node_type="evidence",
            description="Read dependency manifests and normalize Python package names.",
            evidence=analysis.dependency_files,
            evidence_items=dependency_file_evidence,
            outputs={"python_packages": analysis.python_packages},
            parallel_group="static_analysis",
            risk_tags=["python_dependencies"],
            owned_artifacts=[f"manifest:{item}" for item in analysis.dependency_files],
            validation_command="Parse dependency manifests and requirements files.",
        ),
        TopologyNode(
            node_id="entrypoint_scan",
            title="Entrypoint and import scan",
            node_type="evidence",
            description="Find likely runtime entry points and imported top-level modules.",
            evidence=analysis.entry_points,
            evidence_items=[*source_import_evidence, *external_command_evidence],
            outputs={"imported_modules": analysis.imported_modules, "external_commands": analysis.external_commands},
            parallel_group="static_analysis",
            risk_tags=["runtime_entrypoint", "python_imports", "external_commands"],
            owned_artifacts=[f"entrypoint:{item}" for item in analysis.entry_points],
            validation_command="Parse Python AST imports and entry points.",
        ),
        TopologyNode(
            node_id="local_module_scan",
            title="Local module scan",
            node_type="evidence",
            description="Find project-owned Python modules so imports are not misclassified as external packages.",
            evidence=analysis.local_modules,
            evidence_items=local_module_evidence,
            outputs={"local_modules": analysis.local_modules},
            parallel_group="static_analysis",
            risk_tags=["local_module_filter"],
            owned_artifacts=[f"module:{item}" for item in analysis.local_modules],
            validation_command="Compare imported modules with local Python files and packages.",
        ),
        TopologyNode(
            node_id="repair_memory_lookup",
            title="Repair memory lookup",
            node_type="evidence",
            description="Collect historical fixes that match project dependency and import signals.",
            evidence=analysis.repair_memory_hints,
            evidence_items=repair_memory_evidence,
            outputs={"hints": analysis.repair_memory_hints},
            parallel_group="static_analysis",
            risk_tags=["repair_memory"],
            owned_artifacts=[f"memory_hint:{hint}" for hint in analysis.repair_memory_hints],
            validation_command="Retrieve matching successful repair records.",
        ),
        TopologyNode(
            node_id="pattern_memory_lookup",
            title="Pattern memory lookup",
            node_type="evidence",
            description="Collect cross-project deployment patterns that match dependency, import, or command signals.",
            evidence=analysis.pattern_memory_hints,
            evidence_items=pattern_memory_evidence,
            outputs={"hints": analysis.pattern_memory_hints},
            parallel_group="static_analysis",
            risk_tags=["pattern_memory", "cross_project"],
            owned_artifacts=[f"pattern_hint:{hint}" for hint in analysis.pattern_memory_hints],
            validation_command="Retrieve matching high-confidence cross-project patterns.",
        ),
        TopologyNode(
            node_id="base_image_selection",
            title="Base image selection",
            node_type="planning",
            depends_on=["dependency_scan"],
            description="Choose a Python base image before composing package installation steps.",
            evidence=[analysis.base_image],
            outputs={"base_image": analysis.base_image},
            dockerfile_block=dockerfile_blocks["base_image_selection"],
            cache_scope="base_image",
            validation_hint="Changing the base image invalidates every following Docker layer.",
            evidence_items=[
                TopologyEvidenceItem(
                    source_type="base_image_rule",
                    raw_signal=analysis.base_image,
                    inferred_target=analysis.base_image,
                    confidence=0.85,
                )
            ],
            risk_tags=["base_image"],
            owned_artifacts=[f"dockerfile:FROM {analysis.base_image}", "dockerfile:WORKDIR /app"],
            validation_command="docker build .",
            confidence=0.85,
            scheduler_role="base",
            parallel_safe=True,
            resource_keys=["docker_daemon", "network"],
            estimated_cost_weight=0.2,
            checkpoint_scope="base_image",
            repair_scope="base_image_selection",
        ),
        TopologyNode(
            node_id="system_dependency_plan",
            title="System dependency plan",
            node_type="planning",
            depends_on=["dependency_scan", "entrypoint_scan", "pattern_memory_lookup", "repair_memory_lookup"],
            description="Infer apt packages needed by Python extensions and runtime binaries.",
            evidence=analysis.system_packages,
            outputs={
                "apt_packages": analysis.system_packages,
                "pattern_apt_packages": analysis.pattern_apt_packages,
                "memory_apt_packages": analysis.memory_apt_packages,
            },
            dockerfile_block=dockerfile_blocks["system_dependency_plan"],
            cache_scope="system_dependencies",
            validation_hint="Prefer editing only the apt package list in this node block.",
            evidence_items=[*system_package_evidence, *system_pattern_memory_evidence, *system_repair_memory_evidence],
            risk_tags=_system_risk_tags(analysis.system_packages),
            owned_artifacts=[f"apt:{package}" for package in analysis.system_packages],
            validation_command="docker build .",
            confidence=0.8 if analysis.system_packages else 0.45,
            scheduler_role="system-deps",
            parallel_safe=True,
            resource_keys=["docker_build", "docker_daemon", "network", "apt_cache"],
            estimated_cost_weight=2.0 if analysis.system_packages else 0.4,
            checkpoint_scope="system_dependency_plan",
            repair_scope="system_dependency_plan",
        ),
        TopologyNode(
            node_id="python_dependency_plan",
            title="Python dependency plan",
            node_type="planning",
            depends_on=["dependency_scan", "entrypoint_scan", "local_module_scan", "pattern_memory_lookup", "repair_memory_lookup"],
            description="Keep Python package installation separate from native system packages.",
            evidence=[
                *analysis.python_packages,
                *analysis.missing_python_packages,
                *analysis.pattern_python_packages,
                *analysis.memory_python_packages,
            ],
            outputs={
                "python_packages": analysis.python_packages,
                "missing_python_packages": analysis.missing_python_packages,
                "pattern_python_packages": analysis.pattern_python_packages,
                "memory_python_packages": analysis.memory_python_packages,
            },
            dockerfile_block=dockerfile_blocks["python_dependency_plan"],
            cache_scope="python_dependencies",
            validation_hint="Keep dependency manifest COPY before full project COPY to preserve pip cache layers.",
            evidence_items=[
                *dependency_file_evidence,
                *inferred_python_evidence,
                *python_pattern_memory_evidence,
                *python_repair_memory_evidence,
            ],
            risk_tags=_python_risk_tags(analysis),
            owned_artifacts=[
                *[f"pip:{package}" for package in analysis.python_packages],
                *[f"pip:{package}" for package in analysis.missing_python_packages],
                *[f"pip:{package}" for package in analysis.pattern_python_packages],
                *[f"pip:{package}" for package in analysis.memory_python_packages],
            ],
            validation_command=_python_dependency_validation_command(analysis),
            confidence=0.8
            if analysis.dependency_files
            or analysis.missing_python_packages
            or analysis.pattern_python_packages
            or analysis.memory_python_packages
            else 0.45,
            scheduler_role="python-deps",
            parallel_safe=True,
            resource_keys=["docker_build", "docker_daemon", "network", "pip_cache"],
            estimated_cost_weight=2.5
            if analysis.dependency_files
            or analysis.missing_python_packages
            or analysis.pattern_python_packages
            or analysis.memory_python_packages
            else 0.4,
            checkpoint_scope="python_dependency_plan",
            repair_scope="python_dependency_plan",
        ),
        TopologyNode(
            node_id="file_copy_plan",
            title="File copy plan",
            node_type="planning",
            depends_on=["project_scan", "entrypoint_scan"],
            description="Decide which project files must be available before build and runtime validation.",
            evidence=analysis.detected_files[:12],
            dockerfile_block=dockerfile_blocks["file_copy_plan"],
            cache_scope="project_files",
            validation_hint="Full project COPY should stay after dependency installation.",
            evidence_items=_file_evidence_items(analysis.detected_files),
            risk_tags=["file_copy"],
            owned_artifacts=["dockerfile:COPY . ."],
            validation_command="test -f /app/main.py || find /app -maxdepth 2 -type f",
            confidence=0.75,
        ),
        TopologyNode(
            node_id="dockerfile_generation",
            title="Dockerfile generation",
            node_type="execution",
            depends_on=[
                "base_image_selection",
                "system_dependency_plan",
                "python_dependency_plan",
                "file_copy_plan",
            ],
            description="Generate an initial Dockerfile from the planned nodes.",
            risk_tags=["dockerfile_generation"],
            owned_artifacts=["artifact:Dockerfile"],
            validation_command="docker build .",
            scheduler_role="merge-final",
            parallel_safe=False,
            resource_keys=["docker_build", "docker_daemon"],
            estimated_cost_weight=1.0,
            checkpoint_scope="dockerfile_generation",
            repair_scope="dockerfile_generation",
        ),
        TopologyNode(
            node_id="build_validation",
            title="Build validation",
            node_type="verification",
            depends_on=["dockerfile_generation"],
            description="Run docker build and capture stderr/stdout for node-level repair.",
            risk_tags=["build_validation"],
            owned_artifacts=["command:docker build"],
            validation_command="docker build .",
        ),
        TopologyNode(
            node_id="build_failure_repair",
            title="Build failure repair",
            node_type="repair",
            depends_on=["build_validation", "repair_memory_lookup"],
            description="Map build failures back to base image, system package, or Python package nodes.",
            risk_tags=["node_failure_repair"],
            owned_artifacts=["repair_scope:build"],
            validation_command="Re-run docker build after the smallest attributed node edit.",
        ),
        TopologyNode(
            node_id="runtime_validation",
            title="Runtime validation",
            node_type="verification",
            depends_on=["build_validation"],
            description="Run the project entry point inside the image after build succeeds.",
            dockerfile_block=dockerfile_blocks["runtime_validation"],
            cache_scope="runtime_command",
            validation_hint="Runtime failures should update the smallest upstream node that owns the missing dependency or file.",
            evidence_items=_entrypoint_evidence_items(analysis),
            outputs={
                "runtime_validation_mode": analysis.runtime_validation.mode,
                "runtime_validation_command": _runtime_validation_command(analysis),
                "runtime_command_source": analysis.runtime_validation.command_source,
                "expected_success_signal": analysis.runtime_validation.expected_success_signal,
            },
            risk_tags=["runtime_validation"],
            owned_artifacts=[
                f"cmd:{_runtime_validation_command(analysis)}",
                f"runtime_mode:{analysis.runtime_validation.mode}",
                f"runtime_command_source:{analysis.runtime_validation.command_source}",
            ],
            validation_command=_runtime_validation_command(analysis),
            confidence=analysis.runtime_validation.confidence,
            scheduler_role="runtime-validation",
            parallel_safe=False,
            resource_keys=["docker_daemon"],
            estimated_cost_weight=0.5,
            checkpoint_scope="runtime_validation",
            repair_scope="runtime_validation",
        ),
        TopologyNode(
            node_id="runtime_failure_repair",
            title="Runtime failure repair",
            node_type="repair",
            depends_on=["runtime_validation", "repair_memory_lookup"],
            description="Map runtime failures back to missing shared libraries, binaries, or Python packages.",
            risk_tags=["node_failure_repair", "runtime_failure"],
            owned_artifacts=["repair_scope:runtime"],
            validation_command="Re-run docker run after the smallest attributed node edit.",
        ),
        TopologyNode(
            node_id="dockerfile_finalization",
            title="Dockerfile finalization",
            node_type="artifact",
            depends_on=["runtime_validation"],
            description="Persist the final Dockerfile and per-node evidence after successful validation.",
            risk_tags=["final_artifact"],
            owned_artifacts=["artifact:final Dockerfile", "artifact:topology evidence"],
            validation_command="Persist Dockerfile after build and runtime validation pass.",
        ),
    ]
    return BuildTopology(
        project_path=analysis.project_path,
        analysis=analysis,
        nodes=nodes,
        execution_layers=_compute_execution_layers(nodes),
        parallel_build_stages=_parallel_build_stages(analysis),
    )


def generate_topology_dockerfile(topology: BuildTopology) -> str:
    """Generate a cache-friendly Dockerfile from executable topology nodes."""
    node_map = topology.node_map()
    blocks: list[str] = []
    for node_id in EXECUTABLE_DOCKERFILE_NODE_ORDER:
        node = node_map.get(node_id)
        if node is None or not node.dockerfile_block.strip():
            continue
        blocks.append(_wrap_topology_block(node.node_id, node.dockerfile_block))
    return _with_buildkit_syntax("\n\n".join(blocks).rstrip() + "\n", topology.analysis.build_cache)


def generate_multistage_dockerfile(topology: BuildTopology) -> str:
    """Generate a multi-stage Dockerfile that preserves topology markers."""
    analysis = topology.analysis
    python_stage_base = "system-deps" if _python_stage_requires_system_stage(analysis) else "base"
    final_stage_base = "python-deps" if python_stage_base == "system-deps" else "system-deps"

    base_block = "\n".join([f"FROM {analysis.base_image} AS base", "WORKDIR /app"])
    system_block = "\n".join(
        [
            "FROM base AS system-deps",
            _system_dependency_block(analysis.system_packages, cache_plan=analysis.build_cache),
        ]
    )
    python_lines = [
        f"FROM {python_stage_base} AS python-deps",
        _python_dependency_block(
            analysis.dependency_files,
            _dedupe(
                [
                    *analysis.missing_python_packages,
                    *analysis.pattern_python_packages,
                    *analysis.memory_python_packages,
                ]
            ),
            cache_plan=analysis.build_cache,
        ),
    ]
    python_block = "\n".join(python_lines)

    final_lines = [f"FROM {final_stage_base} AS final"]
    if final_stage_base == "system-deps":
        final_lines.append("COPY --from=python-deps /usr/local /usr/local")
    final_lines.append("COPY . .")
    file_copy_block = "\n".join(final_lines)
    runtime_block = _runtime_command_block(analysis)

    blocks = [
        _wrap_topology_block("base_image_selection", base_block),
        _wrap_topology_block("system_dependency_plan", system_block),
        _wrap_topology_block("python_dependency_plan", python_block),
        _wrap_topology_block("file_copy_plan", file_copy_block),
        _wrap_topology_block("runtime_validation", runtime_block),
    ]
    return _with_buildkit_syntax("\n\n".join(blocks).rstrip() + "\n", analysis.build_cache)


def _evidence_by_source(analysis: ProjectAnalysis, source_type: str) -> list[TopologyEvidenceItem]:
    return [item for item in analysis.dependency_evidence if item.source_type == source_type]


def _repair_memory_evidence_for_node(
    evidence_items: list[TopologyEvidenceItem],
    node_id: str,
) -> list[TopologyEvidenceItem]:
    matched: list[TopologyEvidenceItem] = []
    for item in evidence_items:
        metadata_node = item.metadata.get("topology_node_id", "")
        error_type = item.metadata.get("error_type", "")
        fix_strategy = item.metadata.get("fix_strategy", "")
        if metadata_node == node_id:
            matched.append(item)
            continue
        if node_id == "system_dependency_plan" and (
            fix_strategy == "install_apt_packages" or error_type in SYSTEM_DEPENDENCY_ERROR_TYPES
        ):
            matched.append(item)
            continue
        if node_id == "python_dependency_plan" and (
            fix_strategy == "install_python_package" or error_type in PYTHON_DEPENDENCY_ERROR_TYPES
        ):
            matched.append(item)
    return matched


def _pattern_memory_evidence_for_node(
    evidence_items: list[TopologyEvidenceItem],
    node_id: str,
) -> list[TopologyEvidenceItem]:
    matched: list[TopologyEvidenceItem] = []
    for item in evidence_items:
        metadata_node = item.metadata.get("target_node_id", "")
        fix_strategy = item.metadata.get("fix_strategy", "")
        if metadata_node == node_id:
            matched.append(item)
            continue
        if node_id == "system_dependency_plan" and fix_strategy == "install_apt_packages":
            matched.append(item)
            continue
        if node_id == "python_dependency_plan" and fix_strategy == "install_python_package":
            matched.append(item)
    return matched


def _file_evidence_items(files: list[str]) -> list[TopologyEvidenceItem]:
    return [
        TopologyEvidenceItem(
            source_type="project_file",
            raw_signal=file,
            inferred_target=file,
            confidence=0.9,
        )
        for file in files
    ]


def _entrypoint_evidence_items(analysis: ProjectAnalysis) -> list[TopologyEvidenceItem]:
    return [
        TopologyEvidenceItem(
            source_type="runtime_entrypoint",
            raw_signal=entrypoint,
            inferred_target=f"python {entrypoint}",
            confidence=0.8,
        )
        for entrypoint in analysis.entry_points
    ]


def _system_risk_tags(system_packages: list[str]) -> list[str]:
    tags: list[str] = ["system_dependencies"]
    native_build_packages = {"build-essential", "gcc", "g++", "libpq-dev", "libffi-dev", "libcurl4-openssl-dev"}
    shared_library_packages = {"libmagic1", "libgl1", "libgl1-mesa-glx", "libglib2.0-0", "libsndfile1", "libgomp1", "libzbar0"}
    binary_packages = {"tesseract-ocr", "ffmpeg"}
    if any(package in native_build_packages for package in system_packages):
        tags.append("native_build")
    if any(package in shared_library_packages for package in system_packages):
        tags.append("missing_shared_library")
    if any(package in binary_packages for package in system_packages):
        tags.append("missing_system_binary")
    return _dedupe(tags)


def _python_risk_tags(analysis: ProjectAnalysis) -> list[str]:
    tags = ["python_dependencies"]
    if analysis.missing_python_packages or analysis.pattern_python_packages or analysis.memory_python_packages:
        tags.append("missing_python_package")
    if analysis.local_modules:
        tags.append("local_module_filter")
    return tags


def _python_stage_requires_system_stage(analysis: ProjectAnalysis) -> bool:
    native_build_packages = {"build-essential", "gcc", "g++", "libpq-dev", "libffi-dev", "libcurl4-openssl-dev"}
    return any(package in native_build_packages for package in analysis.system_packages)


def _parallel_build_stages(analysis: ProjectAnalysis) -> list[str]:
    if _python_stage_requires_system_stage(analysis):
        return []
    if analysis.system_packages and (
        analysis.dependency_files
        or analysis.missing_python_packages
        or analysis.pattern_python_packages
        or analysis.memory_python_packages
    ):
        return ["system-deps", "python-deps"]
    return []


def create_topology_build_plan(topology: BuildTopology, *, docker_build_limit: int = 3) -> TopologyBuildPlan:
    node_by_role = {node.scheduler_role: node for node in topology.nodes if node.scheduler_role}
    base = node_by_role.get("base")
    system = node_by_role.get("system-deps")
    python = node_by_role.get("python-deps")
    merge = node_by_role.get("merge-final")
    runtime = node_by_role.get("runtime-validation")
    python_depends_on_system = _python_stage_requires_system_stage(topology.analysis)

    steps: list[TopologyBuildStep] = []
    if base is not None:
        steps.append(_topology_build_step(base, step_id="base", depends_on=[]))
    if system is not None:
        steps.append(_topology_build_step(system, step_id="system-deps", depends_on=["base"]))
    if python is not None:
        steps.append(
            _topology_build_step(
                python,
                step_id="python-deps",
                depends_on=["system-deps"] if python_depends_on_system else ["base"],
            )
        )
    merge_depends_on = [step_id for step_id in ("system-deps", "python-deps") if any(step.step_id == step_id for step in steps)]
    if merge is not None:
        steps.append(_topology_build_step(merge, step_id="merge-final", depends_on=merge_depends_on or ["base"]))
    if runtime is not None:
        steps.append(_topology_build_step(runtime, step_id="runtime-validation", depends_on=["merge-final"]))

    build_limit = max(1, docker_build_limit)
    return TopologyBuildPlan(
        steps=steps,
        resource_limits={
            "docker_build": build_limit,
            "docker_daemon": build_limit,
            "network": min(2, build_limit),
            "apt_cache": 1,
            "pip_cache": min(2, build_limit),
        },
    )


def _topology_build_step(node: TopologyNode, *, step_id: str, depends_on: list[str]) -> TopologyBuildStep:
    return TopologyBuildStep(
        step_id=step_id,
        node_id=node.node_id,
        scheduler_role=node.scheduler_role,
        depends_on=depends_on,
        resource_keys=list(node.resource_keys),
        parallel_safe=node.parallel_safe,
        estimated_cost_weight=node.estimated_cost_weight,
        checkpoint_scope=node.checkpoint_scope,
        repair_scope=node.repair_scope,
    )


def _python_dependency_validation_command(analysis: ProjectAnalysis) -> str:
    modules = [
        DEPENDENCY_TO_IMPORT.get(package, package).replace("-", "_")
        for package in [
            *analysis.python_packages,
            *analysis.missing_python_packages,
            *analysis.pattern_python_packages,
            *analysis.memory_python_packages,
        ]
    ][:8]
    if modules:
        imports = "; ".join(f"import {module}" for module in modules)
        return f"python -c {json.dumps(imports)}"
    return "python -m pip check"


def format_runtime_host_command(plan: RuntimeValidationPlan, image_name: str, command: list[str] | None = None) -> str:
    if command is None:
        return plan.host_command.format(image_name=image_name)
    return f"docker run --rm {image_name} {_shell_join_command(command)} 2>&1"


def _runtime_validation_command(analysis: ProjectAnalysis) -> str:
    return _shell_join_command(analysis.runtime_validation.container_command)


def validate_topology_dockerfile(
    dockerfile_text: str,
    *,
    topology: BuildTopology | None = None,
    allowed_node_id: str | None = None,
    original_dockerfile: str | None = None,
    protect_base_image: bool = True,
) -> DockerfileGuardResult:
    """Validate topology-prefill Dockerfile invariants before accepting model edits."""
    violations: list[str] = []
    expected_base_image = topology.analysis.base_image if topology is not None else ""
    guarded_nodes = _guarded_dockerfile_nodes(topology)

    from_match = re.search(r"(?im)^\s*FROM\s+(?P<image>\S+)", dockerfile_text)
    if not from_match:
        violations.append("missing FROM instruction")
    else:
        base_image = from_match.group("image").strip()
        if base_image.lower().startswith(("env-test-image", "topomen-env-test-image")):
            violations.append("base image must not reuse the validation image")
        if protect_base_image and expected_base_image and base_image != expected_base_image:
            violations.append(f"base image changed from {expected_base_image} to {base_image}")

    marker_ranges = _topology_marker_ranges(dockerfile_text, guarded_nodes, violations)
    ordered_positions = [marker_ranges[node_id][0] for node_id in guarded_nodes if node_id in marker_ranges]
    if ordered_positions != sorted(ordered_positions):
        violations.append("TOPOLOGY_NODE blocks are not in the expected cache-friendly order")

    pip_install_match = re.search(r"(?im)^\s*RUN\s+.*pip\s+install\b", dockerfile_text)
    project_copy_match = re.search(r"(?im)^\s*COPY\s+\.\s+\.\s*$", dockerfile_text)
    if not project_copy_match:
        violations.append("COPY . . is missing from the file_copy_plan block")
    elif pip_install_match and project_copy_match.start() < pip_install_match.start():
        violations.append("COPY . . must stay after Python dependency installation")

    if allowed_node_id and original_dockerfile and allowed_node_id in guarded_nodes:
        original_blocks = _extract_topology_blocks(original_dockerfile, guarded_nodes)
        current_blocks = _extract_topology_blocks(dockerfile_text, guarded_nodes)
        for node_id in guarded_nodes:
            if node_id == allowed_node_id:
                continue
            if original_blocks.get(node_id) != current_blocks.get(node_id):
                violations.append(f"node-block repair attempted to modify protected block: {node_id}")

    return DockerfileGuardResult(valid=not violations, violations=violations)


def attribute_failure_to_node(
    log_text: str,
    *,
    stage: str,
    error_type: str | None = None,
    error_signature: str | None = None,
) -> FailureAttribution:
    error_signature = error_signature or extract_error_signature(log_text)
    error_type = error_type or classify_error_type(error_signature, log_text)
    normalized = normalize_error_text(f"{error_signature}\n{log_text}")
    stage = stage if stage in {"build", "run"} else "build"

    if error_type in SYSTEM_DEPENDENCY_ERROR_TYPES:
        return FailureAttribution(
            stage=stage,
            error_signature=error_signature,
            error_type=error_type,
            topology_node_id="system_dependency_plan",
            confidence=0.9,
            reason=f"{error_type} usually requires changing apt/system dependency planning.",
        )
    if error_type in PYTHON_DEPENDENCY_ERROR_TYPES:
        return FailureAttribution(
            stage=stage,
            error_signature=error_signature,
            error_type=error_type,
            topology_node_id="python_dependency_plan",
            confidence=0.9,
            reason=f"{error_type} usually requires changing Python dependency planning.",
        )
    if error_type == "dockerfile_syntax_error":
        return FailureAttribution(
            stage=stage,
            error_signature=error_signature,
            error_type=error_type,
            topology_node_id="dockerfile_generation",
            confidence=0.9,
            reason="Dockerfile parser or shell syntax errors point to Dockerfile generation.",
        )
    if error_type == "build_infra_error":
        return FailureAttribution(
            stage=stage,
            error_signature=error_signature,
            error_type=error_type,
            topology_node_id="build_validation",
            confidence=0.85,
            reason="Docker daemon, BuildKit, cache, or disk errors belong to build validation infrastructure.",
        )
    if _looks_like_project_file_missing(normalized):
        return FailureAttribution(
            stage=stage,
            error_signature=error_signature,
            error_type=error_type,
            topology_node_id="file_copy_plan",
            confidence=0.75,
            reason="The log indicates a missing project file or entry point inside the container.",
        )

    fallback_node = "build_failure_repair" if stage == "build" else "runtime_failure_repair"
    return FailureAttribution(
        stage=stage,
        error_signature=error_signature,
        error_type=error_type,
        topology_node_id=fallback_node,
        confidence=0.35,
        reason="No specific topology node matched; route to the generic failure repair node for the current stage.",
    )


def format_topology_prompt_context(topology: BuildTopology) -> str:
    analysis = topology.analysis
    cache_plan = analysis.build_cache
    lines = [
        "Build topology context. Use this DAG as the repair boundary; when a command fails, repair the matching node",
        "instead of rewriting the whole Dockerfile.",
        f"Project path: {analysis.project_path}",
        f"Base image candidate: {analysis.base_image}",
        f"Dockerfile build mode: {'multistage' if topology.multistage else 'single-stage'}",
        f"Parallel build stages: {_format_list(topology.parallel_build_stages, empty='none')}",
        f"Python packages: {_format_list(analysis.python_packages, empty='none detected')}",
        f"Missing Python packages inferred from imports: {_format_list(analysis.missing_python_packages, empty='none inferred')}",
        f"External command signals: {_format_list(analysis.external_commands, empty='none detected')}",
        f"Pattern apt packages: {_format_list(analysis.pattern_apt_packages, empty='none')}",
        f"Pattern Python packages: {_format_list(analysis.pattern_python_packages, empty='none')}",
        f"Memory apt packages: {_format_list(analysis.memory_apt_packages, empty='none')}",
        f"Memory Python packages: {_format_list(analysis.memory_python_packages, empty='none')}",
        f"Imported modules: {_format_list(analysis.imported_modules, empty='none detected')}",
        f"Local modules: {_format_list(analysis.local_modules, empty='none detected')}",
        f"Likely apt packages: {_format_list(analysis.system_packages, empty='none inferred')}",
        f"Entrypoints: {_format_list(analysis.entry_points, empty='none detected')}",
        f"Build cache strategy: {cache_plan.strategy}",
        f"Build cache enabled: {cache_plan.enabled}",
        f"Build cache reuse existing: {cache_plan.reuse_existing}",
        f"Build cache id scope: {cache_plan.cache_id_scope}",
        f"Build cache dependency hash: {cache_plan.dependency_hash or 'none'}",
    ]
    if cache_plan.enabled:
        lines.append(
            "BuildKit cache ids: "
            f"apt={cache_plan.apt_cache_id}, apt_lists={cache_plan.apt_lists_cache_id}, pip={cache_plan.pip_cache_id}"
        )
    if analysis.repair_memory_hints:
        lines.append("Repair memory hints:")
        lines.extend(f"- {hint}" for hint in analysis.repair_memory_hints[:5])
    if analysis.pattern_memory_hints:
        lines.append("Cross-project pattern memory hints:")
        lines.extend(f"- {hint}" for hint in analysis.pattern_memory_hints[:5])
    lines.append("Execution layers:")
    for index, layer in enumerate(topology.execution_layers, start=1):
        lines.append(f"- Layer {index}: {', '.join(layer)}")
    lines.append("Executable Dockerfile node blocks:")
    for node in topology.nodes:
        if not node.dockerfile_block:
            continue
        lines.append(f"[{node.node_id}] cache_scope={node.cache_scope}, confidence={node.confidence:.2f}")
        if node.risk_tags:
            lines.append(f"risk_tags: {', '.join(node.risk_tags)}")
        if node.owned_artifacts:
            lines.append(f"owned_artifacts: {_format_list(node.owned_artifacts, empty='none')}")
        lines.append(f"validation_hint: {node.validation_hint}")
        if node.validation_command:
            lines.append(f"validation_command: {node.validation_command}")
        lines.extend(f"  {line}" for line in node.dockerfile_block.splitlines())
    lines.append("Node repair rule: build errors usually affect base_image/system_dependency/python_dependency nodes;")
    lines.append("runtime errors usually affect system_dependency/python_dependency/file_copy nodes.")
    return "\n".join(lines)


def write_topology_artifacts(
    topology: BuildTopology,
    output_dir: str | Path,
    *,
    name: str | None = None,
    render_svg: bool = True,
) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_name = _safe_name(name or Path(topology.project_path).name or "project")
    project_dir = output_dir / artifact_name
    project_dir.mkdir(parents=True, exist_ok=True)
    json_path = project_dir / "topology.json"
    mermaid_path = project_dir / "topology.mmd"
    svg_path = project_dir / "topology.svg"
    dockerfile_path = project_dir / "Dockerfile.prefill"
    merge_dockerfile_path = project_dir / "Dockerfile.merge"
    index_md_path = project_dir / "index.md"
    index_html_path = project_dir / "index.html"
    dockerfile_text = generate_multistage_dockerfile(topology) if topology.multistage else generate_topology_dockerfile(topology)
    merge_dockerfile_text = _generate_threaded_merge_preview_dockerfile(topology, artifact_name=artifact_name)
    json_path.write_text(json.dumps(topology.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
    mermaid_path.write_text(topology.to_mermaid(), encoding="utf-8")
    if render_svg:
        svg_path.write_text(_topology_to_svg(topology), encoding="utf-8")
    dockerfile_path.write_text(dockerfile_text, encoding="utf-8")
    merge_dockerfile_path.write_text(merge_dockerfile_text, encoding="utf-8")
    index_md_path.write_text(
        _topology_artifact_index_md(topology, artifact_name=artifact_name, render_svg=render_svg),
        encoding="utf-8",
    )
    index_html_path.write_text(
        _topology_artifact_index_html(topology, artifact_name=artifact_name, render_svg=render_svg),
        encoding="utf-8",
    )
    topologies_index = _write_topologies_index(output_dir)
    artifacts = {
        "directory": str(project_dir),
        "json": str(json_path),
        "mermaid": str(mermaid_path),
        "dockerfile": str(dockerfile_path),
        "dockerfile_prefill": str(dockerfile_path),
        "dockerfile_merge": str(merge_dockerfile_path),
        "index_md": str(index_md_path),
        "index_html": str(index_html_path),
        "topologies_index": str(topologies_index),
    }
    if render_svg:
        artifacts["svg"] = str(svg_path)
    return artifacts


def _build_dockerfile_blocks(analysis: ProjectAnalysis) -> dict[str, str]:
    return {
        "base_image_selection": "\n".join([f"FROM {analysis.base_image}", "WORKDIR /app"]),
        "system_dependency_plan": _system_dependency_block(
            analysis.system_packages,
            cache_plan=analysis.build_cache,
        ),
        "python_dependency_plan": _python_dependency_block(
            analysis.dependency_files,
            _dedupe(
                [
                    *analysis.missing_python_packages,
                    *analysis.pattern_python_packages,
                    *analysis.memory_python_packages,
                ]
            ),
            cache_plan=analysis.build_cache,
        ),
        "file_copy_plan": "COPY . .",
        "runtime_validation": _runtime_command_block(analysis),
    }


def _system_dependency_block(system_packages: list[str], *, cache_plan: BuildCachePlan | None = None) -> str:
    if not system_packages:
        return "# No system packages inferred for this topology."
    packages = " ".join(system_packages)
    if cache_plan is not None and cache_plan.enabled:
        return (
            f"RUN --mount=type=cache,target=/var/cache/apt,id={cache_plan.apt_cache_id},sharing=locked \\\n"
            f"    --mount=type=cache,target=/var/lib/apt/lists,id={cache_plan.apt_lists_cache_id},sharing=locked \\\n"
            "    apt-get update && \\\n"
            f"    apt-get install -y --no-install-recommends {packages}"
        )
    return (
        "RUN apt-get update && "
        f"apt-get install -y --no-install-recommends {packages} && "
        "rm -rf /var/lib/apt/lists/*"
    )


def _python_dependency_block(
    dependency_files: list[str],
    missing_python_packages: list[str],
    *,
    cache_plan: BuildCachePlan | None = None,
) -> str:
    copy_lines = _dependency_copy_lines(dependency_files)
    install_lines = _python_install_lines(dependency_files, missing_python_packages, cache_plan=cache_plan)
    lines = [*copy_lines, *install_lines]
    if lines:
        return "\n".join(lines)
    return "# No Python dependency file detected before project COPY."


def _dependency_copy_lines(dependency_files: list[str]) -> list[str]:
    lines: list[str] = []
    copied_requirement_dir = False
    for dependency_file in dependency_files:
        normalized = dependency_file.replace("\\", "/")
        if normalized.startswith("requirements/"):
            if not copied_requirement_dir:
                lines.append("COPY requirements ./requirements")
                copied_requirement_dir = True
            continue
        if "/" in normalized:
            parent = normalized.rsplit("/", 1)[0]
            lines.append(f"COPY {parent} ./{parent}")
            continue
        lines.append(f"COPY {normalized} .")
    return _dedupe(lines)


def _pip_cache_run_prefix(cache_plan: BuildCachePlan | None) -> str:
    if cache_plan is None or not cache_plan.enabled:
        return "RUN "
    return f"RUN --mount=type=cache,target=/root/.cache/pip,id={cache_plan.pip_cache_id},sharing=locked "


def _python_install_lines(
    dependency_files: list[str],
    missing_python_packages: list[str],
    *,
    cache_plan: BuildCachePlan | None = None,
) -> list[str]:
    requirement_files = [
        dependency_file.replace("\\", "/")
        for dependency_file in dependency_files
        if Path(dependency_file).name.startswith("requirements") and Path(dependency_file).suffix == ".txt"
    ]
    missing_install = " ".join(missing_python_packages)
    pip_cache_prefix = _pip_cache_run_prefix(cache_plan)
    if requirement_files:
        pip_install = "pip install" if cache_plan is not None and cache_plan.enabled else "pip install --no-cache-dir"
        install_parts = [f"{pip_install} -r {path}" for path in requirement_files]
        if missing_install:
            install_parts.append(f"{pip_install} {missing_install}")
        install_command = " && \\\n    ".join(install_parts)
        return [f"{pip_cache_prefix}{install_command}"]
    if any(Path(dependency_file).name == "pyproject.toml" for dependency_file in dependency_files):
        lines = ["# pyproject.toml detected; install the project after COPY . . if needed."]
        if missing_install:
            lines.append(f"{pip_cache_prefix}pip install{'' if cache_plan is not None and cache_plan.enabled else ' --no-cache-dir'} {missing_install}")
        return lines
    if missing_install:
        return [f"{pip_cache_prefix}pip install{'' if cache_plan is not None and cache_plan.enabled else ' --no-cache-dir'} {missing_install}"]
    return []


def _runtime_command_block(analysis: ProjectAnalysis) -> str:
    return f"CMD {json.dumps(analysis.runtime_validation.container_command)}"


def _select_entrypoint(analysis: ProjectAnalysis) -> str:
    if "main.py" in analysis.detected_files:
        return "main.py"
    for entrypoint in analysis.entry_points:
        if entrypoint.endswith(".py"):
            return entrypoint
    return ""


def _select_script_entrypoint(analysis: ProjectAnalysis) -> str:
    for name in ("main.py", "app.py", "run.py"):
        if name in analysis.detected_files:
            return name
    for entrypoint in analysis.entry_points:
        if Path(entrypoint).name in {"main.py", "app.py", "run.py"}:
            return entrypoint
    return ""


def _select_import_smoke_module(analysis: ProjectAnalysis) -> str:
    for module in analysis.local_modules:
        candidate = module.replace("-", "_")
        if candidate not in {"test", "tests", "conftest"}:
            return candidate
    for package in analysis.python_packages:
        module = DEPENDENCY_TO_IMPORT.get(package, package).replace("-", "_")
        if module not in {"pytest", "setuptools", "wheel"}:
            return module
    for module in analysis.imported_modules:
        candidate = DEPENDENCY_TO_IMPORT.get(module, module).replace("-", "_")
        if candidate not in WEB_FRAMEWORK_IMPORTS:
            return candidate
    return ""


def _select_pyproject_script_module(project_path: Path) -> str:
    pyproject_path = project_path / "pyproject.toml"
    if not pyproject_path.is_file():
        return ""
    text = _read_text(pyproject_path)
    scripts_section = re.search(r"(?ms)^\[project\.scripts\]\s*(?P<body>.*?)(^\[|\Z)", text)
    if scripts_section is None:
        return ""
    body = scripts_section.group("body")
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        _, rhs = line.split("=", 1)
        rhs = rhs.strip().strip("'\"")
        if not rhs:
            continue
        module = rhs.split(":", 1)[0].strip()
        if module:
            return module
    return ""


def _has_tests(project_path: Path, detected_files: list[str]) -> bool:
    if "tests/" in detected_files or "test/" in detected_files:
        return True
    if not project_path.exists():
        return False
    return any(project_path.glob("test_*.py")) or any(project_path.glob("tests/test*.py"))


def _tests_use_pytest(project_path: Path, python_packages: set[str]) -> bool:
    if "pytest" in python_packages:
        return True
    tests_dir = project_path / "tests"
    test_files = []
    if tests_dir.exists():
        test_files.extend(sorted(tests_dir.rglob("*.py"), key=lambda item: item.as_posix().lower())[:20])
    test_files.extend(sorted(project_path.glob("test_*.py"), key=lambda item: item.as_posix().lower())[:20])
    for path in test_files:
        text = _read_text(path)
        if "import pytest" in text or "from pytest" in text or "@pytest." in text:
            return True
    return False


def _build_runtime_fallback_candidates(
    analysis: ProjectAnalysis,
    *,
    primary_mode: str,
    primary_command: list[str],
) -> list[dict[str, str | list[str] | float]]:
    candidates: list[dict[str, str | list[str] | float]] = []
    seen: set[tuple[str, ...]] = {tuple(primary_command)}

    def _push(mode: str, command: list[str], command_source: str, reason: str, confidence: float) -> None:
        key = tuple(command)
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {
                "candidate_id": f"{mode}-{len(candidates) + 1}",
                "mode": mode,
                "command": command,
                "command_source": command_source,
                "reason": reason,
                "confidence": round(confidence, 2),
            }
        )

    if primary_mode in {"script", "web_service"}:
        if _has_tests(Path(analysis.project_path), analysis.detected_files):
            test_command = (
                ["python", "-m", "pytest", "-q"]
                if _tests_use_pytest(Path(analysis.project_path), set(analysis.python_packages))
                else ["python", "-m", "unittest", "discover", "-s", "tests"]
            )
            _push("test", test_command, "runtime_fallback_test", "Fallback to test smoke validation.", 0.65)
    import_module = _select_import_smoke_module(analysis)
    if import_module:
        _push(
            "package_import",
            ["python", "-c", f"import {import_module}; print('runtime validation ok')"],
            "runtime_fallback_import",
            f"Fallback to import smoke for {import_module}.",
            0.6,
        )
    return candidates[:3]


def _runtime_plan(
    *,
    mode: str,
    command: list[str],
    command_source: str,
    confidence: float,
    reason: str,
    expected_success_signal: str,
    selected_target: str = "",
    candidate_modes: list[dict[str, str]] | None = None,
    rejected_candidates: list[dict[str, str]] | None = None,
    planner_signals: list[str] | None = None,
    fallback_candidates: list[dict[str, str | list[str] | float]] | None = None,
) -> RuntimeValidationPlan:
    if mode not in RUNTIME_VALIDATION_MODES:
        mode = "noop"
    fallback_payload = fallback_candidates or []
    return RuntimeValidationPlan(
        mode=mode,
        container_command=command,
        host_command=f"docker run --rm {{image_name}} {_shell_join_command(command)} 2>&1",
        command_source=command_source,
        confidence=confidence,
        reason=reason,
        expected_success_signal=expected_success_signal,
        selected_target=selected_target,
        candidate_modes=candidate_modes or [],
        rejected_candidates=rejected_candidates or [],
        planner_signals=planner_signals or [],
        fallback_candidates=fallback_payload,
        max_attempts=1 + len(fallback_payload),
    )


def _shell_join_command(command: list[str]) -> str:
    return " ".join(_shell_quote_arg(part) for part in command)


def _shell_quote_arg(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:=+-]+", value):
        return value
    return '"' + value.replace('"', '\\"') + '"'


def _wrap_topology_block(node_id: str, block: str) -> str:
    return "\n".join(
        [
            f"# TOPOLOGY_NODE: {node_id} START",
            block.rstrip(),
            f"# TOPOLOGY_NODE: {node_id} END",
        ]
    )


def _with_buildkit_syntax(dockerfile_text: str, cache_plan: BuildCachePlan | None) -> str:
    if cache_plan is None or not cache_plan.enabled:
        return dockerfile_text
    if dockerfile_text.startswith("# syntax="):
        return dockerfile_text
    frontend_image = cache_plan.buildkit_frontend_image.strip() or "docker/dockerfile:1.7"
    return f"# syntax={frontend_image}\n\n" + dockerfile_text


def _guarded_dockerfile_nodes(topology: BuildTopology | None) -> list[str]:
    if topology is None:
        return list(EXECUTABLE_DOCKERFILE_NODE_ORDER)
    node_map = topology.node_map()
    return [node_id for node_id in EXECUTABLE_DOCKERFILE_NODE_ORDER if node_map.get(node_id) is not None]


def _topology_marker_ranges(
    dockerfile_text: str,
    node_ids: list[str],
    violations: list[str],
) -> dict[str, tuple[int, int]]:
    ranges: dict[str, tuple[int, int]] = {}
    for node_id in node_ids:
        start_pattern = rf"(?m)^# TOPOLOGY_NODE: {re.escape(node_id)} START\s*$"
        end_pattern = rf"(?m)^# TOPOLOGY_NODE: {re.escape(node_id)} END\s*$"
        starts = list(re.finditer(start_pattern, dockerfile_text))
        ends = list(re.finditer(end_pattern, dockerfile_text))
        if len(starts) != 1 or len(ends) != 1:
            violations.append(f"expected exactly one START/END marker pair for {node_id}")
            continue
        if starts[0].start() > ends[0].start():
            violations.append(f"START marker appears after END marker for {node_id}")
            continue
        ranges[node_id] = (starts[0].start(), ends[0].end())
    return ranges


def _extract_topology_blocks(dockerfile_text: str, node_ids: list[str]) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for node_id in node_ids:
        pattern = re.compile(
            rf"(?ms)^# TOPOLOGY_NODE: {re.escape(node_id)} START\s*\n(?P<body>.*?)"
            rf"^# TOPOLOGY_NODE: {re.escape(node_id)} END\s*$"
        )
        match = pattern.search(dockerfile_text)
        if match:
            blocks[node_id] = match.group("body").strip()
    return blocks


def _collect_project_files(project_path: Path) -> list[str]:
    if not project_path.exists():
        return []
    files: list[str] = []
    for child in sorted(project_path.iterdir(), key=lambda item: item.name.lower()):
        if child.name in IGNORED_DIRS:
            continue
        if child.is_dir():
            files.append(f"{child.name}/")
            continue
        if child.name in ENTRYPOINT_NAMES or any(child.match(pattern) for pattern in DEPENDENCY_FILE_PATTERNS):
            files.append(child.name)
    return files


def _looks_like_project_file_missing(normalized_text: str) -> bool:
    file_missing_patterns = (
        "can't open file",
        "cannot open file",
        "copy failed",
        "file not found",
        "no such file or directory: '/app/",
        "no such file or directory: \"./",
        "no such file or directory: './",
        "no such file or directory: 'main.py'",
        "no such file or directory: \"main.py\"",
    )
    return any(pattern in normalized_text for pattern in file_missing_patterns)


def _collect_dependency_info(project_path: Path) -> tuple[list[str], list[str], str]:
    dependency_files: list[str] = []
    packages: list[str] = []
    requires_python = ""
    if not project_path.exists():
        return dependency_files, packages, requires_python

    for pattern in DEPENDENCY_FILE_PATTERNS:
        for path in sorted(project_path.glob(pattern), key=lambda item: item.as_posix().lower()):
            if path.is_file():
                dependency_files.append(_relative_posix(path, project_path))
                if path.name.startswith("requirements") and path.suffix == ".txt":
                    packages.extend(_parse_requirements(path))
                elif path.name == "pyproject.toml":
                    pyproject_packages, pyproject_requires = _parse_pyproject_text(_read_text(path))
                    packages.extend(pyproject_packages)
                    requires_python = requires_python or pyproject_requires

    return _dedupe(dependency_files), _dedupe(packages), requires_python


def _collect_python_signals(
    project_path: Path,
    *,
    max_python_files: int = 80,
    max_bytes_per_file: int = 262144,
    priority_dirs: list[str] | None = None,
) -> tuple[list[str], list[str], list[str], list[str], dict[str, int | str]]:
    imports: list[str] = []
    entry_points: list[str] = []
    local_modules: list[str] = []
    external_commands: list[str] = []
    if not project_path.exists():
        return imports, entry_points, local_modules, external_commands, {
            "python_files_scanned": 0,
            "strategy": "priority_budget_walk",
        }

    budget = max(1, max_python_files)
    seen_paths: set[str] = set()
    python_files: list[Path] = []
    prioritized_roots = _priority_scan_roots(project_path, priority_dirs)
    for root in prioritized_roots:
        for path in _walk_python_files(root):
            key = path.as_posix().lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            python_files.append(path)
            if len(python_files) >= budget:
                break
        if len(python_files) >= budget:
            break

    if len(python_files) < budget:
        for path in _walk_python_files(project_path):
            key = path.as_posix().lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            python_files.append(path)
            if len(python_files) >= budget:
                break

    for path in python_files:
        text = _read_text(path, max_bytes=max_bytes_per_file)
        relative = _relative_posix(path, project_path)
        if path.name == "__init__.py":
            local_modules.append(path.parent.name)
        else:
            local_modules.append(path.stem)
        if path.name in ENTRYPOINT_NAMES or 'if __name__ == "__main__"' in text or "if __name__ == '__main__'" in text:
            entry_points.append(relative)
        imports.extend(_parse_python_imports(text))
        external_commands.extend(_parse_external_commands(text))
    return (
        _dedupe(imports),
        _dedupe(entry_points),
        _dedupe(_normalize_package_name(item) for item in local_modules),
        _dedupe(external_commands),
        {
            "python_files_scanned": len(python_files),
            "strategy": "priority_budget_walk",
        },
    )


def _collect_repair_memory_matches(
    repair_memory_path: str | Path | None,
    signals: list[str],
    *,
    project_name: str = "",
    project_key: str = "",
    allow_same_project_memory: bool = False,
    limit: int = 5,
) -> list[RetrievalMatch]:
    if repair_memory_path is None:
        return []
    memory = RepairMemory(repair_memory_path, create=False)
    query_text = " ".join(signal for signal in signals if signal)
    return memory.retrieve(
        query_text,
        top_k=limit,
        min_score=REPAIR_MEMORY_CANDIDATE_THRESHOLD,
        success_only=True,
        project_name=project_name,
        project_key=project_key,
        allow_same_project_memory=allow_same_project_memory,
        package_signals=signals,
    )


def _collect_pattern_memory_matches(
    pattern_memory_path: str | Path | None,
    signals: list[str],
    *,
    project_name: str = "",
    project_key: str = "",
    allow_same_project_memory: bool = False,
    limit: int = 5,
) -> list:
    if pattern_memory_path is None:
        return []
    return PatternMemory(pattern_memory_path, create=False).retrieve(
        signals,
        project_name=project_name,
        project_key=project_key,
        top_k=limit,
        allow_same_project_memory=allow_same_project_memory,
    )


def _runtime_validation_memory_signals(analysis: ProjectAnalysis) -> list[str]:
    return infer_runtime_validation_signals(
        imported_modules=analysis.imported_modules,
        python_packages=analysis.python_packages,
        detected_files=analysis.detected_files,
        entry_points=analysis.entry_points,
        external_commands=analysis.external_commands,
    )


def _collect_runtime_validation_memory_matches(
    runtime_validation_memory_path: str | Path | None,
    signals: list[str],
    *,
    project_name: str = "",
    project_key: str = "",
    allow_same_project_memory: bool = False,
    limit: int = 3,
) -> list[RuntimeValidationMatch]:
    if runtime_validation_memory_path is None:
        return []
    return RuntimeValidationMemory(runtime_validation_memory_path, create=False).retrieve(
        signals,
        project_name=project_name,
        project_key=project_key,
        top_k=limit,
        allow_same_project_memory=allow_same_project_memory,
    )


def _project_key_matches_memory(project_key: str, record_project_key: str) -> bool:
    return bool(project_key and record_project_key and project_key == record_project_key)


def _repair_memory_package_metadata(record) -> dict[str, str]:
    apt_packages = _record_apt_packages(record)
    python_packages = _record_python_packages(record)
    metadata: dict[str, str] = {}
    if apt_packages:
        metadata["apt_packages"] = ", ".join(apt_packages)
    if python_packages:
        metadata["python_packages"] = ", ".join(python_packages)
    return metadata


def _repair_memory_prefill_packages(matches: list[RetrievalMatch]) -> tuple[list[str], list[str]]:
    apt_packages: list[str] = []
    python_packages: list[str] = []
    for match in matches:
        if match.evidence_level != "prefill" and match.score < REPAIR_MEMORY_PREFILL_THRESHOLD:
            continue
        record = match.record
        if not getattr(record, "success", False):
            continue
        if record.fix_strategy == "install_apt_packages":
            apt_packages.extend(_record_apt_packages(record))
        elif record.fix_strategy == "install_python_package":
            python_packages.extend(_record_python_packages(record))
    return _dedupe(apt_packages), _dedupe(python_packages)


def _split_package_metadata(value: str) -> list[str]:
    return _dedupe(
        _normalize_package_name(item)
        for item in re.split(r"[\s,]+", value)
        if item.strip()
    )


def _project_name_matches_memory(project_name: str, record_project: str) -> bool:
    if not project_name or not record_project:
        return False
    if project_name in record_project or record_project in project_name:
        return True
    project_tokens = {token for token in re.split(r"[-_.]+", project_name) if token and not re.fullmatch(r"[0-9a-f]{6,}", token)}
    record_tokens = {token for token in re.split(r"[-_.]+", record_project) if token}
    if not project_tokens or not record_tokens:
        return False
    overlap = project_tokens & record_tokens
    return len(overlap) >= 4 and len(overlap) / len(record_tokens) >= 0.6


def _record_apt_packages(record) -> list[str]:
    metadata_packages = _split_package_metadata(str(record.metadata.get("apt_packages", "")))
    if metadata_packages:
        return metadata_packages
    if record.fix_action.lower().startswith("install apt packages:"):
        return _split_package_metadata(record.fix_action.split(":", 1)[1])
    return extract_apt_packages(record.fix_action)


def _record_python_packages(record) -> list[str]:
    metadata_packages = _split_package_metadata(str(record.metadata.get("python_packages", "")))
    if metadata_packages:
        return metadata_packages
    if record.fix_action.lower().startswith("install python packages:"):
        return _split_package_metadata(record.fix_action.split(":", 1)[1])
    return extract_python_packages(record.fix_action)


def _format_repair_memory_hints(matches: list[RetrievalMatch]) -> list[str]:
    return _dedupe(
        [
            f"{match.evidence_level} score={match.score:.2f}: "
            f"{match.record.error_signature} -> {match.record.fix_action}"[:240]
            for match in matches
        ]
    )


def _parse_requirements(path: Path) -> list[str]:
    packages: list[str] = []
    for raw_line in _read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-e ") and "#egg=" in line:
            line = line.rsplit("#egg=", 1)[-1]
        if line.startswith("-"):
            continue
        line = line.split("#", 1)[0].strip()
        package = _normalize_package_name(re.split(r"[<>=!~;\[]", line, maxsplit=1)[0])
        if package:
            packages.append(package)
    return _dedupe(packages)


def _parse_pyproject_text(text: str) -> tuple[list[str], str]:
    packages = []
    requires_python = ""
    for match in re.finditer(r"requires-python\s*=\s*[\"']([^\"']+)[\"']", text):
        requires_python = match.group(1)
        break
    for match in re.finditer(r"[\"']([A-Za-z0-9_.-]+)(?:[<>=!~;\[].*)?[\"']", text):
        package = _normalize_package_name(match.group(1))
        if package and package not in {"project", "version", "description", "readme"}:
            packages.append(package)
    return _dedupe(packages), requires_python


def _parse_python_imports(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module.split(".", 1)[0])
    return _dedupe(
        _normalize_package_name(item)
        for item in imports
        if item and item not in IGNORED_IMPORT_MODULES and not item.startswith("_")
    )


def _parse_external_commands(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    commands: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name in {
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.Popen",
        }:
            commands.extend(_command_from_call(node))
        elif call_name in {"os.system", "os.popen"}:
            commands.extend(_command_from_call(node))
    return _dedupe(
        command
        for command in commands
        if command
        and command
        not in {
            "python",
            "python3",
            "pip",
            "sh",
            "bash",
        }
    )


def _priority_scan_roots(project_path: Path, priority_dirs: list[str] | None = None) -> list[Path]:
    default_priority = ["src", "app", "backend", "server", "api", "services", "tests"]
    roots = [project_path]
    for name in priority_dirs or default_priority:
        candidate = project_path / name
        if candidate.exists() and candidate.is_dir():
            roots.append(candidate)
    return roots


def _walk_python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]
        current = Path(dirpath)
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            yield current / filename


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _command_from_call(node: ast.Call) -> list[str]:
    if not node.args:
        return []
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return [_normalize_command_name(first.value)]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return [_normalize_command_name(head.value)]
    return []


def _normalize_command_name(command: str) -> str:
    first = command.strip().split(maxsplit=1)[0].strip("\"'")
    if "/" in first or "\\" in first:
        first = re.split(r"[/\\]", first)[-1]
    return _normalize_package_name(first)


def _compute_execution_layers(nodes: list[TopologyNode]) -> list[list[str]]:
    remaining = {node.node_id: set(node.depends_on) for node in nodes}
    completed: set[str] = set()
    layers: list[list[str]] = []
    while remaining:
        ready = sorted(node_id for node_id, dependencies in remaining.items() if dependencies <= completed)
        if not ready:
            layers.append(sorted(remaining))
            break
        layers.append(ready)
        completed.update(ready)
        for node_id in ready:
            remaining.pop(node_id, None)
    return layers


def _dedupe(items) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _dedupe_evidence(items: list[TopologyEvidenceItem]) -> list[TopologyEvidenceItem]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[TopologyEvidenceItem] = []
    for item in items:
        key = (item.source_type, item.raw_signal, item.inferred_target)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _format_list(items: list[str], *, empty: str) -> str:
    return ", ".join(items[:12]) if items else empty


def _escape_mermaid_label(text: str) -> str:
    return text.replace('"', "'")


def _has_ignored_parent(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in relative_parts[:-1])


def _normalize_package_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _read_text(path: Path, *, max_bytes: int | None = None) -> str:
    try:
        if max_bytes is not None and max_bytes > 0:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                return handle.read(max_bytes)
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-").lower()
    return safe[:80] or "project"


def _safe_cache_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-").lower()
    return safe[:80] or "topomen"


def _build_dependency_hash(
    project_path: Path,
    *,
    dependency_files: list[str],
    base_image: str,
    system_packages: list[str],
    python_packages: list[str],
    missing_python_packages: list[str],
    pattern_python_packages: list[str],
    memory_python_packages: list[str],
) -> str:
    digest = hashlib.sha256()
    digest.update(base_image.encode("utf-8", errors="ignore"))
    for label, values in {
        "system": system_packages,
        "python": python_packages,
        "missing": missing_python_packages,
        "pattern": pattern_python_packages,
        "memory": memory_python_packages,
    }.items():
        digest.update(f"\n[{label}]\n".encode())
        for value in sorted(values):
            digest.update(value.encode("utf-8", errors="ignore"))
            digest.update(b"\0")
    for dependency_file in sorted(dependency_files):
        normalized = dependency_file.replace("\\", "/")
        digest.update(f"\n[file]{normalized}\n".encode())
        digest.update(_read_text(project_path / normalized, max_bytes=1_000_000).encode("utf-8", errors="ignore"))
    return digest.hexdigest()[:16]


def _build_workspace_hash(project_path: Path) -> str:
    try:
        workspace_key = str(project_path.resolve())
    except OSError:
        workspace_key = str(project_path)
    return hashlib.sha256(workspace_key.encode("utf-8", errors="ignore")).hexdigest()[:8]


def _generate_threaded_merge_preview_dockerfile(topology: BuildTopology, *, artifact_name: str) -> str:
    analysis = topology.analysis
    prefix = f"topomen-internal-{artifact_name}"
    system_needed = bool(analysis.system_packages)
    python_needed = bool(
        analysis.dependency_files
        or analysis.missing_python_packages
        or analysis.pattern_python_packages
        or analysis.memory_python_packages
    )
    blocks = [
        _wrap_topology_block("base_image_selection", "\n".join([f"FROM {analysis.base_image}", "WORKDIR /app"])),
    ]
    if system_needed:
        system_block = "\n".join(
            [
                f"FROM {prefix}-system-deps AS system-deps",
                "# Scheduler concrete merge uses the winner branch image; this artifact is a pre-run preview.",
            ]
        )
    else:
        system_block = "\n".join(
            [
                f"FROM {prefix}-base AS system-deps",
                "# Preview only: no threaded system dependency branch is required.",
            ]
        )
    blocks.append(_wrap_topology_block("system_dependency_plan", system_block))
    if python_needed:
        python_block = "\n".join(
            [
                f"FROM {prefix}-python-deps AS python-deps",
                "# Scheduler concrete merge uses the winner branch image; this artifact is a pre-run preview.",
            ]
        )
    else:
        python_block = "\n".join(
            [
                f"FROM {prefix}-base AS python-deps",
                "# Preview only: no threaded Python dependency branch is required.",
            ]
        )
    blocks.append(_wrap_topology_block("python_dependency_plan", python_block))
    final_base = "python-deps" if python_needed else "system-deps"
    file_copy_lines = [f"FROM {final_base} AS final"]
    if system_needed and python_needed and not _python_stage_requires_system_stage(analysis):
        file_copy_lines.append("COPY --from=python-deps /usr/local /usr/local")
        file_copy_lines[0] = "FROM system-deps AS final"
    file_copy_lines.append("COPY . .")
    blocks.append(_wrap_topology_block("file_copy_plan", "\n".join(file_copy_lines)))
    blocks.append(_wrap_topology_block("runtime_validation", _runtime_command_block(analysis)))
    return _with_buildkit_syntax("\n\n".join(blocks).rstrip() + "\n", analysis.build_cache)


def _topology_to_svg(topology: BuildTopology) -> str:
    analysis = topology.analysis
    layers = topology.execution_layers or [[node.node_id for node in topology.nodes]]
    node_width = 285
    node_height = 126
    x_gap = 72
    y_gap = 28
    margin = 40
    max_rows = max((len(layer) for layer in layers), default=1)
    analysis_width = margin * 2 + len(layers) * node_width + max(0, len(layers) - 1) * x_gap
    threaded_layers = _threaded_build_svg_layers(analysis)
    threaded_max_rows = max((len(layer) for layer in threaded_layers), default=1)
    threaded_width = margin * 2 + len(threaded_layers) * node_width + max(0, len(threaded_layers) - 1) * x_gap
    width = max(1120, analysis_width, threaded_width)
    summary_height = 220
    analysis_title_y = summary_height + 38
    analysis_y = summary_height + 70
    analysis_height = max_rows * node_height + max(0, max_rows - 1) * y_gap
    threaded_title_y = analysis_y + analysis_height + 78
    threaded_y = threaded_title_y + 32
    threaded_height = threaded_max_rows * node_height + max(0, threaded_max_rows - 1) * y_gap
    height = threaded_y + threaded_height + margin

    summary_cards = _svg_summary_cards(topology, width=width, y=68)
    analysis_positions = _svg_layer_positions(
        layers,
        width=width,
        node_width=node_width,
        node_height=node_height,
        x_gap=x_gap,
        y_gap=y_gap,
        y=analysis_y,
    )
    threaded_positions = _svg_layer_positions(
        [[node["id"] for node in layer] for layer in threaded_layers],
        width=width,
        node_width=node_width,
        node_height=node_height,
        x_gap=x_gap,
        y_gap=y_gap,
        y=threaded_y,
    )

    analysis_edges = _svg_edges(
        [(dependency, node.node_id) for node in topology.nodes for dependency in node.depends_on],
        analysis_positions,
        node_width=node_width,
        node_height=node_height,
    )
    threaded_edges = _svg_edges(
        _threaded_build_svg_edges(analysis),
        threaded_positions,
        node_width=node_width,
        node_height=node_height,
    )
    analysis_nodes = [
        _svg_node_card(
            node_id=node.node_id,
            title=node.title,
            subtitle=node.node_type,
            lines=_svg_topology_node_lines(node, analysis),
            position=analysis_positions[node.node_id],
            width=node_width,
            height=node_height,
            fill=_svg_node_fill(node.node_type),
        )
        for node in topology.nodes
        if node.node_id in analysis_positions
    ]
    threaded_nodes = [
        _svg_node_card(
            node_id=node["id"],
            title=node["title"],
            subtitle=node["subtitle"],
            lines=node["lines"],
            position=threaded_positions[node["id"]],
            width=node_width,
            height=node_height,
            fill=node["fill"],
            stroke=node["stroke"],
            opacity=node["opacity"],
        )
        for layer in threaded_layers
        for node in layer
    ]

    parallel_note = (
        "Parallel branch window: system-deps and python-deps can build concurrently."
        if topology.parallel_build_stages
        else "Serialized branch window: python-deps waits for system-deps when native build packages are required."
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="TopoMen project instance topology graph">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#7a6f5d"/>
    </marker>
    <style>
      .title {{ font: 700 14px Georgia, 'Times New Roman', serif; fill: #17202a; }}
      .section {{ font: 700 18px Georgia, 'Times New Roman', serif; fill: #17202a; }}
      .subtitle {{ font: 12px Consolas, 'Courier New', monospace; fill: #52616b; }}
      .body {{ font: 12px Consolas, 'Courier New', monospace; fill: #263238; }}
      .small {{ font: 11px Consolas, 'Courier New', monospace; fill: #5f6c72; }}
    </style>
  </defs>
  <rect width="100%" height="100%" fill="#f8f6f0"/>
  <text x="{margin}" y="38" class="title">TopoMen Project Instance Topology</text>
  <text x="{margin}" y="58" class="small">{html.escape(analysis.project_path)}</text>
  {summary_cards}
  <text x="{margin}" y="{analysis_title_y}" class="section">Analysis and Repair DAG</text>
  <g>{''.join(analysis_edges)}</g>
  <g>{''.join(analysis_nodes)}</g>
  <text x="{margin}" y="{threaded_title_y}" class="section">Threaded Docker Build Plan</text>
  <text x="{margin}" y="{threaded_title_y + 18}" class="small">{html.escape(parallel_note)}</text>
  <g>{''.join(threaded_edges)}</g>
  <g>{''.join(threaded_nodes)}</g>
</svg>
"""


def _svg_layer_positions(
    layers: list[list[str]],
    *,
    width: int,
    node_width: int,
    node_height: int,
    x_gap: int,
    y_gap: int,
    y: int,
) -> dict[str, tuple[int, int]]:
    graph_width = len(layers) * node_width + max(0, len(layers) - 1) * x_gap
    x_start = max(40, (width - graph_width) // 2)
    max_rows = max((len(layer) for layer in layers), default=1)
    graph_height = max_rows * node_height + max(0, max_rows - 1) * y_gap
    positions: dict[str, tuple[int, int]] = {}
    for layer_index, layer in enumerate(layers):
        layer_height = len(layer) * node_height + max(0, len(layer) - 1) * y_gap
        y_start = y + max(0, (graph_height - layer_height) // 2)
        x = x_start + layer_index * (node_width + x_gap)
        for row_index, node_id in enumerate(layer):
            positions[node_id] = (x, y_start + row_index * (node_height + y_gap))
    return positions


def _svg_edges(
    edges_to_draw: list[tuple[str, str]],
    positions: dict[str, tuple[int, int]],
    *,
    node_width: int,
    node_height: int,
) -> list[str]:
    edges = []
    for source_id, target_id in edges_to_draw:
        target = positions.get(target_id)
        if target is None:
            continue
        target_x, target_y = target
        source = positions.get(source_id)
        if source is None:
            continue
        source_x, source_y = source
        edges.append(
            f'<path d="M {source_x + node_width} {source_y + node_height // 2} '
            f'C {source_x + node_width + 42} {source_y + node_height // 2}, '
            f'{target_x - 42} {target_y + node_height // 2}, {target_x} {target_y + node_height // 2}" '
            'fill="none" stroke="#7a6f5d" stroke-width="2" marker-end="url(#arrow)"/>'
        )
    return edges


def _svg_node_card(
    *,
    node_id: str,
    title: str,
    subtitle: str,
    lines: list[str],
    position: tuple[int, int],
    width: int,
    height: int,
    fill: str,
    stroke: str = "#564f44",
    opacity: str = "1",
) -> str:
    x, y = position
    title_lines = _wrap_svg_text(title, max_chars=31)
    text_lines = [
        f'<text x="{x + 14}" y="{y + 24}" class="title">{html.escape(title_lines[0])}</text>',
        *[
            f'<text x="{x + 14}" y="{y + 24 + line_index * 15}" class="title">{html.escape(line)}</text>'
            for line_index, line in enumerate(title_lines[1:], start=1)
        ],
        f'<text x="{x + 14}" y="{y + 50}" class="subtitle">{html.escape(subtitle)}</text>',
    ]
    for line_index, line in enumerate(_wrap_svg_lines(lines, max_chars=35, max_lines=4), start=0):
        text_lines.append(f'<text x="{x + 14}" y="{y + 70 + line_index * 14}" class="body">{html.escape(line)}</text>')
    return "\n".join(
        [
            f'<g id="{html.escape(node_id)}" opacity="{opacity}">',
            (
                f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="12" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
            ),
            *text_lines,
            "</g>",
        ]
    )


def _svg_summary_cards(topology: BuildTopology, *, width: int, y: int) -> str:
    analysis = topology.analysis
    cache = analysis.build_cache
    python_packages = _dedupe(
        [
            *analysis.python_packages,
            *analysis.missing_python_packages,
            *analysis.pattern_python_packages,
            *analysis.memory_python_packages,
        ]
    )
    cards = [
        (
            "Project Signals",
            [
                f"files: {len(analysis.detected_files)}",
                f"deps: {_format_list(analysis.dependency_files, empty='none')}",
                f"imports: {_format_list(analysis.imported_modules, empty='none')}",
            ],
        ),
        (
            "Dependency Instance",
            [
                f"apt: {_format_list(analysis.system_packages, empty='none')}",
                f"pip: {_format_list(python_packages, empty='none')}",
                f"external: {_format_list(analysis.external_commands, empty='none')}",
            ],
        ),
        (
            "Runtime",
            [
                f"mode: {analysis.runtime_validation.mode}",
                f"target: {analysis.runtime_validation.selected_target or 'none'}",
                f"cmd: {_runtime_validation_command(analysis)}",
            ],
        ),
        (
            "Build Cache",
            [
                f"strategy: {cache.strategy}",
                f"scope: {cache.cache_id_scope}",
                f"reuse: {str(cache.reuse_existing).lower()}",
                f"hash: {cache.dependency_hash or 'none'}",
                f"pip cache: {cache.pip_cache_id or 'disabled'}",
            ],
        ),
    ]
    card_gap = 16
    card_width = (width - 80 - card_gap * (len(cards) - 1)) // len(cards)
    rendered = []
    for index, (title, lines) in enumerate(cards):
        x = 40 + index * (card_width + card_gap)
        rendered.append(
            _svg_node_card(
                node_id=f"summary_{index}",
                title=title,
                subtitle="project-specific",
                lines=lines,
                position=(x, y),
                width=card_width,
                height=124,
                fill="#fffaf0",
                stroke="#c9a66b",
            )
        )
    return "\n".join(rendered)


def _svg_topology_node_lines(node: TopologyNode, analysis: ProjectAnalysis) -> list[str]:
    if node.node_id == "base_image_selection":
        return [f"FROM {analysis.base_image}", f"source: {analysis.base_image_source}"]
    if node.node_id == "system_dependency_plan":
        return [
            f"apt: {_format_list(analysis.system_packages, empty='none')}",
            f"pattern: {_format_list(analysis.pattern_apt_packages, empty='none')}",
            f"memory: {_format_list(analysis.memory_apt_packages, empty='none')}",
        ]
    if node.node_id == "python_dependency_plan":
        packages = _dedupe(
            [
                *analysis.python_packages,
                *analysis.missing_python_packages,
                *analysis.pattern_python_packages,
                *analysis.memory_python_packages,
            ]
        )
        return [
            f"pip: {_format_list(packages, empty='none')}",
            f"missing: {_format_list(analysis.missing_python_packages, empty='none')}",
            f"deps: {_format_list(analysis.dependency_files, empty='none')}",
        ]
    if node.node_id == "file_copy_plan":
        return [f"files: {len(analysis.detected_files)}", f"entry: {_format_list(analysis.entry_points, empty='none')}"]
    if node.node_id == "runtime_validation":
        return [
            f"mode: {analysis.runtime_validation.mode}",
            f"target: {analysis.runtime_validation.selected_target or 'none'}",
            f"cmd: {_runtime_validation_command(analysis)}",
        ]
    if node.node_id == "build_validation":
        build_tool = "buildx" if analysis.build_cache.enabled else "docker build"
        return [f"tool: {build_tool}", f"cache: {analysis.build_cache.strategy}"]
    if node.node_id in {"repair_memory_lookup", "pattern_memory_lookup"}:
        return [f"hints: {len(node.evidence)}", f"artifacts: {_format_list(node.owned_artifacts, empty='none')}"]
    if node.outputs:
        output_lines = []
        for key, value in list(node.outputs.items())[:3]:
            if isinstance(value, list):
                output_lines.append(f"{key}: {_format_list([str(item) for item in value], empty='none')}")
            else:
                output_lines.append(f"{key}: {value}")
        return output_lines
    return [f"risk: {_format_list(node.risk_tags, empty='none')}", f"cache: {node.cache_scope}"]


def _threaded_build_svg_layers(analysis: ProjectAnalysis) -> list[list[dict[str, str | list[str]]]]:
    system_needed = bool(analysis.system_packages)
    python_packages = _dedupe(
        [
            *analysis.python_packages,
            *analysis.missing_python_packages,
            *analysis.pattern_python_packages,
            *analysis.memory_python_packages,
        ]
    )
    python_needed = bool(analysis.dependency_files or python_packages)
    python_requires_system = system_needed and python_needed and _python_stage_requires_system_stage(analysis)
    base = _threaded_svg_node(
        "threaded_base",
        "base branch",
        "internal image",
        [f"FROM {analysis.base_image}", "tag: *-threaded-base"],
    )
    system = _threaded_svg_node(
        "threaded_system",
        "system-deps branch",
        "parallel branch" if not python_requires_system else "upstream branch",
        [f"apt: {_format_list(analysis.system_packages, empty='none')}", "tag: *-threaded-system-deps"],
        skipped=not system_needed,
    )
    python = _threaded_svg_node(
        "threaded_python",
        "python-deps branch",
        "parallel branch" if not python_requires_system else "depends on system",
        [
            f"base: {'system-deps' if python_requires_system else 'base'}",
            f"pip: {_format_list(python_packages, empty='none')}",
            "tag: *-threaded-python-deps",
        ],
        skipped=not python_needed,
    )
    merge_lines = [
        "merge Dockerfile",
        f"system: {'winner image' if system_needed else 'base'}",
        f"python: {'winner image' if python_needed else 'base'}",
    ]
    merge = _threaded_svg_node("threaded_merge", "merge stage", "single image synthesis", merge_lines)
    final = _threaded_svg_node(
        "threaded_final",
        "final runtime image",
        "public delivery image",
        ["only image_name is run", f"runtime: {analysis.runtime_validation.mode}"],
    )
    if python_requires_system:
        return [[base], [system], [python], [merge], [final]]
    return [[base], [system, python], [merge], [final]]


def _threaded_svg_node(
    node_id: str,
    title: str,
    subtitle: str,
    lines: list[str],
    *,
    skipped: bool = False,
) -> dict[str, str | list[str]]:
    return {
        "id": node_id,
        "title": title,
        "subtitle": "skipped branch" if skipped else subtitle,
        "lines": ["not required for this project", *lines] if skipped else lines,
        "fill": "#ede7dd" if skipped else "#dff3f6",
        "stroke": "#9a8f80" if skipped else "#2f7180",
        "opacity": "0.62" if skipped else "1",
    }


def _threaded_build_svg_edges(analysis: ProjectAnalysis) -> list[tuple[str, str]]:
    system_needed = bool(analysis.system_packages)
    python_needed = bool(
        analysis.dependency_files
        or analysis.python_packages
        or analysis.missing_python_packages
        or analysis.pattern_python_packages
        or analysis.memory_python_packages
    )
    if system_needed and python_needed and _python_stage_requires_system_stage(analysis):
        return [
            ("threaded_base", "threaded_system"),
            ("threaded_system", "threaded_python"),
            ("threaded_python", "threaded_merge"),
            ("threaded_merge", "threaded_final"),
        ]
    return [
        ("threaded_base", "threaded_system"),
        ("threaded_base", "threaded_python"),
        ("threaded_system", "threaded_merge"),
        ("threaded_python", "threaded_merge"),
        ("threaded_merge", "threaded_final"),
    ]


def _svg_node_fill(node_type: str) -> str:
    return {
        "evidence": "#e6f0ea",
        "planning": "#fff2cc",
        "execution": "#dbeafe",
        "verification": "#e9ddff",
        "repair": "#ffe0d6",
        "artifact": "#dff3f6",
    }.get(node_type, "#fffaf0")


def _wrap_svg_text(text: str, *, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    if not lines:
        lines = [text[:max_chars]]
    return lines[:2]


def _wrap_svg_lines(lines: list[str], *, max_chars: int, max_lines: int) -> list[str]:
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_wrap_svg_text(line, max_chars=max_chars))
        if len(wrapped) >= max_lines:
            return wrapped[:max_lines]
    return wrapped


def _topology_artifact_index_md(topology: BuildTopology, *, artifact_name: str, render_svg: bool) -> str:
    analysis = topology.analysis
    cache = analysis.build_cache
    lines = [
        f"# Topology: {artifact_name}",
        "",
        "## Summary",
        "",
        f"- Project path: `{analysis.project_path}`",
        f"- Base image: `{analysis.base_image}`",
        f"- Build mode: `{'multistage' if topology.multistage else 'single-stage/threaded-ready'}`",
        f"- Parallel build stages: `{_format_list(topology.parallel_build_stages, empty='none')}`",
        f"- Build cache: `{cache.strategy}`",
        f"- Build cache reuse existing: `{cache.reuse_existing}`",
        f"- Build cache id scope: `{cache.cache_id_scope}`",
        f"- Dependency hash: `{cache.dependency_hash or 'none'}`",
    ]
    if cache.enabled:
        lines.extend(
            [
                f"- Apt cache id: `{cache.apt_cache_id}`",
                f"- Apt lists cache id: `{cache.apt_lists_cache_id}`",
                f"- Pip cache id: `{cache.pip_cache_id}`",
            ]
        )
    lines.extend(["", "## Topology Graph", ""])
    if render_svg:
        lines.extend(["![Topology graph](topology.svg)", ""])
    lines.extend(["```mermaid", topology.to_mermaid(), "```", "", "## Execution Layers", ""])
    for index, layer in enumerate(topology.execution_layers, start=1):
        lines.append(f"- Layer {index}: `{', '.join(layer)}`")
    lines.extend(["", "## Node Blocks", ""])
    for node in topology.nodes:
        lines.append(f"### {node.node_id}")
        lines.append("")
        lines.append(f"- Type: `{node.node_type}`")
        lines.append(f"- Cache scope: `{node.cache_scope}`")
        lines.append(f"- Risk tags: `{_format_list(node.risk_tags, empty='none')}`")
        if node.dockerfile_block:
            lines.extend(["", "```Dockerfile", node.dockerfile_block, "```"])
        lines.append("")
    lines.extend(
        [
            "## Files",
            "",
            "- `topology.json`: structured topology and analysis payload.",
            "- `topology.mmd`: Mermaid DAG source.",
            *(["- `topology.svg`: offline rendered project instance topology graph."] if render_svg else []),
            "- `Dockerfile.prefill`: generated executable Dockerfile.",
            "- `Dockerfile.merge`: threaded merge preview before a run; the scheduler writes the concrete winner-image merge Dockerfile in the workspace.",
            "- Threaded branch images are internal and ephemeral; the only public validation image is the scheduler `final_image`.",
            "- `index.html`: offline HTML view.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _topology_artifact_index_html(topology: BuildTopology, *, artifact_name: str, render_svg: bool) -> str:
    analysis = topology.analysis
    cache = analysis.build_cache
    node_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(node.node_id)}</td>"
        f"<td>{html.escape(node.node_type)}</td>"
        f"<td>{html.escape(node.cache_scope)}</td>"
        f"<td>{html.escape(', '.join(node.depends_on) or 'none')}</td>"
        f"<td>{html.escape(', '.join(node.risk_tags) or 'none')}</td>"
        "</tr>"
        for node in topology.nodes
    )
    cache_rows = "\n".join(
        [
            f"<li>strategy: <code>{html.escape(cache.strategy)}</code></li>",
            f"<li>reuse existing: <code>{str(cache.reuse_existing).lower()}</code></li>",
            f"<li>cache id scope: <code>{html.escape(cache.cache_id_scope)}</code></li>",
            f"<li>dependency hash: <code>{html.escape(cache.dependency_hash or 'none')}</code></li>",
            f"<li>apt cache: <code>{html.escape(cache.apt_cache_id or 'disabled')}</code></li>",
            f"<li>apt lists cache: <code>{html.escape(cache.apt_lists_cache_id or 'disabled')}</code></li>",
            f"<li>pip cache: <code>{html.escape(cache.pip_cache_id or 'disabled')}</code></li>",
        ]
    )
    graph_html = (
        '<img src="topology.svg" alt="Topology graph" '
        'style="width:100%;max-width:1200px;border:1px solid #d9c9a3;border-radius:12px;background:#fffaf0;">'
        if render_svg
        else f"<pre>{html.escape(topology.to_mermaid())}</pre>"
    )
    svg_link = '<li><a href="topology.svg">topology.svg</a></li>' if render_svg else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>TopoMen Topology - {html.escape(artifact_name)}</title>
  <style>
    body {{ font-family: Georgia, 'Times New Roman', serif; margin: 32px; color: #17202a; background: #f8f6f0; }}
    code, pre {{ font-family: Consolas, 'Courier New', monospace; }}
    pre {{ padding: 16px; overflow: auto; background: #101820; color: #f2efe8; border-radius: 10px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fffaf0; }}
    th, td {{ border: 1px solid #d9c9a3; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #eadfca; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
    .card {{ background: #fffaf0; border: 1px solid #d9c9a3; border-radius: 12px; padding: 16px; }}
  </style>
</head>
<body>
  <h1>TopoMen Topology: {html.escape(artifact_name)}</h1>
  <div class="grid">
    <section class="card">
      <h2>Project</h2>
      <p><strong>Path:</strong> <code>{html.escape(analysis.project_path)}</code></p>
      <p><strong>Base image:</strong> <code>{html.escape(analysis.base_image)}</code></p>
      <p><strong>Parallel stages:</strong> <code>{html.escape(_format_list(topology.parallel_build_stages, empty='none'))}</code></p>
    </section>
    <section class="card">
      <h2>Build Cache</h2>
      <ul>{cache_rows}</ul>
    </section>
  </div>
  <h2>Topology Graph</h2>
  {graph_html}
  <h2>Mermaid DAG Source</h2>
  <pre>{html.escape(topology.to_mermaid())}</pre>
  <h2>Nodes</h2>
  <table>
    <thead><tr><th>Node</th><th>Type</th><th>Cache Scope</th><th>Depends On</th><th>Risk Tags</th></tr></thead>
    <tbody>{node_rows}</tbody>
  </table>
  <h2>Files</h2>
  <ul>
    <li><a href="topology.json">topology.json</a></li>
    <li><a href="topology.mmd">topology.mmd</a></li>
    {svg_link}
    <li><a href="Dockerfile.prefill">Dockerfile.prefill</a></li>
    <li><a href="Dockerfile.merge">Dockerfile.merge</a></li>
  </ul>
</body>
</html>
"""


def _write_topologies_index(output_dir: Path) -> Path:
    entries = []
    for child in sorted(output_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        if not (child / "index.md").exists():
            continue
        entries.append(f"- [{child.name}]({child.name}/index.md) ([html]({child.name}/index.html))")
    index_path = output_dir / "index.md"
    lines = ["# TopoMen Topology Index", "", *entries]
    if not entries:
        lines.append("- No topology artifacts generated yet.")
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return index_path
