"""Zero-model public calibration; never reads independent grading material."""
import argparse
import json
import subprocess
from pathlib import Path

from contractedge.execution.public import BenchmarkPublicRunner


def main():
    root = Path.cwd()
    out = root / 'outputs/reliability-closure-20261020'
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-name', default='temporal-v3-calibration')
    args = parser.parse_args()
    dest = out / args.output_name
    dest.mkdir(exist_ok=False)
    method = json.loads((out / 'runs/delivery-0.6.15-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705/method_input.json').read_text())
    benchmark = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    def save(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    rows = []
    for mode in ('broken', 'saved_candidate'):
        current = dest / mode
        current.mkdir()
        runner = BenchmarkPublicRunner(benchmark/'public', benchmark/'tools',
            lock_root='/tmp/contractedge-system-native-locks', timeout_seconds=2400,
            query=method['requirement_text'], method_probe_profiles=['alertmanager-reload-v3'])
        save(dest/'state.json', {'status':'RUNNING', 'mode':mode, 'api_calls':0})
        prepared = runner.prepare(method['case_id'], current/'workspace', current/'prepare')
        if mode == 'saved_candidate':
            patch = out/'runs/delivery-0.6.15-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705/candidate.patch'
            subprocess.run(['git', 'apply', str(patch)], cwd=prepared['source_root'], check=True)
        observed = runner.observe(current/'workspace', current/'public')
        row = {'mode':mode, 'public_status':observed['status'],
               'method_probe_status':observed.get('method_probe_status'),
               'checks':[o['behavior_check'] for o in observed['observations'] if o.get('behavior_check')]}
        rows.append(row)
        save(current/'summary.json', row)
        print(json.dumps(row), flush=True)
    save(dest/'summary.json', {'scope':'PUBLIC_CALIBRATION_NOT_END_TO_END_NOT_PANEL', 'api_calls':0, 'rows':rows})
    save(dest/'state.json', {'status':'COMPLETE', 'api_calls':0})


if __name__ == '__main__':
    main()
