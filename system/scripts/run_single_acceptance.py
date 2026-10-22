"""Run only the RW-CAND-130 development acceptance, preserving method identity.

Usage: python3 scripts/run_single_acceptance.py NEW_OUTPUT_DIRECTORY
The API key is read from the existing private credential file, never printed.
"""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.topomen-deps'))
sys.path.insert(0, str(ROOT / 'src'))
from contractedge.cli import _build_provider
from contractedge.orchestration.repair import repair_case


def implementation():
    return {str(p.relative_to(ROOT / 'src')): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / 'src').rglob('*'))
            if p.is_file() and p.suffix in {'.py', '.json'}}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    output = Path(sys.argv[1]).resolve()
    if output.exists():
        raise ValueError('Use a new output directory; prior experiments must be preserved')
    config = json.loads((ROOT / 'configs/deepseek-single-optimized-v1-budget80.json').read_text())
    os.environ[config['api_key_env']] = Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider, tokenizer = _build_provider(config)
    before, money_before = implementation(), provider.money_budget.summary()
    benchmark = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    try:
        report = repair_case(benchmark / 'public', benchmark / 'tools', 'RW-CAND-130',
                             output, provider, tokenizer, config)
    finally:
        if output.is_dir():
            after = implementation()
            write(output / 'implementation-manifest.json', {'before': before, 'after': after, 'unchanged': before == after})
            write(output / 'experiment-config.json', config)
            write(output / 'money-accounting.json', {'before': money_before, 'after': provider.money_budget.summary()})
    print(json.dumps({key: report[key] for key in ('case_id', 'status', 'candidate', 'errors', 'budget')}))
    return 0 if report['status'] == 'PUBLIC_REVIEW_COMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
