"""Correction feedback must describe actual contradictory plan fields."""
from contractedge.contracts.validation import validate_diagnosis,parse_method_input,validate_changeset
from tests.test_contracts import valid_diagnosis,valid_method_input,valid_plan


def test_needs_evidence_empty_changes_reports_planned_ids_not_imaginary_edits():
    d=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
    p=valid_plan();p.update(status='NEEDS_EVIDENCE',changes=[],postconditions=[])
    result=validate_changeset(p,d,None)
    issues=[i for i in result.errors if i.code=='STATUS_INCONSISTENT']
    assert not result.valid
    assert any(i.path=='planned_violation_ids' and 'planned_violation_ids must be empty' in i.message for i in issues)
    assert not any(i.path=='changes' for i in issues)


def test_needs_evidence_with_edits_and_planned_ids_reports_both_fields():
    d=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
    p=valid_plan();p['status']='NEEDS_EVIDENCE'
    result=validate_changeset(p,d,None)
    paths={i.path for i in result.errors if i.code=='STATUS_INCONSISTENT'}
    assert {'changes','planned_violation_ids'}<=paths and not result.valid
