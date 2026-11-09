"""Acceptance: the all-off contractedge-graph run must match a concurrent Live run item by item.

Allowed differences: the method name, the extra read-only ce-tools mounts and the recorded
variant. Everything else in native-config.yaml and the container's HostConfig/Config must match.
Usage: python compare_config.py LIVE_ATTEMPT_DIR GRAPH_ATTEMPT_DIR
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

HOST_KEYS = ('Memory', 'NanoCpus', 'PidsLimit', 'NetworkMode', 'SecurityOpt', 'CapAdd', 'CapDrop', 'Privileged',
             'ReadonlyRootfs', 'ShmSize', 'Ulimits', 'Runtime', 'IpcMode', 'PidMode', 'UsernsMode')
CONFIG_KEYS = ('Image', 'Env', 'User', 'WorkingDir', 'Entrypoint', 'Cmd')


def mounts(inspect):
    return {(m['Destination'], m.get('RW')) for m in inspect['Mounts']}


def normalize(cfg: dict, slot: str) -> dict:
    """Per-run tokens (slot name, telemetry session) become placeholders; ce-tools mounts are dropped."""
    env = dict(cfg.get('environment') or {})
    args, out = list(env.get('run_args') or []), []
    i = 0
    while i < len(args):
        if args[i] == '-v' and i + 1 < len(args) and (':/ce-tools:' in args[i + 1] or
                                                       re.search(r':/usr/local/bin/ce-(?!run:)[a-z]+:', args[i + 1])):
            i += 2
            continue
        value = re.sub(r'cetel-[0-9a-f]{32}', 'cetel-SESSION', args[i].replace(slot, 'SLOT'))
        out.append(value)
        i += 1
    env['run_args'] = out
    return {**cfg, 'environment': env}


def slot_name(attempt: Path) -> str:
    """attempts-<tag>/<method> -> '<tag>-<method>', the token used in workspace and socket paths."""
    return attempt.parent.name.removeprefix('attempts-') + '-' + attempt.name


def compare(live_dir: Path, graph_dir: Path) -> dict:
    live_cfg = normalize(yaml.safe_load((live_dir / 'native-config.yaml').read_text()), slot_name(live_dir))
    graph_cfg = normalize(yaml.safe_load((graph_dir / 'native-config.yaml').read_text()), slot_name(graph_dir))
    variant = graph_cfg.pop('graph_variant', None)
    rows = []
    for section in sorted(set(live_cfg) | set(graph_cfg)):
        same = live_cfg.get(section) == graph_cfg.get(section)
        rows.append({'item': f'native-config.{section}', 'same': same})
    a, b = (json.loads((d / 'container-inspect.json').read_text()) for d in (live_dir, graph_dir))
    for key in HOST_KEYS:
        rows.append({'item': f'HostConfig.{key}', 'same': a['HostConfig'].get(key) == b['HostConfig'].get(key)})
    for key in CONFIG_KEYS:
        if key == 'Cmd':
            continue   # the mini-swe-agent container name/sleep is generated per run
        left, right = a['Config'].get(key), b['Config'].get(key)
        if key == 'Env':   # docker reports -e variables in set order
            left, right = sorted(left or []), sorted(right or [])
        rows.append({'item': f'Config.{key}', 'same': left == right})
    extra, missing = mounts(b) - mounts(a), mounts(a) - mounts(b)
    allowed = all(dest == '/ce-tools' or dest.startswith('/usr/local/bin/ce-') and dest != '/usr/local/bin/ce-run'
                  for dest, _ in extra) and all(rw is False for _, rw in extra)
    rows.append({'item': 'Mounts: Live mounts all present in graph run', 'same': not missing})
    rows.append({'item': 'Mounts: extra mounts are only read-only ce-tools', 'same': allowed, 'extra': sorted(d for d, _ in extra)})
    differing = [r['item'] for r in rows if not r['same']]
    return {'live': str(live_dir), 'graph': str(graph_dir), 'graph_variant': variant, 'rows': rows,
            'differing': differing, 'pass': not differing and bool(variant) and not any(
                variant.get(k) for k in ('graph', 'linkers', 'closure', 'probes', 'checkpoint', 'attribution', 'stop_rule'))
            and variant.get('prompt') == 'live'}


if __name__ == '__main__':
    result = compare(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps(result, indent=1))
    raise SystemExit(0 if result['pass'] else 1)
