from __future__ import annotations

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
from contractedge.changeset.adapter import adapt_model_changeset, bind_changeset_envelope
from contractedge.contracts.models import ContractError, MethodInput, RunContext, ValidationResult, ValidationIssue
from contractedge.contracts.validation import parse_method_input
from contractedge.diagnosis.parse import parse_diagnosis_response, parse_json_response
from contractedge.diagnosis.evidence import bind_evidence_ids, build_evidence_catalog
from contractedge.diagnosis.prompt import build_diagnosis_request
from contractedge.diagnosis.validate import validate_diagnosis
from contractedge.model.provider import ModelProvider
from contractedge.model.budget import BudgetExceeded
from contractedge.orchestration.feedback import feedback_prompt_view
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


def run_front_half(method_input: MethodInput | Mapping[str, Any], workspace: str | Path,
                   provider: ModelProvider, config: Mapping[str, Any],
                   token_counter: Callable[[str], int] | None, *,
                   semantic_reviewer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
                   execution_feedback: Mapping[str, Any] | None = None,
                   public_observations: tuple[Mapping[str, Any], ...] = ()) -> RunTrace:
    started = time.monotonic()
    parsed = method_input if isinstance(method_input, MethodInput) else parse_method_input(method_input)
    run_id = str(config.get("run_id") or uuid.uuid4())
    calls: list[Mapping[str, Any]] = []
    max_total_tokens = int(config.get("max_total_tokens", 24_000))
    max_elapsed_seconds = float(config.get("max_elapsed_seconds", 1_200))
    max_model_calls = int(config.get('max_model_calls', 24))
    reserved_tokens = 0
    run_context_wire = None
    snapshot_id = None
    requirement = None
    scope = None
    candidate_set = None
    diagnosis_wire = None
    changeset_payload = None
    format_retries_used = 0
    truncation_recoveries_used = 0
    separate_truncation_allowance = config.get("planning_truncation_separate_allowance", False)
    if type(separate_truncation_allowance) is not bool:
        raise ValueError("planning_truncation_separate_allowance must be boolean")
    max_format_retries = max(0, int(config.get("max_format_retries", 1)))
    per_evidence_context_correction = config.get('diagnosis_format_retry_per_context', False)
    if type(per_evidence_context_correction) is not bool:
        raise ValueError('diagnosis_format_retry_per_context must be boolean')

    def complete_recorded(request, stage: str):
        previous_timeout = getattr(provider, 'timeout_seconds', None)
        response = None
        try:
            remaining = max_elapsed_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise BudgetExceeded('Model call cannot start after the run deadline')
            if previous_timeout is not None:
                provider.timeout_seconds = min(previous_timeout, remaining)
            response = provider.complete_json(request)
            if time.monotonic() - started >= max_elapsed_seconds:
                raise BudgetExceeded('Model response exceeded the run deadline')
            return response
        except Exception as exc:
            calls.append({"stage": stage, "prompt_version": request.prompt_version,
                          "request_metadata": dict(request.metadata),
                          "response_content": response.content if response is not None else None,
                          "response_metadata": dict(response.metadata) if response is not None else None,
                          "prompt_tokens_reserved": request_tokens(request),
                          "output_tokens_reserved": request.max_output_tokens,
                          "error": f"{type(exc).__name__}: {exc}",
                          "elapsed_seconds": time.monotonic() - started})
            if time.monotonic() - started >= max_elapsed_seconds and not isinstance(exc, BudgetExceeded):
                raise BudgetExceeded('Model call exceeded the run deadline') from exc
            raise
        finally:
            if previous_timeout is not None:
                provider.timeout_seconds = previous_timeout

    def request_tokens(request) -> int:
        if token_counter is None:
            raise ValueError("an explicit model tokenizer/token counter is required")
        count = sum(int(token_counter(str(message.get("content", "")))) for message in request.messages)
        if count < 0:
            raise ValueError("token counter cannot return a negative count")
        return count

    def within_budget(request, prompt_tokens: int) -> bool:
        return (time.monotonic() - started < max_elapsed_seconds
                and len(calls) < max_model_calls
                and prompt_tokens <= int(config['max_context_tokens'])
                and (config.get('model_context_tokens') is None
                     or prompt_tokens + request.max_output_tokens <= int(config['model_context_tokens']))
                and reserved_tokens + prompt_tokens + request.max_output_tokens <= max_total_tokens)

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
        truncation_recoveries_used += 1
        if not separate_truncation_allowance:
            format_retries_used += 1
        result = complete_recorded(retry, 'PLANNING_TRUNCATION_FORMAT_RETRY')
        reserved_tokens += count + retry.max_output_tokens
        calls.append({'stage': 'PLANNING_TRUNCATION_FORMAT_RETRY', 'prompt_version': retry.prompt_version,
            'request_metadata': dict(retry.metadata), 'response_metadata': dict(result.metadata),
            'response_content': result.content, 'elapsed_seconds': time.monotonic() - started,
            'prompt_tokens_reserved': count, 'output_tokens_reserved': retry.max_output_tokens,
            'format_retry_index': format_retries_used,
            'truncation_recovery_index': truncation_recoveries_used})
        return result

    def fit_diagnosis(candidate_set, method, requirement, context):
        """Fit the complete serialized request, including fixed prompt overhead, in context."""
        fitted = candidate_set
        while True:
            request = build_diagnosis_request(method, requirement, fitted, context,
                                              scope_resolution=scope)
            if execution_feedback is not None:
                request = replace(request, messages=request.messages + ({"role": "user", "content":
                    json.dumps({"execution_feedback": feedback_prompt_view(execution_feedback),
                        "instruction": "Analyze the current snapshot. Recheck old claims against fresh source and public observations. Preserve supported behavior; pending obligations remain open. Public model review is not a hidden verifier result."},
                        ensure_ascii=False)},))
            count = request_tokens(request)
            first_limit = min(fitted.max_context_tokens, int(config.get('initial_diagnosis_context_tokens', fitted.max_context_tokens)))
            if count <= first_limit:
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
    try:
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
        units = extract_units(snapshot, {"seed_paths": scope.seed_paths},
                              query=parsed.requirement_text,
                              max_units=max(1, int(config.get("max_units", 80)) - len(parsed.declared_artifacts) * 4),
                              max_lines=int(config.get("max_lines_per_unit", 80)),
                              max_extension_files=int(config.get("max_extension_files", 24)),
                              focused_paths=focused_paths)
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
                             "production_path_review_enabled": config.get("production_path_review_enabled", False),
                             "public_execution_capabilities": config.get('public_execution_capabilities'),
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
                format_retries_used += 1
                validation_feedback = _format_validation_feedback(validation_error, diagnosis_payload)
                calls[-1]["validation_error"] = str(validation_error)
                calls[-1]["validation_feedback"] = validation_feedback
                diagnosis_retry_messages = _append_diagnosis_retry_turn(
                    diagnosis_retry_messages, diagnosis_response.content, validation_feedback)
                feedback = replace(diagnosis_request, messages=diagnosis_retry_messages,
                    metadata={**dict(diagnosis_request.metadata), "format_retry_index": format_retries_used})
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
            if not extension_units and not absence_evidence:
                return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                "EVIDENCE_GAP", requirement, scope, candidate_set, diagnosis.raw, None,
                                tuple(calls), ("no additional in-scope candidates remain for the single extension",),
                                structural_validation, semantic_review,
                                tuple(retrieval_rounds + [{"round_id": 2, "extension_used": False,
                                    "gap_search": gap_search_audit, "reason": "no direct query-matched or dropped candidates"}]),
                                run_context_wire)
            try:
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
                    candidate_set, run_input, requirement, context)
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
                        format_retries_used += 1
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
        if diagnosis.status not in {"READY", "PARTIAL_READY"}:
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id, diagnosis.status,
                            requirement, scope, candidate_set, diagnosis.raw, None, tuple(calls), (),
                            structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)

        feedback: list[Mapping[str, Any]] = []
        changeset_payload = None
        validation = None
        for change_attempt in range(2):
            if config.get('production_path_review_enabled', False):
                context = replace(context, retrieval_config={**context.retrieval_config,
                    'production_path_frontier': production_frontier(candidate_set.candidates, parsed.requirement_text)})
                run_context_wire = asdict(context)
            changeset_request = build_changeset_request(diagnosis, context, feedback)
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
                    if change_attempt == 0:
                        continue
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                    "INVALID_OUTPUT", requirement, scope, candidate_set, diagnosis.raw, None,
                                    tuple(calls), (str(exc),), structural_validation, semantic_review,
                                    tuple(retrieval_rounds), run_context_wire)
                validation = ValidationResult(False, (ValidationIssue('INVALID_MODEL_PLAN', '', str(exc)),))
            else:
                validation = validate_semantic_changeset(changeset_payload, diagnosis, snapshot,
                policy=(config.get('public_execution_capabilities') or {}).get('editable_policy'),
                    frontier=context.retrieval_config.get('production_path_frontier'))
            feedback = [{"code": issue.code, "path": issue.path, "message": issue.message}
                        for issue in validation.errors]
            if feedback:
                calls[-1]['validation_feedback'] = feedback
            planning_gaps = _safe_planning_gaps(changeset_payload)
            if (changeset_payload.get('status') == 'NEEDS_EVIDENCE' and planning_gaps
                    and change_attempt == 0 and not candidate_set.extension_used):
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
                if not extra and not absence:
                    if validation.valid:
                        break
                    continue
                try:
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
                candidate_set, request, count = fit_diagnosis(candidate_set, run_input, requirement, context)
                if request is None or not within_budget(request, count):
                    raise BudgetExceeded('Planning evidence extension exceeds the existing round budget')
                response = complete_recorded(request, 'DIAGNOSIS_EXTENSION')
                reserved_tokens += count + request.max_output_tokens
                history = tuple(request.messages)
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
                        if format_retries_used >= max_format_retries:
                            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                'INVALID_OUTPUT', requirement, scope, candidate_set, None, changeset_payload,
                                tuple(calls), (str(exc),), 'FAIL', semantic_review,
                                tuple(retrieval_rounds), run_context_wire)
                        format_retries_used += 1
                        history = _append_diagnosis_retry_turn(history, response.content,
                            _format_validation_feedback(exc, body if isinstance(body, Mapping) else {}))
                        retry = replace(request, messages=history)
                        count = request_tokens(retry)
                        if count > int(config['max_context_tokens']) or not within_budget(retry, count):
                            raise BudgetExceeded('Planning evidence correction exceeds the existing round budget')
                        response = complete_recorded(retry, 'DIAGNOSIS_FORMAT_RETRY')
                        reserved_tokens += count + retry.max_output_tokens
                if diagnosis.status not in {'READY', 'PARTIAL_READY'}:
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                        diagnosis.status, requirement, scope, candidate_set, diagnosis.raw, None, tuple(calls),
                        (), structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)
                continue
            if validation.valid:
                break
            feedback = [{"code": issue.code, "path": issue.path, "message": issue.message}
                        for issue in validation.errors]
            if change_attempt == 1:
                errors = tuple(f"{issue.code}:{issue.path}:{issue.message}" for issue in validation.errors)
                return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id, "INVALID_PLAN",
                                requirement, scope, candidate_set, diagnosis.raw, changeset_payload, tuple(calls),
                                errors, structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)
        assert validation is not None and changeset_payload is not None
        semantic_review = _semantic_review_result(semantic_reviewer, parsed, requirement,
                                                  diagnosis.raw, changeset_payload, candidate_set)
        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                        str(changeset_payload["status"]), requirement, scope, candidate_set,
                        diagnosis.raw, changeset_payload, tuple(calls), (), structural_validation, semantic_review,
                        tuple(retrieval_rounds), run_context_wire)
    except Exception as exc:
        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot_id,
                        "BUDGET_EXHAUSTED" if isinstance(exc, BudgetExceeded) else "INFRASTRUCTURE_ERROR",
                        requirement, scope, candidate_set, diagnosis_wire, changeset_payload,
                        tuple(calls), (f"{type(exc).__name__}: {exc}",),
                        run_context=run_context_wire)
