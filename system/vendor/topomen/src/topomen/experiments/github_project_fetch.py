#!/usr/bin/env python3

"""Fetch small GitHub projects and emit a repair-experiment manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel, Field
from rich.console import Console

from topomen.core.paths import EXPERIMENT_MANIFESTS_DIR, EXPERIMENT_PROJECTS_DIR

DEPENDENCY_FILE_NAMES = {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"}
ENTRYPOINT_NAMES = {"main.py", "app.py", "server.py", "run.py"}
DOCKERFILE_NAMES = {"Dockerfile", "dockerfile", "Containerfile", "containerfile"}
REJECTION_STAGES = {"metadata", "workspace", "clone", "project_scan"}
REJECTION_REASONS = {
    "archived_repository",
    "clone_or_git_metadata_failed",
    "existing_target_without_force",
    "fork_repository",
    "missing_dependency_manifest",
    "missing_entrypoint",
    "non_python_repository",
    "repository_too_large",
}

console = Console(highlight=False)


class GitHubProjectFetchConfig(BaseModel):
    query: str = "language:Python size:<2000 archived:false fork:false"
    limit: int = Field(default=20, ge=1)
    destination: Path = EXPERIMENT_PROJECTS_DIR / "github"
    manifest_path: Path = EXPERIMENT_MANIFESTS_DIR / "github_small_projects.json"
    rejected_manifest_path: Path | None = None
    rejection_summary_path: Path | None = None
    max_repo_size_kb: int = Field(default=2000, ge=1)
    require_entrypoint: bool = True
    remove_existing_dockerfiles: bool = True
    github_token_env: str = "GITHUB_TOKEN"
    clone_timeout_seconds: int = Field(default=180, ge=1)
    force: bool = False
    from_file: Path | None = None


class GitHubProjectSpec(BaseModel):
    project_name: str
    task: str
    github_url: str
    clone_url: str
    commit_sha: str
    default_branch: str | None = None
    size_kb: int = 0
    stars: int = 0
    selection_reason: str = ""


class GitHubProjectRejectedSpec(BaseModel):
    project_name: str
    github_url: str
    clone_url: str
    default_branch: str | None = None
    size_kb: int = 0
    stars: int = 0
    rejection_stage: str
    rejection_reason: str
    rejection_detail: str = ""


class GitHubProjectRejectionSummary(BaseModel):
    accepted_count: int = 0
    rejected_count: int = 0
    input_candidate_count: int = 0
    scanned_candidate_count: int = 0
    by_stage: dict[str, int] = Field(default_factory=dict)
    by_reason: dict[str, int] = Field(default_factory=dict)


def fetch_github_projects(config: GitHubProjectFetchConfig) -> list[GitHubProjectSpec]:
    config.destination.mkdir(parents=True, exist_ok=True)
    candidates = _load_candidates(config)
    specs: list[GitHubProjectSpec] = []
    rejected_specs: list[GitHubProjectRejectedSpec] = []
    scanned_candidates = 0
    for candidate in candidates:
        if len(specs) >= config.limit:
            break
        scanned_candidates += 1
        preflight_reason = _candidate_rejection_reason(candidate, config)
        if preflight_reason is not None:
            rejected_specs.append(_build_rejected_spec(candidate, stage="metadata", reason=preflight_reason))
            continue
        target = config.destination / _repo_dir_name(candidate)
        if target.exists():
            if not config.force:
                rejected_specs.append(
                    _build_rejected_spec(
                        candidate,
                        stage="workspace",
                        reason="existing_target_without_force",
                        detail=str(target),
                    )
                )
                console.print(f"[yellow]Skipping existing[/yellow] {target}")
                continue
            _safe_rmtree(target, config.destination)
        clone_url = str(candidate["clone_url"])
        try:
            _clone_repo(clone_url, target, timeout_seconds=config.clone_timeout_seconds)
            if not _has_dependency_manifest(target):
                rejected_specs.append(
                    _build_rejected_spec(
                        candidate,
                        stage="project_scan",
                        reason="missing_dependency_manifest",
                        detail=str(target),
                    )
                )
                _safe_rmtree(target, config.destination)
                continue
            if config.require_entrypoint and not _has_entrypoint(target):
                rejected_specs.append(
                    _build_rejected_spec(
                        candidate,
                        stage="project_scan",
                        reason="missing_entrypoint",
                        detail=str(target),
                    )
                )
                _safe_rmtree(target, config.destination)
                continue
            if config.remove_existing_dockerfiles:
                _remove_existing_dockerfiles(target)
            commit_sha = _git_commit_sha(target)
        except (OSError, subprocess.SubprocessError) as exc:
            rejected_specs.append(
                _build_rejected_spec(
                    candidate,
                    stage="clone",
                    reason="clone_or_git_metadata_failed",
                    detail=str(exc),
                )
            )
            console.print(f"[yellow]Skipping failed clone[/yellow] {clone_url}: {exc}")
            if target.exists():
                _safe_rmtree(target, config.destination)
            continue
        specs.append(
            GitHubProjectSpec(
                project_name=str(candidate["name"]),
                task=str(target),
                github_url=str(candidate["html_url"]),
                clone_url=clone_url,
                commit_sha=commit_sha,
                default_branch=str(candidate.get("default_branch") or "") or None,
                size_kb=int(candidate.get("size") or 0),
                stars=int(candidate.get("stargazers_count") or 0),
                selection_reason=_selection_reason(target, candidate),
            )
        )
    _write_manifest(config.manifest_path, specs)
    rejected_manifest_path = _resolve_rejected_manifest_path(config)
    _write_manifest(rejected_manifest_path, rejected_specs)
    summary_path = _resolve_rejection_summary_path(config)
    summary = _build_rejection_summary(
        specs,
        rejected_specs,
        input_candidate_count=len(candidates),
        scanned_candidate_count=scanned_candidates,
    )
    _write_rejection_summary(summary_path, summary)
    return specs


def _load_candidates(config: GitHubProjectFetchConfig) -> list[dict[str, Any]]:
    if config.from_file is not None:
        return _load_candidates_from_file(config.from_file)
    return _search_github_repositories(config)


def _search_github_repositories(config: GitHubProjectFetchConfig) -> list[dict[str, Any]]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get(config.github_token_env)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = requests.get(
        "https://api.github.com/search/repositories",
        params={
            "q": config.query,
            "sort": "stars",
            "order": "asc",
            "per_page": min(max(config.limit * 3, config.limit), 100),
        },
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _load_candidates_from_file(path: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        html_url = line[:-4] if line.endswith(".git") else line
        name = html_url.rstrip("/").rsplit("/", 1)[-1]
        clone_url = line if line.endswith(".git") else f"{html_url}.git"
        candidates.append(
            {
                "name": name,
                "full_name": _full_name_from_url(html_url),
                "html_url": html_url,
                "clone_url": clone_url,
                "default_branch": None,
                "size": 0,
                "stargazers_count": 0,
                "fork": False,
                "archived": False,
                "language": "Python",
            }
        )
    return candidates


def _candidate_rejection_reason(candidate: dict[str, Any], config: GitHubProjectFetchConfig) -> str | None:
    if candidate.get("fork"):
        return "fork_repository"
    if candidate.get("archived"):
        return "archived_repository"
    if str(candidate.get("language") or "").lower() not in {"", "python"}:
        return "non_python_repository"
    size = int(candidate.get("size") or 0)
    if size > config.max_repo_size_kb:
        return "repository_too_large"
    return None


def _repo_dir_name(candidate: dict[str, Any]) -> str:
    raw = str(candidate.get("full_name") or candidate.get("name") or "repo")
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "__", raw).strip("._-")
    return safe[:100] or "repo"


def _clone_repo(clone_url: str, target: Path, *, timeout_seconds: int) -> None:
    subprocess.run(
        ["git", "clone", "--depth", "1", clone_url, str(target)],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _git_commit_sha(target: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _has_dependency_manifest(project_path: Path) -> bool:
    return any((project_path / name).is_file() for name in DEPENDENCY_FILE_NAMES) or any(
        project_path.glob("requirements/*.txt")
    )


def _has_entrypoint(project_path: Path) -> bool:
    return any((project_path / name).is_file() for name in ENTRYPOINT_NAMES)


def _remove_existing_dockerfiles(project_path: Path) -> None:
    for name in DOCKERFILE_NAMES:
        path = project_path / name
        if path.is_file():
            path.unlink()


def _selection_reason(project_path: Path, candidate: dict[str, Any]) -> str:
    dependency_files = sorted(name for name in DEPENDENCY_FILE_NAMES if (project_path / name).is_file())
    entrypoints = sorted(name for name in ENTRYPOINT_NAMES if (project_path / name).is_file())
    return (
        f"small Python repo; size_kb={int(candidate.get('size') or 0)}; "
        f"dependencies={','.join(dependency_files) or 'requirements/'}; "
        f"entrypoints={','.join(entrypoints) or 'none'}"
    )



def _resolve_rejected_manifest_path(config: GitHubProjectFetchConfig) -> Path:
    if config.rejected_manifest_path is not None:
        return config.rejected_manifest_path
    manifest_name = config.manifest_path.stem + "_rejected" + config.manifest_path.suffix
    return config.manifest_path.with_name(manifest_name)



def _build_rejected_spec(
    candidate: dict[str, Any],
    *,
    stage: str,
    reason: str,
    detail: str = "",
) -> GitHubProjectRejectedSpec:
    if stage not in REJECTION_STAGES:
        msg = f"Unsupported rejection stage: {stage}"
        raise ValueError(msg)
    if reason not in REJECTION_REASONS:
        msg = f"Unsupported rejection reason: {reason}"
        raise ValueError(msg)
    return GitHubProjectRejectedSpec(
        project_name=str(candidate.get("name") or _repo_dir_name(candidate)),
        github_url=str(candidate.get("html_url") or ""),
        clone_url=str(candidate.get("clone_url") or ""),
        default_branch=str(candidate.get("default_branch") or "") or None,
        size_kb=int(candidate.get("size") or 0),
        stars=int(candidate.get("stargazers_count") or 0),
        rejection_stage=stage,
        rejection_reason=reason,
        rejection_detail=detail,
    )


def _write_manifest(path: Path, specs: Sequence[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([spec.model_dump(mode="json") for spec in specs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _resolve_rejection_summary_path(config: GitHubProjectFetchConfig) -> Path:
    if config.rejection_summary_path is not None:
        return config.rejection_summary_path
    summary_name = config.manifest_path.stem + "_rejection_summary.json"
    return config.manifest_path.with_name(summary_name)


def _build_rejection_summary(
    accepted_specs: Sequence[GitHubProjectSpec],
    rejected_specs: Sequence[GitHubProjectRejectedSpec],
    *,
    input_candidate_count: int,
    scanned_candidate_count: int,
) -> GitHubProjectRejectionSummary:
    by_stage = dict(sorted(Counter(spec.rejection_stage for spec in rejected_specs).items()))
    by_reason = dict(sorted(Counter(spec.rejection_reason for spec in rejected_specs).items()))
    return GitHubProjectRejectionSummary(
        accepted_count=len(accepted_specs),
        rejected_count=len(rejected_specs),
        input_candidate_count=input_candidate_count,
        scanned_candidate_count=scanned_candidate_count,
        by_stage=by_stage,
        by_reason=by_reason,
    )


def _write_rejection_summary(path: Path, summary: GitHubProjectRejectionSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_rmtree(target: Path, root: Path) -> None:
    resolved_target = target.resolve()
    resolved_root = root.resolve()
    if resolved_target == resolved_root or not resolved_target.is_relative_to(resolved_root):
        msg = f"Refusing to remove path outside destination: {target}"
        raise ValueError(msg)
    shutil.rmtree(resolved_target, onerror=_handle_readonly_remove)


def _handle_readonly_remove(function, path, exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        raise exc_info[1]


def _full_name_from_url(url: str) -> str:
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else "repo"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch small GitHub Python projects for repair experiments.")
    parser.add_argument("--query", default=GitHubProjectFetchConfig().query, help="GitHub repository search query.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum accepted repositories.")
    parser.add_argument("--destination", type=Path, default=EXPERIMENT_PROJECTS_DIR / "github")
    parser.add_argument("--manifest-path", type=Path, default=EXPERIMENT_MANIFESTS_DIR / "github_small_projects.json")
    parser.add_argument("--rejected-manifest-path", type=Path, help="Write rejected candidates and reasons to this manifest path.")
    parser.add_argument("--rejection-summary-path", type=Path, help="Write rejected stage/reason aggregates to this JSON path.")
    parser.add_argument("--max-repo-size-kb", type=int, default=2000)
    parser.add_argument("--no-require-entrypoint", action="store_true")
    parser.add_argument("--keep-dockerfiles", action="store_true")
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--clone-timeout-seconds", type=int, default=180)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--from-file", type=Path, help="Read repo URLs from a text file instead of GitHub Search API.")
    return parser


def main_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    specs = fetch_github_projects(
        GitHubProjectFetchConfig(
            query=args.query,
            limit=args.limit,
            destination=args.destination,
            manifest_path=args.manifest_path,
            rejected_manifest_path=args.rejected_manifest_path,
            rejection_summary_path=args.rejection_summary_path,
            max_repo_size_kb=args.max_repo_size_kb,
            require_entrypoint=not args.no_require_entrypoint,
            remove_existing_dockerfiles=not args.keep_dockerfiles,
            github_token_env=args.github_token_env,
            clone_timeout_seconds=args.clone_timeout_seconds,
            force=args.force,
            from_file=args.from_file,
        )
    )
    rejected_manifest_path = args.rejected_manifest_path or args.manifest_path.with_name(
        args.manifest_path.stem + "_rejected" + args.manifest_path.suffix
    )
    rejection_summary_path = args.rejection_summary_path or args.manifest_path.with_name(
        args.manifest_path.stem + "_rejection_summary.json"
    )
    rejected_count = "?"
    if rejection_summary_path.exists():
        try:
            rejected_count = str(json.loads(rejection_summary_path.read_text(encoding="utf-8")).get("rejected_count", "?"))
        except (OSError, json.JSONDecodeError):
            rejected_count = "?"
    console.print(
        f"Fetched {len(specs)} project(s), rejected {rejected_count}. "
        f"Accepted manifest: {args.manifest_path}; rejected manifest: {rejected_manifest_path}; "
        f"rejection summary: {rejection_summary_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
