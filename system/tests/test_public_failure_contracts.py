"""Present malformed replay inputs must never become a complete package."""
import pytest

from tests.test_public_failure import fixture, tool, write


def export_changed(tmp_path, role, body):
    run, source, recipe, _, _ = fixture(tmp_path)
    write(run / (role + '.json'), body)
    recipe['files'][role] = role + '.json'
    return tool.export_package(run, source, recipe, tmp_path / 'package')


@pytest.mark.parametrize('role', ['method_input', 'front_trace', 'front_config'])
@pytest.mark.parametrize('body', [{}, [], None, '', 0, False])
def test_present_empty_or_wrong_type_is_invalid(tmp_path, role, body):
    result = export_changed(tmp_path, role, body)
    assert result['status'] == 'INVALID', result
    assert any(role in e for e in result['errors']), result


@pytest.mark.parametrize('role', ['method_input', 'front_trace'])
@pytest.mark.parametrize('change', ['version', 'required'])
def test_contract_version_and_required_fields(tmp_path, role, change):
    run, source, recipe, _, _ = fixture(tmp_path)
    body = tool.load(run / (role + '.json'))
    if change == 'version':
        body['schema_version'] = 'ce.unknown.v99'
    else:
        body.pop('case_id')
    write(run / (role + '.json'), body)
    result = tool.export_package(run, source, recipe, tmp_path / 'package')
    assert result['status'] == 'INVALID', result


@pytest.mark.parametrize('field,body', [
    ('model', {}), ('model', {'model': ''}), ('max_context_tokens', None),
    ('max_context_tokens', True), ('max_context_tokens', 0),
    ('max_total_tokens', -1), ('max_model_calls', '12'),
    ('max_format_retries', False), ('max_seconds', float('nan')),
])
def test_effective_config_requires_valid_limits(tmp_path, field, body):
    run, source, recipe, _, _ = fixture(tmp_path)
    cfg = tool.load(run / 'front_config.json'); cfg[field] = body
    write(run / 'front_config.json', cfg)
    assert tool.export_package(run, source, recipe, tmp_path / 'package')['status'] == 'INVALID'


@pytest.mark.parametrize('role,body', [
    ('public_observations', {}), ('public_observations', None),
    ('public_observations', [None]), ('public_observations', [{}]),
    ('model_calls', {}), ('model_calls', [None]), ('model_calls', [[]]),
    ('execution_feedback', {}), ('execution_feedback', []),
])
def test_malformed_observations_ledger_and_feedback(tmp_path, role, body):
    result = export_changed(tmp_path, role, body)
    assert result['status'] == 'INVALID', result


@pytest.mark.parametrize('field,body', [('messages', None), ('metadata', []),
    ('temperature', float('inf')), ('max_output_tokens', True), ('thinking', {})])
def test_present_request_wrong_types_are_invalid(tmp_path, field, body):
    run, source, recipe, calls, _ = fixture(tmp_path)
    calls.calls[0]['request'][field] = body
    write(run / 'model_calls.json', calls.calls)
    assert tool.export_package(run, source, recipe, tmp_path / 'package')['status'] == 'INVALID'


def test_invalid_role_json_is_structured_invalid(tmp_path):
    run, source, recipe, _, _ = fixture(tmp_path)
    (run / 'front_config.json').write_text('{invalid json', encoding='utf-8')
    result = tool.export_package(run, source, recipe, tmp_path / 'package')
    assert result['status'] == 'INVALID'
    assert 'INVALID_ARTIFACT_JSON:front_config' in result['errors']


@pytest.mark.parametrize('body', [None, [], {}, {'schema_version': 'wrong'}])
def test_malformed_manifest_is_structured_invalid(tmp_path, body):
    out = tmp_path / 'package'; out.mkdir(); write(out / 'manifest.json', body)
    assert tool.check_package(out)['status'] == 'INVALID'


def test_optional_null_feedback_and_empty_observations_remain_complete(tmp_path):
    assert export_changed(tmp_path, 'execution_feedback', None)['status'] == 'COMPLETE'


def test_observation_snapshot_binding_is_checked(tmp_path):
    run, source, recipe, _, _ = fixture(tmp_path)
    write(run / 'public_observations.json', [{'id': 'OBS', 'snapshot_id': 'wrong',
        'kind': 'PUBLIC_OBSERVATION', 'locator': {}, 'content_digest': '0' * 64,
        'excerpt': 'observed', 'origin': 'public-check', 'evidence_kind': 'EXECUTION_ONLY'}])
    assert tool.export_package(run, source, recipe, tmp_path / 'package')['status'] == 'INVALID'


def test_empty_selected_call_range_is_incomplete(tmp_path):
    run, source, recipe, _, _ = fixture(tmp_path)
    trace = tool.load(run / 'front_trace.json'); trace['model_calls'] = []
    write(run / 'front_trace.json', trace); recipe['model_call_range'] = [0, 0]
    result = tool.export_package(run, source, recipe, tmp_path / 'package')
    assert result['status'] == 'INCOMPLETE', result


def test_valid_observation_extensions_are_supported(tmp_path):
    run, source, recipe, _, _ = fixture(tmp_path)
    write(run / 'public_observations.json', [{'id': 'OBS', 'snapshot_id': recipe['broken_snapshot_id'],
        'kind': 'PUBLIC_OBSERVATION', 'locator': {}, 'content_digest': '0' * 64,
        'excerpt': 'observed', 'origin': 'public-check', 'evidence_kind': 'EXECUTION_ONLY',
        'public_runner_extension': {'exit_code': 0}}])
    assert tool.export_package(run, source, recipe, tmp_path / 'package')['status'] == 'COMPLETE'
