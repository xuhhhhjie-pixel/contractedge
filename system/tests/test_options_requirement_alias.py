"""OPTIONS checks require the stated obligation and actual stdlib binding."""
import pytest

from contractedge.execution.topomen import PlanRejected
from tests.test_task_rule_scope import execute_edit, GO_SOURCE, OPTIONS, OPTIONS_CALL


TIMEOUT_ONLY = 'The HTTP OPTIONS timeout must be configurable.'
CONFIGURED_BACKGROUND = ('    ctx, cancel := context.WithTimeout(context.Background(), timeout)\n'
                         '    defer cancel()\n' + OPTIONS_CALL)


def test_configurable_timeout_does_not_add_an_unstated_caller_context_contract(tmp_path):
    result, actual = execute_edit(tmp_path, requirement=TIMEOUT_ONLY, locator=(7, 10),
                                 old=OPTIONS_CALL, new=CONFIGURED_BACKGROUND)
    assert result['status'] == 'APPLIED' and CONFIGURED_BACKGROUND in actual


def test_explicit_caller_context_obligation_rejects_configured_background(tmp_path):
    with pytest.raises(PlanRejected, match='TIMEOUT_CONTRACT_VIOLATION'):
        execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10),
                     old=OPTIONS_CALL, new=CONFIGURED_BACKGROUND)
    assert (tmp_path / 'main.go').read_text() == GO_SOURCE


def test_negated_caller_context_clause_does_not_activate_background_ban(tmp_path):
    requirement = TIMEOUT_ONLY[:-1] + ' and need not preserve the caller context.'
    result, _ = execute_edit(tmp_path, requirement=requirement, locator=(7, 10),
                             old=OPTIONS_CALL, new=CONFIGURED_BACKGROUND)
    assert result['status'] == 'APPLIED'


def aliased_source():
    return GO_SOURCE.replace('"context"', 'ctxpkg "context"').replace('"time"', 'tm "time"').replace(
        'context.', 'ctxpkg.').replace('time.', 'tm.')


@pytest.mark.parametrize('duration,parent', [('3*tm.Second', 'ctx'), ('timeout', 'ctxpkg.Background()')])
def test_standard_library_alias_cannot_bypass_an_explicit_contract(tmp_path, duration, parent):
    source = aliased_source()
    change = f'    ctx, cancel := ctxpkg.WithTimeout({parent}, {duration})\n    defer cancel()\n' + OPTIONS_CALL
    with pytest.raises(PlanRejected, match='TIMEOUT_CONTRACT_VIOLATION'):
        execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10), source=source,
                     old=OPTIONS_CALL, new=change)
    assert (tmp_path / 'main.go').read_text() == source


def test_standard_library_alias_accepts_configured_caller_timeout(tmp_path):
    change = '    ctx, cancel := ctxpkg.WithTimeout(ctx, timeout)\n    defer cancel()\n' + OPTIONS_CALL
    result, _ = execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10), source=aliased_source(),
                             old=OPTIONS_CALL, new=change)
    assert result['status'] == 'APPLIED'


def test_unrelated_package_named_context_does_not_inherit_standard_library_semantics(tmp_path):
    source = GO_SOURCE.replace('"context"', '"example.test/context"')
    result, _ = execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10), source=source,
                             old=OPTIONS_CALL, new=CONFIGURED_BACKGROUND)
    assert result['status'] == 'APPLIED'


def test_locally_shadowed_context_does_not_inherit_standard_library_semantics(tmp_path):
    change = '    context := customContextFactory()\n' + CONFIGURED_BACKGROUND
    result, _ = execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10),
                             old=OPTIONS_CALL, new=change)
    assert result['status'] == 'APPLIED'
