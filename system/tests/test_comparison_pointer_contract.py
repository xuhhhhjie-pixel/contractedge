"""Generic path-contract regressions from a saved public equality failure."""
import copy
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.validation import parse_method_input, validate_changeset, validate_diagnosis
from contractedge.execution.comparison import compare_declared_input, json_pointer
from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.provider import ModelResponse
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan
from tests.test_review_integrity_retry import CONFIG, Sequence
from tests.test_optimization_contract_flow import observation


BAD = ['nodeSelector', 'resource.value', 'raw/output.json#value', '#/value', '/bad~', '/bad~2', '\n']
GOOD = ['', '/', '/value', '/a.b', '/a~1b/~0key', '/数组/0', '/#key', '/line\nbreak']


def diagnosis():
    return validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)


@pytest.mark.parametrize('group', ['postconditions', 'preserve_constraints', 'open_obligations'])
@pytest.mark.parametrize('field', ['input_pointer', 'output_pointer'])
@pytest.mark.parametrize('pointer', BAD)
def test_plan_rejects_invalid_pointer_at_contract_boundary(group, field, pointer):
    plan = valid_plan()
    plan[group] = [{'id': 'CHECK', 'comparison_requirements': [dict(
        artifact_id='INPUT', input_pointer='/value', output_pointer='/value')]}]
    plan[group][0]['comparison_requirements'][0][field] = pointer
    # Other incomplete fixture fields may fail too. This specific path must be
    # rejected by the schema itself, before target resolution or execution.
    request = build_changeset_request(diagnosis(), SimpleNamespace(run_id='run-1', case_id='CASE',
        snapshot_id='snap-0', input_digest='a'*64, model_config={'model':'fixture'},
        budget={'max_output_tokens':1000}))
    errors = list(Draft202012Validator(request.output_schema).iter_errors(plan))
    assert any(list(e.absolute_path) == [group, 0, 'comparison_requirements', 0, field]
               for e in errors)
    # The canonical validator must enforce the same grammar.
    result = validate_changeset(plan, diagnosis(), None)
    assert not result.valid
    assert any(field in e.message or field in e.path for e in result.errors)


@pytest.mark.parametrize('pointer', GOOD)
def test_model_schema_preserves_valid_pointer_strings(pointer):
    request = build_changeset_request(diagnosis(), SimpleNamespace(run_id='run-1', case_id='CASE',
        snapshot_id='snap-0', input_digest='a'*64, model_config={'model':'fixture'},
        budget={'max_output_tokens':1000}))
    for group in ['postconditions', 'preserve_constraints', 'open_obligations']:
        fields = request.output_schema['properties'][group]['items']['properties']['comparison_requirements']['items']['properties']
        for field in ['input_pointer', 'output_pointer']:
            assert Draft202012Validator(fields[field]).is_valid(pointer)


@pytest.mark.parametrize('pointer', BAD)
def test_runtime_refuses_malformed_pointer_even_for_matching_literal_keys(pointer):
    with pytest.raises(ValueError):
        json_pointer({pointer.lstrip('/'): True}, pointer)


def test_pointer_lookup_preserves_root_empty_key_escaping_and_dots():
    value = {'': 1, 'a.b': 2, 'a/b': {'~key': ['三']}}
    assert json_pointer(value, '') is value
    assert json_pointer(value, '/') == 1
    assert json_pointer(value, '/a.b') == 2
    assert json_pointer(value, '/a~1b/~0key/0') == '三'
    for ptr in ['/a~1b/~0key/00', '/a~1b/~0key/-', '/missing']:
        with pytest.raises((ValueError, KeyError)):
            json_pointer(value, ptr)


@pytest.mark.parametrize('patch,reason', [
    ({'input_pointer':'value'}, 'INVALID_INPUT_POINTER'),
    ({'output_pointer':'raw/x.json#value'}, 'INVALID_OUTPUT_POINTER'),
    ({'artifact_id':'MISSING'}, 'INPUT_ARTIFACT_MISSING'),
    ({'observation_id':'MISSING'}, 'OUTPUT_OBSERVATION_MISSING'),
    ({'input_pointer':'/missing'}, 'INPUT_FIELD_UNAVAILABLE'),
    ({'output_pointer':'/missing'}, 'OUTPUT_FIELD_UNAVAILABLE'),
])
def test_unavailable_explains_boundary_without_fabricating_equality(patch, reason):
    spec = dict(artifact_id='INPUT', input_pointer='/value', observation_id='OBS', output_pointer='/value')
    spec.update(patch)
    result = compare_declared_input(spec, [{'id':'INPUT','content':'value: true'}], [observation('{"value":true}')])
    assert result['status'] == 'UNAVAILABLE'
    assert result['reason'] == reason
    assert 'expected' not in result and 'actual' not in result


def test_review_correction_explains_pointer_syntax_and_keeps_retry_bound():
    claim = {'checks':[{'id':'P','status':'PASS','rationale':'Claim',
        'comparisons':[dict(artifact_id='INPUT', input_pointer='value',
                            observation_id='OBS', output_pointer='resource.value')],
        'evidence':[{'observation_id':'OBS','quote':'true'}]}],
        'failure_kind':'UNKNOWN','rationale':'Claim'}
    unknown = copy.deepcopy(claim)
    unknown['checks'][0].update(status='UNKNOWN', evidence=[], comparisons=[])
    provider = Sequence([ModelResponse(json.dumps(claim), {}), ModelResponse(json.dumps(unknown), {})])
    result = ModelPublicReviewer(provider, len, CONFIG).review('Selected value',
        {'postconditions':[{'id':'P','statement':'Selected value'}]},
        {'status':'EXECUTION_PASS','observations':[observation('{"value":true}')],
         'declared_artifacts':[{'id':'INPUT','content':'value: true'}]})
    feedback = provider.requests[1].messages[-1]['content']
    assert 'JSON Pointer' in feedback and '~0' in feedback and '~1' in feedback
    assert 'input_pointer' in feedback and 'output_pointer' in feedback
    assert result['decision'] == 'UNKNOWN' and len(provider.requests) == 2
