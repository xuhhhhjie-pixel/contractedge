"""Probe records and the probe budget (provisional; to be replaced by the probe owner)."""
from __future__ import annotations

import subprocess
import time

from contractedge.graph_repair import state

KINDS = ('violate', 'preserve')


def path():
    return state.STATE / 'probes.json'


def load() -> list[dict]:
    return state.read_json(path(), []) or []


def save(probes: list[dict]) -> None:
    state.write_json(path(), probes)


def add(probes, *, edge, kind, cmd, local=False) -> dict:
    if kind not in KINDS:
        raise ValueError(f'kind must be one of {KINDS}')
    if not cmd.strip():
        raise ValueError('cmd is required')
    n = 1 + max((int(p['id'][1:]) for p in probes), default=0)
    value = {'id': f'P{n}', 'edge': edge, 'kind': kind, 'cmd': cmd, 'local': bool(local), 'status': 'NEW',
             'calibration': None, 'results': {}}
    probes.append(value)
    return value


def execute(probe: dict, timeout: int) -> dict:
    """Run one probe: through the published runtime (ce-run) unless it is a local static check."""
    if probe.get('local'):
        argv = ['bash', '-lc', probe['cmd']]
    else:
        argv = ['ce-run', '/bin/sh', '-c', probe['cmd']]
    began = time.monotonic()
    try:
        done = subprocess.run(argv, cwd=state.WORKSPACE, capture_output=True, timeout=timeout)
        code, output, timed_out = done.returncode, (done.stdout + done.stderr).decode(errors='replace'), False
    except subprocess.TimeoutExpired as exc:
        code, timed_out = 124, True
        output = ((exc.stdout or b'') + (exc.stderr or b'')).decode(errors='replace')
    return {'exit_code': code, 'timed_out': timed_out, 'seconds': round(time.monotonic() - began, 2),
            'tail': output[-1500:]}


# ------------------------------------------------------------------ budget
def budget() -> dict:
    """Probe share so far. Agent-written counters plus probe execution seconds."""
    counters = state.read_json(state.STATE / 'budget.json', {}) or {}
    seconds = sum(e.get('probe_seconds', 0) for e in _events())
    return {'probe_seconds': round(seconds, 1), 'n_calls': counters.get('n_calls', 0),
            'probe_calls': counters.get('probe_calls', 0), 'elapsed': counters.get('elapsed', 0)}


def _events():
    try:
        import json
        with (state.STATE / 'probe-events.jsonl').open() as stream:
            return [json.loads(line) for line in stream if line.strip()]
    except FileNotFoundError:
        return []


def log_seconds(probe_id, seconds, phase):
    state.append_jsonl(state.STATE / 'probe-events.jsonl', {'probe': probe_id, 'probe_seconds': seconds,
                                                            'phase': phase, 'at': time.time()})


def disabled() -> dict | None:
    return state.read_json(state.STATE / 'probes_disabled.json', None)


def check_budget(variant: dict) -> dict | None:
    """Disable probes for the rest of the run when they exceed the time or call share."""
    if disabled():
        return disabled()
    used = budget()
    wall = variant.get('wall_seconds', 1800)
    reason = None
    if used['probe_seconds'] > variant.get('probe_time_fraction', 0.2) * wall:
        reason = f"probe time {used['probe_seconds']}s exceeds {variant.get('probe_time_fraction', 0.2):.0%} of wall"
    elif used['probe_calls'] > variant.get('probe_call_fraction', 0.15) * max(used['n_calls'], 40):
        reason = f"probe-related calls {used['probe_calls']} exceed {variant.get('probe_call_fraction', 0.15):.0%} of calls"
    if reason:
        value = {'reason': reason, 'usage': used, 'fallback': 'public check passing is the stop condition'}
        state.write_json(state.STATE / 'probes_disabled.json', value)
        return value
    return None
