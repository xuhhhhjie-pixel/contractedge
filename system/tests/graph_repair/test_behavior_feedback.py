"""Behaviour feedback: outcomes from evidence only, next actions, staleness, summary and the submit reminder."""
from pathlib import Path

from contractedge.graph_repair import behavior
from contractedge.graph_repair import behavior_checks as checks
from contractedge.graph_repair import behavior_feedback as feedback

FIXTURES = Path(__file__).with_name('gotest_fixtures') / 'go1.26'
TASK = ('On a successful upgrade the Released condition must be True. The Ready condition must also be True after a '
        'successful upgrade. Existing failure reporting must keep working.')
OFFERED = {'./status': ['TestReleased', 'TestReady', 'TestFailureReported']}
BASE = 'T0'


def recorded(name):
    return (FIXTURES / f'{name}.out').read_text(), int((FIXTURES / f'{name}.rc').read_text())


def setup():
    snap = behavior.empty()
    behavior.apply_proposals(snap, [
        {'kind': 'target', 'condition': 'successful upgrade', 'expected': 'Released=True',
         'source_refs': [{'source': 'task', 'quote': 'the Released condition must be True'}]},
        {'kind': 'target', 'condition': 'successful upgrade', 'expected': 'Ready=True',
         'source_refs': [{'source': 'task', 'quote': 'The Ready condition must also be True'}]},
        {'kind': 'preserve', 'condition': 'failed upgrade', 'expected': 'failure is reported',
         'source_refs': [{'source': 'task', 'quote': 'Existing failure reporting must keep working'}]},
    ], behavior.Sources(TASK, lambda p: None), 'test')
    checks.plan(snap, [{'obligations': ['O1'], 'package': './status', 'run': '^TestReleased$', 'scenario': 'released'},
                       {'obligations': ['O2'], 'package': './status', 'run': '^TestReady$', 'scenario': 'ready'},
                       {'obligations': ['O3'], 'package': './status', 'run': '^TestFailureReported$',
                        'scenario': 'failure'}], OFFERED, 120)
    return snap


def run(snap, cid, name, tree, tmp_path, *, after=None, code=None, output=None):
    out, rc = recorded(name) if name else (output, code)
    trees = [tree, after or tree]
    ev = checks.execute(snap['checks'][cid], snap, lambda c, t: {'output': out, 'returncode': rc if code is None else code},
                        lambda: trees.pop(0), 'img', tmp_path)
    return ev, feedback.reduce(snap, ev, after or tree, BASE)


def test_partial_pass_points_at_the_missing_half_then_shared_fix_rechecks_both(tmp_path):
    snap = setup()
    run(snap, 'C1', 'pass', 'T1', tmp_path)                       # Released passes on candidate P1
    _, bundle = run(snap, 'C2', 'fail', 'T1', tmp_path)           # Ready still fails
    o1, o2 = snap['obligations']['O1'], snap['obligations']['O2']
    assert behavior.outcome(o1, 'released', 'T1') == 'PASS_AT_SNAPSHOT'
    assert behavior.outcome(o2, 'ready', 'T1') == 'FAIL_AT_SNAPSHOT'
    assert [(a['type'], a['obligation_refs']) for a in bundle['next_actions']] == [('REPAIR', ['O2'])]
    assert 'Next: REPAIR O2' in bundle['text'] and 'Add(2, 2) = 4, want 5' in bundle['text']
    assert 'O1' not in bundle['text'].split('Next:')[1]
    # The shared helper is edited (P2): both earlier results are stale and both checks are due again.
    assert feedback.stale(snap, 'T1', 'T2') == ['O1', 'O2']
    assert behavior.outcome(o1, 'released', 'T2') == 'UNKNOWN'
    # Failing first, then never run, then previously passing.
    assert [c['id'] for c in checks.pending(snap, 'T2', 'img')] == ['C2', 'C3', 'C1']
    run(snap, 'C2', 'pass', 'T2', tmp_path)
    run(snap, 'C1', 'pass', 'T2', tmp_path)
    s = feedback.summary(snap, 'T2')
    assert [(p['id'], p['scenario']) for p in s['observed_pass']] == [('O1', 'released'), ('O2', 'ready')]
    assert s['failed'] == [] and [u['id'] for u in s['unknown']] == ['O3']     # never checked: unknown, not passing


def test_repeated_valid_failures_stay_failures(tmp_path):
    snap = setup()
    for tree in ('T1', 'T2', 'T3'):
        _, bundle = run(snap, 'C2', 'fail', tree, tmp_path)
        assert bundle['next_actions'][0]['type'] == 'REPAIR'
    o2 = snap['obligations']['O2']
    assert o2['grounding'] == 'GROUNDED' and behavior.outcome(o2, 'ready', 'T3') == 'FAIL_AT_SNAPSHOT'


def test_an_explanation_does_not_remove_a_failure(tmp_path):
    snap = setup()
    run(snap, 'C2', 'fail', 'T1', tmp_path)
    delta = behavior.apply_proposals(snap, [
        {'op': 'retract', 'id': 'O2', 'reason': 'this is the expected change'},
        {'op': 'revise', 'id': 'O2', 'expected': 'Ready may stay False', 'source_refs': []},
    ], behavior.Sources(TASK, lambda p: None), 'model')
    assert len(delta['rejected']) == 2
    assert behavior.outcome(snap['obligations']['O2'], 'ready', 'T1') == 'FAIL_AT_SNAPSHOT'
    assert feedback.summary(snap, 'T1')['failed'][0]['id'] == 'O2'


