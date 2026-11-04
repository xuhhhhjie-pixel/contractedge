"""Original requirement and current source bind the optional timeout guard."""
import pytest

from contractedge.execution.topomen import PlanRejected
from tests.test_task_rule_scope import execute_edit, OPTIONS, OPTIONS_CALL, FIXED


def test_options_timeout_guard_rejects_fixed_background_deadline(tmp_path):
    with pytest.raises(PlanRejected, match='TIMEOUT_CONTRACT_VIOLATION'):
        execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10), old=OPTIONS_CALL,
                     new=FIXED + '\n' + OPTIONS_CALL)


def test_options_timeout_guard_does_not_invent_a_carrier_type_contract(tmp_path):
    # The old global *time.Duration ban was not supported by original source
    # binding. Type correctness belongs to the source contract and public build.
    result, actual = execute_edit(tmp_path, requirement=OPTIONS,
        old='func metrics(ctx context.Context)',
        new='func metrics(ctx context.Context, optional *time.Duration)')
    assert result['status'] == 'APPLIED' and 'optional *time.Duration' in actual


def test_options_timeout_guard_accepts_configured_caller_context(tmp_path):
    result, _ = execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10), old=OPTIONS_CALL,
        new='    ctx, cancel := context.WithTimeout(ctx, timeout)\n    defer cancel()\n' + OPTIONS_CALL)
    assert result['status'] == 'APPLIED'

