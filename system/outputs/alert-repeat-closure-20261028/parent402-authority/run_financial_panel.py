"""Original46 only; stop before dispatch after a known necessary gate failure."""
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

S = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(S / 'scripts'))
from fixed_panel import verify_panel, verified_records, b_gate, sha, TERMINAL_OUTCOMES
from run_fixed_panel import run_slot, pair_budget, exclusive, write_json

R = Path('/home/xhj/contractedge-fixed46-official-v068-resumed-20261028')
P = R / 'panel.json'
KEY = Path('/home/xhj/.config/contractedge/deepseek-official-20261028.key')
stamp = lambda: datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def stop(*_):
    raise KeyboardInterrupt('Explicit stop; preserve slot and audit before resumption')


def necessary_failure(slot, row, rows):
    if slot['variant'] == 'candidate':
        if row['public_decision'] == 'FAIL':
            return 'Existing public FAIL rejection gate'
        if slot['group'] in {'A', 'repeat', 'recovery'} and row['status'] != 'PASS':
            return 'Existing required candidate PASS gate'
    same = [r for r in rows if (r['group'], r['case_id'], r['repeat']) == (slot['group'], slot['case_id'], slot['repeat'])]
    baseline = next((r for r in same if r['variant'] == 'baseline'), None)
    candidate = next((r for r in same if r['variant'] == 'candidate'), None)
    if baseline and candidate and baseline['status'] == 'PASS' and candidate['status'] != 'PASS':
        return 'Existing baseline PASS to candidate failure regression gate'
    return None


def main():
    panel = verify_panel(P)
    out = Path(panel['variants']['candidate']['root']) / 'outputs/alert-repeat-closure-20261028'
    qualification = Path('/home/xhj/contractedge-parent402-v068-20261028/qualification.json')
    report = json.loads(qualification.read_text())
    assert report['status'] == 'PASS' and report['parent_files'] == {name: sha(S / name) for name in report['parent_files']}
    assert report['method_source_sha256'] == panel['variants']['candidate']['source_sha256']
    assert panel['predecessor']['interpretation_policy'] == 'ce.explicit_http402_ready_prefix.v1'
    assert json.loads((out / 'V067_BEHAVIOR_PLAN_CONTINUATION_RESULT.json').read_text())['status'] == 'PASS_CONTROLLED_BEHAVIOR_FAILURE_PLAN_REPAIR'
    for name in ['V068_FULL_REGRESSION.json', 'V068_PACKAGE_VERIFICATION.json', 'V068_ACTUAL_ISOLATION.json', 'V068_KPT_PLAN_CORRECTION.json']:
        report = json.loads((out / name).read_text())
        assert report['source_sha256'] == panel['variants']['candidate']['source_sha256']
        assert report['status'] == ('PASS_PLAN_CORRECTION' if 'CORRECTION' in name else 'PASS')
    signal.signal(signal.SIGTERM, stop)
    with (R / 'phase-controller.lock').open('a') as phase_lock, (R / 'controller.lock').open('a') as lock:
        fcntl.flock(phase_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        exclusive(R / 'controller-start.json', dict(started_at=stamp(), owner_pid=os.getpid(),
            panel_sha256=sha(P), controller_sha256=sha(__file__), groups=['A', 'repeat', 'recovery', 'B'],
            original_thresholds_unchanged=True, stop_before_next_dispatch_on_known_necessary_failure=True,
            automatic_method_retry=False, formal150_started=False, formal_ready=False))
        completed = []
        try:
            for group in ['A', 'repeat', 'recovery', 'B']:
                rows = verified_records(P, panel)
                if group == 'B' and not b_gate(panel, rows)['open']:
                    raise ValueError('Original B prerequisites are not open')
                selected = [s for s in panel['slots'] if s['group'] == group]
                for slot in selected:
                    rows = verified_records(P, panel)
                    done = {r['slot_id'] for r in rows}
                    if slot['slot_id'] in done:
                        continue
                    pair = [s for s in selected if (s['case_id'], s['repeat']) == (slot['case_id'], slot['repeat']) and s['slot_id'] not in done]
                    budget = pair_budget(panel, pair)
                    if not budget['admitted']:
                        write_json(R / 'acceptance-state.json', dict(status='BUDGET_PAIR_ADMISSION_STOP', next_slot=slot,
                            budget=budget, closed_slots=len(rows), formal_ready=False))
                        return
                    state = dict(status='RUNNING', group=group, active_slot=slot, owner_pid=os.getpid(),
                        closed_slots=len(rows), completed_groups=completed, at=stamp(), formal_ready=False)
                    write_json(R / 'acceptance-state.json', state)
                    write_json(R / 'state.json', state)
                    row = run_slot(P, slot['slot_id'], KEY)
                    rows = verified_records(P, panel)
                    print(json.dumps({k: row[k] for k in ['case_id', 'group', 'variant', 'status', 'public_decision']}), flush=True)
                    reason = necessary_failure(slot, row, rows)
                    if row['status'] not in TERMINAL_OUTCOMES or reason:
                        state = dict(status='STOPPED_EFFECT_PREREQUISITE' if reason else 'SCORING_INFRASTRUCTURE_STOP',
                            reason=reason, last_slot=slot, closed_slots=len(rows), at=stamp(), formal_ready=False)
                        write_json(R / 'acceptance-state.json', state)
                        write_json(R / 'state.json', state)
                        return
                completed.append(group)
                if group != 'B':
                    subset = [s for s in panel['slots'] if s['group'] in completed]
                    ids = {s['slot_id'] for s in subset}
                    gate = b_gate({**panel, 'slots': subset}, [r for r in rows if r['slot_id'] in ids])
                    write_json(R / (group + '-gate.json'), dict(gate=gate, closed_slots=len(rows), at=stamp()))
                    if not gate['open']:
                        write_json(R / 'acceptance-state.json', dict(status='STOPPED_EFFECT_PREREQUISITE', group=group, gate=gate, formal_ready=False))
                        return
            state = dict(status='ORIGINAL46_CLOSED_PENDING_FINAL_EFFECT_AND_DELIVERY_ASSESSMENT',
                closed_slots=len(verified_records(P, panel)), completed_groups=completed, at=stamp(), formal_ready=False)
            write_json(R / 'acceptance-state.json', state)
            write_json(R / 'state.json', state)
        except BaseException as exc:
            write_json(R / 'acceptance-state.json', dict(status='AUDIT_REQUIRED', error_type=type(exc).__name__,
                completed_groups=completed, automatic_method_retry=False, formal_ready=False))
            raise


if __name__ == '__main__':
    main()
