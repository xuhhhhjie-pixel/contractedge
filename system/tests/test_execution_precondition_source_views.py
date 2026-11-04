"""Precondition analysis retains full source coordinates and fresh helper contracts."""
import copy
import hashlib
import json

import pytest

from contractedge.execution.topomen import build_execution_request, check_edited_helper_preconditions
from contractedge.source.snapshot import compute_snapshot
from tests.test_helper_preconditions import HELPERS, source
from tests.test_repair_execution import bound_plan


def _request(root, caller, *, caller_line=1, target_helpers=False):
    (root / 'templates').mkdir()
    (root / 'templates/serviceaccount.yaml').write_text('name: default\n')
    (root / 'apply.go').write_text(caller)
    chunks = ['package helper\n']
    records = []
    for helper in HELPERS:
        start = sum(len(chunk.splitlines()) for chunk in chunks) + 1
        excerpt = helper['excerpt'] + '\n'
        chunks.append(excerpt)
        records.append(dict(id=helper['id'], kind='SOURCE_SPAN', delivery_required=True,
            locator={'path': 'helpers.go', 'start_line': start,
                     'end_line': start + len(excerpt.splitlines()) - 1},
            excerpt=excerpt, content_digest=hashlib.sha256(excerpt.encode()).hexdigest()))
    (root / 'helpers.go').write_text(''.join(chunks))
    plan = bound_plan(root)
    snapshot = compute_snapshot(root)
    for record in records:
        record['snapshot_id'] = snapshot.snapshot_id
    operation = plan['operations'][0]
    operation['target'].update(path='apply.go', content_digest=snapshot.file_digests['apply.go'],
        locator={'start_line': caller_line, 'end_line': caller_line})
    if target_helpers:
        helper_operation = copy.deepcopy(operation)
        helper_operation['change_id'] = 'C2'
        helper_operation['target'].update(path='helpers.go', content_digest=snapshot.file_digests['helpers.go'],
            locator={'start_line': 1, 'end_line': len(''.join(chunks).splitlines())})
        plan['operations'].append(helper_operation)
    plan['coordination_context'] = {'source_support': records}
    config = dict(model='fixture', max_context_tokens=100000, execution_max_output_tokens=1000,
                  max_execution_support_bytes=24000, helper_preconditions_enabled=True)
    request = build_execution_request(plan, root, {'allowed_paths': ['*.go']}, len, config,
                                      requirement='Preserve resource identities on every merge path')
    return request, json.loads(request.messages[1]['content']), config, records


def _checks(report):
    checks = [row for row in report['checks'] if row['path'] == 'apply.go' and row['call'] == 'Merge']
    assert len(checks) == 2, 'Known merge contracts must retain explicit initialization and cleanup checks'
    assert {row['precondition'] for row in checks} == {'RESOURCE_ID_INITIALIZATION', 'RESOURCE_ID_CLEANUP'}
    return {row['precondition']: row for row in checks}


def test_stitched_target_window_does_not_invent_conflict_or_rebase_call_line(tmp_path):
    lines = ['// filler\n'] * 310
    for line, text in {1: 'package p\n', 110: 'func Apply() {\n', 130: ' rt.Initialize(input)\n',
                       260: ' input = rt.Merge(output, selected, input)\n',
                       270: ' rt.Cleanup(input)\n', 280: '}\n'}.items():
        lines[line - 1] = text
    request, payload, config, _ = _request(tmp_path, ''.join(lines), caller_line=260)
    target = next(item for item in payload['current_target_files'] if item['path'] == 'apply.go')
    assert target['window_segments'] == [[1, 120], [240, 280]]
    assert 'rt.Initialize(input)' not in target['content']
    for report in [payload['helper_preconditions'], check_edited_helper_preconditions(request, tmp_path, config)]:
        checks = _checks(report)
        assert {row['status'] for row in checks.values()} == {'CHECKED_SYNTAX'}
        assert {row['call_line'] for row in checks.values()} == {260}


def test_covered_helper_transport_dedup_preserves_prompt_and_postedit_analysis(tmp_path):
    request, payload, config, _ = _request(tmp_path, source(), target_helpers=True)
    assert payload['read_only_source_support'] == []
    for report in [payload['helper_preconditions'], check_edited_helper_preconditions(request, tmp_path, config)]:
        checks = _checks(report)
        assert checks['RESOURCE_ID_INITIALIZATION']['status'] == 'CONFLICT'
        assert checks['RESOURCE_ID_CLEANUP']['status'] == 'CHECKED_SYNTAX'
        assert {row['call_line'] for row in checks.values()} == {4}


