"""CLI wrapper for memory retrieval benchmark evaluation."""

from topomen.experiments.memory_retrieval_benchmark import (
    build_parser,
    evaluate_memory_retrieval,
    load_retrieval_cases,
    main_cli,
    render_memory_retrieval_markdown,
)

__all__ = [
    "build_parser",
    "evaluate_memory_retrieval",
    "load_retrieval_cases",
    "main_cli",
    "render_memory_retrieval_markdown",
]

if __name__ == "__main__":
    raise SystemExit(main_cli())