def test_environment_build_and_unclear_results_never_ask_for_production_repairs(tmp_path):
    snap = setup()
    _, bundle = run(snap, 'C2', 'rocache', 'T1', tmp_path)
    assert [a['type'] for a in bundle['next_actions']] == ['FIX_ENV']
    assert 'do not change production code' in bundle['text']
    _, bundle = run(snap, 'C2', 'build', 'T2', tmp_path)
    assert [a['type'] for a in bundle['next_actions']] == ['READ']
    _, bundle = run(snap, 'C2', None, 'T3', tmp_path, output='', code=124)
    assert [a['type'] for a in bundle['next_actions']] == ['DEFER']
    assert snap['obligations']['O2']['outcomes'] == {}


def test_a_preserve_failure_comes_first(tmp_path):
    snap = setup()
    snap['checks']['C1']['obligation_refs'].append({'id': 'O3', 'version': 1})
    _, bundle = run(snap, 'C1', 'fail', 'T1', tmp_path)
    nexts = [l for l in bundle['text'].splitlines() if l.startswith('Next:')]
    assert nexts[0].startswith('Next: REPAIR O3') and 'must be preserved' in nexts[0]


def test_calibration_on_the_original_version(tmp_path):
    snap = setup()
    _, bundle = run(snap, 'C1', 'pass', BASE, tmp_path)           # passes before any fix
    assert snap['checks']['C1']['baseline_result'] == 'PASS'
    assert bundle['next_actions'][0]['type'] == 'READ' and 'cannot show the target' in bundle['text']
    run(snap, 'C2', 'fail', BASE, tmp_path)                       # reproduces the defect
    run(snap, 'C2', 'pass', 'T1', tmp_path)
    assert snap['obligations']['O2']['outcomes']['ready']['calibrated'] is True
    _, bundle = run(snap, 'C3', 'fail', BASE, tmp_path)           # a preserve test failing before any edit
    assert bundle['next_actions'][0]['type'] == 'RESOLVE_CONFLICT'


def test_evidence_for_another_version_changes_nothing(tmp_path):
    snap = setup()
    _, bundle = run(snap, 'C2', 'fail', 'T1', tmp_path, after='T2')
    assert bundle['next_actions'][0]['type'] == 'CHECK' and snap['obligations']['O2']['outcomes'] == {}
    ev, _ = run(snap, 'C2', 'fail', 'T3', tmp_path)
    ev2 = dict(ev, id='E99')
    assert feedback.reduce(snap, ev2, 'T4', BASE)['next_actions'][0]['type'] == 'CHECK'


def test_a_revised_requirement_ignores_old_evidence(tmp_path):
    snap = setup()
    ev, _ = run(snap, 'C2', 'fail', 'T1', tmp_path)
    behavior.apply_proposals(snap, [{'op': 'revise', 'id': 'O2', 'expected': 'Ready=True after the upgrade',
                                     'source_refs': [{'source': 'task', 'quote': 'The Ready condition must also be True'}]}],
                             behavior.Sources(TASK, lambda p: None), 'test')
    assert feedback.reduce(snap, dict(ev, id='E50'), 'T1', BASE)['changes'] == []
    assert behavior.outcome(snap['obligations']['O2'], 'ready', 'T1') == 'UNKNOWN'


def test_the_same_failure_is_shown_once(tmp_path):
    snap = setup()
    ev, bundle = run(snap, 'C2', 'fail', 'T1', tmp_path)
    assert bundle['text']
    assert feedback.reduce(snap, dict(ev, id='E7'), 'T1', BASE)['text'] == ''


def test_submit_reminder_only_for_unseen_failures_and_only_once(tmp_path):
    snap = setup()
    assert feedback.submit_reminder(snap, 'T1') is None             # unknown requirements never block
    run(snap, 'C1', 'fail', 'T1', tmp_path)                         # shown in an observation already
    assert feedback.submit_reminder(snap, 'T1') is None
    ev = checks.execute(snap['checks']['C2'], snap, lambda c, t: {'output': recorded('fail')[0], 'returncode': 1},
                        lambda: 'T1', 'img', tmp_path)                # run during before_submit, not shown yet
    text = feedback.submit_reminder(snap, 'T1')
    assert text and 'O2' in text and 'TestWrong' in text and 'shown once' in text
    assert feedback.submit_reminder(snap, 'T1') is None
    assert ev['result'] == 'SEMANTIC_FAIL'


def test_candidate_rank_does_not_trade_a_regression_for_more_passes(tmp_path):
    snap = setup()
    run(snap, 'C1', 'pass', 'TA', tmp_path)
    run(snap, 'C2', 'pass', 'TA', tmp_path)
    run(snap, 'C3', 'fail', 'TA', tmp_path)                       # two targets pass but a preserve fails
    run(snap, 'C1', 'pass', 'TB', tmp_path)                       # one target passes, preserve unchecked
    assert feedback.rank(snap, 'TA') == (-1, 2) and feedback.rank(snap, 'TB') == (0, 1)
    assert max(['TA', 'TB'], key=lambda t: feedback.rank(snap, t)) == 'TB'
    s = feedback.summary(snap, 'TB')
    assert [u['id'] for u in s['unknown']] == ['O2', 'O3']        # unchecked preserve is unknown, not safe
