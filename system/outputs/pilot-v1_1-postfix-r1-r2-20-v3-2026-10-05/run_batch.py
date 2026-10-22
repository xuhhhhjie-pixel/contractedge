from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

PILOT = Path(__file__).resolve().parent
PUBLIC_ROOT = Path('/home/xhj/contractedge-system/outputs/pilot-v1_1-r1-r2-v2/public')
manifest = [json.loads(line) for line in (PILOT / 'pilot_manifest.jsonl').read_text().splitlines() if line.strip()]
traces = PILOT / 'traces'
configs = PILOT / 'case-configs'
attempts_path = PILOT / 'attempts.jsonl'
trace_manifest_path = PILOT / 'trace_manifest.jsonl'
state_path = PILOT / 'run_state.json'
completed = {json.loads(line)['case_id'] for line in trace_manifest_path.read_text().splitlines() if line.strip()} if trace_manifest_path.exists() else set()


def write_state(status: str, current: str | None = None) -> None:
    payload = {'status': status, 'attempted': len(completed), 'total': len(manifest), 'current_case': current}
    temporary = state_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n')
    temporary.replace(state_path)


write_state('RUNNING')
try:
    for row in manifest:
        case_id = row['case_id']
        if case_id in completed:
            continue
        trace_relative = f'traces/{case_id}.trace.json'
        trace_path = PILOT / trace_relative
        config_path = configs / f'{case_id}.json'
        write_state('RUNNING', case_id)
        started = time.monotonic()
        command = [sys.executable, '-m', 'contractedge.cli', 'run', '--public-root', str(PUBLIC_ROOT),
                   '--case-id', case_id, '--config', str(config_path), '--output', str(trace_path)]
        result = subprocess.run(command, text=True, capture_output=True,
                                env={'PATH': __import__('os').environ.get('PATH', ''),
                                     'PYTHONPATH': '/tmp/contractedge-pilot-deps:src'})
        elapsed = time.monotonic() - started
        (PILOT / f'{case_id}.stdout.txt').write_text(result.stdout)
        (PILOT / f'{case_id}.stderr.txt').write_text(result.stderr)
        record = {'case_id': case_id, 'return_code': result.returncode, 'elapsed_seconds': round(elapsed, 3),
                  'trace_written': trace_path.is_file(), 'stdout_path': f'{case_id}.stdout.txt',
                  'stderr_path': f'{case_id}.stderr.txt'}
        with attempts_path.open('a') as stream:
            stream.write(json.dumps(record, sort_keys=True) + '\n')
            stream.flush()
        if trace_path.is_file():
            with trace_manifest_path.open('a') as stream:
                stream.write(json.dumps({'case_id': case_id, 'trace': trace_relative}, sort_keys=True) + '\n')
                stream.flush()
            completed.add(case_id)
        print(json.dumps(record, sort_keys=True), flush=True)
        write_state('RUNNING' if len(completed) < len(manifest) else 'COMPLETE')
except KeyboardInterrupt:
    write_state('INTERRUPTED')
    raise
