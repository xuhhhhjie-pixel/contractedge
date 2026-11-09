"""Calibrate probes on the original version: violate probes must fail, preserve probes must pass.

When the work tree has edits they are saved as a commit, the original tree is written out, the
probes run, and the edits are restored. A marker lets the agent finish the restore if this
command is killed half way.
"""
from __future__ import annotations

from contractedge.graph_repair import checkpoint, state
from contractedge.graph_repair.probes import spec

MARKER = 'calibration-in-progress.json'


def _save_current() -> str:
    tree = checkpoint.current_tree()
    commit = checkpoint._git('commit-tree', tree, '-p', 'HEAD', '-m', 'ce-calibration-save',
                             check=True).stdout.decode().strip()
    checkpoint._git('update-ref', 'refs/ce/calibration-save', commit)
    return commit


def resume() -> dict | None:
    marker = state.read_json(state.STATE / MARKER, None)
    if not marker:
        return None
    result = checkpoint.restore(marker['restore'])
    (state.STATE / MARKER).unlink(missing_ok=True)
    return {'resumed_restore': marker['restore'], **result}


def calibrate(variant: dict) -> dict:
    probes = spec.load()
    todo = [p for p in probes if p['status'] == 'NEW']
    if not todo:
        return {'calibrated': [], 'discarded': [], 'note': 'no new probes'}
    baseline = checkpoint.baseline_commit()
    original_tree = checkpoint._git('rev-parse', baseline + '^{tree}').stdout.decode().strip()
    current = checkpoint.current_tree()
    saved = None
    if current != original_tree:
        saved = _save_current()
        state.write_json(state.STATE / MARKER, {'restore': saved})
        checkpoint.restore(baseline)
    done, dropped = [], []
    try:
        for probe in todo:
            outcome = spec.execute(probe, variant.get('probe_timeout_seconds', 300))
            spec.log_seconds(probe['id'], outcome['seconds'], 'calibrate')
            ok = (outcome['exit_code'] != 0 and not outcome['timed_out']) if probe['kind'] == 'violate' \
                else outcome['exit_code'] == 0
            probe['calibration'] = {**outcome, 'tree': original_tree}
            probe['status'] = 'CALIBRATED' if ok else 'DISCARDED'
            if ok:
                done.append(probe['id'])
            else:
                why = ('passes on the original version, so it cannot show the violation' if probe['kind'] == 'violate'
                       else 'fails on the original version, so it is not a preserved behaviour')
                if outcome['timed_out']:
                    why = 'timed out on the original version'
                probe['calibration']['discard_reason'] = why
                dropped.append({'id': probe['id'], 'reason': why, 'tail': outcome['tail'][-400:]})
    finally:
        spec.save(probes)
        if saved:
            checkpoint.restore(saved)
            (state.STATE / MARKER).unlink(missing_ok=True)
    return {'calibrated': done, 'discarded': dropped, 'original_tree': original_tree,
            'work_tree_restored': bool(saved)}
