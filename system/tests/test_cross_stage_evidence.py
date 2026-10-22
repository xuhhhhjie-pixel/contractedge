import hashlib
from pathlib import Path

from contractedge.execution import helper_context
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def fixture(root):
    (root/'go.mod').write_text('module example.org/demo\n')
    (root/'worker').mkdir()
    (root/'worker/state.go').write_text('package worker\nfunc NewWorker() *Worker { return &Worker{} }\n'
        'func (w *Worker) Run() { w.initialize() }\n'
        'func (w *Worker) initialize() { w.groups = empty() }\n'
        'func (w *Worker) Groups() { returnGroups() }\n')
    (root/'main.go').write_text('package main\nimport "example.org/demo/worker"\n'
        'func serve() {\n w := worker.NewWorker()\n go w.Run()\n w.Groups()\n}\n')
    snapshot = compute_snapshot(root)
    seed = parse_source_units('main.go', (root/'main.go').read_text(), snapshot.snapshot_id)
    return snapshot, seed


def test_preplan_source_lookup_delivers_current_lifecycle_without_accepted_plan(tmp_path):
    snapshot, seed = fixture(tmp_path)
    units, audit = helper_context.source_lifecycle_units(snapshot, seed)
    text = '\n'.join(u['text'] for u in units)
    assert 'w.groups = empty()' in text and 'func NewWorker()' in text
    assert all(u['snapshot_id'] == snapshot.snapshot_id for u in units)
    assert all(hashlib.sha256(u['text'].encode()).hexdigest() == u['content_digest'] for u in units)
    assert len(units) <= 6 and len({u['path'] for u in units}) <= 4
    assert all(u['required_for_gap_delivery'] for u in units)


def test_rediagnosis_rereads_current_source_instead_of_reusing_old_body(tmp_path):
    old, seed = fixture(tmp_path)
    before, _ = helper_context.source_lifecycle_units(old, seed)
    target = tmp_path/'worker/state.go'
    target.write_text(target.read_text().replace('empty()', 'restored()'))
    current = compute_snapshot(tmp_path)
    fresh_seed = parse_source_units('main.go', (tmp_path/'main.go').read_text(), current.snapshot_id)
    after, _ = helper_context.source_lifecycle_units(current, fresh_seed)
    assert before and after
    assert all(u['snapshot_id'] == current.snapshot_id for u in after)
    assert 'restored()' in '\n'.join(u['text'] for u in after)
    assert 'empty()' not in '\n'.join(u['text'] for u in after)
    stale, audit = helper_context.source_lifecycle_units(current, seed)
    assert stale == [] and any(a['result'] == 'STALE_SOURCE_SEED' for a in audit)


def test_config_and_nonasync_paths_do_not_acquire_lifecycle_requirements(tmp_path):
    snapshot, seed = fixture(tmp_path)
    p = tmp_path/'main.go';p.write_text(p.read_text().replace('go w.Run()', 'w.Run()'))
    snapshot = compute_snapshot(tmp_path)
    seed = parse_source_units('main.go', p.read_text(), snapshot.snapshot_id)
    units, _ = helper_context.source_lifecycle_units(snapshot, seed)
    assert units == []


def test_missing_local_package_is_audited_without_stale_evidence(tmp_path):
    _, _ = fixture(tmp_path)
    (tmp_path/'worker/state.go').unlink()
    snapshot = compute_snapshot(tmp_path)
    seed = parse_source_units('main.go', (tmp_path/'main.go').read_text(), snapshot.snapshot_id)
    units, audit = helper_context.source_lifecycle_units(snapshot, seed)
    assert not units
    assert audit


def test_callee_changed_after_snapshot_is_not_relabelled_as_fresh(tmp_path):
    snapshot, seed = fixture(tmp_path)
    target = tmp_path/'worker/state.go'
    target.write_text(target.read_text().replace('empty()', 'unverified()'))
    units, audit = helper_context.source_lifecycle_units(snapshot, seed)
    assert not units
    assert any(row['result']=='SOURCE_CHANGED_AFTER_SNAPSHOT' for row in audit)


def test_planning_keeps_fresh_readonly_support_without_inventing_editable_targets():
    import json
    from types import SimpleNamespace
    from contractedge.changeset.prompt import build_changeset_request
    from contractedge.contracts.models import RunContext
    text = 'func Run() { initialize() }\n'
    evidence = dict(id='SRC-helper', kind='SOURCE_SPAN', snapshot_id='S1',
        content_digest=hashlib.sha256(text.encode()).hexdigest(), excerpt=text,
        locator=dict(path='state.go', start_line=1, end_line=1))
    context = RunContext('run', 'case', 1, 'S1', 'S0', 'input',
        retrieval_config={'necessary_source_support': [evidence]})
    request = build_changeset_request(SimpleNamespace(raw={}), context)
    body = json.loads(request.messages[1]['content'])
    assert body['read_only_source_support'] == [evidence]
    assert body['existing_target_paths'] == []
    assert 'readiness' in request.messages[0]['content'].lower()


def test_planning_rejects_support_from_old_snapshot():
    import pytest
    from types import SimpleNamespace
    from contractedge.changeset.prompt import build_changeset_request
    from contractedge.contracts.models import RunContext
    context = RunContext('run', 'case', 1, 'S1', 'S0', 'input',
        retrieval_config={'necessary_source_support': [dict(id='x', snapshot_id='S0', excerpt='old')]})
    with pytest.raises(ValueError, match='source support'):
        build_changeset_request(SimpleNamespace(raw={}), context)
