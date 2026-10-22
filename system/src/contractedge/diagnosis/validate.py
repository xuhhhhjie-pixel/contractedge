from __future__ import annotations

import hashlib
from typing import Any, Mapping

from contractedge.contracts.models import ContractError, DiagnosisPacket, MethodInput, RunContext
from contractedge.contracts.validation import validate_diagnosis as validate_contract_diagnosis
from contractedge.retrieval.candidates import CandidateSet


def validate_diagnosis(payload: Mapping[str, Any], method_input: MethodInput,
                       run_context: RunContext | None, candidate_set: CandidateSet) -> DiagnosisPacket:
    packet = validate_contract_diagnosis(payload, method_input, run_context)
    if run_context is not None and run_context.retrieval_config.get('operation_consistency_enabled'):
        from contractedge.diagnosis.operation_consistency import operation_facts, validate_operation_claims, validate_public_check_claims
        issues=validate_operation_claims(payload,operation_facts(method_input.public_evidence),
            dict(requirement_text=method_input.requirement_text,
                 public_contract_evidence=list(method_input.public_evidence),
                 operation_expectations=run_context.retrieval_config.get('operation_expectations',[])))
        issues.extend(validate_public_check_claims(payload,method_input.public_evidence,run_context.snapshot_id))
        if issues:
            first=issues[0]
            raise ContractError(first['code'],'operation_assessments',str(issues))
    candidate_by_id = {str(item["id"]): item for item in candidate_set.candidates}
    selected_paths = {str(item.get("path", "")) for item in candidate_set.candidates}
    public_by_digest = {}
    for item in method_input.public_evidence:
        public_by_digest.setdefault(str(item['content_digest']), []).append(item)
    artifacts_by_id = {str(item["id"]): item for item in method_input.declared_artifacts}
    for index, evidence in enumerate(packet.evidence_refs):
        if evidence["kind"] == "SOURCE_SPAN":
            path = evidence["locator"].get("path")
            if path not in selected_paths:
                raise ContractError("EVIDENCE_NOT_IN_CANDIDATES", f"evidence_refs[{index}].locator.path",
                                    "source evidence must point to a selected candidate path")
            digest = evidence["content_digest"]
            if digest not in {str(item.get("content_digest", "")) for item in candidate_by_id.values()}:
                raise ContractError("EVIDENCE_DIGEST_NOT_IN_CANDIDATES", f"evidence_refs[{index}].content_digest",
                                    "source evidence digest must match a selected candidate")
        elif evidence["kind"] == "PUBLIC_OBSERVATION":
            public_records = public_by_digest.get(str(evidence["content_digest"]), [])
            if not public_records:
                raise ContractError("PUBLIC_EVIDENCE_NOT_IN_INPUT", f"evidence_refs[{index}]",
                                    "public observations must cite a trusted method-input evidence record")
            if not any(evidence['excerpt'] == record['excerpt'] and evidence['locator'] == record['locator']
                       for record in public_records):
                raise ContractError("PUBLIC_EVIDENCE_MISMATCH", f"evidence_refs[{index}]",
                                    "public observation text and locator must match the copied evidence record")
        elif evidence["kind"] == "DECLARED_ARTIFACT":
            artifact = artifacts_by_id.get(str(evidence["locator"].get("artifact_id", "")))
            if artifact is None:
                raise ContractError("DECLARED_ARTIFACT_NOT_IN_INPUT", f"evidence_refs[{index}]",
                                    "declared-artifact evidence must resolve to a copied public input artifact")
            locator = evidence['locator']
            start, end = locator.get('start_char', 0), locator.get('end_char', len(artifact['content']))
            if (not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(artifact['content'])
                    or artifact['content'][start:end] != evidence['excerpt']
                    or hashlib.sha256(evidence['excerpt'].encode()).hexdigest() != evidence['content_digest']
                    or locator.get('artifact_content_digest', artifact['content_digest']) != artifact['content_digest']
                    or locator.get('path') != f"cases/{method_input.case_id}/{artifact['path']}"):
                raise ContractError("DECLARED_ARTIFACT_MISMATCH", f"evidence_refs[{index}]",
                                    "declared-artifact evidence must match its exact public input content and locator")
        elif evidence["kind"] == "REQUIREMENT_TEXT":
            excerpt = evidence["excerpt"]
            if not excerpt or excerpt not in method_input.requirement_text:
                raise ContractError("REQUIREMENT_EVIDENCE_MISMATCH", f"evidence_refs[{index}].excerpt",
                                    "requirement evidence must be copied from the original public requirement")
            if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != evidence["content_digest"]:
                raise ContractError("EVIDENCE_DIGEST_MISMATCH", f"evidence_refs[{index}].content_digest",
                                    "requirement evidence digest must match the cited excerpt")
    return packet


def semantic_review(decision: str, rationale: str, reviewer_id: str) -> dict[str, str]:
    if decision not in {"ACCEPT", "REVISE", "UNCERTAIN"}:
        raise ValueError("semantic review decision must be ACCEPT, REVISE, or UNCERTAIN")
    if not rationale.strip() or not reviewer_id.strip():
        raise ValueError("semantic review must retain rationale and reviewer identity")
    return {"decision": decision, "rationale": rationale, "reviewer_id": reviewer_id,
            "evidence_status": "REVIEW_SIGNAL_NOT_PROOF"}
