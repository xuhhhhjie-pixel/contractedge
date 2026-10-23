from __future__ import annotations

from contextlib import nullcontext
import time
import uuid
import re
import json
import hashlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from contractedge.changeset.prompt import build_changeset_request
from contractedge.retrieval.frontier import production_frontier
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
from contractedge.changeset.adapter import adapt_model_changeset, bind_changeset_envelope
from contractedge.contracts.models import ContractError, MethodInput, RunContext, ValidationResult, ValidationIssue
from contractedge.contracts.validation import parse_method_input, canonical_digest
from contractedge.diagnosis.parse import parse_diagnosis_response, parse_json_response
from contractedge.diagnosis.evidence import bind_evidence_ids, build_evidence_catalog, field_contract_contexts
from contractedge.diagnosis.prompt import build_diagnosis_request
from contractedge.diagnosis.validate import validate_diagnosis
from contractedge.model.provider import ModelProvider, ModelResponse
from contractedge.contracts.stages import StageResult, front_status
from contractedge.orchestration.state import PolicyLedger, RepairState
from contractedge.orchestration.transitions import transition
from contractedge.model.budget import BudgetExceeded
from contractedge.model.admission import quote_request, context_fits, complete_quoted
from contractedge.orchestration.feedback import feedback_prompt_view
from contractedge.orchestration.semantic_delivery import SemanticDeliveryError, delivery_context, install_monitor
from contractedge.requirement.normalize import normalize_requirement
from contractedge.retrieval.candidates import CandidateSet, build_candidate_set, extend_once, trim_context_bundle
from contractedge.retrieval.edges import build_direct_reference_edges
from contractedge.source.scopes import (
    DEFAULT_SCOPE_MATCHES_PER_HINT, DEFAULT_SCOPE_SEARCH_BYTES, DEFAULT_SCOPE_SEARCH_FILE_BYTES,
    DEFAULT_SCOPE_SEARCH_FILES, ScopeResolution, resolve_scope,
)
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.absence import collect_absence_search_evidence
from contractedge.source.units import extract_declared_artifact_units, extract_gap_extension_units, extract_units


def _safe_planning_gaps(payload):
    """Validate only the read-only query contract; never approve a broken plan."""
    if not isinstance(payload, Mapping) or payload.get('status') != 'NEEDS_EVIDENCE':
        return []
    from jsonschema import Draft202012Validator
    from contractedge.contracts.validation import _load_schema
    gaps = payload.get('evidence_gaps', [])
    schema = _load_schema('ce.semantic_changeset.v1_1')['properties']['evidence_gaps']
    if not Draft202012Validator(schema).is_valid(gaps):
        return []
    return [g for g in gaps if any(g['locator_hint'].get(k) for k in ('path', 'symbol', 'search_term'))]


def _output_was_truncated(response: Any) -> bool:
    metadata = getattr(response, "metadata", {})
    reason = str(metadata.get("finish_reason", "")).casefold() if isinstance(metadata, Mapping) else ""
    return reason in {"length", "max_tokens", "token_limit"}


def _format_validation_feedback(error: Exception, payload: Mapping[str, Any]) -> str:
    """Give a bounded structural correction without selecting semantic labels for the model."""
    message = str(error)
    if 'CAUSAL_CLAIM_UNSUPPORTED' in message:
        return ('An observed failure and its timing establish the failed behavior, not its production cause. '
            'A SUPPORTED violation needs actual SOURCE_SPAN evidence of the mechanism and an explanation connecting '
            'that source to the observed failure. Keep support_basis consistent with the records actually cited. '
            'Do not upgrade OBSERVED_ONLY into a supported cause just to pass validation. If the mechanism is absent '
            'or uncertain, retain UNRESOLVED/EVIDENCE_GAP and provide a located evidence request instead of inventing '
            'a causal repair. Original validation: '+message[:3000]+'. Return the full corrected Diagnosis body.')
    consumer_match=re.search(r'PRODUCTION_CONSUMER_UNEVIDENCED at units\[(\d+)\]',message)
    if consumer_match:
        units=payload.get('units',[]);index=int(consumer_match[1])
        unit=units[index] if isinstance(units,list) and index<len(units) else {}
        return ('A PRESENT PRODUCTION_CONSUMER denotes implementation, so it must cite an allowed SOURCE_SPAN. '
            'The affected unit is '+str(unit.get('id',index))+', with locator_refs '+str(unit.get('locator_refs',[]))+
            '. A PUBLIC_OBSERVATION proves a result, not the implementation that produced it. If this unit denotes '
            'an output/object at the public boundary, use OBSERVED_STATE or GENERATED_STATE as supported; if it denotes '
            'code, cite the actual implementation already present in evidence_catalog. Do not relabel an unknown '
            'implementation as proven present, invent a source citation, drop required relations, or infer causality '
            'from an observation alone. Preserve unresolved gaps when necessary. Correct all other listed independent '
            'errors as well. Original validation: '+message[:3000]+'. Return the complete corrected Diagnosis body.')
    if "MISSING_UNIT_UNSUPPORTED" in message:
        return (
            "The validator rejected an EXPECTED_MISSING semantic unit because its cited records do not establish absence. "
            "Do not infer absence from selected-candidate omission or from a missing filename in an incomplete search. "
            "Use EXPECTED_MISSING only when an allowed SOURCE_INVENTORY, STATIC_SEARCH, or PUBLIC_OBSERVATION record "
            "directly supports that exact absence claim and cite its exact evidence ID in locator_refs. If no supplied "
            "record supports absence, change existence to UNKNOWN and revise any relation that treated the role as proven missing. "
            "Return the full corrected Diagnosis body."
        )
    conflict = re.search(r"OUTCOME_CATEGORY_CONFLICT at outcome_coverage\[(\d+)\]", message)
    if conflict:
        index = int(conflict.group(1))
        coverage = payload.get("outcome_coverage", [])
        row = coverage[index] if isinstance(coverage, list) and index < len(coverage) else {}
        outcome_id = row.get("outcome_id", "the affected outcome") if isinstance(row, Mapping) else "the affected outcome"
        return (
            f"Outcome {outcome_id} is assigned to more than one category. Re-evaluate it from the supplied evidence, "
            "then choose exactly one: (A) keep supported violation IDs in repair_supported and set both "
            "preserve_required and unresolved to empty; (B) if the behavior is a separate required invariant, "
            "put its outcome ID only in preserve_required and provide its satisfaction record; or (C) if no "
            "supported actionable violation covers it, put its ID only in unresolved and clear repair_supported "
            "and preserve_required. Keep status consistent: READY requires no unresolved outcomes or evidence gaps; "
            "PARTIAL_READY requires supported work plus a distinct open outcome; EVIDENCE_GAP means no supported "
            "actionable violation. Return the full corrected JSON object."
        )
    if "OUTCOME_SUPPORTED_VIOLATION_UNCOVERED" in message:
        return (
            "The validation found a causal_status=SUPPORTED Violation linked through an ExpectedRelation to this outcome, "
            "but the outcome was not assigned REPAIR_SUPPORTED. If the cited static/public evidence supports the cause, "
            "select REPAIR_SUPPORTED, remove that outcome from unverified_outcomes, and clear evidence_gaps already "
            "resolved by those citations; use READY only if every outcome is closed and no evidence gap remains. "
            "If the evidence does not establish the cause, mark the Violation TENTATIVE, select UNRESOLVED, and use "
            "EVIDENCE_GAP. Return one internally consistent full DiagnosisPacket."
        )
    if "STATUS_INCONSISTENT" in message:
        return (
            "Diagnosis status contradicts its coverage or evidence_gaps. Recheck all outcome dispositions and remove "
            "only gaps that the cited evidence actually resolves. READY requires supported coverage for every outcome, "
            "no unresolved outcomes, and no evidence_gaps. PARTIAL_READY requires supported work plus a distinct open "
            "outcome. EVIDENCE_GAP requires that no supported actionable Violation be linked to the unresolved outcome. "
            "Return one internally consistent full DiagnosisPacket."
        )
    if "MODEL_COVERAGE_SUPPORT_MISSING" in message:
        index_match = re.search(r"MODEL_COVERAGE_SUPPORT_MISSING at outcome_coverage\[(\d+)\]", message)
        coverage = payload.get("outcome_coverage", [])
        outcome_id = "the affected outcome"
        if isinstance(coverage, Mapping):
            keys = list(coverage)
            if index_match and int(index_match.group(1)) < len(keys):
                outcome_id = str(keys[int(index_match.group(1))])
            elif keys:
                outcome_id = str(keys[0])
        elif isinstance(coverage, list) and index_match:
            index = int(index_match.group(1))
            if index < len(coverage) and isinstance(coverage[index], Mapping):
                outcome_id = str(coverage[index].get("outcome_id", outcome_id))
        violations = [item for item in payload.get("violations", []) if isinstance(item, Mapping)]
        supported = [item for item in violations if item.get("causal_status") == "SUPPORTED"]
        relations_by_id = {str(item.get("id")): item for item in payload.get("expected_relations", [])
                           if isinstance(item, Mapping) and item.get("id")}
        linked = [item for item in supported
                  if outcome_id in relations_by_id.get(str(item.get("relation_id")), {}).get("requirement_refs", [])]
        diagnosis = (f" The rejected response contains {len(violations)} Violation records, "
                     f"{len(supported)} marked SUPPORTED, and {len(linked)} linked to {outcome_id} through the "
                     "ExpectedRelation requirement_refs.")
        if not violations:
            diagnosis += (f" Set {outcome_id} to UNRESOLVED and status to EVIDENCE_GAP unless the supplied "
                          "public/static evidence directly supports a specific causal Violation.")
        return (
            "An outcome cannot be REPAIR_SUPPORTED unless at least one causal_status=SUPPORTED Violation links through "
            "its relation_id to an ExpectedRelation whose requirement_refs contains that exact outcome ID."
            + diagnosis + " Do not invent a Violation or change relation references only to satisfy validation. If the "
            "supplied evidence does not support that complete chain, set this outcome's disposition to UNRESOLVED, "
            "clear any preservation claim for it, and set status=EVIDENCE_GAP when no other supported actionable "
            "violation remains. If the chain is supported, preserve its evidence-backed IDs and select "
            "REPAIR_SUPPORTED. Return the full corrected Diagnosis body."
        )
    return ("Your previous response failed the output contract: " + message[:4000]
            + ". Correct every listed independent error, not only the first. Represent supported configuration paths through actual evidenced production consumers. Do not invent edges, relabel roles or drop atoms just to pass validation; use unresolved/gaps when causal support is missing. Return the corrected top-level DiagnosisPacket JSON object only; do not wrap it in another key.")


