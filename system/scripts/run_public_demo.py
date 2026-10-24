#!/usr/bin/env python3
"""Run published source-repair examples without a benchmark installation."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from contractedge.development.public_demo import main
if __name__ == '__main__':
    raise SystemExit(main())
