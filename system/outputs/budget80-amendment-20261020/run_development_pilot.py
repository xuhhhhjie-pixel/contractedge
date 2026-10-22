"""Five development cases only, deterministically selected before model calls."""
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
    config = json.loads((ROOT / 'configs/deepseek-development-pilot.json').read_text())
    partition = benchmark / 'partition/member_groups.jsonl'
    groups = {row['case_id']: row for row in map(json.loads, partition.read_text().splitlines())}
    members = read_members(benchmark / 'public')
    selected = [next(row['case_id'] for row in members if row['primary_class'] == cls
                     and groups[row['case_id']]['candidate_split'] == 'development')
                for cls in ('R1', 'R2', 'R3', 'R4', 'R5')]
    assert len(set(selected)) == 5 and config['development_only']
    output.mkdir(parents=True)
    write(output / 'selection.json', {
        'rule': 'First development member of each R1-R5 class in frozen public manifest order',
        'selected_cases': selected, 'partition_sha256': file_sha(partition),
        'method_input': 'PUBLIC_ONLY', 'use_hidden_outcomes_for_selection': False,
        'scope': 'Five-case stability pilot; formal150 experiment is not authorized by this launcher'})
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
            case_ids=selected, max_new_cases=5, tools_root=benchmark / 'tools')
    finally:
        after = implementation()
        write(output / 'implementation-manifest.json', {'before': before, 'after': after, 'unchanged': before == after})
        write(output / 'money-accounting.json', {'before': money_before, 'after': provider.money_budget.summary()})
    print(json.dumps({'selected_cases': selected, 'records': [r for r in result['records']
        if r['case_id'] in selected], 'stop_reason': result.get('stop_reason'),
        'denominator': result['case_count'], 'money': provider.money_budget.summary()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
