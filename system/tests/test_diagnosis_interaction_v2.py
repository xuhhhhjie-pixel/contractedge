"""Diagnosis interface for source context v2 (front-half redesign batch 3a).

Spec section 6: lookup and final diagnosis are separate, mutually exclusive
replies; the model's own object is never rewritten by binding; corrections
point at fields the model can edit; no fixed expression caps.
"""
import copy
import json

import pytest

from contractedge.diagnosis.interaction import (InteractionError, LOOKUP_SCHEMA, interaction_output_schema,
                                                parse_diagnosis_interaction)
from contractedge.model.output_contract import strict_transport_schema
from contractedge.model.schema import constrained_output_schema
from contractedge.orchestration.runner import _append_diagnosis_retry_turn, _model_field_feedback

BODY_SCHEMA = {'type': 'object', 'required': ['status'], 'additionalProperties': False,
               'properties': {'status': {'type': 'string', 'enum': ['READY', 'EVIDENCE_GAP']},
                              'evidence_gaps': {'type': 'array', 'items': {
                                  'type': 'object', 'additionalProperties': False,
                                  'properties': {'summary': {'type': 'string'}}}}}}


def reply(**value):
    return json.dumps(value)


def test_lookup_needs_only_questions_and_gets_runner_ids():
    result = parse_diagnosis_interaction(reply(response_kind='LOOKUP', lookup={'evidence_gaps': [
        {'summary': 'Which consumer reads PlainHTTP?'},
        {'summary': 'Where is the reload callback?', 'locator_hint': {'path': ' cmd/main.go '}}]}), BODY_SCHEMA)
    assert result.kind == 'LOOKUP' and result.diagnosis_content is None
    assert [g['id'] for g in result.evidence_gaps] == ['L1', 'L2']
    assert result.evidence_gaps[1]['locator_hint'] == {'path': 'cmd/main.go'}


@pytest.mark.parametrize('lookup', [
    {'evidence_gaps': []},
    {'evidence_gaps': [{'summary': '   '}]},
    {'evidence_gaps': [{'summary': 'x', 'locator_hint': {}}]},
    {'evidence_gaps': [{'summary': 'x', 'locator_hint': {'symbol': ' '}}]},
    {'evidence_gaps': [{'id': 'G1', 'summary': 'x'}]},
])
def test_invalid_lookup_is_rejected_not_relaxed(lookup):
    with pytest.raises(InteractionError):
        parse_diagnosis_interaction(reply(response_kind='LOOKUP', lookup=lookup), BODY_SCHEMA)


@pytest.mark.parametrize('value', [
    {'response_kind': 'LOOKUP', 'lookup': {'evidence_gaps': [{'summary': 'x'}]}, 'diagnosis': {'status': 'READY'}},
    {'response_kind': 'DIAGNOSIS', 'lookup': {'evidence_gaps': [{'summary': 'x'}]}},
    {'response_kind': 'LOOKUP'},
    {'response_kind': 'MAYBE', 'diagnosis': {'status': 'READY'}},
    {'status': 'READY'},
])
def test_mixed_or_unlabelled_replies_cannot_bypass_validation(value):
    with pytest.raises(InteractionError):
        parse_diagnosis_interaction(json.dumps(value), BODY_SCHEMA)


def test_diagnosis_branch_is_checked_against_this_request_schema_first():
    good = parse_diagnosis_interaction(reply(response_kind='DIAGNOSIS', diagnosis={'status': 'READY'},
                                             lookup=None), BODY_SCHEMA)
    assert good.kind == 'DIAGNOSIS' and json.loads(good.diagnosis_content) == {'status': 'READY'}
    with pytest.raises(InteractionError) as error:
        parse_diagnosis_interaction(reply(response_kind='DIAGNOSIS', diagnosis={'status': 'DONE'}), BODY_SCHEMA)
    assert error.value.code == 'MODEL_SCHEMA_MISMATCH' and error.value.path == 'diagnosis.status'


def test_interaction_envelope_is_encodable_by_strict_transport():
    wire = strict_transport_schema(interaction_output_schema(BODY_SCHEMA))
    assert set(wire['required']) == {'response_kind', 'lookup', 'diagnosis'}   # optional members become nullable
    assert LOOKUP_SCHEMA['properties']['evidence_gaps']['minItems'] == 1


