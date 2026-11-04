"""Request-related words alone do not create an additional observable state."""
import pytest

from contractedge.changeset.state_mapping import validate_state_mapping
from tests.test_task_rule_scope import state_diagnosis, STATES


@pytest.mark.parametrize('raw', [
    'Distinguish an absent requested resource from an existing requested resource.',
    'Distinguish a missing desired resource from a present desired resource.',
    'Differentiate configured and unset declaration values.',
])
def test_requested_two_state_requirement_accepts_two_source_bound_cases(raw):
    diagnosis = state_diagnosis(raw, [raw])
    rows = [dict(case=case, observed_state=state, obligation_refs=['A1'], evidence_refs=['EV1'])
            for case, state in [('absent', 'nil'), ('present', 'configured value')]]
    assert validate_state_mapping({'state_mapping': rows}, diagnosis) == []


@pytest.mark.parametrize('raw', [
    STATES,
    'Distinguish three states: absent, present, and pending resources.',
])
def test_explicit_three_states_do_not_impose_a_separate_representation(raw):
    diagnosis = state_diagnosis(raw, [raw])
    assert validate_state_mapping({}, diagnosis) == []
