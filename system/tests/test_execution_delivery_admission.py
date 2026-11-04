"""Independent source-delivery and budget counterexamples; no provider calls."""
import hashlib
import json

import pytest

from contractedge.execution import helper_context, topomen
from contractedge.source.snapshot import compute_snapshot


def go_imports(root, count=9):
    (root / 'go.mod').write_text('module example.org/review\n')
    (root / 'helpers').mkdir()
    (root / 'helpers/helper.go').write_text('package helpers\n' + ''.join(
        f'func H{i}() {{ marker{i}() }}\n' for i in range(count)))
    text = ('package review\nimport h "example.org/review/helpers"\nfunc Run() { '
            + '; '.join(f'h.H{i}()' for i in range(count)) + ' }\n')
    (root / 'main.go').write_text(text)
    return [dict(path='main.go', content=text)], [dict(target=dict(
        path='main.go', locator=dict(start_line=3, end_line=3)))]


def plan_for(root, operations):
    snapshot = compute_snapshot(root)
    target = operations[0]['target']
    return dict(schema_version='contractedge.topomen_plan.v1', status='READY',
        snapshot_id=snapshot.snapshot_id, input_digest='independent-test',
        operations=[dict(change_id='C1', target={**target, 'kind': 'EXISTING',
            'snapshot_id': snapshot.snapshot_id,
            'content_digest': snapshot.file_digests[target['path']]},
            operation='MODIFY', depends_on=[], semantic_action='Preserve call contracts',
            intent='Update caller', postcondition_refs=['P1'], violation_refs=['V1'])],
        postconditions=[dict(id='P1', statement='Caller preserves required behavior')],
        preserve_constraints=[], open_obligations=[])


def request_body(root, plan, **config):
    effective = dict(model='test', provider='openai_compatible', max_context_tokens=100000,
        execution_max_output_tokens=1000, execution_helper_support_enabled=True,
        max_execution_support_bytes=24000)
    effective.update(config)
    request = topomen.build_execution_request(plan, root,
        {'allowed_paths': [plan['operations'][0]['target']['path']]}, len, effective,
        requirement='Preserve the caller and helper contracts')
    return json.loads(request.messages[1]['content'])


def test_existing_eight_records_do_not_use_new_helper_slots(tmp_path):
    targets, operations = go_imports(tmp_path)
    records, _ = helper_context.local_helper_support(compute_snapshot(tmp_path), targets, operations)
    assert len(records) == 8
    plan = plan_for(tmp_path, operations)
    plan['coordination_context'] = dict(source_support=records)
    body = request_body(tmp_path, plan)
    delivered = body['read_only_source_support']
    assert len(delivered) == 9
    assert any('marker8()' in r['excerpt'] for r in delivered)
    assert all(delivered.count(r) == 1 for r in records)
    assert body['accepted_plan']['operations'] == plan['operations']


def test_existing_record_does_not_consume_remaining_byte_budget_again(tmp_path):
    targets, operations = go_imports(tmp_path, count=2)
    records, _ = helper_context.local_helper_support(compute_snapshot(tmp_path), targets, operations)
    assert len(records) == 2
    plan = plan_for(tmp_path, operations)
    plan['coordination_context'] = dict(source_support=records[:1])
    limit = sum(len(r['excerpt'].encode()) for r in records)
    body = request_body(tmp_path, plan, max_execution_support_bytes=limit)
    assert len(body['read_only_source_support']) == 2
    assert sum(len(r['excerpt'].encode()) for r in body['read_only_source_support']) == limit


def test_complete_target_methods_do_not_hide_imported_helper(tmp_path):
    (tmp_path / 'go.mod').write_text('module example.org/review\n')
    (tmp_path / 'helpers').mkdir()
    (tmp_path / 'helpers/helper.go').write_text('package helpers\nfunc Missing() { uniqueMissingContract() }\n')
    text = ('package review\nimport h "example.org/review/helpers"\ntype Processor struct{}\n'
            'func (p *Processor) Run() { ' + '; '.join(f'p.H{i}()' for i in range(8)) + '; h.Missing() }\n'
            + ''.join(f'func (p *Processor) H{i}() {{ local{i}() }}\n' for i in range(8)))
    (tmp_path / 'main.go').write_text(text)
    operations = [dict(target=dict(path='main.go', locator=dict(start_line=4, end_line=4)))]
    body = request_body(tmp_path, plan_for(tmp_path, operations))
    assert body['current_target_files'][0]['content'] == text
    assert len(body['read_only_source_support']) == 1
    assert 'uniqueMissingContract()' in body['read_only_source_support'][0]['excerpt']


def test_new_helper_count_remains_eight_without_previous_delivery(tmp_path):
    targets, operations = go_imports(tmp_path, count=12)
    body = request_body(tmp_path, plan_for(tmp_path, operations))
    assert len(body['read_only_source_support']) == 8


@pytest.mark.parametrize('slots', [0, 1, 6])
def test_remaining_helper_slots_are_enforced_before_return(tmp_path, slots):
    targets, operations = go_imports(tmp_path, count=10)
    records, _ = helper_context.local_helper_support(compute_snapshot(tmp_path), targets, operations,
        max_records=slots)
    assert len(records) == slots


