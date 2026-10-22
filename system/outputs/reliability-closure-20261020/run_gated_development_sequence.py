"""Preserve registered attempts, automatically stop at a failed or incomplete gate."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

OUT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('guarded',OUT/'run_guarded_registered_group.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)

def pipeline(read_first,dispatch_group,read_group,record):
    first=read_first()
    if first!='PASS':record('HELD_FIRST_RECOVERY_NOT_PASS',first_recovery_status=first);return
    for group in ['A','repeat','recovery']:
        record('RUNNING',group=group)
        dispatch_group(group)
        state=read_group(group)
        if state.get('status')!='COMPLETE':record('HELD_GROUP_REQUIRES_AUDIT',group=group,group_status=state.get('status'));return
    record('A_REPEAT_RECOVERY_COMPLETE_B_CLOSED')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',required=True);args=parser.parse_args();freeze_path=Path(args.freeze);freeze=guard.read(freeze_path);version=freeze['version'];receipt=guard.read(OUT/f'GUARDED_CONTROL_REGISTRATION_v{version.replace(".","")}.json');assert receipt['controller_sha256']==guard.sha(OUT/'run_guarded_registered_group.py')
    target=OUT/f'development-sequence-v{version}-state.json'
    def record(status,**kwargs):
        guard.write(target,dict(status=status,version=version,freeze_sha256=guard.sha(freeze_path),controller_sha256=guard.sha(OUT/'run_guarded_registered_group.py'),sequence_sha256=guard.sha(__file__),updated_at_unix=time.time(),B_opened=False,formal_ready=False,**kwargs))
    def first():
        record('WAITING_REGISTERED_KPT_RECOVERY')
        started=time.monotonic()
        while time.monotonic()-started<18000:
            path=OUT/f'independent-v{version}/records.json';rows=guard.read(path) if path.exists() else []
            selected=[r for r in rows if (r['group'],r['case_id'],r['repeat'],r['variant'])==('recovery','R3-KPT-4673',0,'candidate')]
            if len(selected)>1:return 'DUPLICATE_REQUIRES_AUDIT'
            if selected:
                if selected[0]['status']=='PASS':
                    output=Path(selected[0]['output']);batch=guard.read(output/'batch.json')
                    method=next(r for r in batch['records'] if r['case_id']=='R3-KPT-4673')
                    if guard.public_failure(method,output):return 'PUBLIC_FAILURE_WITH_INDEPENDENT_PASS'
                return selected[0]['status']
            time.sleep(5)
        return 'INCOMPLETE'
    pipeline(first,lambda group:guard.dispatch(freeze_path,group),lambda group:guard.read(OUT/f'delivery-{version}-{group}-state.json'),record)

if __name__=='__main__':main()
