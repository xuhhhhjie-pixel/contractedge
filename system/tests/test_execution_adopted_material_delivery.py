"""Builder-selected helpers must survive the final prepared request boundary."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.execution.topomen import build_execution_request
from contractedge.model.admission import quote_request
from contractedge.orchestration.semantic_delivery import (
    SemanticDeliveryError, delivery_context, inspect_delivery, require_delivery,
)
from contractedge.source.snapshot import compute_snapshot
from tests.test_repair_execution import bound_plan
from tests.test_execution_support_windows import fixture as window_fixture


REQUIREMENT = 'Merge the requested inputs and preserve their identities.'


class PreparedTransport:
    def __init__(self, mutate=None):
        self.mutate = mutate

    def prepare_request(self, request):
        messages = json.loads(json.dumps(request.messages))
        payload = json.loads(messages[1]['content'])
        if self.mutate:
            self.mutate(payload)
        messages[1]['content'] = json.dumps(payload)
        body = json.dumps({'messages': messages}).encode()
        return SimpleNamespace(body=body, metadata={}, overhead='', output_schema=None,
            wire_schema=None, audit_record=lambda: {'body_sha256': hashlib.sha256(body).hexdigest()})

    def complete_prepared_json(self, request, prepared):
        raise AssertionError('This regression inspects preparation only; no dispatch is allowed')


def build(root, monkeypatch, *, dependency=False):
    (root / 'templates').mkdir()
    (root / 'templates/serviceaccount.yaml').write_text('name: default\n')
    (root / 'go.mod').write_text('module example.org/demo\n')
    (root / 'main.go').write_text('package main\nimport r "example.org/demo/runtime"\nfunc main(){ r.Merge(items) }\n')
    (root / 'runtime').mkdir()
    (root / 'runtime/utils.go').write_text('package runtime\n// Merge requires assigned IDs.\nfunc Merge(items []string) []string { return items }\n')
    snapshot = compute_snapshot(root)
    plan = bound_plan(root)
    plan['operations'][0]['target'].update(path='main.go', content_digest=snapshot.file_digests['main.go'],
        locator={'start_line': 3, 'end_line': 3})
    if dependency:
        from contractedge.execution import helper_context
        text = 'package external\ntype MergeOptions struct { KeepIDs bool }\n'
        record = dict(id='PUBLICDEP-external', kind='PUBLIC_DEPENDENCY_DECLARATION',
            module='example.org/external', version='v1.0.0', package='external', type_name='MergeOptions',
            current_source_snapshot=snapshot.snapshot_id, failure_snapshot='prior-trial',
            archive_path='public/module.tar.gz', archive_sha256='a' * 64, member='external/options.go',
            content_digest=hashlib.sha256(text.encode()).hexdigest(), excerpt=text, read_only=True,
            environment_sha256='b' * 64,
            interpretation='Exact pinned module source; read only, not an editable target.')
        monkeypatch.setattr(helper_context, 'compiler_dependency_support', lambda *args, **kwargs: ([record], []))
    config = dict(model='test', max_context_tokens=100000, execution_max_output_tokens=1000,
        execution_helper_support_enabled=True, max_execution_support_bytes=24000)
    request = build_execution_request(plan, root, {'allowed_paths': ['main.go']}, len, config,
        requirement=REQUIREMENT)
    return request, plan


def inspect(request, plan, root, mutate=None):
    frame = delivery_context({'requirement_text': REQUIREMENT}, consumer='EXECUTION',
        snapshot_id=plan['snapshot_id'], plan=plan, source_root=root)
    provider = PreparedTransport(mutate)
    quote = quote_request(provider, request, len)
    assert quote.prepared is not None
    return inspect_delivery(request, quote, frame)


@pytest.mark.parametrize('dependency', [False, True])
def test_dynamic_material_is_checked_even_without_upstream_diagnosis_record(tmp_path, monkeypatch, dependency):
    request, plan = build(tmp_path, monkeypatch, dependency=dependency)
    report = inspect(request, plan, tmp_path)
    require_delivery(report)
    payload = json.loads(request.messages[1]['content'])
    records = payload['read_only_source_support']
    if dependency:
        records += payload['compiler_api_context']['declarations']
    rows = {row['id']: row for row in report['subjects']}
    assert records
    for record in records:
        assert rows[record['id']]['required_content_gate'] is True
        assert rows[record['id']]['classification'] == 'FULL_ORIGINAL'


@pytest.mark.parametrize('dependency', [False, True])
@pytest.mark.parametrize('mutation', ['remove', 'change_body', 'change_origin'])
def test_prepared_dynamic_material_loss_or_drift_blocks_dispatch(tmp_path, monkeypatch, dependency, mutation):
    request, plan = build(tmp_path, monkeypatch, dependency=dependency)

    def mutate(payload):
        records = (payload['compiler_api_context']['declarations'] if dependency
                   else payload['read_only_source_support'])
        if mutation == 'remove':
            records.clear()
        elif mutation == 'change_body':
            records[0]['excerpt'] += '// Content introduced by a faulty transport.\n'
        else:
            records[0]['archive_sha256' if dependency else 'origin'] = 'Changed provenance'

    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(request, plan, tmp_path, mutate))


def test_exact_target_dedup_remains_valid_without_duplicate_helper_body(tmp_path):
    plan, config, record = window_fixture(tmp_path, language='go', support_start=195,
        target_start=205, target_end=207)
    config.update(max_execution_support_bytes=0, helper_preconditions_enabled=False)
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['worker.go']}, len,
        config, requirement=REQUIREMENT)
    payload = json.loads(request.messages[1]['content'])
    assert payload['read_only_source_support'] == []
    assert record['excerpt'] in payload['current_target_files'][0]['content']
    report = inspect(request, plan, tmp_path)
    require_delivery(report)
    row = next(row for row in report['subjects'] if row['id'] == record['id'])
    assert row['required_content_gate'] is True
    assert row['classification'] == 'FULL_ORIGINAL'


def test_same_file_dynamic_helper_shared_in_target_cannot_disappear_after_selection(tmp_path):
    (tmp_path / 'templates').mkdir()
    (tmp_path / 'templates/serviceaccount.yaml').write_text('name: default\n')
    (tmp_path / 'go.mod').write_text('module example.org/demo\n')
    helper = 'func (w *Worker) Helper() { println("preserve") }\n'
    (tmp_path / 'main.go').write_text('package main\ntype Worker struct{}\n'
        'func (w *Worker) Run() { w.Helper() }\n' + helper)
    snapshot = compute_snapshot(tmp_path)
    plan = bound_plan(tmp_path)
    plan['operations'][0]['target'].update(path='main.go', content_digest=snapshot.file_digests['main.go'],
        locator={'start_line': 3, 'end_line': 3})
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['main.go']}, len,
        dict(model='test', max_context_tokens=100000, execution_max_output_tokens=1000,
             execution_helper_support_enabled=True), requirement=REQUIREMENT)
    payload = json.loads(request.messages[1]['content'])
    assert any(row['result'] == 'ALREADY_DELIVERED_HELPER' for row in request.metadata['read_only_support_audit'])
    assert payload['read_only_source_support'] == []
    require_delivery(inspect(request, plan, tmp_path))

    def drop_helper(body):
        target = body['current_target_files'][0]
        target['content'] = target['content'].replace(helper, '')

    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(request, plan, tmp_path, drop_helper))


def test_prepared_transport_cannot_shrink_selected_windows_even_to_other_valid_windows(tmp_path):
    plan, config, _ = window_fixture(tmp_path, language='go', support_start=150,
        target_start=260, target_end=262)
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['worker.go']}, len,
        config, requirement=REQUIREMENT)
    require_delivery(inspect(request, plan, tmp_path))

    def shrink(body):
        target = body['current_target_files'][0]
        assert len(target['window_segments']) == 2
        begin, end = target['window_segments'][0]
        target['content'] = ''.join((tmp_path / 'worker.go').read_text().splitlines(keepends=True)[begin - 1:end])
        target['window_segments'] = [[begin, end]]
        target['line_start'], target['line_end'] = begin, end

    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(request, plan, tmp_path, shrink))
