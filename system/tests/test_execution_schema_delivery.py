"""The compatible transport must deliver the executor's concrete contract."""
import io
import json
from unittest.mock import patch

import pytest

from contractedge.execution.topomen import (
    build_execution_request, ModelPlanExecutor, PlanRejected,
    validate_execution_response,
)
from contractedge.model.provider import OpenAICompatibleProvider, ModelResponse
from tests.test_repair_execution import bound_plan


def test_execution_target_window_preserves_locator_content_without_full_file_duplication():
    from contractedge.execution.topomen import _execution_source_window

    source = ''.join(f'line-{index}\n' for index in range(1, 101))
    window, start, end, truncated = _execution_source_window(source, [(40, 45)], context_lines=2)
    assert truncated is True
    assert (start, end) == (38, 47)
    assert 'line-40\n' in window and 'line-45\n' in window
    assert 'line-1\n' not in window and 'line-100\n' not in window


def test_go_execution_target_window_keeps_bounded_file_header_and_target_context():
    from contractedge.execution.topomen import _execution_source_window

    source = ''.join(f'line-{index}\n' for index in range(1, 201))
    window, start, end, truncated = _execution_source_window(
        source, [(180, 185)], context_lines=2, path='pkg/runtime/options.go')
    assert truncated is True
    assert (start, end) == (1, 187)
    assert 'line-1\n' in window and 'line-120\n' in window
    assert 'line-180\n' in window and 'line-185\n' in window
    assert 'line-200\n' not in window
    assert 'omits original lines 121-177' in window


def test_go_config_window_keeps_public_to_internal_conversion_bridge():
    from contractedge.execution.topomen import _execution_source_window

    source = ''.join(f'line-{index}\n' for index in range(1, 501))
    source = source.replace('line-350\n', 'func (c *Config) toInternal() {\n')
    window, _start, end, truncated = _execution_source_window(
        source, [(92, 142)], context_lines=2, path='pkg/runtime/config.go')
    assert truncated is True
    assert end >= 430
    assert 'func (c *Config) toInternal()' in window


def fixture(root, *, schema_in_prompt=True):
    (root / 'templates').mkdir()
    (root / 'templates/serviceaccount.yaml').write_text('name: default\n')
    return bound_plan(root), dict(model='fixture', provider='openai_compatible',
        max_context_tokens=40000, execution_max_output_tokens=1000,
        schema_in_prompt=schema_in_prompt)


@pytest.mark.parametrize('setting',['enabled','omitted'])
def test_compatible_http_messages_carry_the_actual_edit_schema(tmp_path,setting):
    plan, config = fixture(tmp_path)
    if setting=='omitted':config.pop('schema_in_prompt')
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']},
        len, config, requirement='Select the configured name')
    captured = []
    def send(req, **kwargs):
        captured.append(json.loads(req.data))
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{}'},
            'finish_reason': 'stop'}]}).encode())
    with patch.dict('os.environ', {'CE_SCHEMA_TEST_KEY': 'fixture'}), \
            patch('urllib.request.urlopen', side_effect=send):
        OpenAICompatibleProvider('https://fixture.invalid', 'CE_SCHEMA_TEST_KEY').complete_json(request)
    payload = json.loads(captured[0]['messages'][1]['content'])
    schema = payload['output_schema']
    assert schema['required'] == ['edits']
    assert schema['additionalProperties'] is False
    item = schema['properties']['edits']['items']
    assert set(item['required']) == {'change_id', 'path', 'operation', 'old_text', 'new_text'}
    assert item['properties']['change_id']['enum'] == ['C1']
    assert item['properties']['path']['enum'] == ['templates/serviceaccount.yaml']
    assert item['additionalProperties'] is False
    assert schema == request.output_schema


def test_native_schema_transport_does_not_duplicate_schema_in_messages(tmp_path):
    plan, config = fixture(tmp_path, schema_in_prompt=False)
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']},
        len, config, requirement='Select the configured name')
    assert request.output_schema['required'] == ['edits']
    assert 'output_schema' not in json.loads(request.messages[1]['content'])


def test_separate_regions_for_one_change_apply_as_one_validated_batch(tmp_path):
    from contractedge.execution.topomen import apply_edits
    plan,config=fixture(tmp_path)
    source=tmp_path/'templates/serviceaccount.yaml'
    source.write_text('name: default\nmarker: before\n')
    plan=bound_plan(tmp_path)
    body={'edits':[dict(change_id='C1',path='templates/serviceaccount.yaml',operation='MODIFY',
        old_text=old,new_text=new) for old,new in [('name: default','name: selected'),('marker: before','marker: after')]]}
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},len,config,requirement='Select name and marker')
    validated=validate_execution_response(ModelResponse(json.dumps(body),{}),request)
    result=apply_edits(plan,tmp_path,{'allowed_paths':['templates/**']},validated)
    assert result['status']=='APPLIED'
    assert source.read_text()=='name: selected\nmarker: after\n'


def test_numbered_empty_edit_field_stays_rejected_without_source_changes(tmp_path):
    plan,config=fixture(tmp_path);source=tmp_path/'templates/serviceaccount.yaml'
    body={'edits':[dict(change_id='C1',path='templates/serviceaccount.yaml',operation='MODIFY',
        old_text='name: default',new_text='name: selected',old_text_2='')]}
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},len,config,requirement='Select name')
    with pytest.raises(PlanRejected,match='Additional properties'):
        validate_execution_response(ModelResponse(json.dumps(body),{}),request)
    assert source.read_text()=='name: default\n'


def test_literal_multiline_edit_applies_and_double_escaped_anchor_stays_rejected(tmp_path):
    plan, config = fixture(tmp_path)
    source = tmp_path / 'templates/serviceaccount.yaml'
    source.write_text('name: default\n\tnested: true\n')
    plan = bound_plan(tmp_path)
    body = {'edits': [dict(change_id='C1', path='templates/serviceaccount.yaml', operation='MODIFY',
        old_text='name: default\n\tnested: true', new_text='name: selected\n\tnested: true')]}
    class LiteralProvider:
        def complete_json(self, request):
            payload = json.loads(request.messages[1]['content'])
            assert payload['current_target_files'][0]['content'] == 'name: default\n\tnested: true\n'
            return ModelResponse(json.dumps(body), {'finish_reason': 'stop'})
    result = ModelPlanExecutor(LiteralProvider(), len, config).execute(plan, tmp_path,
        {'allowed_paths': ['templates/**']}, requirement='Select the configured name')
    assert result['status'] == 'APPLIED'
    assert source.read_text() == 'name: selected\n\tnested: true\n'
    source.write_text('name: default\n\tnested: true\n')
    body['edits'][0]['old_text'] = r'name: default\n\tnested: true'
    with pytest.raises(PlanRejected, match='exactly once'):
        ModelPlanExecutor(LiteralProvider(), len, config).execute(plan, tmp_path,
            {'allowed_paths': ['templates/**']}, requirement='Select the configured name')
    assert source.read_text() == 'name: default\n\tnested: true\n'


def test_transport_metadata_is_never_accepted_as_edit_content(tmp_path):
    plan, config = fixture(tmp_path)
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']},
        len, config, requirement='Select the configured name')
    body = dict(type='json_object', edits=[dict(change_id='C1', path='templates/serviceaccount.yaml',
        operation='MODIFY', old_text='name: default', new_text='name: selected')])
    with pytest.raises(PlanRejected, match='Additional properties'):
        validate_execution_response(ModelResponse(json.dumps(body), {'finish_reason': 'stop'}), request)
