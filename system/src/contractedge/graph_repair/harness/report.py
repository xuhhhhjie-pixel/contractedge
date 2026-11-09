"""Group comparison for the controlled experiment (M5 / holdout) from frozen results.

Answers: does A (full) beat B (concurrent Live) by more than run-to-run variation; on the F
subset, how much of A-B comes from the generic submit mechanism (F-B) versus the contract graph
(A-F); what C/D/E change relative to A. Outputs JSON plus Markdown tables; per-case rows keep
the evidence needed to explain every failure and regression.
Usage: python report.py EXPERIMENT_ROOT PHASE OUT_PREFIX
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

LISTS = ['live_wrong', 'live_unsubmitted_correct', 'live_policy_rejected', 'nofix_not_overlapping', 'noregress']
NAMES = {'live_wrong': 'Live 改错', 'live_unsubmitted_correct': 'Live 修对未提交', 'live_policy_rejected': 'Live 违反提交规则',
         'nofix_not_overlapping': '无人修对（不重叠）', 'noregress': '不退步集', 'holdout': '留出集'}


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def lines(path):
    try:
        return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def evidence(root: Path, run: dict) -> dict:
    key = f"{run['tag']}-{run['method']}"
    result = load(root / 'results' / run['case_id'] / f'{key}.json')
    row = {'case_id': run['case_id'], 'group': run['group'], 'rep': run['rep'], 'tag': run['tag'], 'finished': bool(result)}
    issue = None
    for name in (f"generate-{run['case_id']}-{key}", f"score-{run['case_id']}-{key}", f"prepare-{run['case_id']}"):
        issue = issue or load(root / 'issues' / f'{name}.json')
    row['issue'] = issue and issue['error']
    if not result:
        return row
    attempt = root / 'cases' / run['case_id'] / 'active-case' / f"attempts-{run['tag']}" / run['method']
    native = load(attempt / 'result.json', {})
    agent = lines(attempt / 'ce-state/agent-events.jsonl')
    checks = [e for e in agent if e['event'] == 'submit_check']
    score = load(root / 'scoring-private' / run['case_id'] / key / 'score/score.json', {})
    cells = score.get('cells') if isinstance(score.get('cells'), list) else []
    row.update(status=result['execution_status'], score=result.get('score'), e2e=bool(result.get('end_to_end_success')),
               reason=result.get('score_reason'), requests=result['requests'], tokens=result['known_total_tokens'],
               cost=float(result['cost_cny'] or 0), forced=(native.get('forced_submission') or {}).get('reason'),
               checks=len(checks), last_unmet=[f"{u['kind']}:{u['id']}" for u in (checks[-1]['unmet'] if checks else [])][:6],
               auto_notes=sum(1 for e in agent if e['event'] == 'auto_graph'),
               cells_failed=[c.get('name') for c in cells if c.get('expected') != c.get('outcome')],
               elapsed=(load(attempt / 'SUPERVISOR_RESULT.json', {}) or {}).get('elapsed_seconds'))
    return row


def rate(rows, field='e2e'):
    done = [r for r in rows if r.get('finished')]
    return (sum(1 for r in done if r.get(field)) , len(done))


def fmt(pair):
    k, n = pair
    return f'{k}/{n} ({k / n:.0%})' if n else '—'


def main(root, phase, prefix):
    root = Path(root)
    lists = load(root / 'freeze/EVALUATION_LISTS.json')
    runs = load(root / f'runs-{phase}.json')['runs']
    rows = [evidence(root, r) for r in runs]
    by = defaultdict(list)
    for r in rows:
        by[(r['group'], r['rep'])].append(r)
    cat = {c: name for name in LISTS + ['holdout'] for c in lists.get(name, [])}
    out = {'phase': phase, 'groups': {}, 'paired': {}, 'categories': {}, 'rows': rows}
    for (g, rep), items in sorted(by.items()):
        done = [r for r in items if r.get('finished')]
        out['groups'][f'{g}{rep}'] = {'cases': len(items), 'finished': len(done), 'e2e': rate(items), 'hidden_pass': rate(items, 'score_pass'),
                                      'cost_cny': round(sum(r['cost'] for r in done), 3),
                                      'requests_mean': round(sum(r['requests'] for r in done) / max(1, len(done)), 1),
                                      'forced': sum(1 for r in done if r.get('forced'))}
        for r in items:
            r['score_pass'] = r.get('score') == 'PASS'
        out['groups'][f'{g}{rep}']['hidden_pass'] = rate(items, 'score_pass')

    def cases_of(group):
        return sorted({r['case_id'] for r in rows if r['group'] == group})

    def success(group, cid):
        vals = [r['e2e'] for r in rows if r['group'] == group and r['case_id'] == cid and r.get('finished')]
        return sum(vals) / len(vals) if vals else None

    for g in sorted({r['group'] for r in rows} - {'B'}):
        common = [c for c in cases_of(g) if success(g, c) is not None and success('B', c) is not None
                  and (g == 'A' or success('A', c) is not None)]
        if not common:
            continue
        mean = lambda grp: round(sum(success(grp, c) for c in common) / len(common), 3)
        out['paired'][g] = {'cases': len(common), f'{g}_rate': mean(g), 'B_rate': mean('B'), 'A_rate': mean('A'),
                            f'{g}_minus_B': round(mean(g) - mean('B'), 3), f'A_minus_{g}': round(mean('A') - mean(g), 3),
                            'wins_vs_B': [c for c in common if success(g, c) > success('B', c)],
                            'losses_vs_B': [c for c in common if success(g, c) < success('B', c)]}
    # Run-to-run variation inside A and inside B (same cases, two repetitions).
    for g in ('A', 'B'):
        r1, r2 = rate(by.get((g, 1), [])), rate(by.get((g, 2), []))
        if r1[1] and r2[1]:
            out['paired'].setdefault('variation', {})[g] = {'rep1': r1, 'rep2': r2, 'abs_diff_rate': round(abs(r1[0] / r1[1] - r2[0] / r2[1]), 3),
                                                            'flipped_cases': sorted({r['case_id'] for r in by[(g, 1)] if r.get('finished')
                                                                                     for s in by[(g, 2)] if s['case_id'] == r['case_id'] and s.get('finished') and s['e2e'] != r['e2e']})}
    for name in LISTS + ['holdout']:
        members = set(lists.get(name, []))
        out['categories'][name] = {f'{g}{rep}': fmt(rate([r for r in items if r['case_id'] in members]))
                                   for (g, rep), items in sorted(by.items()) if any(r['case_id'] in members for r in items)}
    Path(prefix + '.json').write_text(json.dumps(out, indent=1, ensure_ascii=False))
    md = [f'# {phase} 结果表', '', '| 组 | 完成 | 端到端成功 | 隐藏评分通过（不论是否提交） | 费用（元） | 平均调用 | 强制提交 |', '|---|---|---|---|---|---|---|']
    for k, v in out['groups'].items():
        md.append(f"| {k} | {v['finished']}/{v['cases']} | {fmt(v['e2e'])} | {fmt(v['hidden_pass'])} | {v['cost_cny']} | {v['requests_mean']} | {v['forced']} |")
    md += ['', '| 类别 | ' + ' | '.join(sorted(out['groups'])) + ' |', '|---|' + '---|' * len(out['groups'])]
    for name, vals in out['categories'].items():
        if vals:
            md.append(f'| {NAMES[name]} | ' + ' | '.join(vals.get(k, '—') for k in sorted(out['groups'])) + ' |')
    md += ['', '## 配对比较', '', '```json', json.dumps(out['paired'], indent=1, ensure_ascii=False), '```', '', '## 逐例',
           '', '| 案例 | 类别 | 组 | 状态 | 评分 | 原因 | 调用 | 强制提交 | 最后未满足项 | 未通过的评分单元 |', '|---|---|---|---|---|---|---|---|---|---|']
    for r in sorted(rows, key=lambda r: (r['case_id'], r['group'], r['rep'])):
        if not r.get('finished'):
            md.append(f"| {r['case_id']} | {NAMES.get(cat.get(r['case_id']), '')} | {r['group']}{r['rep']} | 未完成 | | {r.get('issue') or ''} | | | | |")
            continue
        md.append(f"| {r['case_id']} | {NAMES.get(cat.get(r['case_id']), '')} | {r['group']}{r['rep']} | {r['status']} | {r['score']} | {r['reason']} | "
                  f"{r['requests']} | {r.get('forced') or ''} | {', '.join(r['last_unmet'])} | {', '.join(map(str, r['cells_failed']))} |")
    Path(prefix + '.md').write_text('\n'.join(md) + '\n')
    print(json.dumps({k: v for k, v in out.items() if k != 'rows'}, indent=1, ensure_ascii=False)[:4000])


if __name__ == '__main__':
    main(*sys.argv[1:4])
