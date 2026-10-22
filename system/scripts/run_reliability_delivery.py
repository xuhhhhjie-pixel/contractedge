"""Run registered paired acceptance on one frozen source tree, without scoring input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/reliability-closure-20261020'


def read(path):
    return json.loads(Path(path).read_text())


def write(path,value):
    path=Path(path);temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--group',choices=['A','B','repeat','recovery'],required=True)
    parser.add_argument('--freeze',default=str(OUT/'frozen-method-v0.6.1.json'))
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();freeze=read(args.freeze)
    registration_path=Path(freeze['registration_path']);registration=read(registration_path)
    tag='delivery-'+freeze['version']+'-'+args.group
    state_path=OUT/(tag+'-state.json')
    configs={'baseline':Path(freeze.get('baseline_configuration_path',ROOT/'configs/deepseek-development-final-v1-budget80.json')),
             'candidate':Path(freeze['configuration_path'])}
    roots={'baseline':Path(freeze['baseline_root']),'candidate':Path(freeze['candidate_root'])}
    if args.group in ['A','B']:members=[row['case_id'] for row in registration[args.group]];repeats=[0]
    elif args.group=='repeat':members=registration['key_repeat_cases'];repeats=[1]
    else:members=list(registration['recovery_seed_paths']);repeats=[0,1]
    schedule=[]
    for case in members:
        for repeat in repeats:
            order=['baseline','candidate']
            if int(hashlib.sha256((args.group+case+str(repeat)).encode()).hexdigest(),16)%2:order.reverse()
            for variant in order:
                name=f'{tag}-{case}-{repeat}-{variant}'
                schedule.append(dict(case_id=case,repeat=repeat,variant=variant,name=name,
                    output=str(OUT/'runs'/name)))
    if args.dry_run:
        print(json.dumps(dict(tag=tag,schedule=schedule),indent=2));return
    records=[]
    def state(status,**extra):
        write(state_path,dict(status=status,freeze_sha256=hashlib.sha256(Path(args.freeze).read_bytes()).hexdigest(),
            schedule=schedule,records=records,**extra))
    for index,item in enumerate(schedule):
        variant=item['variant']
        for relative,digest in freeze.get('harness_files',{}).items():
            if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:
                state('FROZEN_HARNESS_MISMATCH',path=relative,active=item);return
        files={str(p.relative_to(roots[variant]/'src')):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((roots[variant]/'src').rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
        expected=freeze['files'] if variant=='candidate' else freeze['baseline_source_files']
        if files!=expected or hashlib.sha256(configs[variant].read_bytes()).hexdigest()!=freeze['configuration_hashes'][variant]:
            state('FROZEN_IDENTITY_MISMATCH',active=item);return
        output=Path(item['output'])
        if not output.exists():
            if index%2==0:
                cfg=read(configs['candidate']);ledger=read(cfg['money_budget']['path'])
                spent=sum(c.get('charged_micro_cny',c['reserved_micro_cny']) for c in ledger['calls'])
                output_bound=cfg['max_model_calls']*max(cfg['max_output_tokens'],cfg['execution_max_output_tokens'],cfg['review_max_output_tokens'])
                pair_bound=2*(cfg['max_total_tokens']*2+min(cfg['max_total_tokens'],output_bound)*6)
                if ledger['settings']['limit_micro_cny']-spent<pair_bound:
                    state('BUDGET_PAIR_ADMISSION_STOP',next_index=index,required_micro_cny=pair_bound);return
            if args.group=='B':
                registration['B_opened_for_method_execution']=True;write(registration_path,registration)
            command=[sys.executable,str(Path(freeze.get('worker_path',ROOT/'scripts/run_reliability_worker.py'))),'--method-root',str(roots[item['variant']]),
                '--config',str(configs[item['variant']]),'--output',str(output),'--case',item['case_id']]
            if args.group=='recovery':command+=['--seed',str(ROOT/registration['recovery_seed_paths'][item['case_id']])]
            state('RUNNING',active=item)
            with (OUT/(item['name']+'.log')).open('w') as stream:
                result=subprocess.run(command,cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'.topomen-deps')},
                    stdout=stream,stderr=subprocess.STDOUT)
            if result.returncode:
                state('WORKER_ERROR_REQUIRES_AUDIT',active=item,returncode=result.returncode);return
        if not (output/'batch.json').exists():
            state('INCOMPLETE_RUN_REQUIRES_AUDIT',active=item);return
        batch=read(output/'batch.json');record=next(r for r in batch['records'] if r['case_id']==item['case_id'])
        if record['status']=='RUNNING':
            state('INCOMPLETE_RUN_REQUIRES_AUDIT',active=item);return
        records.append({**item,'method_status':record['status'],
            'batch_sha256':hashlib.sha256((output/'batch.json').read_bytes()).hexdigest()})
        state('RUNNING');print(json.dumps(records[-1]),flush=True)
    state('COMPLETE')


if __name__=='__main__':main()
