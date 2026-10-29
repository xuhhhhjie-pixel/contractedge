"""The real isolation panel must bind the current finite fixture protocol."""
import importlib.util
from pathlib import Path

import pytest


def panel():
    path = Path(__file__).resolve().parents[1] / 'scripts/run_trial_isolation_panel.py'
    spec = importlib.util.spec_from_file_location('trial_panel_dispatch_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def calls(dialect):
    return [{'prompt_version': version} for version in [
        'diagnosis.v1.1.24', 'changeset.v1.1.14',
        'topomen-source-execution.v1' if dialect == 'topomen-native'
        else 'modify-only-literal-execution.v1']]


@pytest.mark.parametrize('dialect', ['topomen-native', 'modify-only'])
def test_actual_current_fixture_dispatch_reaches_intended_fault_boundary(tmp_path, dialect):
    result = panel().validate_review_path(tmp_path, 'after-applied', dialect, calls(dialect))
    assert result['review'] == 'NOT_REACHED_AT_FAULT_BOUNDARY'
    assert result['model_review_dispatches'] == 0
    assert result['actual_prompt_versions'] == [item['prompt_version'] for item in calls(dialect)]


@pytest.mark.parametrize('mutation', ['old-planner', 'pre-registry-planner', 'reordered', 'extra-review', 'wrong-consumer'])
def test_dispatch_binding_still_rejects_stale_or_unexpected_calls(tmp_path, mutation):
    actual = calls('topomen-native')
    if mutation == 'old-planner':
        actual[1]['prompt_version'] = 'changeset.v1.1.10'
    elif mutation == 'pre-registry-planner':
        actual[1]['prompt_version'] = 'changeset.v1.1.12'
    elif mutation == 'reordered':
        actual[0], actual[1] = actual[1], actual[0]
    elif mutation == 'extra-review':
        actual.append({'prompt_version': 'public-postcondition-review.v2'})
    else:
        actual[2]['prompt_version'] = 'modify-only-literal-execution.v1'
    with pytest.raises(RuntimeError, match='dispatch sequence differs'):
        panel().validate_review_path(tmp_path, 'after-applied', 'topomen-native', actual)
