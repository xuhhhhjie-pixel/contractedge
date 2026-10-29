"""Continue only still-unclaimed A slots; a 402-stopped slot remains unresolved.

No retry, slot replacement, score inheritance, or later-group admission.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

SYSTEM=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(SYSTEM/'src'),str(SYSTEM/'scripts')]
from fixed_panel import verify_panel,verified_records,sha
from run_fixed_panel import run_slot,pair_budget,exclusive,write_json
from verify_development_snapshot import verify_snapshot

ROOT=Path('/home/xhj/contractedge-fixed46-official-v068-20261028')
PANEL=ROOT/'panel.json'
STOPPED='fixed46-official-v068-20261028-bcd9a8d56a4e00f2f902f406'
KEY=Path('/home/xhj/.config/contractedge/deepseek-official-20261028.key')
STAMP=lambda:datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()

def failure(slot,row,rows):
    if slot['variant']=='candidate' and (row['status']!='PASS' or row['public_decision']=='FAIL'):
        return 'Original necessary candidate acceptance condition failed'
    pair=[r for r in rows if (r['case_id'],r['repeat'],r['group'])==(slot['case_id'],slot['repeat'],slot['group'])]
    base=next((r for r in pair if r['variant']=='baseline'),None)
    cand=next((r for r in pair if r['variant']=='candidate'),None)
    if base and cand and base['status']=='PASS' and cand['status']!='PASS':
        return 'Original baseline PASS to candidate failure regression gate'

def stop(*_): raise KeyboardInterrupt('Retain current attempt; no automatic retry')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');a=ap.parse_args()
    p=verify_panel(PANEL);rows=verified_records(PANEL,p)
    freeze=json.loads((SYSTEM/'docs/development/quote-binding-v068-20261028.snapshot.json').read_text())
    assert verify_snapshot(SYSTEM,freeze,require_current_bindings=True)['status']=='PASS'
    audit=SYSTEM/'outputs/alert-repeat-closure-20261028/V068_ACK_PROVIDER_STOP_AUDIT.json'
    saved=json.loads(audit.read_text());assert saved['stopped_slot']==STOPPED and saved['panel_sha256']==sha(PANEL)
    assert all(sha(name)==digest for name,digest in saved['proof'].items())
    done={r['slot_id'] for r in rows}
    pending=[s for s in p['slots'] if s['group']=='A' and s['slot_id'] not in done and s['slot_id']!=STOPPED]
    proof=dict(status='PREFLIGHT_PASS',checked_at=STAMP(),panel_sha256=sha(PANEL),source_sha256=freeze['source_sha256'],
        closed_slots=len(rows),unresolved_slot=STOPPED,unclaimed_A_slots=[s['slot_id'] for s in pending],
        driver_sha256=sha(__file__),new_model_calls=0,all_later_groups_closed=True,
        scope='Independent remaining A pairs only while exact 402 continuation is audited. Original slot membership, per-pair variant order, thresholds, configuration and old artifacts unchanged.',formal_ready=False)
    if not a.execute: print(json.dumps(proof));return
    signal.signal(signal.SIGTERM,stop)
    with (ROOT/'phase-controller.lock').open('a') as ph,(ROOT/'controller.lock').open('a') as co:
        fcntl.flock(ph,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(co,fcntl.LOCK_EX|fcntl.LOCK_NB)
        exclusive(ROOT/'available-A-controller-start.json',{**proof,'owner_pid':os.getpid(),'execute':True})
        try:
            for slot in pending:
                rows=verified_records(PANEL,p);done={r['slot_id'] for r in rows}
                pair=[s for s in pending if s['case_id']==slot['case_id'] and s['slot_id'] not in done]
                budget=pair_budget(p,pair)
                if not budget['admitted']:
                    write_json(ROOT/'acceptance-state.json',dict(status='BUDGET_PAIR_ADMISSION_STOP',budget=budget,closed_slots=len(rows),formal_ready=False));return
                state=dict(status='RUNNING_REMAINING_A_WITH_ACK_UNRESOLVED',active_slot=slot,
                    unresolved_slot=STOPPED,closed_slots=len(rows),owner_pid=os.getpid(),at=STAMP(),formal_ready=False)
                for n in ['acceptance-state.json','state.json']:write_json(ROOT/n,state)
                row=run_slot(PANEL,slot['slot_id'],KEY);rows=verified_records(PANEL,p)
                print(json.dumps({k:row[k] for k in ['case_id','group','variant','status','public_decision']}),flush=True)
                reason=failure(slot,row,rows)
                if reason:
                    state=dict(status='STOPPED_EFFECT_PREREQUISITE',reason=reason,last_slot=slot,closed_slots=len(rows),unresolved_slot=STOPPED,at=STAMP(),formal_ready=False)
                    for n in ['acceptance-state.json','state.json']:write_json(ROOT/n,state)
                    return
            state=dict(status='A_OTHER_SLOTS_CLOSED_ACK_INFRASTRUCTURE_PENDING',closed_slots=len(verified_records(PANEL,p)),unresolved_slot=STOPPED,at=STAMP(),all_later_groups_closed=True,formal_ready=False)
            for n in ['acceptance-state.json','state.json']:write_json(ROOT/n,state)
        except BaseException as e:
            write_json(ROOT/'acceptance-state.json',dict(status='AUDIT_REQUIRED',error_type=type(e).__name__,unresolved_slot=STOPPED,at=STAMP(),automatic_retry=False,formal_ready=False))
            raise

if __name__=='__main__':main()
