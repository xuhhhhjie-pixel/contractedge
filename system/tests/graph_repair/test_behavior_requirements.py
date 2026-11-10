"""Behaviour requirements: grounding, rejection of unsupported claims, versions and the snapshot."""
import json

import pytest

from contractedge.graph_repair import behavior

TASK = ('When the upgrade succeeds, the Released condition must be set to True with reason UpgradeSucceeded. '
        'The Ready condition must also be True after a successful upgrade.')
FILES = {'/testbed/pkg/status/status_test.go': 'func TestReleased(t *testing.T) {\n\t// Released stays False on failure\n}\n'}


def sources():
    return behavior.Sources(TASK, FILES.get)


def task_ref(quote):
    return [{'source': 'task', 'quote': quote}]


def add(snapshot, condition, expected, refs, kind='target', **extra):
    return behavior.apply_proposals(snapshot, [{'op': 'add', 'kind': kind, 'condition': condition,
                                                'expected': expected, 'source_refs': refs, **extra}], sources(), 'test')


def test_task_quote_grounds_a_requirement():
    snap = behavior.empty()
    delta = add(snap, 'upgrade succeeds', 'Released=True, reason UpgradeSucceeded',
                task_ref('the Released condition must be set to True with reason UpgradeSucceeded'))
    assert delta['added'] == ['O1']
    o1 = snap['obligations']['O1']
    assert o1['grounding'] == 'GROUNDED' and o1['version'] == 1 and o1['outcomes'] == {}
    assert o1['source_refs'][0]['content_sha256'] == behavior.sha(TASK)


def test_file_quote_is_verified_against_the_file():
    snap = behavior.empty()
    delta = add(snap, 'upgrade fails', 'Released stays False', [{'source': 'file', 'path': 'pkg/status/status_test.go',
                                                                  'quote': 'Released stays False on failure'}], kind='preserve')
    assert delta['added'] == ['O1']
    ref = snap['obligations']['O1']['source_refs'][0]
    assert ref['path'] == '/testbed/pkg/status/status_test.go' and ref['line'] == 2


@pytest.mark.parametrize('refs, why', [
    ([], 'no source_refs'),
    ([{'source': 'file', 'path': '/etc/passwd', 'quote': 'root:x:0:0:root'}], 'outside the allowed public sources'),
    ([{'source': 'file', 'path': '../fixed/status.go', 'quote': 'Ready must be True'}], 'outside the allowed public sources'),
    ([{'source': 'file', 'path': '.git/config', 'quote': 'repositoryformatversion'}], 'outside the allowed public sources'),
    ([{'source': 'file', 'path': 'pkg/missing.go', 'quote': 'Ready must be True'}], 'cannot be read'),
    ([{'source': 'task', 'quote': 'Ready must be True even when the upgrade is retried'}], 'quote not found'),
    ([{'source': 'oracle', 'quote': 'Ready must be True always'}], "source must be 'task' or 'file'"),
    ([{'source': 'task', 'quote': 'Ready'}], 'quote must be'),
])
def test_unsupported_requirements_stay_unresolved(refs, why):
    snap = behavior.empty()
    delta = add(snap, 'upgrade retried', 'Ready=True', refs)
    assert delta['unresolved'] == ['O1'] and delta['added'] == []
    o1 = snap['obligations']['O1']
    assert o1['grounding'] == 'UNRESOLVED' and o1['source_refs'] == []
    assert any(why in note for note in o1['grounding_notes'])
    assert behavior.active(snap) == []


def test_public_inputs_are_a_source_but_escapes_are_not():
    files = {'/inputs/requirement.md': 'The external-name value must not alternate between identifiers.\n'}
    src = behavior.Sources(TASK, files.get)
    ok, _ = src.check({'source': 'file', 'path': '/inputs/requirement.md', 'quote': 'must not alternate between identifiers'})
    assert ok and ok['path'] == '/inputs/requirement.md'
    for path in ('/inputs/../etc/passwd', '/inputsx/requirement.md', '/private/answer.md'):
        assert src.check({'source': 'file', 'path': path, 'quote': 'must not alternate between'})[0] is None


def test_pairing_does_not_ground_the_missing_half():
    """Released is quoted; a Ready requirement without its own quote is not grounded by the pair."""
    snap = behavior.empty()
    behavior.apply_proposals(snap, [
        {'kind': 'target', 'condition': 'upgrade succeeds', 'expected': 'Released=True',
         'source_refs': task_ref('the Released condition must be set to True')},
        {'kind': 'target', 'condition': 'upgrade succeeds', 'expected': 'Ready=True on retry as well',
         'source_refs': task_ref('the Released condition must be set to True')[:0]},
    ], sources(), 'test')
    assert [o['id'] for o in behavior.active(snap)] == ['O1']
    assert snap['obligations']['O2']['grounding'] == 'UNRESOLVED'


def test_declared_conflict_is_unresolved_even_with_a_quote():
    snap = behavior.empty()
    delta = add(snap, 'upgrade succeeds', 'Ready=True', task_ref('The Ready condition must also be True'),
                conflict='existing test TestReady expects Ready=False here')
    assert delta['unresolved'] == ['O1']
    assert 'conflict: existing test TestReady expects Ready=False here' in snap['obligations']['O1']['grounding_notes']


