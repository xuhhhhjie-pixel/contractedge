"""List what still needs a probe and suggest concrete probe shapes (render/static level first)."""
from __future__ import annotations

from contractedge.graph_repair.graph import store

HINTS = {
    'helm': "helm template /work/<chart> --set <key>=<value> | grep -q '<expected rendered text>'",
    'static': "grep -q '<expected code>' /testbed/<file>   (add --local)",
    'go': "cd /work && go test ./<pkg>/ -run '<TestName>' -count=1   (only when render/static is impossible)",
    'kustomize': "kustomize build /work/<overlay> | grep -q '<expected>'",
}


def pending(graph: dict, probes: list[dict]) -> list[dict]:
    covered_violate = {p['edge'] for p in probes if p['kind'] == 'violate' and p['status'] != 'DISCARDED'}
    covered_preserve = {p['edge'] for p in probes if p['kind'] == 'preserve' and p['status'] != 'DISCARDED'}
    items = []
    for edge in graph['edges']:
        if edge['status'] == 'VIOLATED' and edge['id'] not in covered_violate:
            source, target = store.node(graph, edge['from']), store.node(graph, edge['to'])
            items.append({'edge': edge['id'], 'kind': 'violate', 'from': f"{source['path']}:{source['span'][0]}",
                          'to': f"{target['path']}:{target['span'][0]}", 'note': edge.get('note', ''),
                          'expect': 'FAILS on the original version, PASSES after your fix'})
    for item in graph['preserve']:
        if item['edge'] not in covered_preserve:
            items.append({'edge': item['edge'], 'kind': 'preserve', 'scenario': item['scenario'],
                          'expect': 'PASSES on the original version and must keep passing'})
    return items


def report(graph: dict, probes: list[dict]) -> dict:
    return {'pending': pending(graph, probes), 'hints': HINTS,
            'add_with': "ce-probe add --edge E1 --kind violate --cmd '<shell>' [--local]",
            'then': 'ce-probe calibrate  (runs every new probe once on the original version)'}