def test_v2_schema_drops_generation_caps_but_keeps_contract_limits():
    schema = {'type': 'object', 'properties': {
        'units': {'type': 'array', 'items': {'type': 'string'}},
        'evidence_gaps': {'type': 'array', 'items': {'type': 'string'}},
        'kept': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string', 'maxLength': 9}}}}
    compact = constrained_output_schema(schema)
    full = constrained_output_schema(schema, compact_limits=False)
    assert compact['properties']['units']['maxItems'] == 16 and compact['properties']['evidence_gaps']['maxItems'] == 4
    assert compact['properties']['units']['items']['maxLength'] == 600
    assert 'maxItems' not in full['properties']['units'] and 'maxLength' not in full['properties']['units']['items']
    assert full['properties']['kept']['maxItems'] == 5 and full['properties']['kept']['items']['maxLength'] == 9


def test_bind_never_rewrites_the_parsed_model_object(monkeypatch):
    from contractedge.orchestration import runner
    model = {'obligation_atoms': [{'id': 'A1', 'outcome_id': 'O1', 'source_quote': 'q'}]}
    before = copy.deepcopy(model)

    def binder(payload, requirement):
        for atom in payload['obligation_atoms']:
            atom['stable_obligation_key'] = 'OB-x'
        return payload
    monkeypatch.setattr('contractedge.requirement.normalize.bind_obligation_sources', binder)
    monkeypatch.setattr(runner, 'build_evidence_catalog', lambda *a, **k: [])
    monkeypatch.setattr(runner, 'bind_evidence_ids', lambda refs, catalog: [])

    class Context:
        run_id, snapshot_id, retrieval_config = 'run', 'snap', {}

    class Input:
        input_digest = 'input'
    bound, _ = runner._bind_diagnosis_envelope(model, Input(), Context(), {'outcomes': []}, None)
    assert bound['obligation_atoms'][0]['stable_obligation_key'] == 'OB-x'
    assert model == before


def test_feedback_points_at_model_disposition_fields():
    model = {'outcome_coverage': {'O1': {'disposition': 'REPAIR_SUPPORTED'}, 'O2': {'disposition': 'PRESERVE_REQUIRED'}}}
    bound = {'outcome_coverage': [{'outcome_id': 'O1'}, {'outcome_id': 'O2'}]}
    text = ('OUTCOME_SUPPORTED_VIOLATION_UNCOVERED at outcome_coverage[0]: x; '
            'REFERENCE_NOT_FOUND at outcome_coverage[0].repair_supported[1]; '
            'PRESERVE_EVIDENCE_MISSING at outcome_coverage[1].preserve_current_satisfaction[0]; '
            'bad ref outcome_coverage[1].preserve_current_satisfaction[0].evidence_refs[2]. '
            'Remove only those downgraded IDs from outcome_coverage.repair_supported; keep other source-backed '
            'supported violations and their relations.')
    mapped = _model_field_feedback(text, model, bound)
    assert 'outcome_coverage[' not in mapped and 'repair_supported' not in mapped
    assert 'outcome_coverage.O1.disposition' in mapped and 'outcome_coverage.O2.preservation_status' in mapped
    assert 'outcome_coverage.O2.preservation_evidence_refs' in mapped
    assert 'Keep the other valid causes' in mapped
    # A model that wrote the array encoding keeps the original paths.
    assert _model_field_feedback(text, {'outcome_coverage': []}, bound) == text


def test_v2_correction_keeps_every_independent_error_without_tail_cut():
    base = ({'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'})
    first = _append_diagnosis_retry_turn(base, 'draft1', 'E1 ' + 'x' * 20000)
    v1 = _append_diagnosis_retry_turn(first, 'draft2', 'E2 ' + 'y' * 20000)
    v2 = _append_diagnosis_retry_turn(first, 'draft2', 'E2 ' + 'y' * 20000, bounded=False)
    assert 'E1 ' not in v1[-1]['content'] and len(v1[-1]['content'].encode()) <= 24000
    assert 'E1 ' in v2[-1]['content'] and 'E2 ' in v2[-1]['content']
    assert [m['content'] for m in v2 if m['role'] == 'assistant'] == ['draft2']   # only the latest draft
