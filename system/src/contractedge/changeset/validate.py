from __future__ import annotations

from typing import Any, Mapping

from contractedge.contracts.models import DiagnosisPacket, Snapshot, ValidationResult, ValidationIssue
from contractedge.contracts.validation import validate_changeset
from contractedge.contracts.permissions import target_permission_error
from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
from contractedge.contracts.public_checks import public_check_catalog


def validate_semantic_changeset(payload: Mapping[str, Any], diagnosis: DiagnosisPacket,
                                snapshot: Snapshot | None, *, policy: Mapping | None = None,
                                frontier: list | None = None,
                                prerequisites: list | None = None, capabilities: Mapping | None = None) -> ValidationResult:
    result = validate_changeset(payload, diagnosis, snapshot)
    if result.value is None:
        return result
    issues = list(result.errors)
    try:
        consumer = resolve_execution_capabilities(capabilities)
        check_catalog = public_check_catalog(consumer)
    except ValueError as exc:
        issues.append(ValidationIssue('INVALID_EXECUTION_CAPABILITIES', 'public_execution_capabilities', str(exc)))
        return ValidationResult(False, tuple(issues), result.value)
    for index, change in enumerate(payload['changes']):
        if change['operation'] not in consumer['supported_operations']:
            issues.append(ValidationIssue('UNSUPPORTED_EXECUTION_OPERATION', f'changes[{index}].operation',
                'Execution consumer supports only ' + ', '.join(consumer['supported_operations'])))
    if prerequisites is not None:
        expected = {r['source_evidence_ref'] for r in prerequisites}
        rows = payload.get('precondition_review', [])
        supplied = [r['source_evidence_ref'] for r in rows]
        if set(supplied) != expected or len(supplied) != len(set(supplied)):
            issues.append(ValidationIssue('PRECONDITION_REVIEW_INCOMPLETE', 'precondition_review',
                'Review every supplied prerequisite source exactly once: '+', '.join(sorted(expected))))
        checks = {r['id']:r for kind in ('postconditions', 'preserve_constraints') for r in payload.get(kind, [])}
        opened = {r['id'] for r in payload.get('open_obligations', [])}
        for i, row in enumerate(rows):
            refs, unresolved = row['verification_refs'], row['open_obligation_refs']
            disposition = row['disposition']
            valid = set(refs) <= set(checks) and set(unresolved) <= opened
            valid = valid and ((disposition == 'REQUIRED' and bool(refs))
                or (disposition == 'UNRESOLVED' and bool(unresolved) and not refs)
                or (disposition == 'NOT_APPLICABLE' and not refs and not unresolved))
            if not valid:
                issues.append(ValidationIssue('PRECONDITION_VERIFICATION_INVALID', f'precondition_review[{i}]',
                    'Required conditions bind real verification IDs; unresolved conditions bind real open obligations; not-applicable has no verification claim'))
            elif disposition == 'REQUIRED' and not unresolved and not any(
                    checks[r].get('verification_kind') in {'PUBLIC_BEHAVIOR', 'PUBLIC_STRUCTURE', 'DECLARED_INPUT_EQUALITY'} for r in refs):
                issues.append(ValidationIssue('PRECONDITION_UNVERIFIED', f'precondition_review[{i}]',
                    'Build-only or unknown checks cannot verify runtime prerequisites; retain an open obligation'))
    for index, change in enumerate(payload['changes']):
        target = change['target']
        path = target['path'] if target['kind'] == 'EXISTING' else target['proposed_path']
        error = target_permission_error(path, policy)
        if error:
            issues.append(ValidationIssue(error, f'changes[{index}].target',
                'Source evidence is read-only unless public edit policy permits this target: ' + path))
    for kind in ('postconditions','preserve_constraints','open_obligations'):
        for i,item in enumerate(payload.get(kind, [])):
            refs = item.get('behavior_check_refs', [])
            path = kind + f'[{i}]'
            if refs and (item.get('check_coverage') not in {'ALL_OF','NECESSARY_ONLY','SUPPORTING'} or not item.get('coverage_rationale')):
                issues.append(ValidationIssue('CHECK_COVERAGE_REQUIRED', path,
                    'Probe bindings must explicitly declare necessary/sufficient scope and rationale'))
            # Legacy standalone validators may have no capability declaration.
            # Actual declared contexts must not accept invented check IDs.
            if 'registered_public_checks' in consumer or 'method_behavior_probes' in consumer:
                for ref in refs:
                    definition = check_catalog.get(ref)
                    if definition is None:
                        issues.append(ValidationIssue('UNREGISTERED_BEHAVIOR_CHECK', path, 'Unknown public check: ' + ref))
                    elif definition['scenario'] is not None and (item.get('verification_scope') or {}).get('scenario') != definition['scenario']:
                        issues.append(ValidationIssue('BEHAVIOR_CHECK_SCENARIO_MISMATCH', path,
                            'Binding ' + ref + ' requires declared scenario ' + definition['scenario']))
    if diagnosis.raw.get('requirement_spec',{}).get('traceability_required'):
        if payload.get('preserve_constraints') and 'preservation_impact' not in payload:
            issues.append(ValidationIssue('PRESERVATION_IMPACT_INCOMPLETE','preservation_impact',
                'New traceability plans must account for all preservation impacts'))
    if 'preservation_impact' in payload:
        rows = payload['preservation_impact']
        expected = {p['id'] for p in payload['preserve_constraints']}
        supplied = [r['preserve_id'] for r in rows]
        if set(supplied) != expected or len(supplied) != len(set(supplied)):
            issues.append(ValidationIssue('PRESERVATION_IMPACT_INCOMPLETE','preservation_impact',
                'Every preservation constraint needs exactly one impact scope'))
        changes = {c['id'] for c in payload['changes']}
        for row in rows:
            if (not set(row['change_refs']) <= changes or
                    (row['scope']=='CHANGES' and not row['change_refs']) or
                    (row['scope']=='GLOBAL' and row['change_refs'])):
                issues.append(ValidationIssue('PRESERVATION_IMPACT_INVALID','preservation_impact',
                    'CHANGES needs real influencing changes; GLOBAL has no speculative change ids'))
    if frontier is not None:
        expected = {r['source_evidence_id']:r for r in frontier}
        rows = payload.get('production_path_review', [])
        supplied = [r['source_evidence_id'] for r in rows]
        if set(supplied) != set(expected) or len(supplied) != len(set(supplied)):
            issues.append(ValidationIssue('PRODUCTION_BRANCH_UNACCOUNTED', 'production_path_review',
                'Review every supplied lexical sibling exactly once: ' + ', '.join(expected)))
        changes = {c['id']:c for c in payload['changes']}
        opened = {o['id'] for o in payload['open_obligations']}
        for i,row in enumerate(rows):
            item = expected.get(row['source_evidence_id'])
            if item is None: continue
            path = f'production_path_review[{i}]'
            if row['disposition'] == 'MODIFY':
                refs = row['change_refs']
                def covers(cid):
                    target = changes.get(cid, {}).get('target', {})
                    branch = item['locator']
                    if target.get('kind') != 'EXISTING' or target.get('path') != item['path']:
                        return False
                    units = {u['id']: u for u in diagnosis.raw['units']}
                    evidence = {e['id']: e for e in diagnosis.raw['evidence_refs']}
                    for uid in target.get('unit_refs', []):
                        for eid in units.get(uid, {}).get('locator_refs', []):
                            record = evidence.get(eid, {})
                            loc = record.get('locator', {})
                            if (record.get('kind') == 'SOURCE_SPAN' and loc.get('path') == item['path']
                                and type(loc.get('start_line')) is int and type(loc.get('end_line')) is int
                                and loc['start_line'] <= branch['end_line'] and loc['end_line'] >= branch['start_line']):
                                return True
                    return False
                if not refs or not all(covers(c) for c in refs):
                    issues.append(ValidationIssue('PRODUCTION_BRANCH_CHANGE_INVALID', path,
                        'MODIFY requires actual changes targeting this evidenced branch; file equality alone is insufficient'))
                if row['open_obligation_refs']:
                    issues.append(ValidationIssue('PRODUCTION_BRANCH_MODIFY_OPEN_REFS',
                        path + '.open_obligation_refs',
                        'MODIFY requires empty open_obligation_refs in this branch row. '
                        'Keep unverified runtime behavior in the plan open_obligations and corresponding checks; '
                        'do not delete those obligations or claim verification. If the repair intent for this branch '
                        'itself is unresolved, use UNRESOLVED with real open_obligation_refs and no change_refs.'))
            elif row['disposition'] == 'UNRESOLVED':
                refs = row['open_obligation_refs']
                if not refs or not set(refs) <= opened or row['change_refs']:
                    issues.append(ValidationIssue('PRODUCTION_BRANCH_OPEN_INVALID', path,
                        'Unresolved production branch must reference existing open obligations'))
            elif row['change_refs'] or row['open_obligation_refs']:
                issues.append(ValidationIssue('PRODUCTION_BRANCH_DISPOSITION_INVALID', path,
                    'PRESERVE/OUT_OF_SCOPE requires rationale, without claiming modification or unresolved refs'))
    return ValidationResult(not issues, tuple(issues), result.value)
