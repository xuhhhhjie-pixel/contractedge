"""Sequential experiment control only: no model inputs or grading implementation.

Use immutable registered slots and common worker; wait for independent aggregate
before advancing past each candidate. A failed slot remains failed, never retried.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent

def read(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    temp=Path(path).with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)
def files(root):return {str(p.relative_to(root/'src')):sha(p) for p in sorted((root/'src').rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}


def public_failure(row,output):
    if not row.get('trace'):return False
    trace=read(Path(output)/row['trace']);feedback=trace.get('last_feedback') or {}
    return bool(feedback.get('to_snapshot')==trace.get('final_snapshot_id')
        and (feedback.get('semantic_outcome')=='PUBLIC_REVIEW_FAIL' or any(
            o.get('snapshot_id')==trace.get('final_snapshot_id')
            and o.get('evidence_kind')=='PUBLIC_BEHAVIOR'
            and o.get('behavior_check',{}).get('probe_sha256')
            and o['behavior_check'].get('status')=='FAIL'
            for o in feedback.get('public_observations',[]))))


def dispatch(freeze_path,group,*,worker_run=None,score_read=None,pause=time.sleep,score_timeout=7200):
    freeze=read(freeze_path);version=freeze['version'];registration=read(freeze['registration_path'])
    assert group in {'A','repeat','recovery'},'B remains closed pending earlier gates'
    path=OUT/f'delivery-{version}-{group}-state.json';initial=read(path);schedule=initial['schedule'];records=[]
    def state(status,**extra):write(path,dict(tag=f'delivery-{version}-{group}',status=status,freeze_sha256=sha(freeze_path),schedule=schedule,records=records,controller_sha256=sha(__file__),**extra))
    if worker_run is None:
        def worker_run(command,log):
            with log.open('w') as stream:return subprocess.run(command,cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'.topomen-deps')},stdout=stream,stderr=subprocess.STDOUT).returncode
    if score_read is None:
        def score_read():
            path=OUT/f'independent-v{version}/records.json';return read(path) if path.exists() else []
    for item in schedule:
        current_status=ROOT/'CURRENT_STATUS.json'
        if current_status.exists():
            try:current_version=read(current_status).get('implementation_version')
            except (ValueError,OSError):state('CURRENT_STATUS_REQUIRES_AUDIT',active=item);return
            if current_version!=version:state('SUPERSEDED_METHOD_HOLD',active=item);return
        for rel,digest in freeze['harness_files'].items():
            if sha(ROOT/rel)!=digest:state('FROZEN_HARNESS_MISMATCH',active=item);return
        variant=item['variant'];method=Path(freeze[variant+'_root']);config=Path(freeze['configuration_path'] if variant=='candidate' else freeze['baseline_configuration_path'])
        if files(method)!=(freeze['files'] if variant=='candidate' else freeze['baseline_source_files']) or sha(config)!=freeze['configuration_hashes'][variant]:state('FROZEN_IDENTITY_MISMATCH',active=item);return
        output=Path(item['output'])
        if not output.exists():
            cfg=read(freeze['configuration_path']);ledger=read(cfg['money_budget']['path']);spent=sum(c.get('charged_micro_cny',c['reserved_micro_cny']) for c in ledger['calls'])
            output_bound=cfg['max_model_calls']*max(cfg['max_output_tokens'],cfg['execution_max_output_tokens'],cfg['review_max_output_tokens'])
            pair_bound=2*(cfg['max_total_tokens']*2+min(cfg['max_total_tokens'],output_bound)*6)
            if ledger['settings']['limit_micro_cny']-spent<pair_bound:state('BUDGET_PAIR_ADMISSION_STOP',active=item);return
            command=[sys.executable,str(freeze['worker_path']),'--method-root',str(method),'--config',str(config),'--output',str(output),'--case',item['case_id']]
            if group=='recovery':command+=['--seed',str(ROOT/registration['recovery_seed_paths'][item['case_id']])]
            state('RUNNING',active=item)
            code=worker_run(command,OUT/(item['name']+'.log'))
            if code:state('WORKER_ERROR_REQUIRES_AUDIT',active=item,returncode=code);return
        batch=output/'batch.json'
        if not batch.exists():state('INCOMPLETE_RUN_REQUIRES_AUDIT',active=item);return
        row=next(r for r in read(batch)['records'] if r['case_id']==item['case_id'])
        if row['status'] in {'RUNNING','NOT_RUN'}:state('INCOMPLETE_RUN_REQUIRES_AUDIT',active=item);return
        records.append({**item,'method_status':row['status'],'batch_sha256':sha(batch)})
        state('WAITING_INDEPENDENT_SCORE',active=item)
        started=time.monotonic()
        while True:
            scored=[r for r in score_read() if (r['group'],r['case_id'],r['repeat'],r['variant'])==(group,item['case_id'],item['repeat'],variant)]
            if len(scored)>1:state('DUPLICATE_SCORE_REQUIRES_AUDIT',active=item);return
            if scored:break
            if time.monotonic()-started>=score_timeout:state('SCORING_INCOMPLETE_REQUIRES_AUDIT',active=item);return
            pause(5)
        records[-1]['independent_status']=scored[0]['status']
        if variant=='candidate' and public_failure(row,output):
            state('HELD_CANDIDATE_PUBLIC_FAILURE',active=item);return
        if scored[0]['status'] in {'SCORER_ERROR','SCORER_TIMEOUT','INVALID_SCORE','RUNNING','NOT_RUN'}:
            state('SCORING_INCOMPLETE_REQUIRES_AUDIT',active=item);return
        if variant=='candidate' and scored[0]['status']!='PASS':state('HELD_CANDIDATE_FAILURE',active=item);return
        state('RUNNING',active=None)
    state('COMPLETE',active=None)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',required=True);parser.add_argument('--group',choices=['A','repeat','recovery'],required=True);args=parser.parse_args();dispatch(Path(args.freeze),args.group)
