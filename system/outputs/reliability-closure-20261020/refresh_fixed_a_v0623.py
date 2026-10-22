"""Summarize registered A outputs only; never invokes a method or a scorer."""
import json
from datetime import datetime, timezone
from pathlib import Path


OUT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def main():
    state = read(OUT / 'delivery-0.6.23-A-state.json')
    scores = read(OUT / 'independent-v0.6.23/records.json')
    by_key = {(r['group'], r['case_id'], r['repeat'], r['variant']): r for r in scores}
    rows = []
    for item in state['schedule']:
        directory = Path(item['output'])
        case = directory / 'cases' / item['case_id']
        row = {k: item[k] for k in ('case_id', 'variant', 'repeat', 'output')}
        row.update(method_status='NOT_RUN', independent_status='NOT_RUN')
        batch = directory / 'batch.json'
        if batch.exists():
            selected = next(r for r in read(batch)['records'] if r['case_id'] == item['case_id'])
            row['method_status'] = selected['status']
            row['patch_bytes'] = (selected.get('candidate') or {}).get('patch_bytes', 0)
        model_calls = case / 'model_calls.json'
        if model_calls.exists():
            row['model_calls_started'] = len(read(model_calls))
        trace = case / 'repair.trace.json'
        if trace.exists():
            trace = read(trace)
            row['semantic_outcome'] = (trace.get('last_feedback') or {}).get('semantic_outcome')
            row['budget'] = trace.get('budget')
        score = by_key.get(('A', item['case_id'], item['repeat'], item['variant']))
        if score:
            row['independent_status'] = score['status']
        elif row['method_status'] not in ('NOT_RUN', 'RUNNING'):
            row['independent_status'] = 'PENDING_SCORE'
        rows.append(row)
    ledger = read(Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json'))
    spent = sum(r.get('charged_micro_cny', r['reserved_micro_cny']) for r in ledger['calls']) / 1e6
    summary = dict(as_of=datetime.now(timezone.utc).isoformat(), version='0.6.23', group='A',
                   planned_attempts=14, planned_cases=7, formal_ready=False,
                   finalized_attempts=sum(r['method_status'] not in ('NOT_RUN', 'RUNNING') for r in rows),
                   scored_attempts=sum(r['independent_status'] not in ('NOT_RUN', 'PENDING_SCORE') for r in rows),
                   controller_status=state['status'], active=state.get('active'), rows=rows,
                   cost_snapshot=dict(committed_including_reservations_cny=spent, limit_cny=150,
                                      remaining_upper_cny=150-spent),
                   scope='Incomplete fixed panel; registered existing slots counted once; no B execution or final acceptance')
    write(OUT / 'G5_A_PROGRESS_v0623.json', summary)
    table = ['# 冻结0.6.23 A组实时截点', '', f"记录时间：{summary['as_of']}。",
             '固定7案例×2版本=14次；独立评分仍未完成的项明确保留，G4同一尝试只计一次。', '',
             '|案例|基线独立结果|候选独立结果|', '|---|---|---|']
    cases = list(dict.fromkeys(r['case_id'] for r in rows))
    for case_id in cases:
        pair = {r['variant']: r for r in rows if r['case_id'] == case_id}
        values = [pair[v]['independent_status'] if pair[v]['method_status'] != 'RUNNING' else 'RUNNING'
                  for v in ('baseline', 'candidate')]
        table.append('|' + '|'.join([case_id, *values]) + '|')
    table += ['', '完整记录见同名JSON、各原始batch与independent-v0.6.23/records.json。',
              '本表不授予正式就绪；B关闭，重复与恢复尚需对应登记验收。', '']
    (OUT / 'G5_A_PROGRESS_v0623.md').write_text('\n'.join(table))
    progress = read(OUT / 'CURRENT_PROGRESS.json')
    progress.update(as_of=summary['as_of'], budget_snapshot=summary['cost_snapshot'])
    progress['current_experiment'].update(finalized_registered_attempts=summary['finalized_attempts'],
                                           scored_registered_attempts=summary['scored_attempts'],
                                           active=summary['active'], progress='G5_A_PROGRESS_v0623.json')
    write(OUT / 'CURRENT_PROGRESS.json', progress)
    print(json.dumps({k: summary[k] for k in ('as_of', 'finalized_attempts', 'scored_attempts',
                                             'controller_status', 'active', 'cost_snapshot')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
