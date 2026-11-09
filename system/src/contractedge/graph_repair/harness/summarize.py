"""Per-run and per-group summary of a phase, read from frozen run evidence and hidden-score receipts.

Probe share: calls = model requests whose action runs ce-probe; tokens = those requests' tokens
(wire ledger, in request order); time = probe execution seconds over the run's wall time.
Usage: python summarize.py EXPERIMENT_ROOT PHASE OUT_JSON
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ACTION = re.compile(r'```bash\s*\n(.*?)\n```', re.S)


def lines(path: Path):
    try:
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def load(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def cells(root: Path, run) -> dict:
    score = load(root / 'scoring-private' / run['case_id'] / f"{run['tag']}-{run['method']}" / 'score/score.json', {})
    raw = score.get('cells') or []
    if isinstance(raw, str):
        return {'cells_text_only': True}
    rows = [{'name': c.get('name'), 'expected': c.get('expected'), 'outcome': c.get('outcome')} for c in raw]
    return {'cells': rows, 'cells_matching': sum(c['expected'] == c['outcome'] for c in rows), 'cells_total': len(rows)}


def run_metrics(root: Path, run: dict) -> dict:
    attempt = root / 'cases' / run['case_id'] / 'active-case' / f"attempts-{run['tag']}" / run['method']
    result = load(attempt / 'result.json', {})
    usage = [r for r in lines(attempt / 'wire/usage.jsonl') if r.get('phase') == 'request_finished']
    tokens = [r.get('usage', {}).get('total_tokens', 0) for r in usage]
    traj = load(attempt / 'trajectory.json', {}) or {}
    assistant = [m for m in traj.get('messages', []) if m.get('role') == 'assistant']
    actions = [(ACTION.findall(m.get('content', '')) or [''])[0] for m in assistant]
    probe_idx = [i for i, a in enumerate(actions) if 'ce-probe' in a]
    tool_idx = [i for i, a in enumerate(actions) if re.search(r'(?<![\w-])ce-(search|read|follow|links|graph|closure|probe|ckpt|rollback|submit)\b', a)]
    state = attempt / 'ce-state'
    probe_seconds = sum(e.get('probe_seconds', 0) for e in lines(state / 'probe-events.jsonl'))
    agent = lines(state / 'agent-events.jsonl')
    checks = [e for e in agent if e['event'] == 'submit_check']
    probes = load(state / 'probes.json', []) or []
    supervisor = load(attempt / 'SUPERVISOR_RESULT.json', {})
    elapsed = supervisor.get('elapsed_seconds') or 0
    blocked_by_probe = sum(1 for e in checks if any(u['kind'] in ('violate_probe', 'preserve_probe') for u in e.get('unmet', [])))
    return {
        'steps': len(assistant), 'tokens_total': sum(tokens),
        'probe_calls': len(probe_idx), 'probe_call_share': round(len(probe_idx) / max(1, len(assistant)), 3),
        'probe_token_share': round(sum(tokens[i] for i in probe_idx if i < len(tokens)) / max(1, sum(tokens)), 3),
        'probe_seconds': round(probe_seconds, 1), 'probe_time_share': round(probe_seconds / max(1.0, elapsed), 3),
        'ce_tool_calls': len(tool_idx), 'elapsed_seconds': round(elapsed, 1),
        'submit_checks': len(checks), 'submit_checks_passed': sum(1 for e in checks if e.get('passed')),
        'blocked_by_probe': blocked_by_probe,
        'forced_submission': (result.get('forced_submission') or {}).get('reason'),
        'checkpoints': result.get('checkpoints'),
        'probes_calibrated': sum(p['status'] in ('CALIBRATED', 'SUSPECT') for p in probes),
        'probes_discarded': sum(p['status'] == 'DISCARDED' for p in probes),
        'probes_suspect': sum(p['status'] == 'SUSPECT' for p in probes),
        'probes_disabled': load(state / 'probes_disabled.json', None),
        'graph_nodes': len((load(state / 'graph.json', {}) or {}).get('nodes', [])),
        'graph_edges': len((load(state / 'graph.json', {}) or {}).get('edges', [])),
        'attributions': sum(1 for e in agent if e['event'] == 'attribution'),
        'tool_errors': sum(1 for e in lines(state / 'events.jsonl') if e.get('exit_code') == 2),
    }


def main(root, phase, out):
    root = Path(root)
    runs = load(root / f'runs-{phase}.json')['runs']
    rows = []
    for run in runs:
        key = f"{run['tag']}-{run['method']}"
        result = load(root / 'results' / run['case_id'] / f'{key}.json')
        issue = next((load(p) for p in (root / 'issues').glob(f"*{run['case_id']}*.json")
                      if key in p.name or p.name == f"prepare-{run['case_id']}.json"), None) if (root / 'issues').exists() else None
        row = {**run, 'result': result, 'issue': issue}
        if result:
            row.update(metrics=run_metrics(root, run), score_cells=cells(root, run))
        rows.append(row)
    groups = defaultdict(list)
    for row in rows:
        groups[row['tag']].append(row)
    summary = {}
    for tag, items in groups.items():
        done = [r for r in items if r['result']]
        summary[tag] = {'runs': len(items), 'finished': len(done),
                        'submitted': sum(r['result']['execution_status'] == 'NATIVE_SUBMITTED' for r in done),
                        'end_to_end_success': sum(bool(r['result'].get('end_to_end_success')) for r in done),
                        'hidden_pass_any_status': sum(r['result'].get('score') == 'PASS' for r in done),
                        'cost_cny': round(sum(float(r['result']['cost_cny'] or 0) for r in done), 4),
                        'requests': sum(r['result']['requests'] for r in done),
                        'issues': [r['case_id'] for r in items if r['issue']]}
        m = [r['metrics'] for r in done if r.get('metrics')]
        if m and items[0]['method'] == 'contractedge-graph':
            summary[tag].update({
                'probe_call_share': round(sum(x['probe_calls'] for x in m) / max(1, sum(x['steps'] for x in m)), 3),
                'probe_token_share_mean': round(sum(x['probe_token_share'] for x in m) / len(m), 3),
                'probe_time_share_mean': round(sum(x['probe_time_share'] for x in m) / len(m), 3),
                'forced_submissions': sum(1 for x in m if x['forced_submission']),
                'runs_blocked_by_probe': sum(1 for x in m if x['blocked_by_probe']),
                'probes_calibrated': sum(x['probes_calibrated'] for x in m), 'probes_discarded': sum(x['probes_discarded'] for x in m),
                'tool_errors': sum(x['tool_errors'] for x in m)})
    Path(out).write_text(json.dumps({'phase': phase, 'summary': summary, 'runs': rows}, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:4])