def _changeset_retry_feedback(error: Exception) -> list[dict[str, str]]:
    """Keep adapter/contract error codes intact so a revision can address the real failure."""
    message = str(error)
    match = re.match(r"^([A-Z][A-Z0-9_]+):", message)
    code = match.group(1) if match else "INVALID_JSON"
    return [{"code": code, "message": message[:1200]}]


def _append_diagnosis_retry_turn(messages, response_content: str, validation_feedback: str):
    """Retain rejected drafts and earlier corrections within the existing budget."""
    if 'CAUSAL_CLAIM_UNSUPPORTED' in validation_feedback:
        available=[]
        for message in messages:
            if message.get('role')!='user':continue
            try:value=json.loads(message['content'])
            except (KeyError,TypeError,ValueError):continue
            if not isinstance(value,dict):continue
            for record in value.get('evidence_catalog',[]):
                if record.get('kind')!='SOURCE_SPAN':continue
                locator=record.get('locator',{});path=locator.get('path','')
                if not path.endswith(('.go','.py','.js','.ts','.tsx','.c','.h','.java')):continue
                available.append(dict(id=record['id'],path=path,start_line=locator.get('start_line'),end_line=locator.get('end_line')))
        validation_feedback+=' Available source locations already supplied: '+json.dumps(available[:8])+'. Inspect their actual excerpts; availability alone is not causal support.'
    return tuple(messages) + (
        {"role": "assistant", "content": response_content},
        {"role": "user", "content": validation_feedback},
    )


