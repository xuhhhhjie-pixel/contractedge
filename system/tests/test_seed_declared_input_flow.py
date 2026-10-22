"""Seed feedback must validate the same published inputs as review/execution."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from contractedge.orchestration.feedback import make_feedback
from tests.test_observation_capability_gate import plan,claim,record


def harness():
    spec=importlib.util.spec_from_file_location('worker_v8',Path(__file__).parents[1]/'scripts/run_reliability_worker_v8.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.mark.parametrize('stage',['seed','continuation'])
@pytest.mark.parametrize('expected_input',['value: false\n',None])
def test_seed_and_continuation_preserve_comparison_input_without_fabrication(stage,expected_input):
    semantic=plan('DECLARED_INPUT_EQUALITY',comparison_requirements=[dict(artifact_id='INPUT',
        input_pointer='/value',output_pointer='/value')])
    # Only the public method input can supply expected values; a carried model
    # artifact must not override it or stand in for an absent input.
    semantic['declared_artifacts']=[dict(id='INPUT',content='value: true\n')]
    method=SimpleNamespace(declared_artifacts=([dict(id='INPUT',content=expected_input)] if expected_input else []))
    envelope=harness().seeded_review_envelope(semantic,method,{'public_execution_capabilities':{}},
        {'retained_constraints':[]} if stage=='continuation' else None)
    check=claim(comparisons=[dict(artifact_id='INPUT',input_pointer='/value',observation_id='OBS',output_pointer='/value')]);check['status']='FAIL'
    feedback=make_feedback(case_id='CASE',run_id='run',input_digest='a'*64,round_id=0,
        from_snapshot='S0',to_snapshot='S1',patch_digest='b'*64,execution_status='EXECUTION_PASS',
        observations=[record()],plan=envelope,review=dict(decision='FAIL',checks=[check],failure_kind='EXECUTION'))
    assert feedback['public_check_results'][0]['status']==('FAIL' if expected_input else 'UNKNOWN')
    assert feedback['route']==('EXECUTE' if expected_input else 'STOP')
    assert feedback['semantic_outcome']==('PUBLIC_REVIEW_FAIL' if expected_input else 'UNKNOWN')
    assert semantic['declared_artifacts'][0]['content']=='value: true\n'
