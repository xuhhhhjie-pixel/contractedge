"""Compiler recovery reads pinned public API declarations rather than guessing."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from contractedge.execution import helper_context
from contractedge.source.snapshot import compute_snapshot


API = '''package check
type ChangeList struct { Entries []*Change }
func (c *ChangeList) ChangedAt(path string) bool { return false }
'''


def digest(value): return hashlib.sha256(value).hexdigest()


def fixture(tmp_path, *, member='gomod/example.org/toolbox@v1.2.3/pkg/check/changes.go', body=API):
    workspace = tmp_path/'workspace'; source = workspace/'source/repo'; source.mkdir(parents=True)
    inputs = workspace/'inputs'; inputs.mkdir()
    (source/'go.mod').write_text('module example.org/service\nrequire example.org/toolbox v1.2.3\n')
    (source/'main.go').write_text('package service\nimport external "example.org/toolbox/pkg/check"\nfunc update(change *external.ChangeList) {}\n')
    archive = inputs/'dependencies.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        info = tarfile.TarInfo(member); raw = body.encode(); info.size = len(raw); tar.addfile(info, io.BytesIO(raw))
    environment = {'public_mounts': [{'archive': archive.name, 'sha256': digest(archive.read_bytes()), 'target': '/work/deps'}]}
    (inputs/'environment.json').write_text(json.dumps(environment))
    (workspace/'workspace.json').write_text(json.dumps({'case_id':'CASE', 'source_root':str(source), 'inputs_root':str(inputs), 'private_materials_loaded':False}))
    snapshot = compute_snapshot(source)
    message = 'main.go:3:12: change.HasUpdates undefined (type *check.ChangeList has no field or method HasUpdates)'
    feedback = {'case_id':'CASE', 'to_snapshot':'failed-trial', 'public_execution_capabilities': {
        'source': {'path':'environment.json','sha256':digest((inputs/'environment.json').read_bytes())}},
        'observation_diagnostics':[{'kind':'CANDIDATE_BUILD_FAILURE', 'operation':'PRODUCTION_COMPILE',
            'compiler':'go', 'status':'FAILED', 'producer':'FROZEN_PUBLIC_GO_COMPILER_DIAGNOSTIC_V1',
            'snapshot_id':'failed-trial', 'scenario':'package-build', 'source_locations':[{
                'path':'main.go','line':3,'column':12,'file_sha256':'a'*64}],
            'stderr_excerpt':message, 'stderr_truncated':False,'stderr_sha256':'b'*64,
            'diagnostic_sha256':digest(message.encode())}]}
    return snapshot, feedback, inputs, source


def collect(snapshot, feedback, **kwargs):
    fn = getattr(helper_context, 'compiler_dependency_support', None)
    assert callable(fn), 'Compiler-guided public dependency API lookup is missing'
    return fn(snapshot, feedback, **kwargs)


def test_renamed_external_type_uses_the_exact_pinned_api_without_edit_permission(tmp_path):
    snapshot, feedback, inputs, source = fixture(tmp_path)
    before = {p: digest(p.read_bytes()) for p in inputs.iterdir()}
    records, audit = collect(snapshot, feedback)
    assert len(records) == 1
    row = records[0]
    assert row['excerpt'] == API and 'ChangedAt(path string) bool' in row['excerpt']
    assert row['kind'] == 'PUBLIC_DEPENDENCY_DECLARATION'
    assert row['module'] == 'example.org/toolbox' and row['version'] == 'v1.2.3'
    assert row['current_source_snapshot'] == snapshot.snapshot_id
    assert row['failure_snapshot'] == 'failed-trial'
    assert row['archive_sha256'] == digest((inputs/'dependencies.tar.gz').read_bytes())
    assert row['read_only'] is True and 'not' in row['interpretation']
    assert any(r['result'] == 'INCLUDED_PUBLIC_DEPENDENCY_API' for r in audit)
    assert all(digest(p.read_bytes()) == value for p, value in before.items())
    assert compute_snapshot(source).snapshot_id == snapshot.snapshot_id


@pytest.mark.parametrize('mutation', ['archive-hash','environment-hash','outside-path','archive-symlink',
    'wrong-case','wrong-failure-snapshot','unregistered-failure','missing-go-mod','version-mismatch',
    'replace-module','ambiguous-import','source-changed','no-failure'])
def test_unbound_or_unavailable_dependency_contract_never_enters_context(tmp_path, mutation):
    snapshot, feedback, inputs, source = fixture(tmp_path)
    env = inputs/'environment.json'
    if mutation == 'archive-hash': (inputs/'dependencies.tar.gz').write_bytes(b'changed')
    elif mutation == 'environment-hash': feedback['public_execution_capabilities']['source']['sha256'] = '0'*64
    elif mutation == 'outside-path':
        value=json.loads(env.read_text()); value['public_mounts'][0]['archive']='../outside.tar.gz'
        env.write_text(json.dumps(value)); feedback['public_execution_capabilities']['source']['sha256']=digest(env.read_bytes())
    elif mutation == 'archive-symlink':
        p=inputs/'dependencies.tar.gz'; other=tmp_path/'outside'; p.rename(other); p.symlink_to(other)
    elif mutation == 'wrong-case': feedback['case_id']='OTHER'
    elif mutation == 'wrong-failure-snapshot': feedback['to_snapshot']='OTHER'
    elif mutation == 'unregistered-failure': feedback['observation_diagnostics'][0]['producer']='candidate stdout'
    elif mutation == 'missing-go-mod': (source/'go.mod').unlink()
    elif mutation == 'version-mismatch':
        (source/'go.mod').write_text('module example.org/service\nrequire example.org/toolbox v9.9.9\n'); snapshot=compute_snapshot(source)
    elif mutation == 'replace-module':
        with (source/'go.mod').open('a') as f:f.write('replace example.org/toolbox => ../other\n')
        snapshot=compute_snapshot(source)
    elif mutation == 'ambiguous-import':
        (source/'main.go').write_text('package service\nimport (\n a "example.org/toolbox/pkg/check"\n b "example.org/other/pkg/check"\n)\n')
        snapshot=compute_snapshot(source)
    elif mutation == 'source-changed': (source/'main.go').write_text('package changed\n')
    elif mutation == 'no-failure': feedback['observation_diagnostics']=[]
    records, audit = collect(snapshot, feedback)
    assert records == []


@pytest.mark.parametrize('max_bytes,max_records', [(8,2),(24000,0)])
def test_dependency_context_respects_existing_record_and_byte_budget(tmp_path, max_bytes, max_records):
    snapshot, feedback, _, _ = fixture(tmp_path)
    records, _ = collect(snapshot, feedback, max_bytes=max_bytes, max_records=max_records)
    assert records == []


@pytest.mark.parametrize('member', ['../gomod/example.org/toolbox@v1.2.3/pkg/check/changes.go',
    '/gomod/example.org/toolbox@v1.2.3/pkg/check/changes.go',
    'gomod/example.org/toolbox@v1.2.3/pkg/check/changes_test.go'])
def test_unsafe_or_test_members_are_not_public_api_definitions(tmp_path, member):
    snapshot, feedback, _, _ = fixture(tmp_path, member=member)
    assert collect(snapshot, feedback)[0] == []


def request_fixture(tmp_path):
    from tests.test_repair_execution import bound_plan
    snapshot, feedback, inputs, source = fixture(tmp_path)
    (source/'templates').mkdir()
    (source/'templates/serviceaccount.yaml').write_text('name: default\n')
    plan = bound_plan(source); snapshot = compute_snapshot(source)
    plan['operations'][0]['target'].update(path='main.go', content_digest=snapshot.file_digests['main.go'],
        locator={'start_line':3,'end_line':3})
    config = dict(model='test', provider='openai_compatible', max_context_tokens=60000,
        execution_max_output_tokens=1000, execution_helper_support_enabled=True,
        max_execution_support_bytes=24000)
    return snapshot, feedback, source, plan, config


@pytest.mark.parametrize('historical', [False, True])
def test_final_execution_request_delivers_compiler_api_from_current_or_rejected_feedback(tmp_path, historical):
    from contractedge.execution.topomen import build_execution_request
    snapshot, feedback, source, plan, config = request_fixture(tmp_path)
    kwargs = dict(previous_attempt=dict(source_snapshot=snapshot.snapshot_id,
        rejected_trial=dict(feedback=feedback))) if historical else dict(feedback=feedback)
    request = build_execution_request(plan,source,{'allowed_paths':['main.go']},len,config,
        requirement='Preserve existing values while updating changed entries',**kwargs)
    body = json.loads(request.messages[1]['content'])
    assert 'compiler_api_context' in body, 'The actual correction request still lacks the public API declaration'
    assert body['compiler_api_context']['declarations'][0]['excerpt'] == API
    assert body['compiler_api_context']['declarations'][0]['current_source_snapshot'] == snapshot.snapshot_id
    assert body['compiler_api_context']['declarations'][0]['failure_snapshot'] == feedback['to_snapshot']
    assert {row['path'] for row in body['current_target_files']} == {'main.go'}
    assert all(row['kind'] == 'SOURCE_SPAN' for row in body['read_only_source_support'])
    assert 'not' in body['compiler_api_context']['policy']
    assert 'compiler_api_context' not in plan


def test_initial_execution_without_compiler_failure_keeps_existing_request_bytes(tmp_path):
    from contractedge.execution.topomen import build_execution_request
    _, _, source, plan, config = request_fixture(tmp_path)
    enabled = build_execution_request(plan,source,{'allowed_paths':['main.go']},len,config,requirement='Keep values')
    disabled = build_execution_request(plan,source,{'allowed_paths':['main.go']},len,
        {**config,'execution_helper_support_enabled':False},requirement='Keep values')
    assert enabled.messages == disabled.messages
    assert 'compiler_api_context' not in json.loads(enabled.messages[1]['content'])


def test_compiler_declaration_shares_existing_support_budget_with_local_helpers(tmp_path):
    from contractedge.execution.topomen import build_execution_request
    snapshot, feedback, source, plan, config = request_fixture(tmp_path)
    (source/'helpers').mkdir()
    (source/'helpers/run.go').write_text('package helpers\nfunc Run() {}\n')
    (source/'main.go').write_text('package service\nimport (\n external "example.org/toolbox/pkg/check"\n h "example.org/service/helpers"\n)\nfunc update(change *external.ChangeList) { h.Run() }\n')
    snapshot = compute_snapshot(source); plan['snapshot_id'] = snapshot.snapshot_id
    plan['operations'][0]['target'].update(snapshot_id=snapshot.snapshot_id,content_digest=snapshot.file_digests['main.go'],
        locator={'start_line':6,'end_line':6})
    request = build_execution_request(plan,source,{'allowed_paths':['main.go']},len,
        {**config,'max_execution_support_bytes':len(API.encode())},requirement='Keep values',feedback=feedback)
    body = json.loads(request.messages[1]['content'])
    assert 'compiler_api_context' in body
    rows = body['compiler_api_context']['declarations'] + body['read_only_source_support']
    assert sum(len(row['excerpt'].encode()) for row in rows) <= len(API.encode())
    assert len(rows) <= 8


def replace_archive(inputs, feedback, members):
    archive = inputs/'dependencies.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name, body in members:
            info = tarfile.TarInfo(name); raw = body.encode(); info.size = len(raw)
            tar.addfile(info,io.BytesIO(raw))
    env = inputs/'environment.json'; value = json.loads(env.read_text())
    value['public_mounts'][0]['sha256'] = digest(archive.read_bytes()); env.write_text(json.dumps(value))
    feedback['public_execution_capabilities']['source']['sha256'] = digest(env.read_bytes())


@pytest.mark.parametrize('override', ['exclude','workspace','vendor'])
def test_unresolved_effective_module_selection_never_delivers_a_pinned_guess(tmp_path, override):
    snapshot, feedback, inputs, source = fixture(tmp_path)
    if override == 'exclude':
        with (source/'go.mod').open('a') as f:f.write('exclude (\n example.org/toolbox v9.9.9\n)\n')
        replace_archive(inputs,feedback,[('gomod/example.org/toolbox@v9.9.9/pkg/check/changes.go',API)])
    elif override == 'workspace':
        (source.parent/'go.work').write_text('go 1.24\nuse ./repo\nreplace example.org/toolbox => ../local\n')
    else:
        (source/'vendor').mkdir(); (source/'vendor/modules.txt').write_text('# example.org/toolbox v9.9.9\n')
    snapshot = compute_snapshot(source)
    assert collect(snapshot,feedback)[0] == [], 'Unresolved effective dependency selection must stay unavailable'


@pytest.mark.parametrize('unread', ['oversized','build-tag','platform-suffix'])
def test_incomplete_or_platform_ambiguous_package_scan_cannot_deliver_false_unique_api(tmp_path, unread):
    snapshot, feedback, inputs, source = fixture(tmp_path)
    prefix = 'gomod/example.org/toolbox@v1.2.3/pkg/check/'
    second = '//go:build linux\n\n'+API if unread == 'build-tag' else API
    if unread == 'oversized': second = '//'+('x'*66000)+'\n'+API
    members = [(prefix+'changes_linux.go',API)] if unread == 'platform-suffix' else [(prefix+'changes.go',API),(prefix+'other.go',second)]
    replace_archive(inputs,feedback,members)
    assert collect(snapshot,feedback)[0] == [], 'Skipped package files cannot establish a unique API'


def test_wrong_excluded_api_never_reaches_final_correction_request(tmp_path):
    from contractedge.execution.topomen import build_execution_request
    _, feedback, source, plan, config = request_fixture(tmp_path)
    with (source/'go.mod').open('a') as f:f.write('exclude (\n example.org/toolbox v9.9.9\n)\n')
    replace_archive(source.parents[1]/'inputs',feedback,[('gomod/example.org/toolbox@v9.9.9/pkg/check/changes.go',API)])
    snapshot = compute_snapshot(source); plan['snapshot_id'] = snapshot.snapshot_id
    plan['operations'][0]['target']['snapshot_id'] = snapshot.snapshot_id
    request = build_execution_request(plan,source,{'allowed_paths':['main.go']},len,config,requirement='Keep values',feedback=feedback)
    assert 'compiler_api_context' not in json.loads(request.messages[1]['content'])
