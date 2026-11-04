"""The frozen seed harness uses the same source-bound build recovery policy."""
import importlib.util
import json
from pathlib import Path
import pytest
from contractedge.execution import public
from contractedge.source.snapshot import compute_snapshot
from tests.test_candidate_regression_guard import run


def harness():
    path=Path(__file__).parents[1]/'scripts/run_reliability_worker_v9.py'
    spec=importlib.util.spec_from_file_location('worker_v9',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.mark.parametrize('kind,rounds,expected',[('confirmed',2,2),('unclassified',2,1),('stale',2,1),('confirmed',1,1)])
def test_seed_build_error_retries_on_restored_source_only_with_current_diagnostic(tmp_path,kind,rounds,expected):
    source=tmp_path/'source';source.mkdir();target=source/'main.go'
    seed='package main\nvar n int = 0\n';target.write_text(seed);sid=compute_snapshot(source).snapshot_id
    initial=dict(route='EXECUTE',to_snapshot=sid);calls=[]
    def attempt(index,feedback,observation,previous):
        calls.append(index)
        if index:
            assert target.read_text()==seed
            assert previous['source_snapshot']==sid
            task=previous['rejected_trial']['feedback']['failure_tasks']['shared_execution_errors'][0]
            assert task['error_field']=='production_compile'
            assert 'cannot use' in task['actual_error'] and task['snapshot_id']!=sid
        target.write_text('package main\nvar n int = '+('"bad"' if not index else '1')+'\n')
        snapshot=compute_snapshot(source);trial=run({} if not index else {'retain':'PASS'},snapshot.snapshot_id)
        if not index:
            raw=tmp_path/'raw';raw.mkdir();(raw/'public_execution.json').write_text(json.dumps(dict(
                status='EXECUTION_FAILED',private_materials_loaded=False,scenarios=[dict(scenario='build',exit_code=1,timed_out=False,output_limited=False)])))
            (raw/'build.stderr').write_text('main.go:2:13: cannot use "bad" as int value in variable declaration\n')
            facts=public.classify_candidate_build_failures(raw,snapshot)
            assert facts
            if kind=='stale':facts[0]['snapshot_id']='old'
            trial.update(status='EXECUTION_FAILED',method_probe_status='NOT_RUN',candidate_build_failures=facts if kind!='unclassified' else [])
        return dict(observation=trial,feedback=dict(route='EXECUTE' if not index else 'STOP',
            to_snapshot=snapshot.snapshot_id,semantic_outcome='UNKNOWN',observation_diagnostics=public.public_failure_diagnostics(trial)),
            execution_mapping=[dict(path='main.go',new_text=target.read_text())])
    result=harness().continue_seeded_repair(source,['main.go'],initial,run({'retain':'PASS'},sid),
        max_rounds=rounds,guard_enabled=True,output=tmp_path/'rounds',attempt=attempt,preservation_ids=lambda f:['retain'])
    assert len(calls)==expected
    assert result['rounds'][0]['candidate_decision']['decision']=='UNCOMPARABLE'
    assert target.read_text()==('package main\nvar n int = 1\n' if expected==2 else seed)
    assert result['source_integrity_verified']
