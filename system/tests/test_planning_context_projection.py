import copy
import hashlib
import json
from types import SimpleNamespace

from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_diagnosis
from tests.test_contracts import valid_diagnosis, valid_method_input


def test_compact_planning_projection_keeps_identity_and_source_delivery():
    raw = valid_diagnosis()
    source_text = raw["evidence_refs"][1]["excerpt"]
    raw["evidence_refs"][1]["content_digest"] = hashlib.sha256(source_text.encode()).hexdigest()
    diagnosis = validate_diagnosis(raw, parse_method_input(valid_method_input()), None)
    support = [dict(diagnosis.raw["evidence_refs"][1], delivery_required=True)]
    context = SimpleNamespace(
        run_id=diagnosis.run_id, case_id="RW-DEMO-001", snapshot_id=diagnosis.snapshot_id,
        input_digest=diagnosis.input_digest, model_config={"model": "fixture"},
        budget={"max_output_tokens": 1000},
        retrieval_config={"compact_planning_context": True,
                          "necessary_source_support": support},
    )

    request = build_changeset_request(diagnosis, context)
    payload = json.loads(request.messages[1]["content"])
    projected = next(row for row in payload["diagnosis_packet"]["evidence_refs"] if row["id"] == "EV2")
    delivered = next(row for row in payload["read_only_source_support"] if row["id"] == "EV2")

    assert "excerpt" not in projected
    assert delivered["excerpt"] == source_text
    assert delivered["snapshot_id"] == diagnosis.snapshot_id
    assert delivered["content_digest"] == hashlib.sha256(source_text.encode()).hexdigest()
    assert payload["diagnosis_digest"] == canonical_digest(diagnosis.raw)
    assert payload["output_schema"] != request.output_schema
    assert "properties" in payload["output_schema"]
    assert "changes" in payload["output_schema"]["properties"]
    assert "maxLength" not in str(payload["output_schema"])


def test_compact_projection_is_opt_in_for_legacy_planning_callers():
    diagnosis = validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)
    context = SimpleNamespace(run_id="run-1", case_id="RW-DEMO-001", snapshot_id=diagnosis.snapshot_id,
                              input_digest=diagnosis.input_digest, model_config={"model": "fixture"},
                              budget={"max_output_tokens": 1000})
    payload = json.loads(build_changeset_request(diagnosis, context).messages[1]["content"])
    assert payload["diagnosis_packet"] == diagnosis.raw
    assert "properties" in payload["output_schema"]


def test_planning_request_carries_exact_declared_artifact_contract():
    diagnosis = validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)
    artifact = {
        "id": "INPUT1", "path": "inputs/declaration.yaml", "media_type": "text/plain",
        "content_digest": "a" * 64,
        "content": "metadata:\n  annotations:\n    example.io/request-timeout: 5s\n",
    }
    context = SimpleNamespace(run_id="run-artifact", case_id="CASE", snapshot_id=diagnosis.snapshot_id,
        input_digest=diagnosis.input_digest, model_config={"model": "fixture"},
        budget={"max_output_tokens": 1000}, retrieval_config={"declared_artifacts": [artifact]})
    request = build_changeset_request(diagnosis, context)
    payload = json.loads(request.messages[1]["content"])
    assert payload["declared_artifacts"] == [artifact]
    assert "exact external input contracts" in request.messages[0]["content"]
    assert "example.io/request-timeout" in payload["declared_artifacts"][0]["content"]


def test_source_continuity_does_not_reintroduce_dropped_source_digest(tmp_path):
    from contractedge.orchestration.runner import (
        diagnosis_continuity, required_planning_source_support,
    )
    from contractedge.diagnosis.evidence import source_context_new,source_context_for_candidates
    from contractedge.retrieval.candidates import build_candidate_set
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.source.units import extract_units

    (tmp_path / 'keep.go').write_text('package worker\nfunc Keep() {}\n')
    (tmp_path / 'drop.go').write_text('package worker\nfunc Drop() {}\n')
    snapshot = compute_snapshot(tmp_path)
    units = extract_units(snapshot, {'seed_paths': ['keep.go', 'drop.go']}, max_extension_files=0)
    selected = build_candidate_set('Keep', None, units, [], top_n=1,
        max_context_tokens=10000, token_counter=len)
    keep = next(u for u in units if u['path'] == 'keep.go')
    dropped = next(u for u in units if u['path'] == 'drop.go')
    records = [dict(id='SRC-' + u['id'], kind='SOURCE_SPAN', snapshot_id=snapshot.snapshot_id,
        content_digest=u['content_digest'], excerpt=u['text'], locator={**u['locator'], 'path': u['path']})
        for u in units]
    prior = dict(snapshot_id=snapshot.snapshot_id, input_digest='input', evidence_refs=records,
        expected_relations=[], violations=[dict(id='V1', relation_id='R1', causal_status='SUPPORTED',
            evidence_refs=[r['id'] for r in records])])
    active = source_context_for_candidates(source_context_new(dict(run_id='run',case_id='case',
        input_digest='input',front_id='front')),SimpleNamespace(public_evidence=(),declared_artifacts=()),
        selected,snapshot.snapshot_id)
    assert active['candidate_ids'] == [keep['id']]
    support = required_planning_source_support(SimpleNamespace(public_evidence=(), declared_artifacts=()),
        selected, snapshot.snapshot_id)
    assert [row['id'] for row in support] == ['SRC-' + keep['id']]
    view = diagnosis_continuity(prior, snapshot.snapshot_id, support, [], input_digest='input')
    claim = view['claims_for_reassessment'][0]
    assert claim['current_evidence_refs'] == ['SRC-' + keep['id']]
    assert claim['unavailable_prior_evidence_refs'] == ['SRC-' + dropped['id']]
