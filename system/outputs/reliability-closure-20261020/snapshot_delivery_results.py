"""Read aggregate outcomes only; never inspect independent verifier inputs."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    lines = ['# 当前冻结版本验证快照', '',
        '生成时间：' + datetime.now(timezone.utc).isoformat(), '',
        '下表仅汇总逐次结果，不合并版本、不挑选最好结果。NOT_RUN 或未运行成员仍保留在登记分母中。', '']
    snapshot = {}
    for version in ['0.6.8', '0.6.9', '0.6.10']:
        registration = json.loads((ROOT / f'experiment-registration-v{version}.json').read_text())
        records_path = ROOT / f'independent-v{version}/records.json'
        records = json.loads(records_path.read_text()) if records_path.exists() else []
        by_key = {(r['group'], r['case_id'], r['repeat'], r['variant']): r for r in records}
        if len(by_key) != len(records):
            raise ValueError('Duplicate independent attempt identity')
        lines += [f'## {version}', '',
            f'独立流程已记录 {len(records)}/46 个登记项（含 NOT_RUN）；B组开放：{registration["B_opened_for_method_execution"]}。', '',
            '|分组|案例|重复号|基线|候选|', '|---|---|---|---|---|']
        rows = []
        for group in ['A', 'repeat', 'recovery', 'B']:
            state = json.loads((ROOT / f'delivery-{version}-{group}-state.json').read_text())
            seen = set()
            for item in state['schedule']:
                key = (group, item['case_id'], item['repeat'])
                if key in seen:
                    continue
                seen.add(key)
                values = []
                for variant in ['baseline', 'candidate']:
                    record = by_key.get((*key, variant))
                    if record:
                        values.append(record['status'])
                        continue
                    planned = next(p for p in state['schedule'] if
                        p['case_id'] == key[1] and p['repeat'] == key[2] and p['variant'] == variant)
                    batch_path = Path(planned['output']) / 'batch.json'
                    value = 'NOT_STARTED'
                    if batch_path.exists():
                        batch = json.loads(batch_path.read_text())
                        result = next(r for r in batch['records'] if r['case_id'] == key[1])
                        value = 'METHOD_' + result['status'] + '; SCORE_PENDING'
                    values.append(value)
                rows.append(dict(group=group, case_id=key[1], repeat=key[2], baseline=values[0], candidate=values[1]))
                lines.append(f'|{group}|{key[1]}|{key[2]}|{values[0]}|{values[1]}|')
        snapshot[version] = dict(rows=rows, independent_record_count=len(records),
            planned_attempts=46, accepted=False, scope='Interim outcomes; no pooled success or generalization claim')
        lines += ['', '验收状态：未通过最终交付验收。独立PASS不代替全部公开目标和保持条件的验收；有限检查通过不自动解除广泛UNKNOWN。', '']
    (ROOT / 'LIVE_RESULTS_20261021.md').write_text('\n'.join(lines) + '\n')
    (ROOT / 'live-results-20261021.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n')
    print({v: d['independent_record_count'] for v, d in snapshot.items()})


if __name__ == '__main__':
    main()
