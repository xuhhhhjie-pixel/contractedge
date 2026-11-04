from contractedge.changeset.state_mapping import validate_state_mapping
from tests.test_contracts import valid_diagnosis


def diagnosis_with_states():
    raw = valid_diagnosis()
    raw["requirement_spec"]["raw_text"] = (
        "A requested value with absent external state must differ from a not-requested value; "
        "an already enabled external state remains present."
    )
    raw["obligation_atoms"] = [
        {"id": "A1", "outcome_id": "O1", "statement": "Requested but absent is distinct", "source_quote": "requested value with absent external state", "support": "EXPLICIT"},
        {"id": "A2", "outcome_id": "O1", "statement": "Not requested remains unset", "source_quote": "not-requested value", "support": "EXPLICIT"},
        {"id": "A3", "outcome_id": "O1", "statement": "Enabled external state remains present", "source_quote": "already enabled external state remains present", "support": "EXPLICIT"},
    ]
    # Coverage is anchored to original requirement text, not a source-code ID.
    for atom in raw['obligation_atoms']:
        atom['source_refs'] = ['requirement_text:0:' + str(len(raw['requirement_spec']['raw_text']))]
    raw["evidence_refs"] = [
        {"id": "EV2", "snapshot_id": "snap-0", "kind": "SOURCE_SPAN",
         "locator": {"path": "src.go", "start_line": 1, "end_line": 4},
         "content_digest": "b" * 64, "excerpt": "state", "origin": "source"}
    ]
    return raw


def test_stateful_requirement_does_not_require_an_optional_table():
    raw = diagnosis_with_states()
    assert validate_state_mapping({}, raw) == []
    assert validate_state_mapping({"state_mapping": []}, raw) == []


def test_state_mapping_accepts_distinct_source_bound_cases():
    raw = diagnosis_with_states()
    payload = {"state_mapping": [
        {"case": "requested but external state absent", "observed_state": "false", "obligation_refs": ["A1"], "evidence_refs": ["EV2"]},
        {"case": "not requested", "observed_state": "nil", "obligation_refs": ["A2"], "evidence_refs": ["EV2"]},
        {"case": "external state present and enabled", "observed_state": "true", "obligation_refs": ["A3"], "evidence_refs": ["EV2"]},
    ]}
    assert validate_state_mapping(payload, raw) == []


def test_state_mapping_is_not_a_second_obligation_coverage_check():
    raw = diagnosis_with_states()
    payload = {"state_mapping": [
        {"case": "requested but external state absent", "observed_state": "false", "obligation_refs": ["A1"], "evidence_refs": ["EV2"]},
        {"case": "not requested", "observed_state": "nil", "obligation_refs": ["A2"], "evidence_refs": ["EV2"]},
        {"case": "external state present and enabled", "observed_state": "true", "obligation_refs": ["A2"], "evidence_refs": ["EV2"]},
    ]}
    assert validate_state_mapping(payload, raw) == []


def test_state_mapping_does_not_ban_a_literal_state_name():
    raw = diagnosis_with_states()
    payload = {"state_mapping": [
        {"case": "requested but external state absent", "observed_state": "unknown", "obligation_refs": ["A1"], "evidence_refs": ["EV2"]},
        {"case": "not requested", "observed_state": "nil", "obligation_refs": ["A2"], "evidence_refs": ["EV2"]},
        {"case": "external state present and enabled", "observed_state": "true", "obligation_refs": ["A3"], "evidence_refs": ["EV2"]},
    ]}
    assert validate_state_mapping(payload, raw) == []