def test_excluded_records_do_not_waive_new_helper_byte_limit(tmp_path):
    targets, operations = go_imports(tmp_path, count=2)
    records, audit = helper_context.local_helper_support(compute_snapshot(tmp_path), targets, operations,
        max_bytes=1, already_supplied=lambda record: 'marker0()' in record['excerpt'])
    assert records == []
    assert any(row.get('result') == 'ALREADY_DELIVERED_HELPER' for row in audit)
    assert any(row.get('result') == 'READ_ONLY_SUPPORT_BUDGET' for row in audit)


def test_invalid_prior_digest_does_not_suppress_fresh_helper(tmp_path):
    targets, operations = go_imports(tmp_path, count=1)
    records, _ = helper_context.local_helper_support(compute_snapshot(tmp_path), targets, operations)
    plan = plan_for(tmp_path, operations)
    plan['coordination_context'] = dict(source_support=[{**records[0], 'content_digest': '0' * 64}])
    delivered = request_body(tmp_path, plan)['read_only_source_support']
    assert delivered == records


@pytest.mark.parametrize('returned', ['p *Other', 'n int, p *Other'])
def test_closure_named_return_shadows_receiver(tmp_path, returned):
    (tmp_path / 'go.mod').write_text('module example.org/review\n')
    text = ('package review\ntype Processor struct{}\n'
            'func (p *Processor) Run() {\n'
            f' f := func() ({returned}) {{ p.refresh(); return }}\n _ = f\n}}\n'
            'func (p *Processor) refresh() { wrongOwnerContract() }\n')
    (tmp_path / 'main.go').write_text(text)
    records, _ = helper_context.local_go_helper_support(compute_snapshot(tmp_path),
        [dict(path='main.go', content=text)],
        [dict(target=dict(path='main.go', locator=dict(start_line=3, end_line=6)))])
    assert not any('wrongOwnerContract()' in r['excerpt'] for r in records)


def test_execution_rejects_snapshot_change_during_context_construction(tmp_path, monkeypatch):
    _, operations = go_imports(tmp_path, count=1)
    plan = plan_for(tmp_path, operations)
    original = topomen.compute_snapshot
    calls = 0

    def changed_snapshot(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            path = tmp_path / 'main.go'
            path.write_text(path.read_text() + '// source changed after acceptance\n')
        return original(root)

    monkeypatch.setattr(topomen, 'compute_snapshot', changed_snapshot)
    with pytest.raises(OSError, match='snapshot') as error:
        request_body(tmp_path, plan)
    from contractedge.contracts.failures import classify_exception
    status, record = classify_exception(error.value, stage='execution_context')
    assert status == 'INFRASTRUCTURE_ERROR'
    assert record['failure_class'] == 'infrastructure'
    assert record['dispatch_state'] == 'not_sent'


def python_imports(root, count=1):
    (root / 'pkg').mkdir()
    text = 'from pkg.helpers import ' + ', '.join(f'h{i}' for i in range(count)) + '\n\ndef run():\n'
    text += ''.join(f'    h{i}()\n' for i in range(count))
    (root / 'main.py').write_text(text)
    (root / 'pkg/helpers.py').write_text(''.join(
        f'def h{i}():\n    marker{i}()\n\n' for i in range(count)))
    targets = [dict(path='main.py', content=text)]
    operations = [dict(target=dict(path='main.py', locator=dict(start_line=3, end_line=3+count)))]
    return targets, operations


def test_python_changed_helper_does_not_claim_old_snapshot(tmp_path):
    targets, operations = python_imports(tmp_path)
    snapshot = compute_snapshot(tmp_path)
    helper = tmp_path / 'pkg/helpers.py'
    helper.write_text(helper.read_text().replace('marker0()', 'changedAfterSnapshot()'))
    records, audit = helper_context.local_helper_support(snapshot, targets, operations)
    assert records == []
    assert any(r.get('result') == 'SOURCE_CHANGED_AFTER_SNAPSHOT' for r in audit)


def test_python_parent_symlink_does_not_escape_snapshot_root(tmp_path):
    targets, operations = python_imports(tmp_path)
    snapshot = compute_snapshot(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + '-external')
    (tmp_path / 'pkg').rename(outside)
    (tmp_path / 'pkg').symlink_to(outside, target_is_directory=True)
    records, _ = helper_context.local_helper_support(snapshot, targets, operations)
    assert records == []


def test_python_existing_records_release_slots_for_new_helper(tmp_path):
    targets, operations = python_imports(tmp_path, count=9)
    records, _ = helper_context.local_helper_support(compute_snapshot(tmp_path), targets, operations)
    assert len(records) == 8
    plan = plan_for(tmp_path, operations)
    plan['coordination_context'] = dict(source_support=records)
    delivered = request_body(tmp_path, plan)['read_only_source_support']
    assert len(delivered) == 9
    assert any('marker8()' in r['excerpt'] for r in delivered)


def test_python_duplicate_definitions_do_not_choose_ambiguous_helper(tmp_path):
    targets, operations = python_imports(tmp_path)
    path = tmp_path / 'pkg/helpers.py'
    path.write_text(path.read_text() + 'def h0():\n    alternative()\n')
    records, _ = helper_context.local_helper_support(compute_snapshot(tmp_path), targets, operations)
    assert records == []
