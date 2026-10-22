"""Apply the already declared final effect gates without changing frozen workers."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.assess_reliability_delivery import assess as assess_behavior


def assess(registration, records):
    result=assess_behavior(registration,records)
    primary=result['primary_counts']
    bcounts={v:sum(r['group']=='B' and r['variant']==v and r['status']=='PASS'
                   for r in records) for v in ('baseline','candidate')}
    improvement=primary['candidate']>primary['baseline']
    transfer=bcounts['candidate']>=bcounts['baseline']
    accepted=result['behavior_gates_passed'] and improvement and transfer
    return {**result,'status':'ACCEPTED' if accepted else 'PENDING' if result['missing'] else 'NOT_ACCEPTED',
        'delivery_effect_gates_passed':accepted,'paired_primary_improvement':improvement,
        'B_counts':bcounts,'B_not_below_baseline':transfer,
        'acceptance_policy':'Final design: all behavior gates, strictly more primary independent PASS, B not below baseline; recovery improvement alone is insufficient',
        'scope':result['scope']+' Final delivery additionally requires the declared primary and B effect gates.'}


def main():
    parser=argparse.ArgumentParser()
    for name in ('registration','records','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    result=assess(json.loads(Path(args.registration).read_text()),json.loads(Path(args.records).read_text()))
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
