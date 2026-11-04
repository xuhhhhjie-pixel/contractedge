"""Completed per-attempt budgets are method results, not global-wallet waivers."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_isolated_coordinator import load
from tests.test_recorded_evidence_gap_terminal import recorded


def budget_record(tmp_path):
    path, ref, raw, trace = recorded(tmp_path)
    program = Path(ref['path'])
    program.write_bytes((Path(__file__).parent / 'fixtures/isolated_method_pre_budget_v3.py').read_bytes())
    ref['sha256'] = hashlib.sha256(program.read_bytes()).hexdigest()
    raw['identity']['harness_files']['run_isolated_method.py'] = ref['sha256']
    raw['terminal_validation']['method_status'] = trace['status'] = 'BUDGET_EXHAUSTED'
    trace.update(errors=['Global model token/time/call budget exhausted'],
        budget=dict(max_tokens=2000000, reserved_tokens=1000, max_seconds=9000,
                    elapsed_seconds=30, max_calls=2, model_calls=2, resumed_model_calls=0),
        rounds=[dict(round_id=0)], final_snapshot_id='S2')
    patch = Path(trace['candidate']['patch_path']); patch.write_bytes(b'sealed existing candidate')
    trace['candidate'].update(patch_bytes=patch.stat().st_size,
        patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest())
    journal = path.parent / 'method-journal'
    (journal / 'model2.json').write_text(json.dumps(dict(op='model.complete', status='DONE', result={})))
    (journal / 'export.json').write_text(json.dumps(dict(op='public.export', status='DONE', result=trace['candidate'])))
    path.write_text(json.dumps(raw)); (Path(raw['identity']['output_path']) / 'repair.trace.json').write_text(json.dumps(trace))
    return path, ref, raw, trace


def test_proven_closed_local_call_limit_is_a_method_result(tmp_path):
    path, _, raw, _ = budget_record(tmp_path)
    bridge = SimpleNamespace(public_infrastructure_failed=False,
        exports={raw['identity']['output_path'] + '/candidate.patch': {}})
    # This classifier case needs no candidate export; sealed-candidate binding
    # is separately verified by the read-only adjudication below.
    case = Path(raw['identity']['output_path'])
    trace = json.loads((case / 'repair.trace.json').read_text()); trace['candidate'] = None
    (case / 'repair.trace.json').write_text(json.dumps(trace))
    assert load().validate_method_result(case.parent, case.name, bridge,
        path.parent / 'method-journal')['classification'] == 'METHOD_RESULT'


@pytest.mark.parametrize('guard', ['wallet', 'unknown', 'not_dispatched', 'public_infrastructure'])
def test_local_budget_status_cannot_waive_parent_policy_or_uncertainty(tmp_path, guard):
    path, _, raw, _ = budget_record(tmp_path)
    case = Path(raw['identity']['output_path'])
    trace = json.loads((case / 'repair.trace.json').read_text()); trace['candidate'] = None
    (case / 'repair.trace.json').write_text(json.dumps(trace))
    journal = path.parent / 'method-journal'
    if guard in {'unknown', 'not_dispatched'}:
        p=journal/'model.json';v=json.loads(p.read_text());v['status']='PENDING' if guard=='unknown' else 'NOT_DISPATCHED';p.write_text(json.dumps(v))
    bridge=SimpleNamespace(public_infrastructure_failed=guard=='public_infrastructure',exports={})
    broker=SimpleNamespace(policy_failures=[dict(category='BUDGET',error_type='MoneyBudgetExceeded')] if guard=='wallet' else [])
    assert load().validate_method_result(case.parent,case.name,bridge,journal,broker=broker)['classification']=='INFRASTRUCTURE_STOP'


def test_original_closed_budget_receipt_is_adjudicated_without_touching_bytes(tmp_path):
    path, ref, raw, _ = budget_record(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = load().read_recorded_terminal(path, recorded_program=ref)
    assert result['receipt']['terminal_validation']['classification'] == 'METHOD_RESULT'
    assert result['receipt']['terminal_validation']['method_status'] == 'BUDGET_EXHAUSTED'
    assert result['raw_receipt'] == raw and result['model_calls_added'] == 0
    assert result['interpretation_policy'] == 'ce.closed_local_call_budget_terminal.v1'
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_scoring_uses_original_export_and_keeps_negative_outcome(tmp_path):
    from tests.test_isolated_scoring import fixture, load as scoring_load
    path, ref, raw, _ = budget_record(tmp_path)
    _, benchmark, output, _ = fixture(tmp_path / 'score-fixture', exit_code=1)
    tool=benchmark/'tools/score_case.py'
    tool.write_text(tool.read_text().replace("'status':'PASS'", "'status':'SEMANTIC_FAIL'"))
    raw['identity']['public_release_files']={};path.write_text(json.dumps(raw))
    before=path.read_bytes()
    result=scoring_load().run(path,benchmark,output,timeout=10,_terminal_program=ref)
    assert result['status']=='SEMANTIC_FAIL'
    assert result['model_calls_added']==0 and result['candidate_selection_frozen'] is True
    assert path.read_bytes()==before


@pytest.mark.parametrize('mutation', ['pending', 'not_dispatched', 'policy', 'source', 'cleanup',
    'budget_not_reached', 'count_mismatch', 'bad_export', 'bad_patch', 'wrong_status',
    'unexpected_error', 'resumed_calls', 'nonfinite_time', 'modified_program'])
def test_uncertainty_or_nonlocal_budget_cannot_be_waived(tmp_path, mutation):
    path, ref, raw, trace = budget_record(tmp_path)
    journal = path.parent / 'method-journal'
    if mutation in {'pending', 'not_dispatched'}:
        p=journal/'model.json';v=json.loads(p.read_text());v['status']='PENDING' if mutation=='pending' else 'NOT_DISPATCHED';p.write_text(json.dumps(v))
    elif mutation == 'policy': raw['terminal_validation']['coordinator_policy_failures']=['MONEY_BUDGET']
    elif mutation == 'source': raw['method_source_unchanged']=False
    elif mutation == 'cleanup': raw['cleanup']['status']='UNKNOWN'
    elif mutation == 'budget_not_reached': trace['budget']['model_calls']=1
    elif mutation == 'count_mismatch': (journal/'model2.json').unlink()
    elif mutation == 'bad_export': (journal/'export.json').unlink()
    elif mutation == 'bad_patch': Path(trace['candidate']['patch_path']).write_bytes(b'changed')
    elif mutation == 'wrong_status': trace['status']='INFRASTRUCTURE_ERROR'
    elif mutation == 'unexpected_error': trace['errors']=['Provider transport failed']
    elif mutation == 'resumed_calls': trace['budget']['resumed_model_calls']=1
    elif mutation == 'nonfinite_time': trace['budget']['elapsed_seconds']=float('nan')
    else: Path(ref['path']).write_text('different program')
    path.write_text(json.dumps(raw));(Path(raw['identity']['output_path'])/'repair.trace.json').write_text(json.dumps(trace))
    with pytest.raises(ValueError): load().read_recorded_terminal(path, recorded_program=ref)
