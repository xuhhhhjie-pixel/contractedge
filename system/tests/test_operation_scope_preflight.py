"""Independent local errors must reach the sole correction together."""
from copy import deepcopy

import pytest

from contractedge.contracts.validation import diagnosis_preflight_issues
from contractedge.orchestration.runner import _bind_diagnosis_envelope
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import parse_method_input
from contractedge.retrieval.candidates import CandidateSet
from tests.test_contracts import valid_diagnosis, valid_method_input


def test_unrelated_scope_omission_is_reported_with_an_independent_extra_field():
    payload = valid_diagnosis()
    payload['operation_assessments'] = [
        dict(operation_id='OP-failed', disposition='REQUIRES_SUCCESS',
             outcome_refs=['O1'], rationale='Operation failed', unexpected=None),
        dict(operation_id='OP-control', disposition='UNRELATED',
             outcome_refs=[], rationale='Control outside the trigger'),
    ]
    original = deepcopy(payload)
    issues = diagnosis_preflight_issues(payload)
    assert any(i.code == 'SCHEMA_INVALID' and 'unexpected' in i.message for i in issues)
    assert any(i.code == 'OPERATION_UNRELATED_UNSUPPORTED'
               and i.path == 'operation_assessments[1].scope_evidence_refs' for i in issues)
    assert payload == original


@pytest.mark.parametrize('rows', [None, 7, [None], [{'disposition': []}],
                                  [{'disposition': 'UNKNOWN'}]])
def test_malformed_or_unknown_assessment_does_not_crash_independent_preflight(rows):
    payload = valid_diagnosis()
    payload['operation_assessments'] = rows
    diagnosis_preflight_issues(payload)


def test_scope_only_citation_is_bound_to_canonical_evidence():
    method = parse_method_input(valid_method_input())
    context = RunContext('run-1', method.case_id, 0, 'snap-0', None, method.input_digest)
    candidate = dict(id='CU-control', path='consumer.py', locator=dict(start_line=1, end_line=1),
                     snapshot_id='snap-0', text='def control(): pass\n', content_digest='b'*64)
    candidates = CandidateSet((candidate,), ('CU-control',), (), {}, (), 10, 10000, 12)
    payload = valid_diagnosis()
    payload['evidence_refs'] = []
    payload['units'] = []
    payload['expected_relations'] = []
    payload['violations'] = []
    payload['outcome_coverage'] = []
    payload['operation_assessments'] = [dict(operation_id='OP-control', disposition='UNRELATED',
        outcome_refs=[], rationale='Different trigger', scope_evidence_refs=['SRC-CU-control'])]
    bound, _ = _bind_diagnosis_envelope(payload, method, context, payload['requirement_spec'], candidates)
    assert [r['id'] for r in bound['evidence_refs']] == ['SRC-CU-control']
    payload['operation_assessments'][0]['scope_evidence_refs'] = ['FORGED']
    with pytest.raises(ValueError, match='unknown catalog ID'):
        _bind_diagnosis_envelope(payload, method, context, payload['requirement_spec'], candidates)


def test_direct_packet_validation_rejects_scope_refs_absent_from_bound_evidence():
    from contractedge.contracts.models import ContractError
    from contractedge.contracts.validation import validate_diagnosis
    payload = valid_diagnosis()
    payload['operation_assessments'] = [dict(operation_id='OP-control', disposition='UNRELATED',
        outcome_refs=[], rationale='Different trigger', scope_evidence_refs=['FORGED'])]
    with pytest.raises(ContractError, match='REFERENCE_NOT_FOUND'):
        validate_diagnosis(payload, parse_method_input(valid_method_input()), None)
