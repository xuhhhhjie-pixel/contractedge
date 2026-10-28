"""Read-only performance/cost accounting for this pinned original46 cohort.

Only the parent actual-dispatch ledger counts model calls. A replayed semantic
transcript is not a second paid request. Costs use the pinned conservative rates,
not a claim about actual provider invoices or account balance. No hidden answers
are read and no method or grader is dispatched.
"""
from collections import Counter,defaultdict
from decimal import Decimal,ROUND_CEILING
import argparse,json,hashlib,sys
from pathlib import Path
from datetime import datetime,timezone
SYSTEM=Path(__file__).resolve().parents[2]
PARENT=Path('/home/xhj/contractedge-parent402-v068-20261028/system')
sys.path[:0]=[str(PARENT/'scripts'),str(SYSTEM/'src')]
from fixed_panel import verify_panel,verified_records,read,sha


def monetary_bound(call,settings):
    usage=call.get('response_metadata',{}).get('usage')
    verified=call.get('response_metadata',{}).get('money_usage_identity_verified') is True
    if (verified and isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ['prompt_tokens','completion_tokens'])):
        prompt,output=usage['prompt_tokens'],usage['completion_tokens'];kind='SETTLED_USAGE_BOUND'
    else:
        # Exact reserve used by this frozen OpenAI-compatible transport.
        body=call['transport_request'];assert len(body['body_utf8'].encode())==body['body_bytes']
        assert hashlib.sha256(body['body_utf8'].encode()).hexdigest()==body['body_sha256']
        prompt,output=body['body_bytes']+1024,call['request']['max_output_tokens'];kind='RESERVATION_RETAINED'
    micro=(Decimal(prompt)*Decimal(str(settings['input_cny_per_million']))+Decimal(output)*Decimal(str(settings['output_cny_per_million']))).to_integral_value(rounding=ROUND_CEILING)
    return int(micro),kind


def collect(path):
    panel=verify_panel(path);rows=verified_records(path,panel);items=[];unique_responses=set()
    for row in rows:
        refs=row['evidence_files'];receipt=read(refs['receipt']['path']);trace=read(refs['trace']['path']);cfg=read(panel['variants'][row['variant']]['config'])
        admin='resource_calls' in refs
        if admin:
            ref=refs['resource_calls'];assert sha(ref['path'])==ref['sha256'];calls=read(ref['path'])
        else:calls=read(Path(refs['receipt']['path']).parent/'parent-model-calls.json')
        assert len(calls)==receipt['parent_budget']['model_calls']
        cost=0;retained=0
        for call in calls:
            amount,kind=monetary_bound(call,cfg['money_budget']);cost+=amount;retained+=kind=='RESERVATION_RETAINED'
            request_id=call.get('response_metadata',{}).get('request_id')
            if request_id:
                assert request_id not in unique_responses,'A replayed received answer must not be counted twice as an actual request'
                unique_responses.add(request_id)
        items.append(dict(slot_id=row['slot_id'],case_id=row['case_id'],group=row['group'],variant=row['variant'],repeat=row['repeat'],
            independent_status=row['status'],public_decision=row['public_decision'],method_status=row['method_status'],
            actual_dispatch_attempts=len(calls),received_model_responses=sum('response_content' in c for c in calls),
            failed_or_unsettled_reservations=retained,cost_upper_bound_micro_cny=cost,
            method_active_elapsed_seconds=receipt['parent_budget']['elapsed_seconds'],patch_bytes=(trace.get('candidate') or {}).get('patch_bytes',0),
            administrative_recovery=admin,uninterrupted_latency_comparable=not admin))
    pairs={}
    for item in items:
        if item['group'] in {'A','B'}:pairs.setdefault((item['group'],item['case_id']),{})[item['variant']]=item
    complete=[p for p in pairs.values() if set(p)=={'baseline','candidate'}]
    summary=dict(complete_pairs=len(complete),candidate_wins=sum(p['candidate']['independent_status']=='PASS' and p['baseline']['independent_status']!='PASS' for p in complete),
        candidate_losses=sum(p['baseline']['independent_status']=='PASS' and p['candidate']['independent_status']!='PASS' for p in complete))
    by_group={g:{v:dict(records=sum(i['group']==g and i['variant']==v for i in items),independent_pass=sum(i['group']==g and i['variant']==v and i['independent_status']=='PASS' for i in items)) for v in ['baseline','candidate']} for g in ['A','repeat','recovery','B']}
    return dict(schema_version='ce.acceptance_metrics.v1',checked_at=datetime.now(timezone.utc).isoformat(),panel_sha256=sha(path),source_sha256=panel['variants']['candidate']['source_sha256'],
        closed_slots=len(rows),registered_slots=len(panel['slots']),not_closed_slots=[s['slot_id'] for s in panel['slots'] if s['slot_id'] not in {r['slot_id'] for r in rows}],
        by_group=by_group,primary_complete_pair_comparison=summary,
        public_pass_independent_negative=[i['slot_id'] for i in items if i['public_decision']=='PASS' and i['independent_status']!='PASS'],
        independent_pass_public_unknown=[i['slot_id'] for i in items if i['independent_status']=='PASS' and i['public_decision']=='UNKNOWN'],
        actual_model_dispatch_attempts=sum(i['actual_dispatch_attempts'] for i in items),cost_upper_bound_micro_cny=sum(i['cost_upper_bound_micro_cny'] for i in items),
        closed_attempts=items,scope='Closed records only; partial active trials and legacy/qualification calls excluded. Original group denominators stay separate. Costs are pinned conservative bounds, not invoices. Active method durations exclude grading; recovered outage wall span is reported separately, not pooled with uninterrupted latency.',model_calls_added=0,grade_dispatches=0,formal_ready=False)


def main():
    p=argparse.ArgumentParser();p.add_argument('--panel',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=collect(args.panel)
    with args.output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:result[k] for k in ['closed_slots','actual_model_dispatch_attempts','cost_upper_bound_micro_cny','primary_complete_pair_comparison','formal_ready']}))

if __name__=='__main__':main()
