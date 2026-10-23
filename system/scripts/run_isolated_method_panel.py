#!/usr/bin/env python3
"""Run 4x6 synthetic repairs in actual isolated sessions; never host fallback.

Requires an existing daemon, digest-pinned cached image containing /usr/bin/python3,
and the repository's ordinary Python runtime. Does not pull/build/change a daemon.
"""
import argparse
from pathlib import Path

from contractedge.development.isolated_panel import validate_runtime_config
from contractedge.development.method_panel import run_panel
from contractedge.development.method_panel_fixtures import CASES, MODES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--docker-host', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--docker-api-version')
    parser.add_argument('--case', choices=tuple(CASES))
    parser.add_argument('--mode', choices=MODES)
    args = parser.parse_args(argv)
    runtime = {'image': args.image, 'docker_host': args.docker_host}
    if args.docker_api_version is not None:
        runtime['docker_api_version'] = args.docker_api_version
    return run_panel(args.output, case_id=args.case, method_mode=args.mode,
        runtime_config=validate_runtime_config(runtime))


if __name__ == '__main__':
    raise SystemExit(main())
