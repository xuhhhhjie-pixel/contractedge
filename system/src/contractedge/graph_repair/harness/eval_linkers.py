"""Linker recall against the 12-case location gold (evaluation only; never a method input).

For each pair of distinct locatable gold units (u, v): seed the linkers with u's locations
(`path:line` for lines in its span, plus its symbol) and count v as reached when any candidate
edge touches one of v's files. Reports pairwise recall and "reached from some other unit".
Usage: python eval_linkers.py GOLD_DIR EXPERIMENT_ROOT OUT_JSON [CASE...]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from contractedge.graph_repair import linkers

MAX_SEED_LINES = 25


def source_root(root: Path, cid: str) -> Path | None:
    path = root / 'cases' / cid / 'active-case/baseline/workspace.json'
    return Path(json.loads(path.read_text())['source_root']) if path.exists() else None


def relative(src: Path, path: str) -> str | None:
    if (src / path).is_file():
        return path
    parts = path.split('/')
    return '/'.join(parts[1:]) if len(parts) > 1 and (src / '/'.join(parts[1:])).is_file() else None


def reached_files(src: Path, unit: dict) -> set[str]:
    files = set()
    for loc in unit['locations']:
        lines = range(loc['start_line'], min(loc['end_line'], loc['start_line'] + MAX_SEED_LINES) + 1)
        seeds = [f"{loc['path']}:{n}" for n in lines] + ([loc['symbol']] if loc.get('symbol') else [])
        for seed in seeds:
            for c in linkers.run(src, seed, limit=10 ** 6)['candidates']:
                files.update(p for p in (c['from']['path'], c['to']['path']) if p and not p.startswith('dependency:'))
    return files


def evaluate(gold: dict, src: Path) -> dict:
    units = []
    for unit in gold['units']:
        if not unit.get('locatable'):
            continue
        locs = []
        for loc in unit['acceptable_locations']:
            rel = relative(src, loc['path'])
            if rel:
                locs.append({**loc, 'path': rel})
        if locs:
            units.append({'id': unit['gt_unit'], 'locations': locs, 'files': {l['path'] for l in locs}})
    if len(units) < 2:
        return {'units': len(units), 'pairs': 0, 'note': 'fewer than two locatable units; no pairwise recall'}
    began = time.monotonic()
    reach = {u['id']: reached_files(src, u) for u in units}
    pairs = [(u, v) for u in units for v in units if u['id'] != v['id']]
    hits = [(u['id'], v['id']) for u, v in pairs if reach[u['id']] & v['files']]
    reached_any = {v for _, v in hits}
    return {'units': len(units), 'pairs': len(pairs), 'pair_hits': len(hits), 'pairwise_recall': round(len(hits) / len(pairs), 3),
            'units_reached_from_another': len(reached_any), 'unit_recall': round(len(reached_any) / len(units), 3),
            'hits': hits, 'candidate_files_per_unit': {k: len(v) for k, v in reach.items()},
            'seconds': round(time.monotonic() - began, 1)}


def main(gold_dir, root, out, *cases):
    gold_dir, root = Path(gold_dir), Path(root)
    rows = {}
    for path in sorted(gold_dir.glob('*.json')):
        gold = json.loads(path.read_text())
        cid = gold['case_id']
        if cases and cid not in cases:
            continue
        src = source_root(root, cid)
        rows[cid] = evaluate(gold, src) if src else {'note': 'case not prepared'}
        print(cid, {k: v for k, v in rows[cid].items() if k not in ('hits',)}, flush=True)
    scored = [r for r in rows.values() if r.get('pairs')]
    total = {'cases_with_pairs': len(scored),
             'pairwise_recall_micro': round(sum(r['pair_hits'] for r in scored) / max(1, sum(r['pairs'] for r in scored)), 3),
             'unit_recall_micro': round(sum(r['units_reached_from_another'] for r in scored) / max(1, sum(r['units'] for r in scored)), 3)}
    Path(out).write_text(json.dumps({'schema': 'contractedge.graph_repair.linker_recall.v1', 'total': total, 'cases': rows,
                                     'gold_used_for_evaluation_only': True}, indent=1))
    print(json.dumps(total))


if __name__ == '__main__':
    main(*sys.argv[1:])
