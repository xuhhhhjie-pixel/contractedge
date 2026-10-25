"""Deterministic comparisons of published inputs and actual public JSON output.

The comparison establishes equality only. Selection of the required artifact,
resource and field is still a semantic claim, never an independent behavior score.
"""
import json
from typing import Mapping
import yaml
from contractedge.contracts.pointers import validate_json_pointer


def json_pointer(value, pointer):
    validate_json_pointer(pointer)
    for part in pointer.split('/')[1:]:
        part = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not part.isdecimal() or part != str(int(part)):
                raise ValueError('Comparison array index must be canonical')
            value = value[int(part)]
        elif isinstance(value, Mapping):
            value = value[part]
        else:
            raise ValueError('Comparison pointer traverses a scalar')
    return value


def compare_declared_input(spec, artifacts, observations):
    """No model-supplied expected values and no missing==missing false pass."""
    def unavailable(reason):
        return {'status': 'UNAVAILABLE', 'spec': dict(spec), 'reason': reason,
                'interpretation': 'No equality claim; repair the indicated evidence or path boundary'}

    for field, reason in [('input_pointer', 'INVALID_INPUT_POINTER'),
                          ('output_pointer', 'INVALID_OUTPUT_POINTER')]:
        try:
            validate_json_pointer(spec.get(field))
        except ValueError:
            return unavailable(reason)
    artifact = next((a for a in artifacts if a['id'] == spec.get('artifact_id')), None)
    if artifact is None:
        return unavailable('INPUT_ARTIFACT_MISSING')
    observation = next((o for o in observations if o['id'] == spec.get('observation_id')), None)
    if observation is None:
        return unavailable('OUTPUT_OBSERVATION_MISSING')
    try:
        published_input = yaml.safe_load(artifact['content'])
    except (KeyError, ValueError, TypeError, yaml.YAMLError):
        return unavailable('INPUT_PARSE_ERROR')
    try:
        fresh_output = json.loads(observation['excerpt'])
    except (KeyError, ValueError, TypeError):
        return unavailable('OUTPUT_PARSE_ERROR')
    try:
        expected = json_pointer(published_input, spec['input_pointer'])
    except (KeyError, IndexError, ValueError, TypeError):
        return unavailable('INPUT_FIELD_UNAVAILABLE')
    try:
        actual = json_pointer(fresh_output, spec['output_pointer'])
    except (KeyError, IndexError, ValueError, TypeError):
        return unavailable('OUTPUT_FIELD_UNAVAILABLE')
    try:
        equal = json.dumps(expected, sort_keys=True, ensure_ascii=False) == json.dumps(actual, sort_keys=True, ensure_ascii=False)
    except (ValueError, TypeError):
        return unavailable('NON_JSON_VALUE')
    return {'status': 'EQUAL' if equal else 'DIFFERENT', 'spec': dict(spec),
            'artifact_content_digest': artifact.get('content_digest'),
            'observation_content_digest': observation.get('content_digest'),
            'interpretation': 'Exact public value comparison; semantic relevance is not independently proved'}
