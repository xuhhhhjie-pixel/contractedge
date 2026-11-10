"""Behaviour checks: planning limits, the go test -json adapter on recorded real output, identities."""
import shlex
from pathlib import Path

import pytest

from contractedge.graph_repair import behavior
from contractedge.graph_repair import behavior_checks as checks

FIXTURES = Path(__file__).with_name('gotest_fixtures')
VERSIONS = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
TASK = 'Add must return the sum of both arguments. Add of two and two must be five in the patched version.'
OFFERED = {'./calc': ['TestAdd', 'TestWrong', 'TestTable'], './status': ['TestReady']}


def recorded(version, name):
    return (FIXTURES / version / f'{name}.out').read_text(), int((FIXTURES / version / f'{name}.rc').read_text())


def snapshot_with(*quotes):
    snap = behavior.empty()
    behavior.apply_proposals(snap, [{'kind': 'target', 'condition': f'case {n}', 'expected': q,
                                     'source_refs': [{'source': 'task', 'quote': q}]}
                                    for n, q in enumerate(quotes)], behavior.Sources(TASK, lambda p: None), 'test')
    return snap


def test_fixtures_cover_two_go_versions():
    assert VERSIONS == ['go1.23', 'go1.26']


@pytest.mark.parametrize('version', VERSIONS)
@pytest.mark.parametrize('name, result, detail', [
    ('pass', 'PASS', '1 test(s) passed'),
    ('fail', 'SEMANTIC_FAIL', '1 failing test(s)'),
    ('subtest', 'SEMANTIC_FAIL', '2 failing test(s)'),
    ('panic', 'SEMANTIC_FAIL', '1 failing test(s)'),
    ('skip', 'INVALID_CHECK', 'only skipped tests'),
    ('nomatch', 'INVALID_CHECK', 'no test was selected'),
    ('notests', 'INVALID_CHECK', 'no test was selected'),
    ('nomod', 'INVALID_CHECK', 'go.mod file not found'),
    ('build', 'BUILD_FAIL', 'the package does not build'),
    ('rocache', 'INFRA_ERROR', 'failed to initialize build cache'),
    ('notfound', 'INFRA_ERROR', 'runner exit 127'),
])
def test_adapter_on_recorded_go_output(version, name, result, detail):
    output, code = recorded(version, name)
    reading = checks.classify(output, code)
    assert reading['result'] == result and detail in reading['detail']


@pytest.mark.parametrize('version', VERSIONS)
def test_failure_details_come_from_the_assertions(version):
    reading = checks.classify(*recorded(version, 'fail'))
    assert reading['failing'] == ['TestWrong'] and 'Add(2, 2) = 4, want 5' in reading['excerpt']
    reading = checks.classify(*recorded(version, 'subtest'))
    assert reading['failing'] == ['TestTable', 'TestTable/bad'] and reading['tests']['TestTable/ok'] == 'pass'
    reading = checks.classify(*recorded(version, 'build'))
    assert 'undefined: undefinedName' in reading['excerpt']


@pytest.mark.parametrize('version', VERSIONS)
def test_exit_code_alone_decides_nothing(version):
    failing, _ = recorded(version, 'fail')
    assert checks.classify(failing, 0)['result'] == 'SEMANTIC_FAIL'        # a masked exit code
    passing, _ = recorded(version, 'pass')
    assert checks.classify(passing, 1)['result'] == 'UNCLASSIFIED'
    assert checks.classify('PASS\nok  \texample.com/m/calc\t0.1s\n', 0)['result'] == 'UNCLASSIFIED'
    assert checks.classify('', 0)['result'] == 'UNCLASSIFIED'
    assert checks.classify(passing, 0, timed_out=True)['result'] == 'TIMEOUT'
    assert checks.classify('', 124)['result'] == 'TIMEOUT'
    assert checks.classify('ce-run: runtime unavailable', 125)['result'] == 'INFRA_ERROR'


