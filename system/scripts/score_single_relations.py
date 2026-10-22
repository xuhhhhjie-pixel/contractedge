"""Independent evaluator for the fixed single-case O/E/V/C/P trace.

Only the selected case's private annotations are loaded. Never import a model
provider or call repair from this evaluator process.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from contractedge.contracts.validation import validate_run_trace
from contractedge.dataset.benchmark import file_sha, method_input_from_task
from contractedge.evaluation.benchmark import load_evaluator_members, normalize_member_gt, traceability
from contractedge.evaluation.gt_adapter import score_run


def main():
    run = Path(sys.argv[1]).resolve(strict=True)
    output = Path(sys.argv[2]).resolve()
    selection = json.loads((run / 'candidate-selection.json').read_text())
    assert selection['case_id'] == 'RW-CAND-130'
    path = run / 'rounds/0/front.trace.json'
    assert file_sha(path) == selection['broken_front_trace_sha256']
    assert file_sha(run / 'candidate.patch') == selection['patch_sha256']
    trace = json.loads(path.read_text())
    validate_run_trace(trace)
    benchmark = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    root, identity, members = load_evaluator_members(benchmark)
    member = next(row for row in members if row['case_id'] == selection['case_id'])
    current, _ = method_input_from_task(root / 'public', selection['case_id'], release_id=identity['release_id'])
    assert trace['input_digest'] == current['input_digest']
    assert trace['snapshot_id'] == current['initial_snapshot_id']
    gt = normalize_member_gt(root, identity, member)
    result = score_run(trace, gt)
    result['traceability'] = traceability(trace)
    result['gt_policy'] = {key: gt[key] for key in ['precision_eligible', 'recall_eligible',
        'full_recall_eligible', 'ambiguity', 'unstructured_relation_annotations']}
    result['gt_sha256'] = member['gt_sha256']
    result['trace_sha256'] = file_sha(path)
    result['scope'] = 'One selected development case; does not estimate formal150 performance'
    with output.open('x') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: result.get(key) for key in ['case_id', 'status', 'precision', 'recall', 'traceability', 'gt_policy']}))


if __name__ == '__main__':
    main()
