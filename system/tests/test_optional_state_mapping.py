"""State descriptions are optional; original obligations keep their own checks."""
import copy

from jsonschema import Draft202012Validator
import pytest

from contractedge.changeset.prompt import _output_schema
from contractedge.changeset.state_mapping import validate_state_mapping
from contractedge.contracts.validation import validate_changeset, validate_diagnosis, parse_method_input
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan


def diagnosis(requirement):
    return {'requirement_spec': {'raw_text': requirement},
            'obligation_atoms': [{'id': 'A1'}, {'id': 'A2'}], 'evidence_refs': [{'id': 'EV1'}]}


def row(case='present', state='enabled', atom='A1', evidence='EV1'):
    return dict(case=case, observed_state=state, obligation_refs=[atom], evidence_refs=[evidence])


@pytest.mark.parametrize('requirement', [
    'Distinguish an absent not-requested resource from an existing requested resource.',
    'Do not distinguish absent and present resources; preserve identical behavior.',
    'Distinguish three states: not requested, requested but absent, and present.',
])
def test_original_words_do_not_create_a_separate_mandatory_table(requirement):
    assert validate_state_mapping({}, diagnosis(requirement)) == []


def test_distinct_scenarios_may_have_the_same_observed_behavior():
    raw = 'Do not distinguish absent and present resources; preserve identical behavior.'
    rows = [row('absent', 'nil'), row('present', 'nil')]
    assert validate_state_mapping({'state_mapping': rows}, diagnosis(raw)) == []


@pytest.mark.parametrize('rows', [[], [row()]])
def test_optional_table_has_no_invented_minimum_number_of_cases(rows):
    assert list(Draft202012Validator(_output_schema()['properties']['state_mapping']).iter_errors(rows)) == []
    assert validate_state_mapping({'state_mapping': rows}, diagnosis('Keep the default.')) == []


@pytest.mark.parametrize('rows', [None, {}, [None], [row(atom='UNKNOWN')],
    [row(evidence='UNKNOWN')], [row(), row()], [{**row(), 'obligation_refs': []}],
    [{**row(), 'observed_state': ' '}], [{**row(), 'evidence_refs': ['EV1', 'EV1']}],
    [{**row(), 'obligation_refs': [1]}]])
def test_optional_table_still_validates_structure_and_actual_references(rows):
    assert validate_state_mapping({'state_mapping': rows}, diagnosis('Keep the default.'))


def test_literal_unknown_is_a_valid_source_state_name_not_a_banned_word():
    assert validate_state_mapping({'state_mapping': [row(state='unknown')]}, diagnosis(
        'Distinguish absent and present resources.')) == []


def test_optional_table_does_not_replace_outcome_and_obligation_coverage():
    original = valid_diagnosis()
    original['obligation_atoms'].append({'id': 'A2', 'outcome_id': 'O1',
        'statement': 'Preserve the enabled condition.', 'source_refs': ['requirement_text:0:49'],
        'support': 'DERIVED'})
    original['expected_relations'].append({**copy.deepcopy(original['expected_relations'][0]),
        'id': 'E2', 'obligation_refs': ['A2']})
    parsed = validate_diagnosis(original, parse_method_input(valid_method_input()), None)
    plan = valid_plan()
    from contractedge.contracts.validation import canonical_digest
    plan['diagnosis_digest'] = canonical_digest(parsed.raw)
    plan['state_mapping'] = [row(atom='A2')]
    result = validate_changeset(plan, parsed, None)
    assert 'OBLIGATION_NOT_COVERED' in {issue.code for issue in result.errors}
    plan['outcome_coverage'] = []
    result = validate_changeset(plan, parsed, None)
    assert 'OUTCOME_NOT_COVERED' in {issue.code for issue in result.errors}