@pytest.mark.parametrize('item, why', [
    ({'obligations': ['O1'], 'package': './calc', 'run': '^TestAdd$', 'cmd': 'grep -q Add calc/calc.go'}, 'no free commands'),
    ({'obligations': ['O1'], 'package': './calc', 'run': 'TestAdd | true'}, 'outside a test-name regular expression'),
    ({'obligations': ['O1'], 'package': './calc', 'run': "TestAdd'; rm -rf /work; '"}, 'outside a test-name'),
    ({'obligations': ['O1'], 'package': '../calc', 'run': '^TestAdd$'}, 'must look like ./dir'),
    ({'obligations': ['O1'], 'package': './calc/../../etc', 'run': '^TestAdd$'}, 'must look like ./dir'),
    ({'obligations': ['O1'], 'package': '/work/calc', 'run': '^TestAdd$'}, 'must look like ./dir'),
    ({'obligations': ['O1'], 'package': './other', 'run': '^TestAdd$'}, 'was not offered'),
    ({'obligations': ['O1'], 'package': './calc', 'run': '^TestInvented$'}, 'matches none of the tests'),
    ({'obligations': ['O1'], 'package': './calc', 'run': '^Test(Add$'}, 'does not compile'),
    ({'obligations': ['O9'], 'package': './calc', 'run': '^TestAdd$'}, 'not a grounded obligation'),
    ({'obligations': [], 'package': './calc', 'run': '^TestAdd$'}, 'obligations are required'),
])
def test_unsafe_or_unsupported_checks_are_rejected(item, why):
    snap = snapshot_with('Add must return the sum of both arguments')
    delta = checks.plan(snap, [item], OFFERED, 120)
    assert delta['added'] == [] and why in delta['rejected'][0]['reason']


def test_unresolved_obligation_cannot_get_a_check():
    snap = behavior.empty()
    behavior.apply_proposals(snap, [{'kind': 'target', 'condition': 'x', 'expected': 'Add returns 5',
                                     'source_refs': [{'source': 'task', 'quote': 'not in the task text at all'}]}],
                             behavior.Sources(TASK, lambda p: None), 'test')
    delta = checks.plan(snap, [{'obligations': ['O1'], 'package': './calc', 'run': '^TestAdd$'}], OFFERED, 120)
    assert 'not a grounded obligation' in delta['rejected'][0]['reason']


def test_command_is_built_and_quoted_by_the_program():
    spec = {'kind': 'go_test', 'package': './calc', 'run': '^TestTable$/^bad$'}
    argv = shlex.split(checks.command(spec))
    assert argv[:3] == ['ce-run', '/bin/sh', '-c']
    assert shlex.split(argv[3]) == ['cd', '/work', '&&', 'go', 'test', '-json', '-count=1', '-run',
                                    '^TestTable$/^bad$', './calc']


def test_same_definition_is_one_check_with_several_links():
    snap = snapshot_with('Add must return the sum of both arguments', 'Add of two and two must be five')
    delta = checks.plan(snap, [{'obligations': ['O1'], 'package': './calc', 'run': '^TestAdd$'},
                               {'obligations': ['O2'], 'package': './calc', 'run': '^TestAdd$'}], OFFERED, 120)
    assert delta['added'] == ['C1'] and delta['linked'] == [['C1', 'O1'], ['C1', 'O2']]
    assert [r['id'] for r in snap['checks']['C1']['obligation_refs']] == ['O1', 'O2']
    assert snap['obligations']['O2']['check_ids'] == ['C1']


class Runner:
    def __init__(self, output, code, trees, timed_out=False):
        self.output, self.code, self.trees, self.timed_out, self.commands = output, code, list(trees), timed_out, []

    def run(self, command, timeout):
        self.commands.append((command, timeout))
        return {'output': self.output, 'returncode': self.code, **({'timed_out': True} if self.timed_out else {})}

    def tree(self):
        return self.trees.pop(0)


def planned():
    snap = snapshot_with('Add must return the sum of both arguments')
    checks.plan(snap, [{'obligations': ['O1'], 'package': './calc', 'run': '^TestAdd$'}], OFFERED, 120)
    return snap, snap['checks']['C1']


