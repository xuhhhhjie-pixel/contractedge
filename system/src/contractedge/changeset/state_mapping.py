"""Validate optional source-bound state descriptions without inferring new duties.

Requirement, obligation and postcondition coverage remain in their existing
validators. A state table is one possible explanation, never another coverage
or distinct-behavior requirement inferred from natural-language keywords.
"""
from __future__ import annotations

from typing import Any, Mapping


def validate_state_mapping(payload: Mapping[str, Any], diagnosis: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    if 'state_mapping' not in payload:
        return []
    rows = payload['state_mapping']
    if not isinstance(rows, list):
        return [('STATE_MAPPING_INVALID', 'state_mapping', 'State mapping must be an array when provided')]
    evidence_ids = {record['id'] for record in diagnosis.get('evidence_refs', [])
                    if isinstance(record, Mapping) and isinstance(record.get('id'), str)}
    atom_ids = {atom['id'] for atom in diagnosis.get('obligation_atoms', [])
                if isinstance(atom, Mapping) and isinstance(atom.get('id'), str)}
    seen_cases, issues = set(), []
    for index, row in enumerate(rows):
        path = f'state_mapping[{index}]'
        if not isinstance(row, Mapping):
            issues.append(('STATE_MAPPING_INVALID', path, 'Each state mapping case must be an object'))
            continue
        case, state = row.get('case'), row.get('observed_state')
        if not isinstance(case, str) or not case.strip() or not isinstance(state, str) or not state.strip():
            issues.append(('STATE_MAPPING_INVALID', path, 'Each case needs non-empty case and observed_state text'))
            continue
        case_key = case.strip().casefold()
        if case_key in seen_cases:
            issues.append(('STATE_MAPPING_DUPLICATE_CASE', path, 'Each described case must appear exactly once'))
        seen_cases.add(case_key)
        for key, allowed, label in [('obligation_refs', atom_ids, 'obligation atom'),
                                    ('evidence_refs', evidence_ids, 'supplied source/observation evidence')]:
            references = row.get(key)
            if (not isinstance(references, list) or not references
                    or any(not isinstance(ref, str) or ref not in allowed for ref in references)
                    or len(set(references)) != len(references)):
                issues.append(('STATE_MAPPING_INVALID', path + '.' + key,
                               'Bind each case to unique existing ' + label + ' IDs'))
    return issues

