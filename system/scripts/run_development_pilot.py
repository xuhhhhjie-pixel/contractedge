"""Bounded development checks, selected before calls; never the formal150 run.

Usage: NEW_OUTPUT [PRESELECTED_JSON]. Default: first development case per class.
An optional selection document must contain a `cases` array of case_id objects.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.topomen-deps'))
sys.path.insert(0, str(ROOT / 'src'))
from contractedge.cli import _build_provider
from contractedge.dataset.benchmark import file_sha, read_members
from contractedge.orchestration.batch import run_batch
from contractedge.orchestration.repair import repair_case
from run_single_acceptance import implementation, write


def main():
    output = Path(sys.argv[1]).resolve()
    if output.exists():
        raise ValueError('Use a new pilot directory to preserve previous attempts')
    benchmark = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    config_path = Path(sys.argv[3]).resolve(strict=True) if len(sys.argv)>3 else ROOT / 'configs/deepseek-development-optimized-v1-budget80.json'
    config = json.loads(config_path.read_text())
    partition = benchmark / 'partition/member_groups.jsonl'
    groups = {row['case_id']: row for row in map(json.loads, partition.read_text().splitlines())}
    members = read_members(benchmark / 'public')
    selected = [next(row['case_id'] for row in members if row['primary_class'] == cls
                     and groups[row['case_id']]['candidate_split'] == 'development')
                for cls in ('R1', 'R2', 'R3', 'R4', 'R5')]
    rule = 'First development member of each R1-R5 class in frozen public manifest order'
    if len(sys.argv) > 2:
        selection = json.loads(Path(sys.argv[2]).read_text())
        selected = [row['case_id'] for row in selection['cases']]
        rule = selection['rule']
    if (not 1 <= len(selected) <= 5 or len(selected) != len(set(selected))
            or not config['development_only'] or any(c not in groups or groups[c]['candidate_split'] != 'development' for c in selected)):
        raise ValueError('This development launcher requires1–5 distinct preselected development members')
    output.mkdir(parents=True)
    write(output / 'selection.json', {
        'rule': rule,
        'selected_cases': selected, 'partition_sha256': file_sha(partition),
        'method_input': 'PUBLIC_ONLY', 'use_hidden_outcomes_for_selection': False,
        'scope': 'Bounded development validation; formal150 is not dispatched by this launcher'})
    write(output / 'experiment-config.json', config)
    before = implementation()
    for name in before:
        path = output / 'method-freeze/src' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / 'src' / name).read_bytes())
    os.environ[config['api_key_env']] = Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider, tokenizer = _build_provider(config)
    money_before = provider.money_budget.summary()
    try:
        result = run_batch(benchmark / 'public', output, config,
            lambda case, directory: repair_case(benchmark / 'public', benchmark / 'tools',
                case, directory, provider, tokenizer, config),
            case_ids=selected, max_new_cases=len(selected), tools_root=benchmark / 'tools')
    finally:
        after = implementation()
        write(output / 'implementation-manifest.json', {'before': before, 'after': after, 'unchanged': before == after})
        write(output / 'money-accounting.json', {'before': money_before, 'after': provider.money_budget.summary()})
    print(json.dumps({'selected_cases': selected, 'records': [r for r in result['records']
        if r['case_id'] in selected], 'stop_reason': result.get('stop_reason'),
        'denominator': result['case_count'], 'money': provider.money_budget.summary()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
