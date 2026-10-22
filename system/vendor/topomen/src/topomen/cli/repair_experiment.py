"""CLI wrapper for the repair experiment runner."""

from topomen.experiments.repair_experiment import build_parser, main_cli, run_batch

__all__ = ["build_parser", "main_cli", "run_batch"]

if __name__ == "__main__":
    raise SystemExit(main_cli())
