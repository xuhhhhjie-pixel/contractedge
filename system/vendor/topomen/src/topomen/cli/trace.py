"""CLI wrapper for topology trace analysis."""

from topomen.experiments.trace_analysis import (
    analyze_trace_files,
    build_parser,
    derive_trace_report_path,
    derive_trace_summary_path,
    main_cli,
)

__all__ = [
    "analyze_trace_files",
    "build_parser",
    "derive_trace_report_path",
    "derive_trace_summary_path",
    "main_cli",
]

if __name__ == "__main__":
    raise SystemExit(main_cli())
