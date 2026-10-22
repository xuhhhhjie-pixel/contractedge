"""CLI wrapper for cumulative repair experiments."""

from topomen.experiments.cumulative_repair_experiment import build_parser, main_cli, run_cumulative_batch

__all__ = ["build_parser", "main_cli", "run_cumulative_batch"]

if __name__ == "__main__":
    raise SystemExit(main_cli())
