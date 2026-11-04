"""Independent association probes: priority is not evidence of a Go binding."""
import pytest

from contractedge.retrieval.candidates import _gap_local_context, build_candidate_set, extend_once
from contractedge.source.parsers import parse_source_units


def go(path, text):
    return parse_source_units(path, text, 'independent-snapshot')[0]


def association(root, *others):
    return _gap_local_context([root, *others], {root['id']},
        [{'id': 'G', 'locator_hint': {'path': 'worker', 'search_term': 'OptionFlag'}}])


def test_unique_free_call_remains_direct_context():
    root = go('worker/root.go', 'func Lookup() { lookupTarget() }\n')
    target = go('worker/target.go', 'func lookupTarget() {}\n')
    context, calls = association(root, target)
    assert target['id'] in context
    assert calls[root['id']] == {target['id']}


@pytest.mark.parametrize('text', [
    'func Lookup(lookupTarget func()) { lookupTarget() }\n',
    'func Lookup() { lookupTarget := remote.Call; lookupTarget() }\n',
    'func Lookup() { var lookupTarget = remote.Call; lookupTarget() }\n',
    'func Lookup() (lookupTarget func()) { lookupTarget(); return }\n',
    'func Lookup() { func(lookupTarget func()) { lookupTarget() }(remote.Call) }\n',
])
def test_shadowed_free_call_does_not_acquire_unrelated_body(text):
    root = go('worker/root.go', text)
    target = go('worker/target.go', 'func lookupTarget() {}\n')
    context, calls = association(root, target)
    assert target['id'] not in calls.get(root['id'], set())
    assert target['id'] not in context


def test_production_call_cannot_bind_test_only_declaration():
    root = go('worker/root.go', 'package worker\nfunc Lookup() { lookupTarget() }\n')
    target = go('worker/target_test.go', 'package worker_test\nfunc lookupTarget() {}\n')
    context, calls = association(root, target)
    assert target['id'] not in calls.get(root['id'], set())
    assert target['id'] not in context


def test_duplicate_free_declarations_are_not_resolved_arbitrarily():
    root = go('worker/root.go', 'func Lookup() { lookupTarget() }\n')
    one = go('worker/one.go', 'func lookupTarget() {}\n')
    two = go('worker/two.go', 'func lookupTarget() {}\n')
    context, calls = association(root, one, two)
    assert not {one['id'], two['id']} & set(calls.get(root['id'], ()))
    assert not {one['id'], two['id']} & context


def test_receiver_shadow_cannot_become_direct_call_preference():
    root = go('worker/root.go', 'func (v *Visitor) Lookup() { v := remote.New(); v.lookupTarget() }\n')
    target = go('worker/target.go', 'func (v *Visitor) lookupTarget() {}\n')
    _, calls = association(root, target)
    assert target['id'] not in calls.get(root['id'], set())


def test_wrong_receiver_does_not_resolve_same_method_name():
    root = go('worker/root.go', 'func (v *Visitor) Lookup() { v.lookupTarget() }\n')
    wrong = go('worker/target.go', 'func (v *Other) lookupTarget() {}\n')
    context, calls = association(root, wrong)
    assert wrong['id'] not in calls.get(root['id'], set())
    assert wrong['id'] not in context


def test_shadow_does_not_displace_task_configuration_in_actual_selection():
    root = go('worker/root.go', 'func Lookup(lookupTarget func()) { lookupTarget() }\n')
    target = go('worker/target.go', 'func lookupTarget() {}\n')
    task = go('config/task.go', 'type Config struct { OptionFlag bool }\n')
    initial = build_candidate_set('Preserve `OptionFlag`', None, [root, task], [],
        top_n=2, max_context_tokens=10000, token_counter=len)
    result = extend_once(initial,
        [{'id': 'G', 'locator_hint': {'path': 'worker', 'search_term': 'lookupTarget'}}],
        [target], max_context_tokens=10000, token_counter=len)
    assert task['id'] in result.selected_ids
    assert target['id'] not in result.selected_ids


def test_masked_comments_and_strings_do_not_add_a_call():
    root = go('worker/root.go', 'func Lookup() { /* lookupTarget() */ println("lookupTarget()") }\n')
    target = go('worker/target.go', 'func lookupTarget() {}\n')
    context, calls = association(root, target)
    assert target['id'] not in calls.get(root['id'], set())
    assert target['id'] not in context


def test_unrelated_same_receiver_peer_cannot_erase_exact_task_configuration():
    root = go('worker/root.go', 'func (w *Worker) Activate() {}\n')
    root.update(required_for_gap_delivery=True, gap_role_keys=['G:anchor'], evidence_gap_refs=['G'])
    peer = go('worker/metrics.go', 'func (w *Worker) ObserveMetrics() { recordMetric() }\n')
    task = go('config/task.go', 'type Config struct { OptionFlag bool }\n')
    initial = build_candidate_set('Preserve `OptionFlag`', None, [task], [],
        top_n=2, max_context_tokens=10000, token_counter=len)
    result = extend_once(initial,
        [{'id': 'G', 'locator_hint': {'path': 'worker', 'symbol': 'Activate'}}],
        [root, peer], max_context_tokens=10000, token_counter=len)
    assert set(result.selected_ids) == {root['id'], task['id']}


@pytest.mark.parametrize('selector', ['remote . lookupTarget()', 'remote.\n lookupTarget()'])
def test_spaced_external_selector_cannot_become_a_local_call(selector):
    root = go('worker/root.go', 'func Lookup() { ' + selector + ' }\n')
    target = go('worker/target.go', 'func lookupTarget() {}\n')
    context, calls = association(root, target)
    assert target['id'] not in calls.get(root['id'], set())
    assert target['id'] not in context


def test_spaced_external_constructor_is_not_a_local_owner_bridge():
    root = go('worker/root.go', 'func (v *Visitor) Lookup() {}\n')
    target = go('worker/remote.go', 'func Remote() { walk(remote . Visitor{}) }\n')
    context, _ = association(root, target)
    assert target['id'] not in context


def test_wrong_receiver_seed_does_not_count_as_delivered_direct_callee(tmp_path):
    from contractedge.retrieval.gap_delivery import locate_gap_roles
    from contractedge.source.snapshot import compute_snapshot

    text = ('package worker\n'
            'type Worker struct {}\n'
            'func (w *Worker) Activate() { w.Stop() }\n'
            'func (o *Other) Stop() {}\n'
            'func (w *Worker) Stop() { cleanup() }\n')
    (tmp_path / 'worker.go').write_text(text)
    snapshot = compute_snapshot(tmp_path)
    units = parse_source_units('worker.go', text, snapshot.snapshot_id)
    wrong = next(u for u in units if '(o *Other)' in u['text'])
    right = next(u for u in units if '(w *Worker) Stop' in u['text'])
    roles, _ = locate_gap_roles(snapshot,
        [{'id': 'G', 'locator_hint': {'path': 'worker.go', 'symbol': 'Activate'}}],
        {'seed_units': [wrong], 'max_new_files': 1})
    assert any(u['id'] == right['id'] and u.get('direct_local_callee_support') for u in roles)
