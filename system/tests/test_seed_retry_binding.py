import importlib.util
from pathlib import Path

from contractedge.source.snapshot import compute_snapshot


def load_harness():
    spec=importlib.util.spec_from_file_location('reliability_worker_v6',Path('scripts/run_reliability_worker_v6.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_seed_retry_preserves_historical_baseline_api(tmp_path):
    original={'id':'original'};prior={'id':'prior'}
    proof={'status':'APPLIED','to_snapshot':compute_snapshot(tmp_path).snapshot_id}
    def old_rebind(plan,source,feedback):
        assert plan is original
        return 'historical-api'
    assert load_harness().bind_seed_retry(old_rebind,original,tmp_path,{},prior,proof)=='historical-api'


def test_seed_retry_uses_current_proof_but_not_restored_trial(tmp_path):
    target=tmp_path/'state.go';target.write_text('package current\n')
    original={'id':'original'};prior={'id':'prior'}
    proof={'status':'APPLIED','to_snapshot':compute_snapshot(tmp_path).snapshot_id}
    calls=[]
    def new_rebind(plan,source,feedback,*,prior_execution=None):
        calls.append((plan,prior_execution));return plan
    bind=load_harness().bind_seed_retry
    assert bind(new_rebind,original,tmp_path,{},prior,proof) is prior
    assert calls[-1][1] is proof
    target.write_text('package restored\n')
    assert bind(new_rebind,original,tmp_path,{},prior,proof) is original
    assert calls[-1][1] is None
