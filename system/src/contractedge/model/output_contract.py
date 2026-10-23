"""Strict transport projection for the closed inline schemas used by the model ports.

Optional fields become nullable only on the wire. Their original absent/null
distinction is restored before the unchanged application contract is validated.
This does not replace semantic validation or resolve arbitrary external schemas.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Mapping

from jsonschema import Draft202012Validator


def _strict_node(schema, path='$'):
    if not isinstance(schema, Mapping):
        raise ValueError('Strict output requires an object schema at ' + path)
    unsupported = {'$ref', '$defs', 'definitions', 'oneOf', 'allOf', 'not', 'if', 'then', 'else', 'patternProperties'} & schema.keys()
    if unsupported:
        raise ValueError('Unsupported strict transport keyword at ' + path + ': ' + ', '.join(sorted(unsupported)))
    result = deepcopy(dict(schema))
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    if 'object' in types or 'properties' in schema:
        properties = schema.get('properties')
        if not isinstance(properties, Mapping) or schema.get('additionalProperties') is not False:
            raise ValueError('Strict output requires closed object properties at ' + path)
        required = set(schema.get('required', []))
        wire = {}
        for name, prop in properties.items():
            lowered = _strict_node(prop, path + '/properties/' + name)
            if name not in required and not Draft202012Validator(prop).is_valid(None):
                lowered = {'anyOf': [lowered, {'type': 'null'}]}
            wire[name] = lowered
        result.update(properties=wire, required=list(properties), additionalProperties=False)
    if 'items' in schema:
        result['items'] = _strict_node(schema['items'], path + '/items')
    if 'anyOf' in schema:
        result['anyOf'] = [_strict_node(branch, path + '/anyOf/' + str(index))
                           for index, branch in enumerate(schema['anyOf'])]
    return result


def strict_transport_schema(schema: Mapping) -> dict:
    Draft202012Validator.check_schema(schema)
    if schema.get('type') != 'object' or 'anyOf' in schema:
        raise ValueError('Strict output root must be an object without a root union')
    result = _strict_node(schema)
    Draft202012Validator.check_schema(result)
    return result


def _restore(value, schema):
    if 'anyOf' in schema:
        candidates = []
        for branch in schema['anyOf']:
            if Draft202012Validator(_strict_node(branch)).is_valid(value):
                candidate = _restore(value, branch)
                if Draft202012Validator(branch).is_valid(candidate):
                    candidates.append(candidate)
        if candidates and all(item == candidates[0] for item in candidates):
            return candidates[0]
        return value
    if isinstance(value, dict) and 'properties' in schema:
        result = {}
        required = set(schema.get('required', []))
        for name, item in value.items():
            prop = schema['properties'].get(name)
            if prop is None:
                result[name] = item
            elif item is None and name not in required and not Draft202012Validator(prop).is_valid(None):
                continue
            else:
                result[name] = _restore(item, prop)
        return result
    if isinstance(value, list) and 'items' in schema:
        return [_restore(item, schema['items']) for item in value]
    return value


def restore_strict_content(content: str, original: Mapping, wire: Mapping) -> tuple[str, str]:
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return content, 'INVALID_JSON'
    if not Draft202012Validator(wire).is_valid(value):
        return content, 'WIRE_SCHEMA_MISMATCH'
    result = _restore(value, original)
    if not Draft202012Validator(original).is_valid(result):
        return content, 'RESTORE_SCHEMA_MISMATCH'
    return json.dumps(result, ensure_ascii=False), 'RESTORED' if result != value else 'UNCHANGED'
