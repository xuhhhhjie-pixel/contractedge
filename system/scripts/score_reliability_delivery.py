"""Independent process: finalized batches only; never read by method workers."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from contractedge.evaluation.repair_batch import evaluate_repair_batch

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/reliability-closure-20261020'
BENCH=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--version',required=True)
    parser.add_argument('--watch',action='store_true');args=parser.parse_args()
    prefix='delivery-'+args.version+'-';score_root=OUT/('independent-v'+args.version)
    score_root.mkdir(exist_ok=True)
    while True:
        records=[]
        for state_path in sorted(OUT.glob(prefix+'*-state.json')):
            state=json.loads(state_path.read_text());group=state_path.name[len(prefix):-len('-state.json')]
            for planned in state['schedule']:
                directory=Path(planned['output']);batch_path=directory/'batch.json'
                if not batch_path.exists():continue
                batch=json.loads(batch_path.read_text())
                item=next(r for r in batch['records'] if r['case_id']==planned['case_id'])
                if item['status']=='RUNNING':continue
                destination=score_root/planned['name'];result_path=destination/'cohort.json'
                if not destination.exists():
                    evaluate_repair_batch(BENCH/'evaluation',directory,BENCH/'tools',destination,timeout_seconds=7200)
                if not result_path.exists():raise ValueError('Incomplete independent output needs audit; not retrying')
                report=json.loads(result_path.read_text())
                if report['batch_sha256']!=hashlib.sha256(batch_path.read_bytes()).hexdigest():
                    raise ValueError('Batch changed after final scoring')
                selected=next((r for r in report['records'] if r['case_id']==planned['case_id']),None)
                if selected is None:raise ValueError('Incomplete independent output needs audit; not retrying')
                records.append(dict(group=group,case_id=planned['case_id'],repeat=planned['repeat'],variant=planned['variant'],
                    status=selected['status'],method_status=item['status'],output=str(directory),score=str(result_path),
                    patch_sha256=(item.get('candidate') or {}).get('patch_sha256')))
        target=score_root/'records.json';temporary=target.with_suffix('.tmp')
        temporary.write_text(json.dumps(records,indent=2)+'\n');temporary.replace(target)
        print(json.dumps({'scored_attempts':len(records),'version':args.version}),flush=True)
        if not args.watch:break
        states=[OUT/(prefix+group+'-state.json') for group in ['A','B','repeat','recovery']]
        if all(p.exists() and json.loads(p.read_text())['status']=='COMPLETE' for p in states) and len(records)==46:break
        time.sleep(20)


if __name__=='__main__':main()
