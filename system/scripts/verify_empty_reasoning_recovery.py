"""One real consumer regression of the recorded empty-reasoning failure.

The original experiment's ROUND_LIMIT result is preserved. This is a separate
engineering check using its public, validated plan, not a primary cohort result.
Prior call/token/time spending remains charged to the verification budget.
"""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.topomen-deps'))
sys.path.insert(0, str(ROOT / 'src'))
from contractedge.cli import _build_provider
from contractedge.contracts.validation import parse_method_input
from contractedge.dataset.admission import check_case_admission
from contractedge.dataset.benchmark import editable_policy, execution_contract, file_sha, method_input_from_task, public_task
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.review import ModelPublicReviewer
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.model.budget import BudgetedProvider
from contractedge.orchestration.feedback import make_feedback, review_plan_with_retained
from contractedge.orchestration.repair import select_observations, validate_ready_trace
from contractedge.source.snapshot import compute_snapshot
from run_single_acceptance import implementation, write


def read(path):
    return json.loads(path.read_text())


def main():
    output = Path(sys.argv[1]).resolve()
    if output.exists():
        raise ValueError('Use a new regression directory')
    original = ROOT / 'outputs/deepseek-development-pilot-v5-20261020/cases/RW-ALERTMANAGER-4705'
    old = read(original / 'repair.trace.json')
    config = read(ROOT / 'configs/deepseek-development-pilot-budget80.json')
    benchmark = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    check_case_admission(benchmark / 'public', old['case_id'], config)
    assert old['status'] == 'ROUND_LIMIT' and not old['candidate']['patch_bytes']
    prior = read(original / 'rounds/1/execution.json')['execution_attempt']
    assert prior['model_response'] == '' and prior['response_metadata']['finish_reason'] == 'length'
    output.mkdir(parents=True)
    write(output / 'origin.json', {'scope': 'ONE_PUBLIC_CONSUMER_REGRESSION; EXCLUDED_FROM_PRIMARY_COHORT',
        'original': str(original), 'original_trace_sha256': file_sha(original / 'repair.trace.json'),
        'original_outcome_preserved': old['status'], 'private_data_used': False,
        'source_plan_sha256': file_sha(original / 'rounds/1/plan.json')})
    os.environ[config['api_key_env']] = Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider, tokenizer = _build_provider(config)
    before, money_before = implementation(), provider.money_budget.summary()
    budget = BudgetedProvider(provider, tokenizer, max_tokens=old['budget']['max_tokens'],
        max_seconds=old['budget']['max_seconds'], max_calls=old['budget']['max_calls'],
        on_update=lambda calls: write(output / 'new-model-calls.json', calls))
    budget.prior_calls = old['budget']['model_calls']
    budget.reserved_tokens = old['budget']['reserved_tokens']
    budget.started -= old['budget']['elapsed_seconds'] + max(0, time.time() - (original / 'repair.trace.json').stat().st_mtime)
    runner = BenchmarkPublicRunner(benchmark / 'public', benchmark / 'tools', lock_root=config['lock_root'],
        timeout_seconds=min(config['public_timeout_seconds'], budget.remaining_seconds))
    report = {'case_id': old['case_id'], 'status': 'STARTING', 'primary_cohort_result': False}
    try:
        record = runner.prepare(old['case_id'], output / 'workspace', output / 'prepare')
        source = Path(record['source_root'])
        method, _ = method_input_from_task(benchmark / 'public', old['case_id'])
        parsed = parse_method_input(method)
        validate_ready_trace(read(original / 'rounds/0/front.trace.json'), parsed, source)
        _, task_root, task, manifest = public_task(benchmark / 'public', old['case_id'])
        policy = editable_policy(task, execution_contract(task_root, task, manifest))
        plan = read(original / 'rounds/1/plan.json')
        snapshot = compute_snapshot(source).snapshot_id
        assert snapshot == prior['source_snapshot']
        feedback = read(original / 'rounds/1/feedback.json')
        observations = select_observations(feedback['public_observations'], parsed.requirement_text,
                                           tokenizer, config['max_public_evidence_tokens'])
        executor = TopoMenAgentExecutor(budget, tokenizer, config,
            on_trace=lambda trace: write(output / 'native-trace.json', trace))
        execution = executor.execute(plan, source, policy, requirement=parsed.requirement_text,
            declared_artifacts=parsed.declared_artifacts, observations=observations,
            feedback=feedback, previous_attempt=prior)
        write(output / 'execution.json', execution)
        candidate = runner.export(output / 'workspace', output / 'candidate.patch', output / 'export')
        write(output / 'candidate-selection.json', {**candidate, 'selected_before_hidden_evaluation': True,
            'scope': 'Engineering regression candidate; never substitutes for the primary pilot candidate'})
        runner.timeout_seconds = min(config['public_timeout_seconds'], budget.remaining_seconds)
        observation = runner.observe(output / 'workspace', output / 'public')
        effective = review_plan_with_retained(plan, feedback)
        review = ModelPublicReviewer(budget, tokenizer, config).review(parsed.requirement_text, effective,
            {**observation, 'declared_artifacts': list(parsed.declared_artifacts),
             'observations': list(select_observations(observation['observations'], parsed.requirement_text,
                 tokenizer, config['max_review_evidence_tokens']))})
        write(output / 'public-review.json', review)
        result = make_feedback(case_id=old['case_id'], run_id=old['run_id'], input_digest=parsed.input_digest,
            round_id=0, from_snapshot=snapshot, to_snapshot=compute_snapshot(source).snapshot_id,
            patch_digest=candidate['patch_sha256'], execution_status=observation['status'],
            observations=observation['observations'], plan=effective, review=review)
        write(output / 'feedback.json', result)
        report.update(status='RECOVERY_EXECUTED', public_status=observation['status'],
            semantic_outcome=result['semantic_outcome'], candidate=candidate,
            retry_thinking=budget.calls[0].get('response_metadata', {}).get('effective_thinking'))
    except Exception as exc:
        report.update(status='RECOVERY_FAILED', error=type(exc).__name__ + ': ' + str(exc))
    finally:
        after = implementation()
        write(output / 'implementation-manifest.json', {'before': before, 'after': after, 'unchanged': before == after})
        write(output / 'money-accounting.json', {'before': money_before, 'after': provider.money_budget.summary(),
            'note': 'Global ledger deltas may include the concurrently running pilot; new-model-calls.json identifies this probe calls'})
        report['budget'] = budget.summary()
        write(output / 'regression.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'RECOVERY_EXECUTED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