def test_duplicates_are_not_added_twice():
    snap = behavior.empty()
    refs = task_ref('The Ready condition must also be True')
    add(snap, 'upgrade succeeds', 'Ready=True', refs)
    delta = add(snap, 'Upgrade   succeeds', 'ready=true', refs)
    assert delta['duplicates'] == ['O1'] and len(snap['obligations']) == 1


def test_invalid_items_are_rejected_not_crashing():
    snap = behavior.empty()
    delta = behavior.apply_proposals(snap, [{'kind': 'maybe', 'condition': 'x', 'expected': 'y'}, 'text',
                                            {'op': 'merge'}, {'kind': 'target', 'condition': '', 'expected': 'y'}],
                                     sources(), 'test')
    assert len(delta['rejected']) == 4 and snap['obligations'] == {}
    assert behavior.apply_proposals(snap, 'not a list', sources(), 'test')['rejected'] == []


def test_semantic_revision_bumps_version_and_drops_outcomes():
    snap = behavior.empty()
    refs = task_ref('The Ready condition must also be True after a successful upgrade')
    add(snap, 'upgrade succeeds', 'Ready=True', refs)
    o1 = snap['obligations']['O1']
    o1['outcomes']['default'] = {'status': 'PASS_AT_SNAPSHOT', 'tree': 'T1', 'version': 1}
    o1['check_ids'] = ['C1']
    behavior.apply_proposals(snap, [{'op': 'revise', 'id': 'O1', 'expected': 'Ready=True after a successful upgrade',
                                     'source_refs': refs}], sources(), 'test')
    assert o1['version'] == 2 and o1['outcomes'] == {} and o1['check_ids'] == []
    assert behavior.outcome(o1, 'default', 'T1') == 'UNKNOWN'


def test_wording_only_revision_keeps_the_version():
    snap = behavior.empty()
    refs = task_ref('The Ready condition must also be True')
    add(snap, 'upgrade succeeds', 'Ready=True', refs)
    snap['obligations']['O1']['outcomes']['default'] = {'status': 'PASS_AT_SNAPSHOT', 'tree': 'T1', 'version': 1}
    behavior.apply_proposals(snap, [{'op': 'revise', 'id': 'O1', 'expected': 'Ready=True ', 'source_refs': refs}],
                             sources(), 'test')
    assert snap['obligations']['O1']['version'] == 1
    assert behavior.outcome(snap['obligations']['O1'], 'default', 'T1') == 'PASS_AT_SNAPSHOT'


def test_revision_and_retraction_need_public_support():
    snap = behavior.empty()
    add(snap, 'upgrade succeeds', 'Ready=True', task_ref('The Ready condition must also be True'))
    delta = behavior.apply_proposals(snap, [
        {'op': 'retract', 'id': 'O1', 'reason': 'the check keeps failing', 'source_refs': []},
        {'op': 'revise', 'id': 'O1', 'expected': 'Ready=False'},
        {'op': 'retract', 'id': 'O9', 'source_refs': task_ref('The Ready condition must also be True')},
    ], sources(), 'test')
    assert len(delta['rejected']) == 3 and snap['obligations']['O1']['grounding'] == 'GROUNDED'
    delta = behavior.apply_proposals(snap, [{'op': 'retract', 'id': 'O1', 'reason': 'task says otherwise',
                                             'source_refs': task_ref('Released condition must be set to True')}],
                                     sources(), 'test')
    assert delta['retracted'] == ['O1'] and snap['obligations']['O1']['grounding'] == 'RETRACTED'


def test_outcome_is_bound_to_tree_and_version():
    o = {'version': 2, 'outcomes': {'s': {'status': 'FAIL_AT_SNAPSHOT', 'tree': 'T2', 'version': 2}}}
    assert behavior.outcome(o, 's', 'T2') == 'FAIL_AT_SNAPSHOT'
    assert behavior.outcome(o, 's', 'T3') == 'UNKNOWN'
    assert behavior.outcome(o, 'other', 'T2') == 'UNKNOWN'
    o['version'] = 3
    assert behavior.outcome(o, 's', 'T2') == 'UNKNOWN'


def test_extraction_review_reports_a_missed_requirement():
    snap = behavior.empty()
    add(snap, 'upgrade succeeds', 'Released=True', task_ref('the Released condition must be set to True'))
    reference = ['the Released condition must be set to True', 'The Ready condition must also be True']
    assert behavior.missing_against(snap, reference) == ['The Ready condition must also be True']


def test_store_roundtrip_and_corruption(tmp_path):
    store = behavior.Store(tmp_path / 'b')
    assert store.load() == behavior.empty()
    snap = behavior.empty()
    add(snap, 'upgrade succeeds', 'Ready=True', task_ref('The Ready condition must also be True'))
    store.save(snap)
    assert store.load() == snap and [p.name for p in (tmp_path / 'b').iterdir()] == ['behavior-state.json']
    store.path.write_text('{"schema": "other"}')
    with pytest.raises(behavior.StateError):
        store.load()
    store.path.write_text('{broken')
    with pytest.raises(behavior.StateError):
        store.load()
    assert store.path.read_text() == '{broken'          # never silently reset
    store.log('x', a=1)
    assert json.loads(store.events.read_text())['event'] == 'x'