@pytest.mark.parametrize('target_helpers', [False, True], ids=['read-only-support', 'covered-target'])
@pytest.mark.parametrize('movement', ['unchanged', 'moved'])
def test_postedit_unchanged_unique_helper_contract_can_be_reread(tmp_path, target_helpers, movement):
    request, _, config, _ = _request(tmp_path, source('len(selectors)>0 || configPath!=""'),
                                    target_helpers=target_helpers)
    if movement == 'moved':
        helper_path = tmp_path / 'helpers.go'
        helper_path.write_text('// New declaration context.\n// Original contracts moved intact.\n' + helper_path.read_text())
    checks = _checks(check_edited_helper_preconditions(request, tmp_path, config))
    assert {row['status'] for row in checks.values()} == {'CHECKED_SYNTAX'}
    assert {row['call_line'] for row in checks.values()} == {4}


@pytest.mark.parametrize('target_helpers', [False, True], ids=['read-only-support', 'covered-target'])
@pytest.mark.parametrize('helper_index', [0, 1, 2], ids=['merge-contract', 'initializer-contract', 'cleanup-contract'])
@pytest.mark.parametrize('mutation', ['changed', 'deleted', 'ambiguous'])
def test_postedit_unverified_helper_contract_keeps_affected_checks_unknown(
        tmp_path, target_helpers, helper_index, mutation):
    request, _, config, records = _request(tmp_path, source('len(selectors)>0 || configPath!=""'),
                                          target_helpers=target_helpers)
    helper_path = tmp_path / 'helpers.go'
    before = helper_path.read_text()
    excerpt = records[helper_index]['excerpt']
    if mutation == 'changed':
        after = before.replace(excerpt, excerpt.replace('ResourceID', 'ChangedID'), 1)
    elif mutation == 'deleted':
        after = before.replace(excerpt, '', 1)
    else:
        after = before + '\n/* An exact archival copy makes the old span ambiguous.\n' + excerpt + '*/\n'
    helper_path.write_text(after)
    checks = _checks(check_edited_helper_preconditions(request, tmp_path, config))
    affected = ({'RESOURCE_ID_INITIALIZATION', 'RESOURCE_ID_CLEANUP'} if helper_index == 0
                else {'RESOURCE_ID_INITIALIZATION'} if helper_index == 1 else {'RESOURCE_ID_CLEANUP'})
    for name, row in checks.items():
        assert row['status'] == ('UNKNOWN' if name in affected else 'CHECKED_SYNTAX')
    assert {row['call_line'] for row in checks.values()} == {4}


@pytest.mark.parametrize('target_helpers', [False, True], ids=['read-only-support', 'covered-target'])
def test_postedit_deleted_helper_file_retains_unknown_checks(tmp_path, target_helpers):
    request, _, config, _ = _request(tmp_path, source('len(selectors)>0 || configPath!=""'),
                                    target_helpers=target_helpers)
    (tmp_path / 'helpers.go').unlink()
    checks = _checks(check_edited_helper_preconditions(request, tmp_path, config))
    assert {row['status'] for row in checks.values()} == {'UNKNOWN'}
    assert {row['call_line'] for row in checks.values()} == {4}


@pytest.mark.parametrize('archive_kind', ['block-comment', 'raw-string'])
@pytest.mark.parametrize('helper_index', [0, 1, 2], ids=['merge-contract', 'initializer-contract', 'cleanup-contract'])
def test_postedit_unique_archived_helper_bytes_do_not_revalidate_changed_live_contract(
        tmp_path, archive_kind, helper_index):
    request, _, config, records = _request(tmp_path, source('len(selectors)>0 || configPath!=""'),
                                          target_helpers=True)
    helper_path = tmp_path / 'helpers.go'
    excerpt = records[helper_index]['excerpt']
    changed = helper_path.read_text().replace(excerpt, excerpt.replace('ResourceID', 'ChangedID'), 1)
    archive = ('\n/* Historical contract only:\n' + excerpt + '*/\n' if archive_kind == 'block-comment'
               else '\nvar archivedContract = `\n' + excerpt + '`\n')
    helper_path.write_text(changed + archive)
    assert helper_path.read_text().count(excerpt) == 1
    checks = _checks(check_edited_helper_preconditions(request, tmp_path, config))
    affected = ({'RESOURCE_ID_INITIALIZATION', 'RESOURCE_ID_CLEANUP'} if helper_index == 0
                else {'RESOURCE_ID_INITIALIZATION'} if helper_index == 1 else {'RESOURCE_ID_CLEANUP'})
    for name, row in checks.items():
        assert row['status'] == ('UNKNOWN' if name in affected else 'CHECKED_SYNTAX')
    assert {row['call_line'] for row in checks.values()} == {4}
