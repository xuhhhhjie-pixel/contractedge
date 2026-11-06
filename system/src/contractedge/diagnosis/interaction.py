"""Diagnosis model interaction for source context v2: a lookup or a final diagnosis.

FRONT_HALF_REDESIGN_IMPLEMENTATION_SPEC section 6.1. A lookup response only
states what still needs to be read; it does not require atoms, relations,
violations or a status, never passes through diagnosis_gate and is never a
FINAL_DIAGNOSIS. A diagnosis response carries the unchanged DiagnosisPacket
body, checked against this request's model schema before binding and the
existing semantic validation. The two are mutually exclusive.

The wire shape is one closed object with a discriminator rather than a root
union, so strict JSON-schema transports can encode it as well.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from jsonschema import Draft202012Validator

_TEXT = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}

LOOKUP_SCHEMA: dict[str, Any] = {
    'type': 'object',
    'required': ['evidence_gaps'],
    'additionalProperties': False,
    'properties': {
        'evidence_gaps': {
            'type': 'array', 'minItems': 1,
            'items': {
                'type': 'object', 'required': ['summary'], 'additionalProperties': False,
                'properties': {
                    'summary': _TEXT,
                    'locator_hint': {
                        'type': 'object', 'additionalProperties': False, 'minProperties': 1,
                        'properties': {'path': _TEXT, 'symbol': _TEXT, 'search_term': _TEXT},
                    },
                },
            },
        },
    },
}

INTERACTION_PROMPT = (
    ' Respond with exactly one JSON object whose response_kind is LOOKUP or DIAGNOSIS.'
    ' Use LOOKUP when you still need to read source before a sound diagnosis: set lookup.evidence_gaps to the'
    ' concrete questions, each with a summary and, when you know one, a locator_hint path, symbol or search_term.'
    ' A LOOKUP needs no atoms, relations, violations or status; the runner\'s locator reads the requested source'
    ' and asks you again with the complete bodies. You may describe a question without guessing a function name.'
    ' Use DIAGNOSIS for the complete DiagnosisPacket body under the diagnosis key, following output_schema.diagnosis.'
    ' Never return both, and never present a partial diagnosis as a lookup or a lookup as a diagnosis.'
    ' Read-only dependency source explains an API; it does not prove that this project calls it and grants no'
    ' permission to edit the dependency.')


def interaction_output_schema(diagnosis_schema: Mapping[str, Any]) -> dict[str, Any]:
    """Closed discriminated envelope around the unchanged per-case diagnosis schema."""
    return {
        'type': 'object', 'required': ['response_kind'], 'additionalProperties': False,
        'properties': {
            'response_kind': {'type': 'string', 'enum': ['LOOKUP', 'DIAGNOSIS']},
            'lookup': LOOKUP_SCHEMA,
            'diagnosis': dict(diagnosis_schema),
        },
    }


class InteractionError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        super().__init__(f'{code} at {path or "<root>"}: {message}')
        self.code, self.path = code, path


@dataclass(frozen=True)
class InteractionResponse:
    kind: str                                # 'LOOKUP' | 'DIAGNOSIS'
    evidence_gaps: tuple[dict[str, Any], ...] = ()
    diagnosis_content: str | None = None     # JSON text of the model's diagnosis body


def _first_error(validator: Draft202012Validator, value: Any, prefix: str) -> InteractionError | None:
    errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    error = errors[0]
    path = prefix + ''.join(f'[{p}]' if isinstance(p, int) else f'.{p}' for p in error.absolute_path)
    return InteractionError('MODEL_SCHEMA_MISMATCH', path, error.message[:500])


def parse_diagnosis_interaction(content: str, diagnosis_schema: Mapping[str, Any]) -> InteractionResponse:
    """Classify one model reply; a malformed or mixed reply never bypasses validation."""
    try:
        value = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise InteractionError('INVALID_JSON', '', str(exc)[:200]) from exc
    if not isinstance(value, dict):
        raise InteractionError('INVALID_JSON', '', 'response must be a JSON object')
    # A strict wire may send absent optional members as null.
    present = {key: item for key, item in value.items() if item is not None}
    unknown = set(present) - {'response_kind', 'lookup', 'diagnosis'}
    if unknown:
        raise InteractionError('INTERACTION_UNKNOWN_FIELD', '', 'unexpected fields ' + ', '.join(sorted(unknown)))
    kind = present.get('response_kind')
    if kind not in ('LOOKUP', 'DIAGNOSIS'):
        raise InteractionError('INTERACTION_KIND_INVALID', 'response_kind', 'must be LOOKUP or DIAGNOSIS')
    if ('lookup' in present) == ('diagnosis' in present) or (kind == 'LOOKUP') != ('lookup' in present):
        raise InteractionError('INTERACTION_AMBIGUOUS', '', 'exactly one of lookup or diagnosis must match response_kind')
    if kind == 'LOOKUP':
        error = _first_error(Draft202012Validator(LOOKUP_SCHEMA), present['lookup'], 'lookup')
        if error:
            raise error
        gaps = tuple({'id': f'L{index}', 'summary': gap['summary'].strip(),
                      **({'locator_hint': {k: v.strip() for k, v in gap['locator_hint'].items()}}
                         if gap.get('locator_hint') else {})}
                     for index, gap in enumerate(present['lookup']['evidence_gaps'], 1))
        return InteractionResponse('LOOKUP', evidence_gaps=gaps)
    body = present['diagnosis']
    error = _first_error(Draft202012Validator(diagnosis_schema), body, 'diagnosis')
    if error:
        raise error
    return InteractionResponse('DIAGNOSIS', diagnosis_content=json.dumps(body, ensure_ascii=False))
