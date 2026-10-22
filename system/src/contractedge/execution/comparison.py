"""Deterministic comparisons of published inputs and actual public JSON output.

The comparison establishes equality only. Selection of the required artifact,
resource and field is still a semantic claim, never an independent behavior score.
"""
import json
from typing import Mapping
import yaml


def json_pointer(value, pointer):
    if not isinstance(pointer, str) or pointer and not pointer.startswith('/'):
        raise ValueError('Comparison needs a JSON Pointer, including empty root pointer')
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
    try:
        artifact = next(a for a in artifacts if a['id'] == spec['artifact_id'])
        observation = next(o for o in observations if o['id'] == spec['observation_id'])
        # All actual values come from the pinned input and fresh output record.
        expected = json_pointer(yaml.safe_load(artifact['content']), spec['input_pointer'])
        actual = json_pointer(json.loads(observation['excerpt']), spec['output_pointer'])
        equal = json.dumps(expected, sort_keys=True, ensure_ascii=False) == json.dumps(actual, sort_keys=True, ensure_ascii=False)
    except (StopIteration, KeyError, IndexError, ValueError, TypeError, yaml.YAMLError):
        return {'status': 'UNAVAILABLE', 'spec': dict(spec),
                'interpretation': 'Missing/non-JSON output or invalid pointer; no equality claim'}
    return {'status': 'EQUAL' if equal else 'DIFFERENT', 'spec': dict(spec),
            'artifact_content_digest': artifact.get('content_digest'),
            'observation_content_digest': observation.get('content_digest'),
            'interpretation': 'Exact public value comparison; semantic relevance is not independently proved'}
