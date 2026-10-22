"""Delivery gates use every registered result, never the best attempt."""
import argparse
import json
from pathlib import Path


def assess(registration, records):
    expected=set()
    for group in ['A','B']:
        expected.update((group,r['case_id'],0,v) for r in registration[group] for v in ['baseline','candidate'])
    expected.update(('repeat',c,1,v) for c in registration['key_repeat_cases'] for v in ['baseline','candidate'])
    expected.update(('recovery',c,r,v) for c in registration['recovery_seed_paths'] for r in [0,1] for v in ['baseline','candidate'])
    table={}
    for row in records:
        key=(row['group'],row['case_id'],row['repeat'],row['variant'])
        if key in table:raise ValueError('Duplicate registered attempt, refusing best-result selection')
        if key not in expected:raise ValueError('Unregistered attempt')
        table[key]=row['status']
    missing=sorted(expected-table.keys())
    def passed(key):return table.get(key)=='PASS'
    primary=[(g,r['case_id']) for g in ['A','B'] for r in registration[g]]
    regressions=[dict(group=g,case_id=c) for g,c in primary
                 if passed((g,c,0,'baseline')) and not passed((g,c,0,'candidate'))]
    old_successes=['RW-CAND-130','R3-KPT-4673','R4-PROVIDER-HELM-408','R4-ACK-CLOUDWATCH-2985','RW-CERTMANAGER-9116']
    historical_preserved=all(passed(('A',c,0,'candidate')) for c in old_successes)
    critical_repeated=all(passed((g,c,r,'candidate')) for c in registration['key_repeat_cases']
                          for g,r in [('A',0),('repeat',1)])
    recovery_complete=all(passed(('recovery',c,r,'candidate')) for c in registration['recovery_seed_paths'] for r in [0,1])
    counts={v:sum(passed((g,c,0,v)) for g,c in primary) for v in ['baseline','candidate']}
    recoveries={v:sum(passed(('recovery',c,r,v)) for c in registration['recovery_seed_paths'] for r in [0,1])
                for v in ['baseline','candidate']}
    ready=not missing and not regressions and historical_preserved and critical_repeated and recovery_complete
    improvement=counts['candidate']>counts['baseline'] or recoveries['candidate']>recoveries['baseline']
    return dict(status='ACCEPTED' if ready else 'PENDING' if missing else 'NOT_ACCEPTED',
        observed_improvement=improvement,behavior_gates_passed=ready,missing=missing,regressions=regressions,
        historical_successes_preserved=historical_preserved,critical_cases_pass_both_attempts=critical_repeated,
        two_repository_recovery_pass_both_attempts=recovery_complete,primary_counts=counts,primary_denominator=len(primary),
        recovery_counts=recoveries,
        effect_claim='Observed paired improvement, not statistical significance' if improvement else 'No measured success-rate improvement; do not claim one',
        scope='Behavior acceptance requires separately verified engineering closure. Fixed development panel and finite repetition only; no statistical significance or full150 guarantee')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--registration',required=True)
    parser.add_argument('--records',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();result=assess(json.loads(Path(args.registration).read_text()),json.loads(Path(args.records).read_text()))
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
