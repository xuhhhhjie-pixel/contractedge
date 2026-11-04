import copy
import hashlib
import json

import pytest

from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_diagnosis
from contractedge.diagnosis.prompt import build_diagnosis_request
from contractedge.model.admission import context_fits, quote_request
from contractedge.model.budget import BudgetExceeded
from contractedge.model.provider import OpenAICompatibleProvider
from contractedge.requirement.normalize import normalize_requirement
from tests.test_contracts import valid_diagnosis, valid_method_input


def artifact(identifier, content):
    return {
        "id": identifier, "path": f"declared/{identifier}.yaml", "media_type": "application/yaml",
        "content": content, "content_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def requests_for(artifacts, *, schema_in_prompt=True, stage=None):
    raw = valid_method_input()
    raw["declared_artifacts"] = copy.deepcopy(artifacts)
    raw["input_digest"] = canonical_digest({key: value for key, value in raw.items() if key != "input_digest"})
    method = parse_method_input(raw)
    context = RunContext(
        "run-input-delivery", method.case_id, 0, method.initial_snapshot_id, None, method.input_digest,
        model_config={"model": "fixture", "schema_in_prompt": schema_in_prompt},
        retrieval_config={"declared_artifacts": method.declared_artifacts},
        budget={"max_output_tokens": 1000},
    )
    candidates = {"candidates": [], "selected_ids": [],
                  "dropped_ids": ["CU-" + row["id"] for row in artifacts], "context_tokens": 0}
    diagnosis_request = (build_diagnosis_request(
        method, normalize_requirement({"requirement_text": method.requirement_text}), candidates, context)
        if stage != 'planning' else None)
    diagnosis = valid_diagnosis()
    diagnosis["input_digest"] = method.input_digest
    planning_request = build_changeset_request(validate_diagnosis(diagnosis, method, None), context)
    return diagnosis_request, planning_request


def serialized_payload(request):
    # Exercise the actual transport serializer without sending or reading credentials.
    provider = OpenAICompatibleProvider("https://fixture.invalid/v1", "UNUSED_FIXTURE_CREDENTIAL")
    wire = json.loads(provider.prepare_request(request).body)
    return wire, json.loads(wire["messages"][1]["content"])


@pytest.mark.parametrize("schema_in_prompt", [True, False])
def test_exact_public_input_survives_when_all_input_candidates_are_dropped(schema_in_prompt):
    content = 'metadata:\n  annotations:\n    example.io/probe-request-timeout: "7s"\nargs:\n  - --probe-request-timeout=7s\n'
    artifacts = [artifact("INPUT1", content)]
    original = copy.deepcopy(artifacts)
    diagnosis, planning = requests_for(artifacts, schema_in_prompt=schema_in_prompt)
    diagnosis_wire, diagnosis_payload = serialized_payload(diagnosis)
    _, planning_payload = serialized_payload(planning)

    assert diagnosis_payload["candidate_set"]["selected_ids"] == []
    assert diagnosis_payload["candidate_set"]["dropped_ids"] == ["CU-INPUT1"]
    assert diagnosis_payload["method_input"]["declared_artifacts"] == original
    assert planning_payload["declared_artifacts"] == original
    assert content == json.loads(diagnosis_wire["messages"][1]["content"])["method_input"]["declared_artifacts"][0]["content"]
    assert artifacts == original
    # Input delivery adds neither source evidence IDs nor production edit targets.
    assert diagnosis_payload["allowed_evidence_ids"] == []
    assert diagnosis_payload["evidence_catalog"] == []
    assert all(row["path"] != original[0]["path"] for row in planning_payload["existing_target_paths"])
    for request in (diagnosis, planning):
        assert "read-only public inputs" in request.messages[0]["content"]
        assert "does not prove that production consumes" in request.messages[0]["content"]


def test_both_stages_keep_complete_utf8_inputs_at_the_same_cap():
    artifacts = [artifact("INPUT1", "雪" * 21845), artifact("INPUT2", "x")]
    diagnosis, planning = requests_for(artifacts)
    _, diagnosis_payload = serialized_payload(diagnosis)
    _, planning_payload = serialized_payload(planning)
    delivered = diagnosis_payload["method_input"]["declared_artifacts"]

    assert delivered == planning_payload["declared_artifacts"]
    assert delivered == artifacts
    assert sum(len(row.get("content", "").encode("utf-8")) for row in delivered) == 64 * 1024


@pytest.mark.parametrize('stage', ['diagnosis', 'planning'])
def test_oversized_artifact_stops_projection_before_any_selected_input_is_omitted(stage):
    artifacts = [artifact("INPUT1", "a" * (64 * 1024 + 1)), artifact("INPUT2", "exact: kept\n")]
    with pytest.raises(BudgetExceeded, match='complete-content budget'):
        requests_for(artifacts, stage=stage)


def test_declared_content_still_counts_toward_full_serialized_request_budget():
    provider = OpenAICompatibleProvider("https://fixture.invalid/v1", "UNUSED_FIXTURE_CREDENTIAL")
    empty, _ = requests_for([])
    request, _ = requests_for([artifact("INPUT1", "read-only-input: " + "x" * 2048)])
    empty_quote = quote_request(provider, empty, len)
    full_quote = quote_request(provider, request, len)
    assert full_quote.input_tokens - empty_quote.input_tokens >= 2048
    assert not context_fits(full_quote, empty_quote.input_tokens)
    assert not context_fits(full_quote, full_quote.input_tokens, full_quote.total_tokens - 1)
    assert context_fits(full_quote, full_quote.input_tokens, full_quote.total_tokens)
