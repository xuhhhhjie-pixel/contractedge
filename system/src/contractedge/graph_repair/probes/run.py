"""Run calibrated probes on the current work tree; results are cached per tree."""
from __future__ import annotations

from contractedge.graph_repair import checkpoint
from contractedge.graph_repair.probes import spec

SUSPECT_AFTER = 3


def run(variant: dict, only: str | None = None, tree: str | None = None) -> dict:
    probes = spec.load()
    tree = tree or checkpoint.current_tree()
    cached = checkpoint.results().get(tree, {}).get('probes', {})
    rows = {}
    for probe in probes:
        if probe['status'] not in ('CALIBRATED', 'SUSPECT') or (only and probe['id'] != only):
            continue
        if probe['id'] in cached:
            rows[probe['id']] = {**cached[probe['id']], 'cached': True}
            continue
        outcome = spec.execute(probe, variant.get('probe_timeout_seconds', 300))
        spec.log_seconds(probe['id'], outcome['seconds'], 'run')
        probe['results'][tree] = {'exit_code': outcome['exit_code'], 'seconds': outcome['seconds']}
        rows[probe['id']] = {'kind': probe['kind'], 'edge': probe['edge'], 'exit_code': outcome['exit_code'],
                             'seconds': outcome['seconds'], 'tail': outcome['tail'][-600:]}
    public = checkpoint.results().get(tree, {}).get('public', {}).get('exit_code')
    for probe in probes:
        failures = [r for r in probe['results'].values() if r['exit_code'] != 0]
        if probe['kind'] == 'violate' and probe['status'] == 'CALIBRATED' and public == 0 \
                and len(failures) >= SUSPECT_AFTER and len(failures) == len(probe['results']):
            probe['status'] = 'SUSPECT'   # recorded, never blocks submission
    spec.save(probes)
    merged = {**cached, **{k: {f: v[f] for f in ('kind', 'edge', 'exit_code', 'seconds')} for k, v in rows.items()
                           if not v.get('cached')}}
    checkpoint.record_result(tree, 'probes', merged)
    return {'tree': tree, 'probes': rows,
            'summary': {'violate_passing': sum(1 for v in merged.values() if v['kind'] == 'violate' and v['exit_code'] == 0),
                        'violate_total': sum(1 for v in merged.values() if v['kind'] == 'violate'),
                        'preserve_failing': sum(1 for v in merged.values() if v['kind'] == 'preserve' and v['exit_code'] != 0)}}