def test_evidence_is_bound_to_tree_definition_and_environment(tmp_path):
    snap, check = planned()
    assert [c['id'] for c in checks.pending(snap, 'T1', 'img@1')] == ['C1']
    runner = Runner(*recorded('go1.26', 'pass'), trees=['T1', 'T1'])
    ev = checks.execute(check, snap, runner.run, runner.tree, 'img@1', tmp_path)
    assert ev['result'] == 'PASS' and ev['identity'] == {'tree_before': 'T1', 'tree_after': 'T1',
                                                          'def_hash': check['def_hash'], 'env': 'img@1'}
    assert ev['obligation_versions'] == {'O1': 1} and Path(ev['log_ref']).read_text().startswith('{')
    assert runner.commands[0][1] == 120 and snap['counters']['check_runs'] == 1
    assert checks.pending(snap, 'T1', 'img@1') == []                     # cached for the same identity
    assert [c['id'] for c in checks.pending(snap, 'T2', 'img@1')] == ['C1']   # source or fixture edited
    assert [c['id'] for c in checks.pending(snap, 'T1', 'img@2')] == ['C1']   # runtime changed
    check['def_hash'] = 'changed-definition'
    assert [c['id'] for c in checks.pending(snap, 'T1', 'img@1')] == ['C1']


def test_a_revised_obligation_unlinks_old_checks():
    snap, _ = planned()
    behavior.apply_proposals(snap, [{'op': 'revise', 'id': 'O1', 'expected': 'Add returns the sum',
                                     'source_refs': [{'source': 'task', 'quote': 'Add must return the sum'}]}],
                             behavior.Sources(TASK, lambda p: None), 'test')
    assert checks.pending(snap, 'T1', 'img') == []


def test_source_change_during_the_check_invalidates_only_that_run(tmp_path):
    snap, check = planned()
    runner = Runner(*recorded('go1.26', 'pass'), trees=['T1', 'T2'])
    ev = checks.execute(check, snap, runner.run, runner.tree, 'img', tmp_path)
    assert ev['result'] == 'INVALID_CHECK' and 'changed while the check ran' in ev['detail']
    assert check['status'] == 'ACTIVE'
    runner = Runner(*recorded('go1.26', 'pass'), trees=[None, 'T1'])
    assert checks.execute(check, snap, runner.run, runner.tree, 'img', tmp_path)['result'] == 'UNCLASSIFIED'


def test_an_invalid_definition_is_not_run_again(tmp_path):
    snap, check = planned()
    runner = Runner(*recorded('go1.23', 'nomatch'), trees=['T1', 'T1'])
    assert checks.execute(check, snap, runner.run, runner.tree, 'img', tmp_path)['result'] == 'INVALID_CHECK'
    assert check['status'] == 'INVALID' and checks.pending(snap, 'T2', 'img') == []


def test_timeouts_are_recorded_and_not_cached_as_results(tmp_path):
    snap, check = planned()
    runner = Runner('', 124, trees=['T1', 'T1'], timed_out=True)
    ev = checks.execute(check, snap, runner.run, runner.tree, 'img', tmp_path)
    assert ev['result'] == 'TIMEOUT' and check['status'] == 'ACTIVE'


def test_failing_checks_are_rerun_first():
    snap = snapshot_with('Add must return the sum of both arguments', 'Add of two and two must be five')
    checks.plan(snap, [{'obligations': ['O1'], 'package': './calc', 'run': '^TestAdd$'},
                       {'obligations': ['O2'], 'package': './calc', 'run': '^TestWrong$'}], OFFERED, 120)
    snap['obligations']['O2']['outcomes']['^TestWrong$'] = {'status': 'FAIL_AT_SNAPSHOT', 'tree': 'T0', 'version': 1}
    assert [c['id'] for c in checks.pending(snap, 'T1', 'img')] == ['C2', 'C1']
