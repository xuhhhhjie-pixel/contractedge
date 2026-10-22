"""CLI wrapper for GitHub project fetching."""

from topomen.experiments.github_project_fetch import build_parser, fetch_github_projects, main_cli

__all__ = ["build_parser", "fetch_github_projects", "main_cli"]

if __name__ == "__main__":
    raise SystemExit(main_cli())
