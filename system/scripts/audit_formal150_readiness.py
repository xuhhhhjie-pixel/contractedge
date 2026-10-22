"""Verify recorded engineering evidence and issue an identity-bound readiness receipt.

This command performs no model calls and does not declare repair-performance success.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from contractedge.orchestration.batch import _method_digest
from contractedge.model.money import MoneyBudget


def read(relative):
    return json.loads((ROOT / relative).read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    evidence = []
    checks = []

    def load(relative):
        value = read(relative)
        evidence.append({'path': str(ROOT / relative), 'sha256': sha(ROOT / relative)})
        return value

    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})

    pre = 'outputs/deepseek-method-preflight-v6-20261020/'
    pilot = 'outputs/deepseek-development-pilot-v7-20261020/'
    production = load('configs/deepseek-formal150-production-v2-budget80.json')
    development = load('configs/deepseek-development-pilot-budget80.json')
    method_digest = _method_digest()
    tests = load(pre + 'tests-final.json')
    check('Current method matches the 230-test/9-subtest passing run',
          tests['method_code_digest'] == method_digest and tests['passed'] == 230
          and tests['subtests_passed'] == 9 and tests['failed'] == tests['skipped'] == 0)
    release = load(pre + 'release150-verified.json')
    check('Frozen150 release identities and all 9134 checksums were verified',
          release['cases'] == release['private_gt_bound'] == release['public_requirements_present'] == 150
          and release['frozen_checksum_files_verified'] == 9134
          and not any(release[k] for k in ['missing_public_launch_entries', 'gt_class_conflicts',
                                        'repository_split_conflicts', 'family_split_conflicts'])
          and release['public_manifest_sha256'] == production['expected_public_manifest_sha256'])
    routes = load('outputs/benchmark150-public-routes-20261019.json')
    check('All150 public launch routes exist (not150 runtime executions)',
          routes['status'] == 'PASS' and routes['verified'] == 150 and routes['candidate_executions'] == 0)
    admission = load(pre + 'admission-current150.json')
    check('Actual115/18/17 partition admission checked without paid calls',
          admission['status'] == 'PASS' and admission['all_five_selected_are_development']
          and admission['authoritative_partition_labels'] == {'development': 115, 'validation': 18, 'blind_test': 17})
    check('Production only changes partition admission from the tested pilot',
          [k for k in sorted(set(production) | set(development))
           if production.get(k) != development.get(k)] == ['development_only']
          and production['development_only'] is False and production['expected_case_count'] == 150)
    batch = load(pilot + 'batch.json')
    selected = load(pilot + 'candidate-selection.json')
    implementation = load(pilot + 'implementation-manifest.json')
    current = {str(p.relative_to(ROOT / 'src')): sha(p)
               for p in sorted((ROOT / 'src').rglob('*')) if p.is_file() and p.suffix in {'.py', '.json'}}
    check('Five-case pilot used this unchanged method and fixed candidates before scoring',
          implementation['unchanged'] and implementation['before'] == implementation['after'] == current
          and selected['selected_before_private_scoring'] and selected['batch_sha256'] == sha(ROOT / pilot / 'batch.json')
          and batch['identity']['method_code_digest'] == method_digest and batch['attempted'] == 5
          and batch['status_counts'] == {'PUBLIC_REVIEW_COMPLETE': 2, 'EVIDENCE_GAP': 2, 'INVALID_OUTPUT': 1, 'NOT_RUN': 145})
    cohort = load('outputs/deepseek-independent-pilot-v7-score-20261020/cohort.json')
    counts = {}
    for record in cohort['records']:
        counts[record['status']] = counts.get(record['status'], 0) + 1
        if record.get('score'):
            score_path = 'outputs/deepseek-independent-pilot-v7-score-20261020/' + record['score']
            score = load(score_path)
            check('Independent patch identity: ' + record['case_id'],
                  record['score_sha256'] == sha(ROOT / score_path)
                  and record['patch_sha256'] == score['patch_sha256'] and record['status'] == score['status'])
    check('Primary independent cohort retains two PASS, three failures and145 NOT_RUN',
          cohort['case_count'] == 150 and cohort['batch_sha256'] == sha(ROOT / pilot / 'batch.json')
          and counts == {'PASS': 2, 'NO_CANDIDATE': 3, 'NOT_RUN': 145})
    single = load('outputs/deepseek-independent-single-v9-score-20261020/score.json')
    check('Accepted single-case fixed candidate independently passes', single['status'] == 'PASS'
          and single['patch_sha256'] == sha(ROOT / 'outputs/deepseek-single-case-acceptance-v9-20261020/candidate.patch'))
    regression = load('outputs/deepseek-empty-reasoning-live-regression-v1-20261020/regression.json')
    regression_score = load('outputs/deepseek-independent-empty-reasoning-regression-score-20261020/score.json')
    check('Real bounded empty-thinking retry was exercised; semantic failure remains explicit',
          regression['status'] == 'RECOVERY_EXECUTED' and regression['public_status'] == 'EXECUTION_PASS'
          and regression['primary_cohort_result'] is False and regression['retry_thinking'] == 'disabled'
          and regression['budget']['resumed_model_calls'] == 5 and regression['budget']['model_calls'] == 7
          and regression_score['status'] == 'SEMANTIC_FAIL'
          and regression_score['patch_sha256'] == regression['candidate']['patch_sha256'])
    launch = load('outputs/deepseek-formal150-launch-preflight-budget80-20261020/batch.json')
    check('Formal150 prepared with exact method and no case dispatch',
          launch['case_count'] == 150 and launch['attempted'] == 0 and launch['status_counts'] == {'NOT_RUN': 150}
          and launch['identity']['method_code_digest'] == method_digest)
    frozen = load('outputs/deepseek-formal150-launch-preflight-budget80-20261020/method-freeze/manifest.json')
    invocations = sorted((ROOT / 'outputs/deepseek-formal150-launch-preflight-budget80-20261020/invocations').glob('*.json'))
    invocation = load(str(invocations[-1].relative_to(ROOT)))
    check('Frozen production source/config/launcher match the zero-call invocation',
          frozen['source_files'] == current
          and frozen['config_sha256'] == sha(ROOT / 'configs/deepseek-formal150-production-v2-budget80.json')
          and invocation['launcher_sha256'] == sha(ROOT / 'scripts/run_formal150.py')
          and invocation['method_code_digest'] == method_digest
          and invocation['production_config_sha256'] == frozen['config_sha256']
          and invocation['mode'] == 'ZERO_CALL_PREFLIGHT' and invocation['money_before'] == invocation['money_after'])
    guard = load(pre + 'formal-missing-receipt-rejection-budget80.json')
    check('Unaccepted formal execution refused before any new model call',
          guard['status'] == 'PASS' and guard['ledger_unchanged'] and guard['model_calls_added'] == 0)
    integrity = load(pre + 'formal-receipt-integrity-rejection-budget80.json')
    check('Unaccepted or mismatched readiness receipts rejected before paid work',
          integrity['status'] == 'PASS' and integrity['model_calls_added'] == 0
          and len(integrity['checks']) == 2 and all(c['ledger_unchanged']
              and c['rejected_before_paid_call'] for c in integrity['checks']))
    images = load('outputs/deepseek-development-pilot-v4-20261020/runtime-image-preflight.json')
    check('All16 frozen runtime images verified locally', images['status'] == 'PASS'
          and images['images'] == 16 and images['references'] == 18)
    money = MoneyBudget(**production['money_budget']).summary()
    check('All experiments share the user-authorized80CNY total cap and retain unknown charges',
          money['limit_micro_cny'] == 80000000 and money['committed_micro_cny'] < 80000000
          and money['pending_calls'] == 1)
    free = shutil.disk_usage(ROOT).free
    check('Current work disk meets the production admission floor', free >= production['min_free_work_disk_bytes'])
    report = {
        'schema_version': 'ce.formal150_engineering_readiness.v1', 'as_of': '2026-10-20',
        'status': 'READY_FOR_FORMAL150' if all(c['passed'] for c in checks) else 'NOT_READY',
        'scope': 'Engineering launch readiness; not all150 executed, not all-case repair success, not full150 funding approval',
        'method_code_digest': method_digest,
        'production_config_sha256': sha(ROOT / 'configs/deepseek-formal150-production-v2-budget80.json'),
        'launcher_sha256': sha(ROOT / 'scripts/run_formal150.py'),
        'audit_script_sha256': sha(Path(__file__)), 'checks': checks, 'evidence': evidence,
        'primary_development_results': counts, 'engineering_regression_private_score': 'SEMANTIC_FAIL',
        'formal150_cases_dispatched': 0, 'money': money, 'free_work_disk_bytes': free,
        'limitations': [
            'Three of five primary development cases did not produce a candidate; no claim all150 can be repaired.',
            'Separate Go retry regression compiled publicly but failed independent behavioral scoring; excluded from primary results.',
            'Remaining80CNY total budget may stop a formal run before150 cases; the cap is not increased by this receipt.',
            'Route and file verification are not fresh all150 runtime executions; host resources may change before launch.',
            'Relation precision/recall requires explicit GT eligibility; missing eligibility remains unknown.',
            'Historical prematurely stopped run exposed one validation case; retained exposure audit is required for research reporting.'
        ]}
    out = ROOT / 'outputs/deepseek-formal150-readiness-budget80-20261020/readiness.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(checks),
                      'failed_checks': [c['name'] for c in checks if not c['passed']],
                      'report': str(out), 'money': money}, ensure_ascii=False))
    if report['status'] != 'READY_FOR_FORMAL150':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
