"""Freeze the graph-repair evaluation lists from the 2026-10-27 baseline table.

Categories follow BASELINE_DIFFERENTIAL_ANALYSIS.md section 8. The no-regress
set is drawn deterministically from Live successes; no new results are read.
Usage: python freeze_lists.py RESULTS_CSV FULL150_BATCH_PLAN HOLDOUT_JSON OUT_JSON
"""
import csv, hashlib, json, sys
from collections import defaultdict

SEED = 'contractedge-graph-repair-noregress-20261109'
PANEL = ['RW-CAND-130', 'R3-KOPF-726', 'R4-PROVIDER-HELM-408', 'R3-KPT-4746', 'R3-KPT-4673',
         'R4-ACK-CLOUDWATCH-2985', 'R4-CERTMANAGER-9204', 'RW-ALERTMANAGER-4705', 'RW-CAND-091',
         'RW-HELM-CONTROLLER-1411', 'R3-SKAFFOLD-9732', 'RW-PROMETHEUS-19838']
LIVE_WRONG = ['RW-CERTMANAGER-9128', 'RW-KEDA-7777', 'R4-PROVIDER-UPJET-AWS-2117', 'R4-FLINK-OPERATOR-1168',
              'RW-DAPR-9173', 'R4-KEDA-8227', 'RW-HELM-CONTROLLER-1411', 'R3-KUSTOMIZE-6237', 'RW-FLUENT-BIT-10720']
LIVE_UNSUBMITTED = ['R3-SKAFFOLD-9892', 'RW-HELM-CONTROLLER-1589', 'RW-TEMPO-6435', 'RW-ARGO-CD-27136',
                    'RW-KEDA-8125', 'R4-EXTERNAL-DNS-6661', 'RW-ARGO-CD-29827', 'RW-OPENCLAW-111511']
LIVE_POLICY = ['R3-HELM-31979', 'R3-KUBEBUILDER-5708', 'RW-DAPR-9612', 'RW-COREDNS-7562', 'RW-DAPR-10132',
               'R4-PROVIDER-UPJET-AWS-2182']
NOREGRESS_PER_CLASS = 4


def key(case_id):
    return hashlib.sha256(f'{SEED}:{case_id}'.encode()).hexdigest()


def main(results_csv, plan_path, holdout_path, out_path):
    rows = list(csv.DictReader(open(results_csv, encoding='utf-8-sig')))
    by = defaultdict(dict)
    for row in rows:
        by[row['case_id']][row['method']] = row
    plan = {c['case_id']: c for c in json.load(open(plan_path))['cases']}
    holdout = json.load(open(holdout_path))
    holdout_ids = [c['case_id'] for c in holdout['cases']]
    repo = {cid: c['repository'] for cid, c in plan.items()}
    # Check the hand-copied categories against the table instead of trusting them.
    live = {cid: m['live-swe-agent'] for cid, m in by.items()}
    for cid in LIVE_WRONG + LIVE_UNSUBMITTED + LIVE_POLICY:
        assert live[cid]['end_to_end_success'] != 'True', cid
    for cid in LIVE_UNSUBMITTED:
        assert live[cid]['functional_score'] == 'PASS', cid
    for cid in LIVE_POLICY:
        assert live[cid]['functional_score'] == 'INVALID_PATCH', cid
    nofix = sorted(c for c, m in by.items()
                   if all(v['end_to_end_success'] != 'True' and v['functional_score'] != 'PASS' for v in m.values()))
    covered = set(LIVE_WRONG + LIVE_UNSUBMITTED + LIVE_POLICY)
    nofix_extra = [c for c in nofix if c not in covered and c not in holdout_ids]
    excluded_repos = {repo[c] for c in PANEL if c in repo} | set(holdout['panel_repositories']) \
        | {c['repository'] for c in holdout['cases']}
    targeted = LIVE_WRONG + LIVE_UNSUBMITTED + LIVE_POLICY + nofix_extra
    taken_repos = set()
    noregress = []
    for cls in ['R1', 'R2', 'R3', 'R4', 'R5']:
        pool = sorted((c for c, v in live.items() if v['end_to_end_success'] == 'True' and v['primary_class'] == cls
                       and c in plan and c not in PANEL and c not in holdout_ids and c not in targeted
                       and repo[c] not in excluded_repos), key=key)
        picked = 0
        for cid in pool:
            if picked == NOREGRESS_PER_CLASS:
                break
            if repo[cid] in taken_repos:
                continue
            taken_repos.add(repo[cid]); noregress.append(cid); picked += 1
    evaluation = targeted + noregress
    assert len(set(evaluation)) == len(evaluation)
    assert not set(evaluation) & set(holdout_ids)
    value = {
        'schema_version': 'contractedge.graph_repair.evaluation_lists.v1',
        'frozen_before_any_graph_repair_run': True,
        'sources': {'results_csv_sha256': hashlib.sha256(open(results_csv, 'rb').read()).hexdigest(),
                    'batch_plan_sha256': hashlib.sha256(open(plan_path, 'rb').read()).hexdigest(),
                    'holdout_sha256': hashlib.sha256(open(holdout_path, 'rb').read()).hexdigest()},
        'noregress_rule': f'Live end-to-end successes in the full150 plan, excluding panel/holdout cases and their repositories '
                          f'and all targeted cases; {NOREGRESS_PER_CLASS} per primary class with distinct repositories, '
                          f'ascending SHA256("{SEED}:case_id"). No method or new result consulted.',
        'panel': PANEL,
        'live_wrong': LIVE_WRONG,
        'live_unsubmitted_correct': LIVE_UNSUBMITTED,
        'live_policy_rejected': LIVE_POLICY,
        'nofix_not_overlapping': nofix_extra,
        'nofix_all_17': nofix,
        'noregress': noregress,
        'holdout': holdout_ids,
        'evaluation_without_holdout': evaluation,
        'panel_overlap_with_evaluation': sorted(set(PANEL) & set(evaluation)),
        'not_in_full150_plan': sorted(c for c in evaluation + holdout_ids if c not in plan),
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(value, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in value.items() if k != 'sources'}, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:5])
