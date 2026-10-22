"""Sequential prespecified paired runs; no scoring or adaptive sample replacement."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/reliability-closure-20261020'


def read(p):return json.loads(Path(p).read_text())


def write(p,value):
    p=Path(p);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(p)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--group',choices=['A','B','repeat','recovery'],required=True)
    args=ap.parse_args();registration=read(OUT/'experiment-registration.json')
    if args.group=='B' and not (OUT/'frozen-method.json').exists():raise ValueError('Method freeze required before B')
    runs=[]
    if args.group in ['A','B']:members=[r['case_id'] for r in registration[args.group]];repeats=[0]
    elif args.group=='repeat':members=registration['key_repeat_cases'];repeats=[1]
    else:members=list(registration['recovery_seed_paths']);repeats=[0,1]
    baseline=OUT/'baseline-v0.5.5';candidate=ROOT/'.superpowers/sdd/2026-10-20-reliability-closure/staged-rc2'
    configs={'baseline':ROOT/'configs/deepseek-development-final-v1-budget80.json','candidate':ROOT/'configs/deepseek-reliability-closure-v1-budget80.json'}
    if not (OUT/'frozen-method.json').exists():raise ValueError('Final source freeze required')
    for case in members:
        for repeat in repeats:
            order=['baseline','candidate'] if int(hashlib.sha256((args.group+case+str(repeat)).encode()).hexdigest(),16)%2==0 else ['candidate','baseline']
            # Joint preflight uses the tighter output-aware bound rather than
            # historical average. All calls still use the shared money guard.
            cfg=read(configs['candidate']);ledger=read(cfg['money_budget']['path'])
            spent=sum(c.get('charged_micro_cny',c['reserved_micro_cny']) for c in ledger['calls'])
            max_output=cfg['max_model_calls']*max(cfg['max_output_tokens'],cfg['execution_max_output_tokens'],cfg['review_max_output_tokens'])
            max_cost=cfg['max_total_tokens']*2+min(cfg['max_total_tokens'],max_output)*6
            if ledger['settings']['limit_micro_cny']-spent<2*max_cost:
                write(OUT/('panel-'+args.group+'-state.json'),{'status':'BUDGET_PAIR_ADMISSION_STOP','records':runs,'case':case,'repeat':repeat,'required_micro_cny':2*max_cost});return
            for variant in order:
                name=f'final-{args.group}-{case}-{repeat}-{variant}';output=OUT/'runs'/name
                if output.exists():
                    if not (output/'batch.json').exists() or any(r['status']=='RUNNING' for r in read(output/'batch.json')['records']):raise ValueError('Existing incomplete run requires audit, not automatic retry')
                else:
                    if args.group=='B':
                        registration['B_opened_for_method_execution']=True;write(OUT/'experiment-registration.json',registration)
                    command=[sys.executable,str(ROOT/'scripts/run_reliability_worker.py'),'--method-root',str(baseline if variant=='baseline' else candidate),
                        '--config',str(configs[variant]),'--output',str(output),'--case',case]
                    if args.group=='recovery':command.extend(['--seed',str(ROOT/registration['recovery_seed_paths'][case])])
                    with (OUT/(name+'.log')).open('w') as stream:
                        process=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'.topomen-deps')})
                    if process.returncode:raise RuntimeError('Worker failed: '+name+'; see saved log')
                state=read(output/'batch.json');record=next(r for r in state['records'] if r['case_id']==case)
                runs.append({'case_id':case,'group':args.group,'repeat':repeat,'variant':variant,'output':str(output),'status':record['status'],
                    'batch_sha256':hashlib.sha256((output/'batch.json').read_bytes()).hexdigest()})
                write(OUT/('panel-'+args.group+'-state.json'),{'status':'RUNNING','records':runs})
                print(json.dumps(runs[-1]),flush=True)
    write(OUT/('panel-'+args.group+'-state.json'),{'status':'COMPLETE','records':runs})


if __name__=='__main__':main()
