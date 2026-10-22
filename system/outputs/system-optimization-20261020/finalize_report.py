"""Read retained receipts and write the optimization handoff; sends no API calls."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / '.topomen-deps'))
from contractedge.orchestration.batch import _method_digest
from contractedge.model.money import MoneyBudget

OUT = Path(__file__).resolve().parent
FINAL = '5c2dc5800aec314cae60571dfc90e379ea78a72f6f1a8b26f8eed99d01f32a5c'


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def batch(run, score):
    path, scores = ROOT / 'outputs' / run, ROOT / 'outputs' / score
    state = read(path / 'batch.json')
    selected = read(path / 'selection.json')['selected_cases']
    grades = read(scores / 'cohort.json')
    assert grades['pending_count'] == 0
    grades = {r['case_id']: r for r in grades['records']}
    manifest = read(path / 'implementation-manifest.json')
    assert manifest['unchanged'] and manifest['before'] == manifest['after']
    freeze = path / 'method-freeze/src'
    assert {p.relative_to(freeze).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in freeze.rglob('*') if p.is_file() and p.suffix in {'.py', '.json'}} == manifest['before']
    assert (path / 'candidate-selection.json').is_file()
    rows = []
    for record in state['records']:
        if record['case_id'] not in selected:
            continue
        assert record['status'] not in {'RUNNING', 'NOT_RUN'}
        candidate = record.get('candidate', {})
        if candidate:
            patch = Path(candidate['patch_path'])
            assert hashlib.sha256(patch.read_bytes()).hexdigest() == candidate['patch_sha256']
        trace = read(path / record['trace']) if record.get('trace') else None
        if trace:
            assert trace['budget']['model_calls'] <= 12 and len(trace['rounds']) <= 2
        rows.append({'case_id': record['case_id'], 'method_status': record['status'],
                     'independent_status': grades[record['case_id']]['status'],
                     'patch_bytes': candidate.get('patch_bytes', 0),
                     'model_calls': trace['budget']['model_calls'] if trace else None,
                     'patch_sha256': candidate.get('patch_sha256')})
    return {'run': run, 'scores': score, 'method_code_digest': state['identity']['method_code_digest'],
            'selected_count': len(selected), 'retained_denominator': state['case_count'],
            'not_run': sum(r['status'] == 'NOT_RUN' for r in state['records']), 'rows': rows}


def main():
    assert _method_digest() == FINAL
    log = (OUT / 'tests-final-audit.log').read_text()
    assert re.search(r'262 passed, 9 subtests passed', log)
    integrity = read(OUT / 'release-integrity-final.json')
    assert integrity['status'] == 'PASS' and integrity['files_checked'] == 9134
    caps, replay = read(OUT / 'capability-audit150.json'), read(OUT / 'replay-summary.json')
    assert caps['cases'] == 150 and caps['status'] == 'PASS' and caps['method_code_digest'] == FINAL
    assert replay['cases'] == 5 and all(r['optimized']['method_code_digest'] == FINAL for r in replay['records'])
    tests = read(OUT / 'tests.json')
    tests.update(log='tests-final-audit.log', completion_audit='PASS')
    write(OUT / 'tests.json', tests)
    cohorts = [batch('deepseek-optimized-pilot-v2-20261020', 'deepseek-independent-optimized-pilot-v2-score-20261020'),
               batch('deepseek-optimized-new-development-v1-20261020', 'deepseek-independent-optimized-new-development-v1-score-20261020'),
               batch('deepseek-optimized-review-regression-v1-20261020', 'deepseek-independent-optimized-review-regression-v1-score-20261020')]
    single_path = ROOT / 'outputs/deepseek-optimized-single-v4-20261020'
    single = read(single_path / 'repair.trace.json')
    grade = read(ROOT / 'outputs/deepseek-independent-optimized-single-v4-score-20261020/score.json')
    assert single['status'] == 'PUBLIC_REVIEW_COMPLETE' and grade['status'] == 'PASS'
    assert grade['patch_sha256'] == single['candidate']['patch_sha256']
    assert read(single_path / 'implementation-manifest.json')['unchanged']
    config = read(ROOT / 'configs/deepseek-development-optimized-v1-budget80.json')
    old_config = read(ROOT / 'configs/deepseek-development-pilot-budget80.json')
    stable = ['provider', 'base_url', 'model', 'top_n', 'max_model_calls', 'max_rounds', 'max_format_retries',
              'max_context_tokens', 'model_context_tokens', 'initial_diagnosis_context_tokens',
              'max_elapsed_seconds', 'execution_backend', 'topomen_source_sha256',
              'max_total_tokens', 'max_output_tokens', 'execution_max_output_tokens', 'review_max_output_tokens']
    assert all(config[k] == old_config[k] for k in stable)
    money = MoneyBudget(**config['money_budget']).summary()
    assert money['limit_micro_cny'] == 80_000_000 and money['committed_micro_cny'] <= 80_000_000
    ledger_path = Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json')
    ledger = read(ledger_path)
    reserved = sum(c['reserved_micro_cny'] for c in ledger['calls'] if c['status'] != 'SETTLED')
    before = 12_403_536
    result = {'implementation_version': '0.4.3', 'method_code_digest': FINAL,
              'scope': 'Approved three-part optimization and bounded development validation; formal150/ablations deferred',
              'tests': tests, 'release_integrity': integrity, 'capability_cases': 150, 'zero_api_replay_cases': 5,
              'unchanged_policy_fields': stable,
              'cohorts': cohorts, 'single_acceptance': {'run': single_path.name, 'case_id': single['case_id'],
                  'method_status': single['status'], 'independent_status': grade['status'],
                  'patch_bytes': single['candidate']['patch_bytes'], 'model_calls': single['budget']['model_calls']},
              'money': {**money, 'committed_upper_cny': money['committed_micro_cny']/1e6,
                  'remaining_upper_cny': (80_000_000-money['committed_micro_cny'])/1e6,
                  'optimization_added_upper_cny': (money['committed_micro_cny']-before)/1e6,
                  'unresolved_reservation_cny': reserved/1e6,
                  'ledger_sha256': hashlib.sha256(ledger_path.read_bytes()).hexdigest()},
              'effectiveness': 'Significant repair-rate improvement NOT PROVEN; failures and interruption retained',
              'formal150_new_dispatches': 0, 'ablation_new_dispatches': 0,
              'interruption': 'RW-CAND-086 final extra regression stopped with unknown diagnosis response; zero-call recovery, reservation retained, no replay'}
    write(OUT / 'final-validation.json', result)
    write(ROOT / 'outputs/deepseek-optimized-review-regression-v1-20261020/money-accounting.json',
          {'before': None, 'before_unavailable_reason': 'Launcher ended before its finally block; original before snapshot existed only in process memory',
           'after': money, 'uncertain_reservations_retained': True, 'new_calls_during_recovery': 0})

    table = ['| 版本/验证组 | 案例 | 方法结果 | 独立结果 | 补丁字节/调用数 |', '|---|---|---|---|---|']
    table.append(f"| 0.4.3 单条回归 | {single['case_id']} | {single['status']} | {grade['status']} | {single['candidate']['patch_bytes']} / {single['budget']['model_calls']} |")
    for label, group in zip(['0.4.2 原5条', '0.4.2 新仓库', '0.4.3 定向回归'], cohorts):
        for row in group['rows']:
            table.append(f"| {label} | {row['case_id']} | {row['method_status']} | {row['independent_status']} | {row['patch_bytes']} / {row['model_calls'] if row['model_calls'] is not None else '中断未结'} |")
    report = OUT / 'IMPLEMENTATION_REPORT.md'
    prefix = report.read_text().split('## 真实验证')[0]
    prefix = prefix.replace('状态：核心改动和工程回归已完成；冻结源码的真实开发验证进行中。',
                            '状态：本轮核心改动、工程回归及有限开发核验已交付；效果提升未获充分证据。')
    prefix = prefix.replace('tests.json和tests-v0.4.2-final.log', 'tests.json和tests-final-audit.log')
    report.write_text(prefix + '## 真实验证\n\n' + '\n'.join(table) + f'''

各验证组分开报告，重复案例不合并成更多独立样本，不选择隐藏评分最高的补丁。0.4.2原5条独立通过1/5，新仓库2/2；0.4.3单条通过，定向回归1条公开及独立通过、1条中断未验证。每个批次继续保留完整150成员及NOT_RUN记录；这些不是正式150条实验结果。

**不能声称修复率已明显提升。** 历史原5条曾通过2/5，本轮0.4.2同组仅1/5，未优于历史记录。样本小、模型输出波动和多个改动同时存在，不能据此确定某一改动造成提升或退化；历史结果仅参考。

KPT现在能取得生产源码、生成1619字节补丁并构建通过，但独立语义失败。公开计划审查已在评分前记录：修改只覆盖runValidators，其他拥有functionConfig的消费者范围尚未证实。这支持继续研究义务覆盖，不能断言隐藏失败仅由此引起。Kopf仍停在计划证据缺口；Alertmanager仍为诊断缺口；ACK取得实现后仍未形成合法生产消费路径。保留这些失败，不放宽契约。

0.4.3的RW-CAND-067在一次公开复核纠错后覆盖P1/P2及保持条件，公开闭环并独立PASS；候选与0.4.2不同，不作为单项改动因果效果证明。最终RW-CAND-086回归中断在未返回诊断请求，原试验保持INTERRUPTED；没有重放未知请求、没有将其计为通过。其0.4.2已完成试验独立PASS仍作为对应版本的记录。

## 费用与数据身份

共享总上限80元；当前总账占用上界 **{money['committed_micro_cny']/1e6:.6f}元**，剩余上界 **{(80_000_000-money['committed_micro_cny'])/1e6:.6f}元**。其中未结预留{reserved/1e6:.6f}元（历史1笔、本轮中断1笔），继续全额计入。相对优化开始前12.403536元，本轮新增占用{(money['committed_micro_cny']-before)/1e6:.6f}元；没有以新目录重置费用。

最终源码：`{FINAL}`。262项测试、9项子测试通过；150条公开能力核验和5条零API回放均绑定最终源码。冻结数据集 **9134个校验条目全部通过**，评分器和数据包保持冻结。独立评价只对固定候选运行，结果不返回修复模型；0.4.3源码在独立评分开始前已冻结。

正式150条和消融未启动；旧正式就绪凭据属于历史方法身份，不能为0.4.3作正式就绪背书。核心功能的工程验证通过，不代表数据集所有成员可修复或当前结果优秀。

## 交付与保留决定

逐项需求证据见COMPLETION_AUDIT.md，机器结果见final-validation.json，完整工作记录见WORK_LOG.md。保留基线、隔离副本、各版本失败、补丁和原始观察以便审计；代价是多占少量本地空间。未合并或发布代码。独立代码审查原Important已修复，0.4.3新增复核逻辑的独立审查无新发现；文档滞后问题已同步。
''')
    status_path = ROOT / 'CURRENT_STATUS.json'
    status = read(status_path)
    status.setdefault('historical_real_api_experiment_pre_optimization', status.get('real_api_experiment'))
    status['implementation_version'] = '0.4.3'
    status['protocol_tests'] = {**tests, 'report': 'outputs/system-optimization-20261020/tests.json'}
    status['system_optimization'] = {'status': 'IMPLEMENTED_AND_BOUNDED_VALIDATION_RECORDED',
        'version': '0.4.3', 'method_code_digest': FINAL,
        'report': 'outputs/system-optimization-20261020/IMPLEMENTATION_REPORT.md',
        'audit': 'outputs/system-optimization-20261020/COMPLETION_AUDIT.md',
        'effectiveness': result['effectiveness'], 'interrupted_extra_regressions': 1,
        'formal150': 'DEFERRED_BY_USER', 'ablation': 'DEFERRED_BY_USER', 'formal_ready': False}
    status['real_api_experiment'] = {'status': 'BOUNDED_DEVELOPMENT_VALIDATION_ONLY',
        'total_limit_cny': 80, 'money': result['money'], 'method_code_digest': FINAL,
        'report': 'outputs/system-optimization-20261020/final-validation.json',
        'new_formal_cases_dispatched': 0, 'new_ablation_cases_dispatched': 0,
        'historical_runs': 'REFERENCE_ONLY; retained separately'}
    status['development_run'] = {**result['single_acceptance'], 'partition': 'development',
        'model': config['model'], 'execution_backend': 'topomen_agent',
        'directory': 'outputs/deepseek-optimized-single-v4-20261020',
        'score': 'outputs/deepseek-independent-optimized-single-v4-score-20261020/score.json',
        'scope': 'One current-method acceptance, not full-dataset effectiveness'}
    status['full150_model_experiment'] = 'CURRENT_METHOD_DEFERRED_BY_USER; historical stopped run remains reference only'
    write(status_path, status)
    print(json.dumps({'version': '0.4.3', 'method_digest': FINAL, 'tests': 262,
                      'money_upper_cny': money['committed_micro_cny']/1e6, 'report': str(report)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