def _translate_model_outcome_coverage(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Translate keyed single-choice model coverage into the v1.1 coverage array."""
    body = dict(payload)
    raw_coverage = body.get("outcome_coverage")
    if isinstance(raw_coverage, Mapping):
        rows = [({"outcome_id": outcome_id, **dict(value)} if isinstance(value, Mapping)
                 else {"outcome_id": outcome_id, "invalid_value": value})
                for outcome_id, value in raw_coverage.items()]
    elif isinstance(raw_coverage, list):
        rows = raw_coverage
    else:
        return body
    if not any(isinstance(row, Mapping) and "disposition" in row for row in rows):
        return body
    translated = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or "disposition" not in row:
            raise ValueError(f"MODEL_COVERAGE_ENCODING_MIXED at outcome_coverage[{index}]")
        outcome_id = row.get("outcome_id")
        disposition = row.get("disposition")
        status = row.get("preservation_status")
        evidence_refs = row.get("preservation_evidence_refs")
        if not isinstance(outcome_id, str) or not isinstance(evidence_refs, list):
            raise ValueError(f"MODEL_COVERAGE_ENCODING_INVALID at outcome_coverage[{index}]")
        if disposition == "REPAIR_SUPPORTED":
            relations_by_id = {str(item.get("id")): item for item in body.get("expected_relations", [])
                               if isinstance(item, Mapping) and item.get("id")}
            supporting = [str(violation["id"]) for violation in body.get("violations", [])
                          if isinstance(violation, Mapping) and violation.get("id")
                          and violation.get("causal_status") == "SUPPORTED"
                          and outcome_id in relations_by_id.get(str(violation.get("relation_id")), {}).get("requirement_refs", [])]
            if not supporting:
                raise ValueError(f"MODEL_COVERAGE_SUPPORT_MISSING at outcome_coverage[{index}]: no supported Violation is linked to this outcome through its ExpectedRelation")
            translated.append({"outcome_id": outcome_id, "repair_supported": supporting,
                               "preserve_required": [], "preserve_current_satisfaction": [], "unresolved": []})
        elif disposition == "PRESERVE_REQUIRED":
            if status not in {"OBSERVED", "INFERRED", "UNKNOWN"}:
                raise ValueError(f"MODEL_COVERAGE_PRESERVATION_STATUS_INVALID at outcome_coverage[{index}]")
            translated.append({"outcome_id": outcome_id, "repair_supported": [],
                               "preserve_required": [outcome_id],
                               "preserve_current_satisfaction": [{"outcome_id": outcome_id,
                                   "status": status, "evidence_refs": evidence_refs}], "unresolved": []})
        elif disposition == "UNRESOLVED":
            translated.append({"outcome_id": outcome_id, "repair_supported": [],
                               "preserve_required": [], "preserve_current_satisfaction": [],
                               "unresolved": [outcome_id]})
        else:
            raise ValueError(f"MODEL_COVERAGE_DISPOSITION_INVALID at outcome_coverage[{index}]")
    body["outcome_coverage"] = translated
    return body


def _collect_diagnosis_evidence_ids(payload: Mapping[str, Any]) -> Any:
    """Make the runner-owned evidence registry the union of all model citations.

    Diagnosis repeats evidence IDs in unit/relation/violation fields for local
    traceability. The top-level evidence_refs array is the canonical record
    registry, so callers should not have to repeat every nested citation there.
    Unknown IDs still reach bind_evidence_ids and are rejected against the
    selected public evidence catalog.
    """
    top_level = payload.get("evidence_refs", [])
    if not isinstance(top_level, list):
        return top_level  # Preserve the invalid shape for the existing validator.
    refs = list(top_level)
    seen = {item for item in top_level if isinstance(item, str)}

    def add_list(value: Any) -> None:
        if not isinstance(value, list):
            return
        for item in value:
            if isinstance(item, str):
                if item not in seen:
                    refs.append(item)
                    seen.add(item)
            else:
                # Keep malformed values visible to bind_evidence_ids rather
                # than silently dropping a bad citation.
                refs.append(item)

    for unit in payload.get("units", []) if isinstance(payload.get("units", []), list) else []:
        if isinstance(unit, Mapping):
            add_list(unit.get("locator_refs"))
    for relation in payload.get("expected_relations", []) if isinstance(payload.get("expected_relations", []), list) else []:
        if isinstance(relation, Mapping):
            add_list(relation.get("support_refs"))
    for violation in payload.get("violations", []) if isinstance(payload.get("violations", []), list) else []:
        if isinstance(violation, Mapping):
            add_list(violation.get("evidence_refs"))
    for coverage in payload.get("outcome_coverage", []) if isinstance(payload.get("outcome_coverage", []), list) else []:
        if not isinstance(coverage, Mapping):
            continue
        add_list(coverage.get("preservation_evidence_refs"))
        satisfaction = coverage.get("preserve_current_satisfaction", [])
        if isinstance(satisfaction, list):
            for item in satisfaction:
                if isinstance(item, Mapping):
                    add_list(item.get("evidence_refs"))
    assessments = payload.get('operation_assessments', [])
    if isinstance(assessments, list):
        for row in assessments:
            if isinstance(row, Mapping):
                add_list(row.get('scope_evidence_refs'))
    return refs


def _bind_diagnosis_envelope(payload: Mapping[str, Any], method_input: MethodInput,
                             context: RunContext, requirement: Mapping[str, Any],
                             candidate_set: CandidateSet
                             ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind runner-owned execution identity, RequirementSpec and evidence outside model output."""
    expected = {"schema_version": "ce.diagnosis_packet.v1_1", "run_id": context.run_id,
                "snapshot_id": context.snapshot_id, "input_digest": method_input.input_digest,
                "requirement_spec": dict(requirement)}
    supplied = {key: payload[key] for key in expected if key in payload}
    body = {key: value for key, value in payload.items() if key not in expected}
    body = _translate_model_outcome_coverage(body)
    catalog = build_evidence_catalog(method_input, candidate_set, context.snapshot_id)
    body["evidence_refs"] = bind_evidence_ids(_collect_diagnosis_evidence_ids(body), catalog)
    body.update(expected)
    from contractedge.requirement.normalize import bind_obligation_sources
    try:
        bind_obligation_sources(body, requirement)
    except ValueError as source_error:
        # Inspect independently checkable semantics on a diagnostic copy only.
        # Invalid source identity remains rejected; this copy is never accepted.
        from copy import deepcopy
        diagnostic = deepcopy(body)
        for atom in diagnostic.get('obligation_atoms', []):
            for key in ('source_quote','scope_quote','stable_obligation_key'):
                atom.pop(key,None)
        try:
            validate_diagnosis(diagnostic, method_input, context, candidate_set)
        except (ValueError, ContractError) as semantic_error:
            raise ValueError(str(source_error)+'; additional independently checked error: '+str(semantic_error)) from source_error
        raise
    model_coverage = payload.get("outcome_coverage")
    if isinstance(model_coverage, Mapping):
        coverage_encoding = "keyed_disposition_adapter"
    elif isinstance(model_coverage, list) and any(isinstance(row, Mapping) and "disposition" in row
                                                  for row in model_coverage):
        coverage_encoding = "legacy_single_disposition_adapter"
    else:
        coverage_encoding = "v1_1_direct"
    return body, {"owner": "runner", "model_supplied_fields": sorted(supplied),
                  "evidence_records_bound": len(body["evidence_refs"]),
                  "outcome_coverage_encoding": coverage_encoding,
                  "mismatched_model_fields": sorted(key for key, value in supplied.items()
                                                       if value != expected[key])}


def _semantic_review_result(reviewer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
                            method_input: MethodInput, requirement: Mapping[str, Any],
                            diagnosis: Mapping[str, Any], changeset: Mapping[str, Any],
                            candidate_set: CandidateSet) -> dict[str, Any]:
    if reviewer is None:
        return {"decision": "UNCERTAIN", "method": "NOT_CONFIGURED", "independent": None,
                "rationale": "No independent semantic reviewer was configured.",
                "interpretation": "reviewer judgment; not a correctness proof"}
    review_input = {
        "case_id": method_input.case_id,
        "requirement_spec": dict(requirement),
        "diagnosis_packet": dict(diagnosis),
        "semantic_changeset": dict(changeset),
        "selected_source_units": [
            {key: unit[key] for key in ("id", "path", "locator", "content_digest", "text", "parser_kind", "truncated")
             if key in unit}
            for unit in candidate_set.candidates
        ],
    }
    try:
        result = reviewer(review_input)
    except Exception as exc:
        return {"decision": "UNCERTAIN", "method": "REVIEWER_ERROR", "independent": None,
                "rationale": f"Semantic reviewer failed: {type(exc).__name__}: {exc}",
                "interpretation": "reviewer judgment; not a correctness proof"}
    if not isinstance(result, Mapping) or result.get("decision") not in {"ACCEPT", "REVISE", "UNCERTAIN"}:
        return {"decision": "UNCERTAIN", "method": "INVALID_REVIEW", "independent": None,
                "rationale": "Reviewer returned an invalid decision record.",
                "interpretation": "reviewer judgment; not a correctness proof"}
    independent = result.get("independent")
    if not isinstance(independent, bool):
        return {"decision": "UNCERTAIN", "method": "INVALID_REVIEW", "independent": None,
                "rationale": "Reviewer record must state whether the review is independent.",
                "interpretation": "reviewer judgment; not a correctness proof"}
    decision = result["decision"]
    submitted_decision = decision
    if decision == "ACCEPT" and not independent:
        decision = "UNCERTAIN"
    return {"decision": decision,
            **({"submitted_decision": submitted_decision} if decision != submitted_decision else {}),
            "method": str(result.get("method", "UNSPECIFIED")),
            "reviewer_id": str(result.get("reviewer_id", "")),
            "independent": independent,
            "rationale": str(result.get("rationale", "")),
            "interpretation": "reviewer judgment; not a correctness proof"}


@dataclass(frozen=True)
class RunTrace:
    run_id: str
    case_id: str
    input_digest: str
    snapshot_id: str | None
    status: str
    requirement_spec: Mapping[str, Any] | None
    scope_resolution: ScopeResolution | None
    candidate_set: CandidateSet | None
    diagnosis: Mapping[str, Any] | None
    changeset: Mapping[str, Any] | None
    model_calls: tuple[Mapping[str, Any], ...]
    errors: tuple[str, ...] = ()
    structural_validation: str | None = None
    semantic_review: Mapping[str, Any] | None = None
    retrieval_rounds: tuple[Mapping[str, Any], ...] = ()
    run_context: Mapping[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = "ce.run_trace.v1_1"
        payload["model_calls"] = list(self.model_calls)
        payload["retrieval_rounds"] = list(self.retrieval_rounds)
        payload["errors"] = list(self.errors)
        return payload



def required_source_prerequisites(candidate_set):
    """One producer/consumer projection of current, already-validated source roles.

    The Ready boundary independently recomputes these entries rather than
    trusting a saved context or dropping its role-specific requirements.
    """
    rows = []
    for unit in candidate_set.candidates:
        contracts = field_contract_contexts([unit], ' '.join(unit.get('source_contract_fields', [])))
        if not unit.get('precondition_review_required') and not contracts:
            continue
        reason = ('Review the source-supported exception-producing guard and caller prerequisites; literal match and local calls are locators, not causal proof'
            if any(role.startswith('PUBLIC_EXCEPTION:') for role in unit.get('gap_role_keys', [])) else
            'Review source-supported initialization, readiness and state preservation; lexical relevance only')
        if contracts:
            if not unit.get('precondition_review_required'):
                reason = 'Review the applicability of the supplied field/API source contract; this is source interpretation, not observed runtime readiness.'
            reason += ' Review these exact source-local field contracts before inferring consumer behavior; producing a value is not proof the consumer applies it: '+json.dumps(contracts,ensure_ascii=False,separators=(',',':'))
        rows.append(dict(source_evidence_ref='SRC-'+unit['id'],reason=reason))
    return rows



def required_planning_source_support(method_input, candidate_set, snapshot_id):
    # The extension is the last bounded acquisition. A diagnosis citation is a
    # claim's support, not a lossless projection of its input context: uncited
    # type/owner definitions can be essential to planning the actual change.
    # Preserve only finally selected current source, under the same Top-N and
    # downstream request limits; this adds neither a scan nor an edit grant.
    required=set(candidate_set.required_endpoint_ids)
    support_ids={'SRC-'+u['id'] for u in candidate_set.candidates
                 if candidate_set.extension_used or u['id'] in required
                 or any(k.startswith('PUBLIC_LIFECYCLE:') for k in u.get('gap_role_keys',[]))}
    return [{**r,'delivery_required':True} for r in build_evidence_catalog(method_input,candidate_set,snapshot_id)
            if r['id'] in support_ids and r.get('kind')=='SOURCE_SPAN']


def diagnosis_continuity(prior, snapshot_id, evidence_catalog, extension_gaps, *, input_digest):
    """Carry reviewable claims, never prior verdicts as current evidence."""
    if not prior or prior.get('snapshot_id') != snapshot_id or prior.get('input_digest') != input_digest:
        return None
    previous = {e['id']:e for e in prior.get('evidence_refs', [])}
    current = {e['id']:e for e in evidence_catalog}
    relations = {r['id']:r for r in prior.get('expected_relations', [])}
    claims = []
    omitted = 0
    for violation in prior.get('violations', []):
        if violation.get('causal_status') != 'SUPPORTED':
            continue
        refs = violation.get('evidence_refs', [])
        available = [ref for ref in refs if ref in previous and ref in current
            and all(previous[ref].get(k) == current[ref].get(k)
                for k in ('snapshot_id','kind','locator','content_digest','excerpt'))]
        claim = {'violation': dict(violation),
                 'relation': relations.get(violation.get('relation_id')),
                 'current_evidence_refs': available,
                 'unavailable_prior_evidence_refs': [ref for ref in refs if ref not in available]}
        trial = json.dumps(claims + [claim], ensure_ascii=False).encode('utf-8')
        if len(claims) >= 8 or len(trial) > 12000:
            omitted += 1
        else:
            claims.append(claim)
    if not claims and not omitted:
        return None
    return {'snapshot_id': snapshot_id, 'prior_diagnosis_digest': canonical_digest(prior),
        'claims_for_reassessment': claims, 'omitted_claim_count': omitted,
        'extension_gaps': [dict(g) for g in extension_gaps[:8]],
        'instruction': 'Prior claims are not trusted facts or edit permission. Cite only exact current evidence_catalog records; unavailable IDs cannot support claims. '
            'Reassess the listed gaps and prior static inference. Missing final/live observation limits verification, not source-supported fault inference. '
            'Revise or withdraw claims when current evidence warrants it; explain in causal_explanation/evidence_gaps.summary. '
            'Missing repair helpers may be introduced only with evidenced caller/interface prerequisites and edit permission. '
            'Unobserved outcomes remain UNKNOWN. Do not automatically retain old verdicts.'}



def run_front_half(method_input: MethodInput | Mapping[str, Any], workspace: str | Path,
                   provider: ModelProvider, config: Mapping[str, Any],
                   token_counter: Callable[[str], int] | None, *,
                   semantic_reviewer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
                   execution_feedback: Mapping[str, Any] | None = None,
                   public_observations: tuple[Mapping[str, Any], ...] = (),
                   journal=None, attempt_id="front") -> RunTrace:
    dispatch_start = len(journal.dispatches) if journal is not None else 0
    if journal is not None:
        install_monitor(provider, journal)
        journal.primary_error = None
    trace = _run_front_half_impl(method_input, workspace, provider, config, token_counter,
        semantic_reviewer=semantic_reviewer, execution_feedback=execution_feedback,
        public_observations=public_observations, journal=journal, attempt_id=attempt_id)
    if journal is not None:
        from contractedge.orchestration.recorder import preserve_error
        def record_final_front():
            inputs = tuple(journal.recorder.capture_value(value, kind, snapshot_id=trace.snapshot_id)
                for value, kind in (
                    (asdict(method_input) if isinstance(method_input, MethodInput) else dict(method_input), "METHOD_INPUT"),
                    (dict(config), "FRONT_CONFIG"), (execution_feedback, "EXECUTION_FEEDBACK"),
                    (list(public_observations), "PUBLIC_OBSERVATIONS")))
            return journal.final_front(trace, attempt_id=attempt_id,
                                       dispatch_start=dispatch_start, input_refs=inputs)
        preserve_error(journal.primary_error, record_final_front)
    return trace


def _run_front_half_impl(method_input: MethodInput | Mapping[str, Any], workspace: str | Path,
                   provider: ModelProvider, config: Mapping[str, Any],
                   token_counter: Callable[[str], int] | None, *,
                   semantic_reviewer=None, execution_feedback=None,
                   public_observations=(), journal=None, attempt_id="front") -> RunTrace:
    started = time.monotonic()
    parsed = method_input if isinstance(method_input, MethodInput) else parse_method_input(method_input)
    run_id = str(config.get("run_id") or uuid.uuid4())
    calls: list[Mapping[str, Any]] = []
    max_total_tokens = int(config.get("max_total_tokens", 24_000))
    max_elapsed_seconds = float(config.get("max_elapsed_seconds", 1_200))
    max_model_calls = int(config.get('max_model_calls', 24))
    reserved_tokens = 0
    request_quotes = {}
    run_context_wire = None
    snapshot_id = None
    requirement = None
    scope = None
    candidate_set = None
    diagnosis_wire = None
    changeset_payload = None
    structural_validation = 'NOT_RUN'
    semantic_review = None
    format_retries_used = 0
    truncation_recoveries_used = 0
    recovered_generation_modes: dict[str, str] = {}
    separate_truncation_allowance = config.get("planning_truncation_separate_allowance", False)
    if type(separate_truncation_allowance) is not bool:
        raise ValueError("planning_truncation_separate_allowance must be boolean")
    engine = getattr(journal, "engine", None)
    policy = engine.policy if engine is not None else PolicyLedger(config)
    max_format_retries = policy.limit("format_correction")
    per_evidence_context_correction = config.get('diagnosis_format_retry_per_context', False)
    if type(per_evidence_context_correction) is not bool:
        raise ValueError('diagnosis_format_retry_per_context must be boolean')

    def diagnosis_gate(diagnosis):
        payload_ref = None
        call_refs = ()
        if journal is not None:
            payload_ref = journal.recorder.capture_value(diagnosis.raw, "FINAL_DIAGNOSIS",
                                                          snapshot_id=snapshot_id)
            produced = [receipt for stage, receipt in journal.dispatches if stage.startswith("DIAGNOSIS")]
            call_refs = produced[-1].call_refs if produced else ()
        result = StageResult("diagnosis", front_status(diagnosis.status), payload_ref,
                             call_refs=call_refs, snapshot_id=snapshot_id)
        live_snapshot = compute_snapshot(source_root).snapshot_id
        return (engine.step(result, current_snapshot=live_snapshot) if engine is not None else
                transition(RepairState(live_snapshot), result))

    def planning_generation_mode(request):
        """Keep a completed truncation recovery for this prompt in this run only.

        This changes transport, not evidence or validation. A different planning
        schema and a fresh front half retain their original generation policy.
        """
        origin = recovered_generation_modes.get(request.prompt_version)
        if origin is None:
            return request
        return replace(request, thinking='disabled', metadata={**request.metadata,
            'planning_generation_policy': {
                'mode': 'disabled', 'reason': 'COMPLETED_REASONING_STARVED_JSON_RECOVERY',
                'origin_stage': origin, 'scope': 'CURRENT_FRONT_HALF_PROMPT_VERSION'}})

    def complete_recorded(request, stage: str):
        ledger_start = journal.position() if journal is not None else 0
        prompt_reservation = request_tokens(request)
        previous_timeout = getattr(provider, 'timeout_seconds', None)
        response = None
        dispatch_error = None
        try:
            remaining = max_elapsed_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise BudgetExceeded('Model call cannot start after the run deadline')
            if previous_timeout is not None:
                provider.timeout_seconds = min(previous_timeout, remaining)
            monitor = getattr(provider, '_semantic_delivery_monitor', None)
            planning = stage.startswith(('SEMANTIC_CHANGESET', 'CHANGESET'))
            semantic = delivery_context(parsed, consumer=stage, snapshot_id=snapshot_id,
                diagnosis=diagnosis_wire, changeset=changeset_payload, feedback=execution_feedback,
                necessary_support=(required_planning_source_support(parsed, candidate_set, snapshot_id)
                                   if planning and candidate_set is not None else ()),
                prerequisites=(required_source_prerequisites(candidate_set)
                               if planning and candidate_set is not None else ()),
                evidence=public_observations, source_requirements=planning)
            with monitor.scope(semantic) if monitor is not None else nullcontext():
                response = complete_quoted(provider, request, request_quote(request))
            if time.monotonic() - started >= max_elapsed_seconds:
                raise BudgetExceeded('Model response exceeded the run deadline')
            return response
        except Exception as exc:
            dispatch_error = exc
            if isinstance(exc, SemanticDeliveryError):
                # Admission attempts are in semantic_delivery receipts, never in
                # this compatibility model-call view without a real dispatch.
                raise
            received = getattr(exc, 'response', None)
            if response is None and isinstance(received, ModelResponse):
                response = received
            calls.append({"stage": stage, "prompt_version": request.prompt_version,
                          "request_metadata": dict(request.metadata),
                          "response_content": response.content if response is not None else None,
                          "response_metadata": dict(response.metadata) if response is not None else None,
                          "prompt_tokens_reserved": prompt_reservation,
                          "output_tokens_reserved": request.max_output_tokens,
                          "provider_error_metadata": dict(getattr(exc, "provider_metadata", {}) or {}),
                          "error": f"{type(exc).__name__}: {exc}",
                          "elapsed_seconds": time.monotonic() - started})
            if time.monotonic() - started >= max_elapsed_seconds and not isinstance(exc, BudgetExceeded):
                raise BudgetExceeded('Model call exceeded the run deadline') from exc
            raise
        finally:
            if previous_timeout is not None:
                provider.timeout_seconds = previous_timeout
            if journal is not None:
                from contractedge.orchestration.recorder import preserve_error
                primary = dispatch_error
                preserve_error(primary, lambda: journal.record_dispatch(stage,
                    attempt_id=attempt_id + ".call-" + str(ledger_start),
                    snapshot_id=snapshot_id, call_start=ledger_start,
                    status="ADMISSION_REJECTED" if isinstance(primary, SemanticDeliveryError)
                           else "ERROR" if primary is not None else "RESPONSE_RECEIVED"))

    def request_quote(request):
        # Keep each immutable request and its prepared bytes together. A changed
        # request is validated again by complete_quoted before reservation.
        key = id(request)
        if key not in request_quotes:
            if token_counter is None:
                raise ValueError("an explicit model tokenizer/token counter is required")
            request_quotes[key] = (request, quote_request(provider, request, token_counter))
        return request_quotes[key][1]

    def request_tokens(request) -> int:
        return request_quote(request).input_tokens

    def within_budget(request, prompt_tokens: int) -> bool:
        quote = request_quote(request)
        return (time.monotonic() - started < max_elapsed_seconds
                and len(calls) < max_model_calls
                and context_fits(quote, int(config['max_context_tokens']), config.get('model_context_tokens'))
                and reserved_tokens + quote.total_tokens <= max_total_tokens)

    def recover_truncated_planning(response, request, stage):
        """Recover known reasoning-starved JSON once within all shared budgets.

        This is a new logged model request, not an HTTP replay or hand repair.
        The identical source/evidence/schema and all shared budgets remain bound.
        """
        nonlocal reserved_tokens, format_retries_used, truncation_recoveries_used
        allowance_exhausted = (truncation_recoveries_used >= 1 if separate_truncation_allowance
                               else format_retries_used >= max_format_retries)
        usage = response.metadata.get('usage') or {}
        completion = usage.get('completion_tokens')
        reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens')
        if (config.get('planning_truncation_retry') != 'disable_thinking'
                or not _output_was_truncated(response) or allowance_exhausted
                or type(completion) is not int or type(reasoning) is not int
                or completion <= 0 or not completion >= reasoning >= completion / 2):
            return response
        retry = replace(request, thinking='disabled', messages=request.messages + (
            {'role': 'assistant', 'content': response.content},
            {'role': 'user', 'content': 'The previous JSON draft was cut off at the generation limit. Return one complete concise JSON object satisfying the original schema and all original obligations. Preserve trigger conditions, evidence-backed claims, preservation and unresolved obligations. Do not invent support or omit required behavior to shorten the response. Use evidence IDs instead of repeating records. This replaces the truncated draft; it does not change the source, evidence or task.'}),
            metadata={**request.metadata, 'planning_recovery': 'REASONING_STARVED_JSON_DISABLE_THINKING',
                      'format_retry_index': format_retries_used + (0 if separate_truncation_allowance else 1),
                      'truncation_recovery_index': truncation_recoveries_used + 1,
                      'recovery_allowance': ('SEPARATE_TRUNCATION_ONCE' if separate_truncation_allowance else 'SHARED_FORMAT_CORRECTION'),
                      'original_stage': stage})
        count = request_tokens(retry)
        if not within_budget(retry, count):
            return response
        truncation_recoveries_used = policy.consume("truncation_recovery", attempt_id)
        if not separate_truncation_allowance:
            format_retries_used = policy.consume("format_correction", "diagnosis:" + attempt_id)
        result = complete_recorded(retry, 'PLANNING_TRUNCATION_FORMAT_RETRY')
        reserved_tokens += count + retry.max_output_tokens
        calls.append({'stage': 'PLANNING_TRUNCATION_FORMAT_RETRY', 'prompt_version': retry.prompt_version,
            'request_metadata': dict(retry.metadata), 'response_metadata': dict(result.metadata),
            'response_content': result.content, 'elapsed_seconds': time.monotonic() - started,
            'prompt_tokens_reserved': count, 'output_tokens_reserved': retry.max_output_tokens,
            'format_retry_index': format_retries_used,
            'truncation_recovery_index': truncation_recoveries_used})
        if not _output_was_truncated(result):
            recovered_generation_modes[request.prompt_version] = stage
        return result

    def fit_diagnosis(candidate_set, method, requirement, context, *, prior=None, extension_gaps=()):
        """Fit the complete serialized request, including fixed prompt overhead, in context."""
        fitted = candidate_set
        contracts = field_contract_contexts(fitted.candidates, parsed.requirement_text)
        by_unit = {}
        for record in contracts:
            by_unit.setdefault(record['candidate_id'], []).append(record['field_name'])
        fitted = replace(fitted, candidates=tuple(
            {**u, 'source_contract_fields': by_unit[u['id']],
             'gap_role_keys': list(dict.fromkeys([*u.get('gap_role_keys', []), 'SOURCE_CONTRACT:'+u['id']]))}
            if u['id'] in by_unit else u for u in fitted.candidates),
            required_endpoint_ids=tuple(dict.fromkeys([*fitted.required_endpoint_ids, *by_unit])))
        if prior and prior.get('snapshot_id') == context.snapshot_id and prior.get('input_digest') == context.input_digest:
            # Reserve exact still-selected source references, within the existing
            # Top-N. The full request fitter/audit remains authoritative.
            prior_ids = {ref[4:] for v in prior.get('violations', [])
                if v.get('causal_status') == 'SUPPORTED' for ref in v.get('evidence_refs', [])
                if ref.startswith('SRC-')}
            pinned = [u['id'] for u in fitted.candidates if u['id'] in prior_ids]
            fitted = replace(fitted, candidates=tuple(
                {**u, 'gap_role_keys': list(dict.fromkeys([*u.get('gap_role_keys', []), 'DIAGNOSIS_CONTINUITY_REFERENCE']))}
                if u['id'] in pinned else u for u in fitted.candidates),
                required_endpoint_ids=tuple(dict.fromkeys([*fitted.required_endpoint_ids, *pinned])))
        required_endpoint_ids=list(dict.fromkeys([*fitted.required_endpoint_ids,
            *[c['id'] for c in fitted.candidates if c.get('gap_role_keys')]]))
        while True:
            if not fitted.candidates:
                return fitted,None,0
            request = build_diagnosis_request(method, requirement, fitted, context,
                                              scope_resolution=scope)
            continuity = diagnosis_continuity(prior, context.snapshot_id,
                build_evidence_catalog(method, fitted, context.snapshot_id), extension_gaps, input_digest=context.input_digest)
            if continuity is not None:
                body = json.loads(request.messages[1]['content'])
                body['diagnosis_continuity'] = continuity
                messages = list(request.messages)
                messages[1] = {**messages[1], 'content': json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}
                request = replace(request, messages=tuple(messages), metadata={**request.metadata,
                    'diagnosis_continuity': {'prior_diagnosis_digest': continuity['prior_diagnosis_digest'],
                        'claim_ids': [c['violation']['id'] for c in continuity['claims_for_reassessment']],
                        'omitted_claim_count': continuity['omitted_claim_count'],
                        'scope': 'SAME_SNAPSHOT_REASSESSMENT_NOT_TRUSTED_EVIDENCE'}})
            if execution_feedback is not None:
                request = replace(request, messages=request.messages + ({"role": "user", "content":
                    json.dumps({"execution_feedback": feedback_prompt_view(execution_feedback),
                        "instruction": "Analyze the current snapshot. Recheck old claims against fresh source and public observations. Preserve supported behavior; pending obligations remain open. Public model review is not a hidden verifier result."},
                        ensure_ascii=False)},))
            request = planning_generation_mode(request)
            count = request_tokens(request)
            first_limit = min(fitted.max_context_tokens, int(config.get('initial_diagnosis_context_tokens', fitted.max_context_tokens)))
            if count <= first_limit:
                if config.get('gap_role_delivery_enabled'):
                    from contractedge.retrieval.gap_delivery import audit_gap_delivery
                    receipt=audit_gap_delivery([{'required_unit_ids':required_endpoint_ids}],list(fitted.candidates),
                        json.dumps([dict(m) for m in request.messages],ensure_ascii=False))
                    request=replace(request,metadata={**request.metadata,'gap_delivery':receipt})
                    if receipt['status']=='INCOMPLETE':
                        fitted=replace(fitted,context_audit=fitted.context_audit+({'gap_delivery':receipt},))
                        return fitted,None,count
                audit = {'request_tokens': count, 'input_limit': first_limit,
                         'reserved_output_tokens': request.max_output_tokens,
                         'selected_ids': list(fitted.selected_ids),
                         'surviving_reference_edges': len(fitted.edges),
                         'drop_reasons': dict(fitted.drop_reasons or {})}
                fitted = replace(fitted, context_tokens=count,
                                 context_audit=fitted.context_audit + (audit,))
                request = replace(request, metadata={**request.metadata, 'context_audit': list(fitted.context_audit)})
                return fitted, request, count
            if not fitted.candidates:
                return fitted, None, count
            fitted = trim_context_bundle(fitted, request_tokens=count, limit=first_limit)

    def evidence_delivery_failure(fitted, diagnosis=None, changeset=None):
        for record in reversed(fitted.context_audit):
            receipt=record.get('gap_delivery',{})
            if receipt.get('status')=='INCOMPLETE':
                if engine is not None:
                    engine.step(StageResult('delivery', 'DELIVERY_INCOMPLETE',
                        journal.recorder.capture_value(receipt, 'SOURCE_DELIVERY', snapshot_id=snapshot_id),
                        snapshot_id=snapshot_id))
                return RunTrace(run_id,parsed.case_id,parsed.input_digest,snapshot.snapshot_id,
                    'EVIDENCE_GAP',requirement,scope,fitted,diagnosis,changeset,tuple(calls),
                    ('REQUIRED_SOURCE_NOT_DELIVERED: '+json.dumps(receipt,ensure_ascii=False),),
                    structural_validation,semantic_review,tuple(retrieval_rounds),run_context_wire)
        return None
    try:
        consumer_capabilities = resolve_execution_capabilities(config.get('public_execution_capabilities'),
            backend=config.get('execution_backend', 'direct'))
        workspace_root = Path(workspace).resolve()
        source_root = workspace_root if parsed.source_root == "." else (workspace_root / parsed.source_root).resolve()
        try:
            source_root.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError("MethodInput source_root escapes the extracted workspace") from exc
        snapshot = compute_snapshot(source_root)
        snapshot_id = snapshot.snapshot_id
        if execution_feedback is not None:
            from contractedge.orchestration.feedback import validate_feedback
            validate_feedback(execution_feedback, case_id=parsed.case_id,
                              input_digest=parsed.input_digest, to_snapshot=snapshot.snapshot_id)
            if execution_feedback['run_id'] != run_id:
                raise ValueError('Execution feedback belongs to another run')
        if execution_feedback is None and snapshot.snapshot_id != parsed.initial_snapshot_id:
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id, "INVALID_INPUT",
                            None, None, None, None, None, tuple(calls),
                            ("extracted Broken source does not match MethodInput.initial_snapshot_id",))
        requirement = normalize_requirement({"requirement_text": parsed.requirement_text}, parsed.public_observable_text)
        if config.get("requirement_traceability_enabled"):
            requirement["traceability_required"] = True
        if config.get('operation_consistency_enabled'):
            requirement['operation_consistency_required']=True
        if config.get('obligation_lineage_enabled'):
            requirement['lineage_required']=True
            requirement['prior_obligation_lineage']=(execution_feedback or {}).get('obligation_lineage', {})
        if requirement["status"] != "READY":
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id, "UNDER_SPECIFIED",
                            requirement, None, None, None, None, tuple(calls), ("public requirement is under-specified",))
        effective_hints = [dict(item) for item in parsed.scope_hints]
        for artifact in parsed.declared_artifacts:
            origin = f"declared_artifacts[{artifact['id']}]"
            try:
                documents = list(yaml.safe_load_all(str(artifact["content"])))
            except yaml.YAMLError:
                documents = []
            for document_index, document in enumerate(documents):
                if not isinstance(document, Mapping):
                    continue
                metadata = document.get("metadata")
                if isinstance(metadata, Mapping) and isinstance(metadata.get("name"), str):
                    value = metadata["name"]
                    effective_hints.append({"kind": "concept", "value": value, "raw_text": value,
                                            "origin_ref": f"{origin}.document[{document_index}].metadata.name"})
                spec = document.get("spec")
                template = spec.get("template") if isinstance(spec, Mapping) else None
                pod_spec = template.get("spec") if isinstance(template, Mapping) else None
                containers = pod_spec.get("containers", []) if isinstance(pod_spec, Mapping) else []
                if isinstance(containers, list):
                    for container_index, container in enumerate(containers):
                        if isinstance(container, Mapping):
                            for key in ("name", "image"):
                                value = container.get(key)
                                if isinstance(value, str) and value:
                                    effective_hints.append({"kind": "concept", "value": value,
                                        "raw_text": value,
                                        "origin_ref": f"{origin}.document[{document_index}].containers[{container_index}].{key}"})
        from contractedge.source.entrypoints import probe_entrypoint_hints
        api_hints, focused_paths, entrypoint_audit = probe_entrypoint_hints(snapshot,
            [*((config.get('public_execution_capabilities') or {}).get('method_behavior_probes',[])),
             *((config.get('public_execution_capabilities') or {}).get('registered_public_observation_adapters',[]))])
        if config.get('configuration_consumer_lookup_enabled'):
            from contractedge.source.entrypoints import configuration_consumer_hints
            seeds=[h['value'] for h in effective_hints if h.get('kind')=='path']
            config_hints, config_paths, config_audit = configuration_consumer_hints(snapshot,seeds,parsed.requirement_text)
            api_hints.extend(config_hints);focused_paths.extend(p for p in config_paths if p not in focused_paths)
            entrypoint_audit['configuration_consumers']=config_audit
        effective_hints.extend(api_hints)
        known_terms = {str(item.get("value", "")).casefold() for item in effective_hints}
        if parsed.declared_artifacts:
            stopwords = {"when", "while", "after", "before", "must", "should", "that", "this",
                         "running", "process", "valid", "updated", "declared", "configuration",
                         "deployment", "configmap", "kubernetes", "container"}
            terms = []
            for token in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", parsed.requirement_text):
                if token.casefold() not in stopwords and token.casefold() not in known_terms and token not in terms:
                    terms.append(token)
            for token in terms[:8]:
                effective_hints.append({"kind": "concept", "value": token, "raw_text": token,
                                        "origin_ref": "derived_from_public_requirement"})
        scope = resolve_scope(
            source_root, snapshot, effective_hints,
            max_search_files=max(0, int(config.get("max_scope_search_files", DEFAULT_SCOPE_SEARCH_FILES))),
            max_search_bytes=max(0, int(config.get("max_scope_search_bytes", DEFAULT_SCOPE_SEARCH_BYTES))),
            max_search_file_bytes=max(0, int(config.get("max_scope_search_file_bytes", DEFAULT_SCOPE_SEARCH_FILE_BYTES))),
            max_matches_per_hint=max(0, int(config.get("max_scope_matches_per_hint", DEFAULT_SCOPE_MATCHES_PER_HINT))),
        )
        original_evidence = parsed.public_evidence if execution_feedback is None else tuple(
            item for item in parsed.public_evidence if item['kind'] == 'REQUIREMENT_TEXT')
        for item in public_observations:
            if (item.get('kind') != 'PUBLIC_OBSERVATION' or item.get('snapshot_id') != snapshot.snapshot_id
                    or hashlib.sha256(str(item.get('excerpt', '')).encode()).hexdigest() != item.get('content_digest')):
                raise ValueError('Public observation does not bind the current snapshot/exact content')
        rebased_evidence = tuple({**dict(item), "snapshot_id": snapshot.snapshot_id}
                                 for item in original_evidence) + tuple(dict(item) for item in public_observations)
        run_input = replace(parsed, public_evidence=rebased_evidence)
        exact_paths = {path for audit in scope.search_audit if audit.get('kind') == 'path'
                       and not str(audit.get('origin_ref','')).startswith(('METHOD_PROBE_PUBLIC_API:','METHOD_PUBLIC_PRODUCER_API:','METHOD_PUBLIC_CONFIG_CONSUMER:'))
                       and audit.get('match_mode') in {'PATH_EXACT', 'PATH_DESCRIPTION_TOKEN', 'PATH_UNIQUE_SUFFIX'}
                       for path in audit.get('matched_paths', [])}
        focused_paths.extend(p for p in sorted(exact_paths) if p not in focused_paths)
        declaration_index=[]
        units = extract_units(snapshot, {"seed_paths": scope.seed_paths},
                              query=parsed.requirement_text,
                              max_units=max(1, int(config.get("max_units", 80)) - len(parsed.declared_artifacts) * 4),
                              max_lines=int(config.get("max_lines_per_unit", 80)),
                              max_extension_files=int(config.get("max_extension_files", 24)),
                              focused_paths=focused_paths,declaration_index=declaration_index)
        input_units = extract_declared_artifact_units(parsed.declared_artifacts, snapshot.snapshot_id,
                                                      max_units=int(config.get("max_declared_units", 40)),
                                                      max_lines=int(config.get("max_lines_per_unit", 80)))
        for unit in units:
            if unit['path'] in exact_paths:
                unit['anchor_priority'] = 2
        seen_inputs = set()
        for unit in input_units:
            if unit['artifact_id'] not in seen_inputs:
                unit['anchor_priority'] = 1
                seen_inputs.add(unit['artifact_id'])
        units.extend(input_units)
        scope = replace(scope, extension_paths=tuple(sorted({str(unit["path"]) for unit in units
                                                             if unit.get("scope_extension") == "DIRECT_REFERENCE"})))
        candidate_set = build_candidate_set(
            parsed.requirement_text, parsed.public_observable_text, units, build_direct_reference_edges(units),
            top_n=int(config.get("top_n", 12)),
            max_context_tokens=int(config["max_context_tokens"]), token_counter=token_counter,
        )
        if config.get('gap_role_delivery_enabled'):
            from contractedge.execution.helper_context import source_lifecycle_units, source_exception_units
            lifecycle_units, lifecycle_audit = source_lifecycle_units(snapshot, candidate_set.candidates)
            entrypoint_audit['lifecycle_source_lookup'] = lifecycle_audit
            exception_units, exception_audit = source_exception_units(snapshot, candidate_set.candidates,
                run_input.public_evidence, max_records=max(0, 6-len(lifecycle_units)),
                max_files=max(0, 4-len({u['path'] for u in lifecycle_units})))
            entrypoint_audit['exception_source_lookup'] = exception_audit
            necessary_units = [*lifecycle_units, *exception_units]
            if necessary_units:
                candidate_set = build_candidate_set(parsed.requirement_text, parsed.public_observable_text,
                    [*units, *necessary_units], build_direct_reference_edges([*units, *necessary_units]),
                    top_n=int(config.get('top_n', 12)), max_context_tokens=int(config['max_context_tokens']),
                    token_counter=token_counter)
                candidate_set = replace(candidate_set,
                    required_endpoint_ids=tuple(u['id'] for u in necessary_units))
        retrieval_rounds: list[Mapping[str, Any]] = [{
            "round_id": 1, "seed_paths": list(scope.seed_paths),
            "probe_entrypoint_resolution": entrypoint_audit,
            "selected_ids": list(candidate_set.selected_ids), "dropped_ids": list(candidate_set.dropped_ids),
            "scope_extension_paths": list(scope.extension_paths), "extension_used": False,
        }]
        context = RunContext(run_id, parsed.case_id,
                             int(execution_feedback['round_id']) + 1 if execution_feedback else 0,
                             snapshot.snapshot_id,
                             execution_feedback['from_snapshot'] if execution_feedback else None, parsed.input_digest,
                             model_config=dict(config.get("model", {})),
            retrieval_config={"top_n": candidate_set.top_n,
                             "operation_consistency_enabled": config.get("operation_consistency_enabled",False),
                             "production_path_review_enabled": config.get("production_path_review_enabled", False),
                             "public_execution_capabilities": consumer_capabilities,
                             "max_context_tokens": candidate_set.max_context_tokens,
                             "max_scope_search_files": max(0, int(config.get("max_scope_search_files", DEFAULT_SCOPE_SEARCH_FILES))),
                             "max_scope_search_bytes": max(0, int(config.get("max_scope_search_bytes", DEFAULT_SCOPE_SEARCH_BYTES))),
                             "max_scope_search_file_bytes": max(0, int(config.get("max_scope_search_file_bytes", DEFAULT_SCOPE_SEARCH_FILE_BYTES))),
                             "max_scope_matches_per_hint": max(0, int(config.get("max_scope_matches_per_hint", DEFAULT_SCOPE_MATCHES_PER_HINT))),
                             "max_absence_search_files": max(0, int(config.get("max_absence_search_files", 2_000))),
                             "max_absence_search_bytes": max(0, int(config.get("max_absence_search_bytes", 64 * 1024 * 1024)))},
                             budget={"max_output_tokens": int(config.get("max_output_tokens", 4096)),
                                     "max_model_calls": max_model_calls,
                                     "max_total_tokens": max_total_tokens,
                                     "max_elapsed_seconds": max_elapsed_seconds})
        run_context_wire = asdict(context)
        if not candidate_set.selected_ids:
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id, "EVIDENCE_GAP",
                            requirement, scope, candidate_set, None, None, tuple(calls),
                            ("no source candidates resolved",), run_context=run_context_wire)

        candidate_set, diagnosis_request, prompt_tokens = fit_diagnosis(
            candidate_set, run_input, requirement, context)
        failure=evidence_delivery_failure(candidate_set)
        if failure is not None:return failure
        if diagnosis_request is None or not within_budget(diagnosis_request, prompt_tokens):
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                            "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None,
                            tuple(calls), ("complete diagnosis request exceeds the context, token, or time budget",),
                            run_context=run_context_wire)
        diagnosis_response = complete_recorded(diagnosis_request, "DIAGNOSIS")
        reserved_tokens += prompt_tokens + diagnosis_request.max_output_tokens
        calls.append({"stage": "DIAGNOSIS", "prompt_version": diagnosis_request.prompt_version,
                      "request_metadata": dict(diagnosis_request.metadata),
                      "response_metadata": dict(diagnosis_response.metadata),
                      "response_content": diagnosis_response.content,
                      "elapsed_seconds": time.monotonic() - started,
                      "prompt_tokens_reserved": prompt_tokens,
                      "output_tokens_reserved": diagnosis_request.max_output_tokens})
        diagnosis_response = recover_truncated_planning(diagnosis_response, diagnosis_request, 'DIAGNOSIS')
        if _output_was_truncated(diagnosis_response):
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                            "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None, tuple(calls),
                            ("Diagnosis output reached the configured generation token limit",),
                            run_context=run_context_wire)
        diagnosis_payload: Mapping[str, Any] = {}
        diagnosis_retry_messages = tuple(diagnosis_request.messages)
        try:
            diagnosis_payload = parse_diagnosis_response(diagnosis_response.content)
            diagnosis_payload, runner_binding = _bind_diagnosis_envelope(
                diagnosis_payload, run_input, context, requirement, candidate_set)
            diagnosis = validate_diagnosis(diagnosis_payload, run_input, context, candidate_set)
            diagnosis_wire = diagnosis.raw
            calls[-1]["runner_binding"] = runner_binding
        except (ValueError, ContractError) as exc:
            validation_error: Exception = exc
            while True:
                if format_retries_used >= max_format_retries:
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                    "INVALID_OUTPUT", requirement, scope, candidate_set, None, None,
                                    tuple(calls), (str(validation_error),), "FAIL",
                                    run_context=run_context_wire)
                format_retries_used = policy.consume("format_correction", "diagnosis:" + attempt_id)
                validation_feedback = _format_validation_feedback(validation_error, diagnosis_payload)
                calls[-1]["validation_error"] = str(validation_error)
                calls[-1]["validation_feedback"] = validation_feedback
                diagnosis_retry_messages = _append_diagnosis_retry_turn(
                    diagnosis_retry_messages, diagnosis_response.content, validation_feedback)
                feedback = replace(diagnosis_request, messages=diagnosis_retry_messages,
                    metadata={**dict(diagnosis_request.metadata), "format_retry_index": format_retries_used})
                feedback = planning_generation_mode(feedback)
                feedback_tokens = request_tokens(feedback)
                if feedback_tokens > int(config["max_context_tokens"]) or not within_budget(feedback, feedback_tokens):
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                    "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None,
                                    tuple(calls), (f"Diagnosis validation failed: {validation_error}",
                                        "Diagnosis format retry exceeds the context, token, or time budget"),
                                    "FAIL", run_context=run_context_wire)
                diagnosis_response = complete_recorded(feedback, "DIAGNOSIS_FORMAT_RETRY")
                reserved_tokens += feedback_tokens + feedback.max_output_tokens
                calls.append({"stage": "DIAGNOSIS_FORMAT_RETRY", "prompt_version": feedback.prompt_version,
                              "request_metadata": dict(feedback.metadata),
                              "response_metadata": dict(diagnosis_response.metadata),
                              "response_content": diagnosis_response.content,
                              "elapsed_seconds": time.monotonic() - started,
                              "prompt_tokens_reserved": feedback_tokens,
                              "output_tokens_reserved": feedback.max_output_tokens,
                              "format_retry_index": format_retries_used})
                diagnosis_response = recover_truncated_planning(diagnosis_response, feedback, 'DIAGNOSIS_FORMAT_RETRY')
                if _output_was_truncated(diagnosis_response):
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                    "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None,
                                    tuple(calls), ("Diagnosis format-retry output reached its token limit",),
                                    "FAIL", run_context=run_context_wire)
                try:
                    diagnosis_payload = parse_diagnosis_response(diagnosis_response.content)
                    diagnosis_payload, runner_binding = _bind_diagnosis_envelope(
                        diagnosis_payload, run_input, context, requirement, candidate_set)
                    diagnosis = validate_diagnosis(diagnosis_payload, run_input, context, candidate_set)
                    diagnosis_wire = diagnosis.raw
                    calls[-1]["runner_binding"] = runner_binding
                    break
                except (ValueError, ContractError) as retry_exc:
                    validation_error = retry_exc
        structural_validation = "PASS"
        semantic_review = {"decision": "UNCERTAIN", "method": "NOT_CONFIGURED",
                           "rationale": "Deterministic validation passed; no independent semantic reviewer was configured."}
        if diagnosis.status == "EVIDENCE_GAP" and diagnosis.evidence_gaps:
            units_by_id = {str(unit["id"]): unit for unit in units}
            dropped_units = [units_by_id[unit_id] for unit_id in candidate_set.dropped_ids
                             if unit_id in units_by_id]
            gap_units, gap_search_audit = extract_gap_extension_units(
                snapshot, scope.seed_paths, units, list(diagnosis.evidence_gaps),
                max_files=int(config.get("max_gap_search_files", 2_000)),
                max_units=int(config.get("max_gap_units", 40)),
                max_lines=int(config.get("max_lines_per_unit", 80)),
                max_declaration_lines=int(config.get("max_gap_declaration_lines", 512)),
            )
            absence_evidence, absence_search_audit = collect_absence_search_evidence(
                snapshot, list(diagnosis.evidence_gaps),
                max_files=max(0, int(config.get("max_absence_search_files", 2_000))),
                max_bytes=max(0, int(config.get("max_absence_search_bytes", 64 * 1024 * 1024))),
            )
            gap_search_audit["absence_search"] = absence_search_audit
            extension_units = dropped_units + gap_units
            if config.get('gap_role_delivery_enabled'):
                from contractedge.retrieval.gap_delivery import locate_gap_roles
                roles,role_audit=locate_gap_roles(snapshot,list(diagnosis.evidence_gaps),
                    dict(max_new_files=4,seed_units=units,seed_declarations=declaration_index))
                extension_units.extend(roles)
                gap_search_audit['role_locations']=role_audit
            if not extension_units and not absence_evidence:
                return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                "EVIDENCE_GAP", requirement, scope, candidate_set, diagnosis.raw, None,
                                tuple(calls), ("no additional in-scope candidates remain for the single extension",),
                                structural_validation, semantic_review,
                                tuple(retrieval_rounds + [{"round_id": 2, "extension_used": False,
                                    "gap_search": gap_search_audit, "reason": "no direct query-matched or dropped candidates"}]),
                                run_context_wire)
            try:
                policy.consume("evidence_extension", attempt_id)
                candidate_set = extend_once(
                    candidate_set, diagnosis.evidence_gaps, extension_units,
                    max_context_tokens=int(config["max_context_tokens"]), token_counter=token_counter,
                )
                candidate_set = replace(candidate_set, supplemental_evidence=absence_evidence)
            except ValueError:
                return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                "EVIDENCE_GAP", requirement, scope, candidate_set, diagnosis.raw, None,
                                tuple(calls), ("evidence gap is not locatable or no bounded extension is available",),
                                structural_validation, semantic_review,
                                tuple(retrieval_rounds + [{"round_id": 2, "extension_used": False,
                                    "gap_search": gap_search_audit, "reason": "gap was not locatable"}]),
                                run_context_wire)
            retrieval_rounds.append({"round_id": 2, "extension_used": True,
                "selected_ids": list(candidate_set.selected_ids), "dropped_ids": list(candidate_set.dropped_ids),
                "gap_search": gap_search_audit})
            if candidate_set.selected_ids:
                candidate_set, diagnosis_request, prompt_tokens = fit_diagnosis(
                    candidate_set, run_input, requirement, context, prior=diagnosis.raw, extension_gaps=diagnosis.evidence_gaps)
                failure=evidence_delivery_failure(candidate_set,diagnosis.raw)
                if failure is not None:return failure
                if diagnosis_request is None or not within_budget(diagnosis_request, prompt_tokens):
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                    "BUDGET_EXHAUSTED", requirement, scope, candidate_set, diagnosis.raw, None,
                                    tuple(calls), ("diagnosis extension exceeds the context, token, or time budget",),
                                    structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)
                diagnosis_response = complete_recorded(diagnosis_request, "DIAGNOSIS_EXTENSION")
                reserved_tokens += prompt_tokens + diagnosis_request.max_output_tokens
                calls.append({"stage": "DIAGNOSIS_EXTENSION", "prompt_version": diagnosis_request.prompt_version,
                              "request_metadata": dict(diagnosis_request.metadata),
                              "response_metadata": dict(diagnosis_response.metadata),
                              "response_content": diagnosis_response.content,
                              "elapsed_seconds": time.monotonic() - started,
                              "extension_used": True, "semantic_call_index": 2,
                              "prompt_tokens_reserved": prompt_tokens,
                              "output_tokens_reserved": diagnosis_request.max_output_tokens})
                diagnosis_response = recover_truncated_planning(diagnosis_response, diagnosis_request, 'DIAGNOSIS_EXTENSION')
                if _output_was_truncated(diagnosis_response):
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                    "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None,
                                    tuple(calls), ("Diagnosis extension output reached the configured generation token limit",),
                                    run_context=run_context_wire)
                try:
                    diagnosis_payload = parse_diagnosis_response(diagnosis_response.content)
                    diagnosis_payload, runner_binding = _bind_diagnosis_envelope(
                        diagnosis_payload, run_input, context, requirement, candidate_set)
                    diagnosis = validate_diagnosis(diagnosis_payload, run_input, context, candidate_set)
                    diagnosis_wire = diagnosis.raw
                    calls[-1]["runner_binding"] = runner_binding
                except (ValueError, ContractError) as exc:
                    # This response was generated after the one explicit source
                    # extension. An optional, separate single correction belongs
                    # to this fresh evidence context, never to an HTTP retry.
                    if max_format_retries == 0 or (not per_evidence_context_correction and format_retries_used >= max_format_retries):
                        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                        "INVALID_OUTPUT", requirement, scope, candidate_set, None, None,
                                        tuple(calls), (str(exc),), "FAIL", semantic_review,
                                        tuple(retrieval_rounds), run_context_wire)
                    if not per_evidence_context_correction:
                        format_retries_used = policy.consume("format_correction", "diagnosis:" + attempt_id)
                    if per_evidence_context_correction:
                        policy.consume("format_correction", "diagnosis-extension:" + attempt_id)
                    extension_retry_index = 1 if per_evidence_context_correction else format_retries_used
                    validation_feedback = _format_validation_feedback(exc, diagnosis_payload
                        if isinstance(diagnosis_payload, Mapping) else {})
                    calls[-1]["validation_error"] = str(exc)
                    calls[-1]["validation_feedback"] = validation_feedback
                    extension_feedback = replace(diagnosis_request,
                        messages=_append_diagnosis_retry_turn(diagnosis_request.messages,
                            diagnosis_response.content, validation_feedback),
                        metadata={**dict(diagnosis_request.metadata),
                                  "format_retry_index": extension_retry_index,
                                  "correction_allowance": ('FRESH_EVIDENCE_CONTEXT_ONCE' if per_evidence_context_correction else 'SHARED_FORMAT_CORRECTION'),
                                  "semantic_call_index": 2})
                    extension_feedback = planning_generation_mode(extension_feedback)
                    extension_feedback_tokens = request_tokens(extension_feedback)
                    if (extension_feedback_tokens > int(config["max_context_tokens"])
                            or not within_budget(extension_feedback, extension_feedback_tokens)):
                        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                        "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None,
                                        tuple(calls), (f"Diagnosis extension validation failed: {exc}",
                                            "Diagnosis extension format retry exceeds the context, token, or time budget"),
                                        "FAIL", run_context=run_context_wire)
                    diagnosis_response = complete_recorded(extension_feedback, "DIAGNOSIS_FORMAT_RETRY")
                    reserved_tokens += extension_feedback_tokens + extension_feedback.max_output_tokens
                    calls.append({"stage": "DIAGNOSIS_FORMAT_RETRY",
                                  "prompt_version": extension_feedback.prompt_version,
                                  "request_metadata": dict(extension_feedback.metadata),
                                  "response_metadata": dict(diagnosis_response.metadata),
                                  "response_content": diagnosis_response.content,
                                  "elapsed_seconds": time.monotonic() - started,
                                  "prompt_tokens_reserved": extension_feedback_tokens,
                                  "output_tokens_reserved": extension_feedback.max_output_tokens,
                                  "format_retry_index": extension_retry_index,
                                  "semantic_call_index": 2})
                    diagnosis_response = recover_truncated_planning(diagnosis_response, extension_feedback, 'DIAGNOSIS_FORMAT_RETRY')
                    if _output_was_truncated(diagnosis_response):
                        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                        "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None,
                                        tuple(calls), ("Diagnosis extension format retry output reached its token limit",),
                                        "FAIL", run_context=run_context_wire)
                    try:
                        diagnosis_payload = parse_diagnosis_response(diagnosis_response.content)
                        diagnosis_payload, runner_binding = _bind_diagnosis_envelope(
                            diagnosis_payload, run_input, context, requirement, candidate_set)
                        diagnosis = validate_diagnosis(diagnosis_payload, run_input, context, candidate_set)
                        diagnosis_wire = diagnosis.raw
                        calls[-1]["runner_binding"] = runner_binding
                    except (ValueError, ContractError) as retry_exc:
                        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                        "INVALID_OUTPUT", requirement, scope, candidate_set, None, None,
                                        tuple(calls), (str(retry_exc),), "FAIL", semantic_review,
                                        tuple(retrieval_rounds), run_context_wire)
        diagnosis_decision = diagnosis_gate(diagnosis)
        if diagnosis_decision.action != "PLAN":
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                            diagnosis.status if diagnosis_decision.action == "STOP" else "EVIDENCE_GAP",
                            requirement, scope, candidate_set, diagnosis.raw, None, tuple(calls),
                            () if diagnosis_decision.action == "STOP" else ("DIAGNOSIS_SNAPSHOT_STALE",),
                            structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)

        feedback: list[Mapping[str, Any]] = []
        changeset_payload = None
        validation = None
        change_attempt = -1
        planning_corrections_used = 0
        planning_extension_attempted = False
        while True:
            # A valid source request is not a malformed plan. Keep the one
            # acquisition and the configured correction allowance separate;
            # corrections are never reset by fresh evidence. Every unsuccessful
            # path below either leaves feedback (charged here) or terminates.
            change_attempt += 1
            if feedback:
                if planning_corrections_used >= max_format_retries:
                    errors = tuple(f"{item['code']}:{item.get('path', '')}:{item['message']}" for item in feedback)
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                        'INVALID_PLAN' if validation is not None else 'INVALID_OUTPUT',
                        requirement, scope, candidate_set, diagnosis.raw, changeset_payload,
                        tuple(calls), errors, structural_validation, semantic_review,
                        tuple(retrieval_rounds), run_context_wire)
                planning_corrections_used = policy.consume("format_correction", "planning:" + attempt_id)
            if config.get('production_path_review_enabled', False):
                context = replace(context, retrieval_config={**context.retrieval_config,
                    'production_path_frontier': production_frontier(candidate_set.candidates, parsed.requirement_text)})
                run_context_wire = asdict(context)
            context = replace(context, retrieval_config={**context.retrieval_config,
                    'necessary_source_support': required_planning_source_support(run_input,candidate_set,snapshot.snapshot_id),
                    'source_prerequisites': required_source_prerequisites(candidate_set)})
            run_context_wire = asdict(context)
            changeset_request = build_changeset_request(diagnosis, context, feedback)
            changeset_request = replace(changeset_request, metadata={**changeset_request.metadata,
                'planning_format_retry_index': planning_corrections_used})
            changeset_request = planning_generation_mode(changeset_request)
            prompt_tokens = request_tokens(changeset_request)
            if not within_budget(changeset_request, prompt_tokens):
                return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                "BUDGET_EXHAUSTED", requirement, scope, candidate_set, diagnosis.raw,
                                changeset_payload, tuple(calls),
                                ("ChangeSet request exceeds the total token or time budget",),
                                structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)
            changeset_response = complete_recorded(changeset_request,
                "SEMANTIC_CHANGESET" if change_attempt == 0 else "CHANGESET_REVISION")
            reserved_tokens += prompt_tokens + changeset_request.max_output_tokens
            calls.append({"stage": "SEMANTIC_CHANGESET" if change_attempt == 0 else "CHANGESET_REVISION",
                          "prompt_version": changeset_request.prompt_version,
                          "request_metadata": dict(changeset_request.metadata),
                          "response_metadata": dict(changeset_response.metadata),
                          "response_content": changeset_response.content,
                          "elapsed_seconds": time.monotonic() - started,
                          "semantic_call_index": change_attempt + 1,
                          "prompt_tokens_reserved": prompt_tokens,
                          "output_tokens_reserved": changeset_request.max_output_tokens})
            changeset_response = recover_truncated_planning(changeset_response, changeset_request, 'SEMANTIC_CHANGESET')
            if _output_was_truncated(changeset_response):
                return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                "BUDGET_EXHAUSTED", requirement, scope, candidate_set, diagnosis.raw,
                                None, tuple(calls), ("ChangeSet output reached the configured generation token limit",),
                                structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)
            identity_bound = False
            changeset_payload = None
            validation = None
            try:
                changeset_payload = parse_json_response(changeset_response.content)
                changeset_payload, changeset_binding = bind_changeset_envelope(
                    changeset_payload, diagnosis, run_id, snapshot.snapshot_id, parsed.input_digest)
                identity_bound = True
                calls[-1]["runner_binding"] = changeset_binding
                changeset_payload = adapt_model_changeset(changeset_payload, diagnosis, snapshot)
            except ValueError as exc:
                feedback = _changeset_retry_feedback(exc)
                calls[-1]["validation_error"] = str(exc)
                calls[-1]["validation_feedback"] = feedback
                if not identity_bound or not _safe_planning_gaps(changeset_payload):
                    continue
                validation = ValidationResult(False, (ValidationIssue('INVALID_MODEL_PLAN', '', str(exc)),))
            else:
                validation = validate_semantic_changeset(changeset_payload, diagnosis, snapshot,
                policy=(config.get('public_execution_capabilities') or {}).get('editable_policy'),
                    frontier=context.retrieval_config.get('production_path_frontier'),
                    prerequisites=context.retrieval_config.get('source_prerequisites'), capabilities=consumer_capabilities)
            feedback = [{"code": issue.code, "path": issue.path, "message": issue.message}
                        for issue in validation.errors]
            if feedback:
                calls[-1]['validation_feedback'] = feedback
            planning_gaps = _safe_planning_gaps(changeset_payload)
            if (changeset_payload.get('status') == 'NEEDS_EVIDENCE' and planning_gaps
                    and not planning_extension_attempted and not candidate_set.extension_used):
                planning_extension_attempted = True
                gap_units, audit = extract_gap_extension_units(snapshot, scope.seed_paths, units,
                    planning_gaps, max_files=int(config.get('max_gap_search_files', 2000)),
                    max_units=int(config.get('max_gap_units', 40)),
                    max_lines=int(config.get('max_lines_per_unit', 80)),
                    max_declaration_lines=int(config.get("max_gap_declaration_lines", 512)))
                absence, absence_audit = collect_absence_search_evidence(snapshot, planning_gaps,
                    max_files=int(config.get('max_absence_search_files', 2000)),
                    max_bytes=int(config.get('max_absence_search_bytes', 64 * 1024 * 1024)))
                audit['absence_search'] = absence_audit
                by_id = {u['id']: u for u in units}
                extra = [by_id[i] for i in candidate_set.dropped_ids if i in by_id] + gap_units
                if config.get('gap_role_delivery_enabled'):
                    from contractedge.retrieval.gap_delivery import locate_gap_roles
                    roles,role_audit=locate_gap_roles(snapshot,planning_gaps,
                        dict(max_new_files=4,seed_units=units,seed_declarations=declaration_index))
                    extra.extend(roles);audit['role_locations']=role_audit
                if not extra and not absence:
                    if validation.valid:
                        break
                    continue
                try:
                    policy.consume("evidence_extension", attempt_id)
                    candidate_set = extend_once(candidate_set, planning_gaps, extra,
                        max_context_tokens=int(config['max_context_tokens']), token_counter=token_counter)
                except ValueError:
                    if validation.valid:
                        break
                    continue
                candidate_set = replace(candidate_set, supplemental_evidence=absence)
                retrieval_rounds.append({'round_id': 2, 'extension_used': True,
                    'trigger': 'CHANGESET_NEEDS_EVIDENCE', 'selected_ids': list(candidate_set.selected_ids),
                    'gap_search': audit})
                candidate_set, request, count = fit_diagnosis(candidate_set, run_input, requirement, context, prior=diagnosis.raw, extension_gaps=planning_gaps)
                failure=evidence_delivery_failure(candidate_set,diagnosis.raw,changeset_payload)
                if failure is not None:return failure
                if request is None or not within_budget(request, count):
                    raise BudgetExceeded('Planning evidence extension exceeds the existing round budget')
                response = complete_recorded(request, 'DIAGNOSIS_EXTENSION')
                reserved_tokens += count + request.max_output_tokens
                history = tuple(request.messages)
                planning_context_corrections=0
                while True:
                    calls.append({'stage': 'DIAGNOSIS_EXTENSION' if history == tuple(request.messages) else 'DIAGNOSIS_FORMAT_RETRY',
                        'prompt_version': request.prompt_version, 'response_content': response.content,
                        'response_metadata': dict(response.metadata), 'request_metadata': dict(request.metadata),
                        'extension_used': True, 'trigger': 'CHANGESET_NEEDS_EVIDENCE',
                        'elapsed_seconds': time.monotonic() - started,
                        'prompt_tokens_reserved': count, 'output_tokens_reserved': request.max_output_tokens})
                    response = recover_truncated_planning(response, request, 'DIAGNOSIS_EXTENSION')
                    if _output_was_truncated(response):
                        raise BudgetExceeded('Planning evidence extension output was truncated')
                    body = {}
                    try:
                        body = parse_diagnosis_response(response.content)
                        body, binding = _bind_diagnosis_envelope(body, run_input, context, requirement, candidate_set)
                        diagnosis = validate_diagnosis(body, run_input, context, candidate_set)
                        diagnosis_wire = diagnosis.raw
                        calls[-1]['runner_binding'] = binding
                        break
                    except (ValueError, ContractError) as exc:
                        allowance_used=(planning_context_corrections if per_evidence_context_correction else format_retries_used)
                        if allowance_used >= max_format_retries:
                            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                'INVALID_OUTPUT', requirement, scope, candidate_set, None, changeset_payload,
                                tuple(calls), (str(exc),), 'FAIL', semantic_review,
                                tuple(retrieval_rounds), run_context_wire)
                        planning_context_corrections = (policy.consume("format_correction", "planning-evidence:" + attempt_id)
                            if per_evidence_context_correction else planning_context_corrections + 1)
                        if not per_evidence_context_correction:format_retries_used = policy.consume("format_correction", "diagnosis:" + attempt_id)
                        history = _append_diagnosis_retry_turn(history, response.content,
                            _format_validation_feedback(exc, body if isinstance(body, Mapping) else {}))
                        retry = replace(request, messages=history,metadata={**request.metadata,
                            'correction_allowance':'FRESH_EVIDENCE_CONTEXT_ONCE' if per_evidence_context_correction else 'SHARED_FORMAT_CORRECTION',
                            'format_retry_index':planning_context_corrections if per_evidence_context_correction else format_retries_used})
                        retry = planning_generation_mode(retry)
                        count = request_tokens(retry)
                        if count > int(config['max_context_tokens']) or not within_budget(retry, count):
                            raise BudgetExceeded('Planning evidence correction exceeds the existing round budget')
                        response = complete_recorded(retry, 'DIAGNOSIS_FORMAT_RETRY')
                        reserved_tokens += count + retry.max_output_tokens
                diagnosis_decision = diagnosis_gate(diagnosis)
                if diagnosis_decision.action != "PLAN":
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                        diagnosis.status if diagnosis_decision.action == "STOP" else "EVIDENCE_GAP",
                        requirement, scope, candidate_set, diagnosis.raw, None, tuple(calls),
                        () if diagnosis_decision.action == "STOP" else ("DIAGNOSIS_SNAPSHOT_STALE",),
                        structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)
                # The next plan is based on a new diagnosis. Previous plan
                # errors do not describe it, but any used correction stays used.
                feedback = []
                continue
            if validation.valid:
                break
            feedback = [{"code": issue.code, "path": issue.path, "message": issue.message}
                        for issue in validation.errors]
        assert validation is not None and changeset_payload is not None
        semantic_review = _semantic_review_result(semantic_reviewer, parsed, requirement,
                                                  diagnosis.raw, changeset_payload, candidate_set)
        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                        str(changeset_payload["status"]), requirement, scope, candidate_set,
                        diagnosis.raw, changeset_payload, tuple(calls), (), structural_validation, semantic_review,
                        tuple(retrieval_rounds), run_context_wire)
    except Exception as exc:
        if journal is not None:
            journal.primary_error = exc
        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot_id,
                        "EVIDENCE_DELIVERY_INCOMPLETE" if isinstance(exc, SemanticDeliveryError) else
                        "BUDGET_EXHAUSTED" if isinstance(exc, BudgetExceeded) else "INFRASTRUCTURE_ERROR",
                        requirement, scope, candidate_set, diagnosis_wire, changeset_payload,
                        tuple(calls), (f"{type(exc).__name__}: {exc}",),
                        run_context=run_context_wire)
