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
from contractedge.changeset.validate import validate_semantic_changeset, production_branch_rebinding_gaps
from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
from contractedge.changeset.adapter import adapt_model_changeset, bind_changeset_envelope
from contractedge.contracts.models import ContractError, MethodInput, RunContext, ValidationResult, ValidationIssue
from contractedge.contracts.validation import (parse_method_input, canonical_digest,
    compact_validation_feedback, validation_layer)
from contractedge.contracts.failures import classify_exception, make_failure
from contractedge.diagnosis.parse import parse_diagnosis_response, parse_json_response
from contractedge.diagnosis.evidence import (bind_evidence_ids, build_evidence_catalog, field_contract_contexts,
    source_context_new, source_context_offer, source_context_records, source_context_commit,
    source_context_for_candidates, source_context_restart, merge_evidence_records,
    same_evidence_content, source_context_key, source_context_contract)
from contractedge.diagnosis.prompt import build_diagnosis_request
from contractedge.diagnosis.validate import validate_diagnosis
from contractedge.model.provider import ModelProvider, ModelResponse
from contractedge.model.config import model_config
from contractedge.contracts.stages import StageResult, front_status
from contractedge.orchestration.state import PolicyLedger, RepairState
from contractedge.orchestration.transitions import transition
from contractedge.model.budget import BudgetExceeded, BudgetedProvider, recorded_dispatch_state
from contractedge.model.admission import quote_request, require_context, complete_quoted
from contractedge.orchestration.feedback import feedback_prompt_view
from contractedge.orchestration.semantic_delivery import SemanticDeliveryError, delivery_context, install_monitor
from contractedge.requirement.normalize import normalize_requirement
from contractedge.retrieval.candidates import (CandidateSet, build_candidate_set, extend_once,
    trim_context_bundle, requires_gap_delivery, source_extension_dependency_allowance)
from contractedge.retrieval.edges import build_direct_reference_edges
from contractedge.source.scopes import (
    DEFAULT_SCOPE_MATCHES_PER_HINT, DEFAULT_SCOPE_SEARCH_BYTES, DEFAULT_SCOPE_SEARCH_FILE_BYTES,
    DEFAULT_SCOPE_SEARCH_FILES, ScopeResolution, resolve_scope,
)
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.absence import collect_absence_search_evidence
from contractedge.source.units import (extract_declared_artifact_units, extract_gap_extension_units,
                                       extract_units, materialize_complete_units)


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


def _annotate_gap_delivery_call(call: dict, diagnosis: Mapping[str, Any]) -> None:
    """Persist found/selected/delivered/cited facts after a valid diagnosis."""
    metadata = call.get("request_metadata")
    if not isinstance(metadata, Mapping) or not isinstance(metadata.get("gap_delivery"), Mapping):
        return
    from contractedge.retrieval.gap_delivery import annotate_gap_delivery_citations
    receipt = annotate_gap_delivery_citations(metadata["gap_delivery"], diagnosis)
    call["gap_delivery"] = receipt
    call["request_metadata"] = {**dict(metadata), "gap_delivery": receipt}


def _format_validation_feedback(error: Exception, payload: Mapping[str, Any]) -> str:
    """Add a bounded active-layer marker to the existing correction prose."""
    code = getattr(error, "code", None)
    if not isinstance(code, str):
        match = re.search(r"\b([A-Z][A-Z0-9_]{3,})\b", str(error))
        code = match.group(1) if match else "VALIDATION_ERROR"
    layer = validation_layer(code, getattr(error, "path", ""))
    body = _format_validation_feedback_body(error, payload)
    return (f"Validation layer: {layer}. Validation code: {code}. Correct only the currently actionable "
            f"{layer} errors; preserve unresolved later-layer facts.\n" + body)


def _format_validation_feedback_body(error: Exception, payload: Mapping[str, Any]) -> str:
    """Give a bounded structural correction without selecting semantic labels for the model."""
    message = str(error)
    structured = compact_validation_feedback(getattr(error, "issues", ()))
    if structured:
        # ContractError.__str__ may contain later-layer issues in a compact
        # diagnostic suffix.  Replace it with only the active layer so a
        # correction cannot spend its allowance on blocked downstream errors.
        code = getattr(error, "code", "VALIDATION_ERROR")
        path = getattr(error, "path", "")
        message = (f"{code} at {path or '<root>'}: active issues="
                   + json.dumps(structured, ensure_ascii=False, separators=(",", ":")))
    if 'CAUSAL_CLAIM_UNSUPPORTED' in message:
        violations = [item for item in payload.get('violations', []) if isinstance(item, Mapping)]
        observed_only = [str(item.get('id')) for item in violations
                         if item.get('causal_status') == 'SUPPORTED'
                         and item.get('support_basis') == 'OBSERVED_ONLY' and item.get('id')]
        supported = [str(item.get('id')) for item in violations
                     if item.get('causal_status') == 'SUPPORTED'
                     and item.get('support_basis') != 'OBSERVED_ONLY' and item.get('id')]
        details = (
            f" Invalid supported violation IDs backed only by observation: {observed_only}."
            f" Supported IDs with non-observational support that may remain supported: {supported}."
            if observed_only else " The current response contains no valid source-backed supported violation list; re-check each violation.")
        return ('An observed failure and its timing establish the failed behavior, not its production cause. '
            'A SUPPORTED violation needs actual SOURCE_SPAN evidence of the mechanism and an explanation connecting '
            'that source to the observed failure.' + details + ' For every listed invalid ID, preserve the violation '
            'record and evidence but set causal_status=TENTATIVE (and keep support_basis=OBSERVED_ONLY), unless the '
            'supplied SOURCE_SPAN directly proves the mechanism. Remove only those downgraded IDs from '
            'outcome_coverage.repair_supported; keep other source-backed supported violations and their relations. '
            'Do not change the atom inventory or delete valid relations. Do not upgrade OBSERVED_ONLY into a supported '
            'cause just to pass validation. If no supported violation remains, use UNRESOLVED/EVIDENCE_GAP and a '
            'located evidence request. Return the full corrected Diagnosis body.')
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
    if "CLAUSE_DISPOSITION_MISMATCH" in message:
        # Traceability binding runs before the normal semantic validator.  A
        # model can therefore receive a clause error together with a semantic
        # diagnostic copy that reports an orphaned atom.  Tell it which side of
        # that boundary is authoritative: BACKGROUND is scope metadata and
        # must not become an obligation or a relation.  Without this specific
        # correction, the model commonly "fixes" the error by adding a fake
        # relation for the disclaimer, which then fails binding again.
        background_atoms = []
        for row in payload.get("clause_treatments", []) if isinstance(payload.get("clause_treatments", []), list) else []:
            if not isinstance(row, Mapping) or row.get("disposition") == "OBLIGATION":
                continue
            refs = row.get("obligation_refs", [])
            if isinstance(refs, list):
                background_atoms.extend(str(ref) for ref in refs if isinstance(ref, str))
        refs_text = ", ".join(dict.fromkeys(background_atoms)) or "the atom(s) attached to a non-OBLIGATION clause"
        return (
            "Traceability classification is inconsistent. A clause marked BACKGROUND or UNCERTAIN must have an empty "
            "obligation_refs array; it describes observation scope or an unresolved limitation and is not a repair "
            "obligation. The affected background atom IDs are " + refs_text + ". Remove only those disclaimer atoms and "
            "relations/violations that exist solely for them, or reclassify the clause as OBLIGATION only when the raw "
            "requirement actually requires that behavior. Preserve every real obligation atom and its relation coverage. "
            "Do not satisfy this error by inventing a production edge. Return the complete corrected Diagnosis body."
        )
    if "OBLIGATION_RELATION_MISSING" in message:
        missing = re.findall(r"atomic obligation ([A-Za-z][A-Za-z0-9_-]*) is not represented", message)
        atoms = payload.get("obligation_atoms", [])
        relations = payload.get("expected_relations", [])
        atom_ids = [str(item.get("id")) for item in atoms
                    if isinstance(item, Mapping) and item.get("id")]
        represented = sorted({str(ref) for relation in relations if isinstance(relation, Mapping)
                              for ref in relation.get("obligation_refs", [])
                              if isinstance(ref, str)})
        missing_text = ", ".join(missing) if missing else "the reported atom(s)"
        causal_hint = ""
        observed_only = [str(item.get("id")) for item in payload.get("violations", [])
                         if isinstance(item, Mapping) and item.get("id")
                         and item.get("causal_status") == "SUPPORTED"
                         and item.get("support_basis") == "OBSERVED_ONLY"]
        if observed_only:
            causal_hint = (" Also correct the independently visible causal issue in the same response: supported "
                "violations backed only by PUBLIC_OBSERVATION cannot remain causal_status=SUPPORTED. For "
                + ", ".join(observed_only) + ", set causal_status=TENTATIVE and remove only those IDs from "
                "repair_supported, unless the supplied source span directly proves the mechanism; keep source-backed "
                "supported violations unchanged.")
        return (
            "The atom inventory is already the validated interpretation of the raw requirement. "
            f"The following atom(s) are currently missing from ExpectedRelation.obligation_refs: {missing_text}. "
            f"Existing atom IDs are {atom_ids}; already represented atom IDs are {represented}. "
            "Add or repair only the relation coverage for the missing atom(s). Do not delete, renumber, "
            "merge, or rewrite any existing obligation atom, and do not delete valid existing relations, "
            "violations, evidence references, or outcome coverage merely to make the graph smaller. "
            "Every missing atom must appear in at least one relation.obligation_refs, with its exact parent "
            "outcome_id in relation.requirement_refs. Use only existing units and supplied evidence; if the "
            "available evidence cannot support a causal path, keep the atom represented as an expected relation "
            "and request the missing evidence rather than inventing a consumer or citation. Preserve any other "
            "supported local violations for that outcome; use EVIDENCE_GAP with the remaining blocking gap, "
            "and use UNRESOLVED coverage only if no supported local violation is linked to that outcome. "
            + causal_hint + " "
            "Return the complete corrected Diagnosis body with all prior atoms preserved."
        )
    if "PRODUCTION_PATH_INCOMPLETE" in message:
        return (
            "The proposed executable diagnosis lacks a supported production path. Preserve any independently "
            "supported local violations and their REPAIR_SUPPORTED coverage. Inspect the supplied production "
            "source before connecting units; an expected relation alone does not prove its edge. Do not omit "
            "UNKNOWN endpoints, relabel units, or add an unsupported edge to obtain READY. If a required mechanism "
            "or endpoint is still unavailable, keep the local support, name a locatable evidence_gaps entry, retain "
            "broader unverified behavior in unverified_outcomes, and use EVIDENCE_GAP. Sufficient static source "
            "can support an executable diagnosis without claiming that post-repair runtime behavior has passed. "
            "Original validation: " + message[:3000] + ". Return the full corrected Diagnosis body."
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
            "and preserve_required. REPAIR_SUPPORTED records local causal support, not completion of the whole "
            "outcome. Keep downstream blocking gaps in evidence_gaps and broader unverified behavior in "
            "unverified_outcomes, even for the same supported outcome. READY requires sufficient production "
            "evidence and no blocking gaps or unresolved outcomes; PARTIAL_READY requires supported executable "
            "work plus a distinct unresolved outcome. EVIDENCE_GAP may retain supported local violations when "
            "the diagnosis is not yet executable. Return the full corrected JSON object."
        )
    if "OUTCOME_SUPPORTED_VIOLATION_UNCOVERED" in message:
        return (
            "The validation found a causal_status=SUPPORTED Violation linked through an ExpectedRelation to this outcome, "
            "but the outcome was not assigned REPAIR_SUPPORTED. If the cited static/public evidence supports the cause, "
            "select REPAIR_SUPPORTED for that local support. Preserve that outcome in unverified_outcomes when "
            "its broader behavior remains unverified, and clear only evidence_gaps actually resolved by those "
            "citations. Keep EVIDENCE_GAP while a downstream mechanism or actionable target remains unsupported, "
            "without discarding a supported upstream violation. Use READY only with sufficient production "
            "evidence and no blocking gap or unresolved outcome; READY does not establish behavioral PASS. "
            "If the evidence does not establish the cause, mark the Violation TENTATIVE, select UNRESOLVED, and use "
            "EVIDENCE_GAP. Return one internally consistent full DiagnosisPacket."
        )
    if "STATUS_INCONSISTENT" in message:
        return (
            "Diagnosis status contradicts its coverage or evidence_gaps. Recheck all outcome dispositions and remove "
            "only gaps that the cited evidence actually resolves. READY requires supported coverage for every outcome, "
            "no unresolved outcomes, and no evidence_gaps. PARTIAL_READY requires supported work plus a distinct open "
            "outcome. EVIDENCE_GAP may preserve local SUPPORTED violations and REPAIR_SUPPORTED coverage when "
            "missing evidence prevents an executable diagnosis. Retain broader unverified behavior in "
            "unverified_outcomes; do not clear it merely because a local cause is supported. "
            "Original validation: " + message[:3000] + ". Return one internally consistent full DiagnosisPacket."
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
    # Preserve the established wire shape for a single parse/adapter error;
    # layer grouping is applied once a validator has produced independent
    # issues.  This keeps replay compatibility for old correction ledgers.
    return [{"code": code, "message": message[:1200]}]


def _configuration_graph_feedback(messages, response_content: str) -> str:
    """Inventory a rejected graph; canonical source anchors do not prove its edges."""
    try:
        draft = parse_diagnosis_response(response_content)
    except (TypeError, ValueError):
        return ''
    if not isinstance(draft, Mapping):
        return ''
    request = {}
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get('role') != 'user':
            continue
        try:
            value = json.loads(message.get('content', ''))
        except (TypeError, ValueError):
            continue
        if isinstance(value, Mapping) and 'run_context' in value and 'evidence_catalog' in value:
            request = value
            break
    context = request.get('run_context')
    snapshot = context.get('snapshot_id') if isinstance(context, Mapping) else None
    catalog = request.get('evidence_catalog')
    catalog = catalog if isinstance(catalog, list) else []
    records = {}
    for record in catalog:
        if isinstance(record, Mapping) and isinstance(record.get('id'), str):
            records.setdefault(record['id'], []).append(record)
    sources = {}
    for identity, matches in records.items():
        if len(matches) != 1:
            continue
        record = matches[0]; excerpt = record.get('excerpt'); locator = record.get('locator')
        try:
            excerpt_digest = hashlib.sha256(excerpt.encode()).hexdigest() if isinstance(excerpt, str) else None
        except UnicodeError:
            continue
        if (not isinstance(snapshot, str) or not snapshot or record.get('snapshot_id') != snapshot
                or record.get('kind') != 'SOURCE_SPAN' or not isinstance(excerpt, str)
                or excerpt_digest != record.get('content_digest')
                or not isinstance(locator, Mapping) or not isinstance(locator.get('path'), str)):
            continue
        sources[identity] = dict(id=identity, locator=dict(locator), content_digest=record['content_digest'])
    units = draft.get('units'); units = units if isinstance(units, list) else []
    units_by_id = {}
    for unit in units:
        if isinstance(unit, Mapping) and isinstance(unit.get('id'), str):
            units_by_id.setdefault(unit['id'], []).append(unit)
    candidates = dict(configurations=[], source_anchored_consumers=[], unanchored_consumers=[],
                      terminals=[], relations=[], current_source_spans=[], ambiguous_unit_ids=[])
    cited_sources = set()
    for identity, matches in units_by_id.items():
        if len(matches) != 1:
            candidates['ambiguous_unit_ids'].append(identity)
            continue
        unit = matches[0]; refs = unit.get('requirement_refs'); locators = unit.get('locator_refs')
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            continue
        row = dict(unit_id=identity, requirement_refs=list(refs))
        role = unit.get('semantic_role_class')
        if not isinstance(role, str):
            continue
        if role == 'CONFIGURATION_INPUT':
            candidates['configurations'].append(row)
        elif role == 'PRODUCTION_CONSUMER' and unit.get('existence') == 'PRESENT':
            source_refs = list(dict.fromkeys(ref for ref in locators if isinstance(ref, str) and ref in sources)) if isinstance(locators, list) else []
            if source_refs:
                row['source_refs'] = source_refs
                cited_sources.update(source_refs)
                candidates['source_anchored_consumers'].append(row)
            else:
                candidates['unanchored_consumers'].append(row)
        elif role in {'GENERATED_STATE', 'RUNTIME_CONSUMER', 'OBSERVED_STATE'}:
            candidates['terminals'].append(row)
    relations = draft.get('expected_relations')
    for relation in relations if isinstance(relations, list) else []:
        if (not isinstance(relation, Mapping) or not isinstance(relation.get('id'), str)
                or any(not isinstance(relation.get(key), list) or not all(isinstance(ref, str) for ref in relation[key])
                       for key in ('source_unit_ids', 'target_unit_ids', 'requirement_refs'))):
            continue
        candidates['relations'].append({key: relation[key] for key in
            ('id', 'source_unit_ids', 'target_unit_ids', 'requirement_refs')})
    candidates['current_source_spans'] = [record for identity, record in sources.items() if identity in cited_sources]
    prefix = ('\nNaming a function in mechanism text does not route an edge through its unit. '
              'Inspect the existing units and actual directed endpoints against supplied excerpts. '
              'Source anchoring proves availability, not the claimed role or causality. Do not invent '
              'edges, new consumers or source citations to satisfy validation; retain unresolved gaps when needed. '
              'Rejected graph inventory (not verified causality): ')
    result = {key: [] for key in candidates}
    result['omitted'] = {key: len(rows) for key, rows in candidates.items()}
    def serialize():
        return prefix + json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    # Round robin keeps one large category from starving all endpoint categories.
    for index in range(max(map(len, candidates.values()), default=0)):
        for key, rows in candidates.items():
            if index >= len(rows) or len(result[key]) >= 8:
                continue
            result[key].append(rows[index]); result['omitted'][key] -= 1
            try:
                fits = len(serialize().encode()) <= 4096
            except UnicodeError:
                fits = False
            if not fits:
                result[key].pop(); result['omitted'][key] += 1
    return serialize()


def _append_diagnosis_retry_turn(messages, response_content: str, validation_feedback: str):
    """Append one correction without accumulating prior correction turns.

    A diagnosis request carries a large, immutable evidence bundle.  Keeping
    every previous assistant draft and validation message makes a second
    correction pay for the same bundle repeatedly and can exhaust the shared
    round budget before the semantic stage is reached.  The first correction
    still preserves the exact input tuple for replay compatibility.  Once a
    correction already exists, retain the request prefix (system prompt plus
    the current structured user context and any structured execution feedback)
    and replace the old draft/feedback with the latest pair.  This changes
    transport history only; evidence IDs, snapshot identity and validator
    rules remain unchanged.
    """
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
    if 'PRODUCTION_PATH_INCOMPLETE' in validation_feedback:
        validation_feedback += _configuration_graph_feedback(messages, response_content)
    # Keep the original request context, but do not carry an earlier
    # assistant draft or validation feedback into the next correction.  A
    # model request can contain auxiliary structured user turns (for example
    # execution feedback) before the first response; those remain part of the
    # immutable prefix.  The first invocation has no assistant turn and is
    # intentionally byte-for-byte compatible with the old request shape.
    base_messages = tuple(messages)
    first_assistant = next((index for index, message in enumerate(base_messages)
                            if isinstance(message, Mapping)
                            and message.get("role") == "assistant"), None)
    feedback_history = []
    if first_assistant is not None:
        # Validation feedback is small state, unlike the evidence bundle.  A
        # later correction must still see independent errors discovered by an
        # earlier pass, otherwise fixing one field can regress another.  Keep
        # a de-duplicated, bounded ledger of those messages while dropping the
        # old assistant drafts themselves.
        for message in base_messages[first_assistant + 1:]:
            if isinstance(message, Mapping) and message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str) and content and content not in feedback_history:
                    feedback_history.append(content)
        base_messages = tuple(base_messages[:first_assistant])
    if feedback_history:
        feedback_rows = [*feedback_history, validation_feedback]
        deduplicated = []
        for content in feedback_rows:
            if content not in deduplicated:
                deduplicated.append(content)
        validation_feedback = "\n\n".join(deduplicated)
        # Feedback is diagnostic control data, not source evidence.  Bound it
        # independently so an unexpectedly verbose validator cannot recreate
        # the original context-growth problem.
        feedback_limit = 24_000
        encoded = validation_feedback.encode("utf-8", errors="replace")
        if len(encoded) > feedback_limit:
            current = encoded[-feedback_limit:]
            validation_feedback = current.decode("utf-8", errors="ignore")
    return base_messages + (
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
    catalog = build_evidence_catalog(method_input, candidate_set, context.snapshot_id,
        source_context=context.retrieval_config.get('source_context'))
    body["evidence_refs"] = bind_evidence_ids(_collect_diagnosis_evidence_ids(body), catalog)
    # This is the canonical record registry, not just the model's chosen
    # citations. Keep already-budgeted current observations available to plans
    # even when the diagnosis did not use them to justify a causal claim.
    retained_public_ids = []
    bound_ids = {record['id'] for record in body['evidence_refs']}
    for record in catalog:
        if record.get('kind') != 'PUBLIC_OBSERVATION':
            continue
        if record.get('snapshot_id') != context.snapshot_id:
            raise ValueError('Planning observations must belong to the current snapshot')
        if record['id'] not in bound_ids:
            body['evidence_refs'].append(dict(record))
            bound_ids.add(record['id'])
            retained_public_ids.append(record['id'])
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
                  "retained_uncited_public_evidence_ids": retained_public_ids,
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
    # Engine-only facts.  These are persisted in the trace but never added to
    # the model output contract.
    failure_records: tuple[Mapping[str, Any], ...] = ()
    primary_failure: Mapping[str, Any] | None = None
    secondary_failure: Mapping[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = "ce.run_trace.v1_1"
        payload["model_calls"] = list(self.model_calls)
        payload["retrieval_rounds"] = list(self.retrieval_rounds)
        payload["errors"] = list(self.errors)
        payload["failure_records"] = list(self.failure_records)
        return payload


def _front_failure_projection(trace: RunTrace, journal=None, dispatch_start: int = 0) -> RunTrace:
    """Persist one bounded engine failure for a non-successful front trace.

    The front-half implementation has many deliberate early returns.  Keeping
    this projection at the boundary avoids duplicating failure bookkeeping in
    every return path while retaining the original public status and errors.
    """
    if trace.failure_records:
        if journal is None:
            return trace
        recent = journal.dispatches[dispatch_start:]
        current_receipt = getattr(getattr(journal, 'primary_error', None),
                                  '_front_failure_dispatch_receipt', None)
        refs = (current_receipt.call_refs
                if any(receipt is current_receipt for _, receipt in recent) else ())
        if not refs or all(record.get("call_ref") for record in trace.failure_records):
            return trace
        ref = refs[-1]
        call_ref = f"{ref.run_id}:{ref.segment_index}:{ref.absolute_call_index}"
        records = tuple({**dict(record), "call_ref": record.get("call_ref") or call_ref}
                        for record in trace.failure_records)
        primary = ({**dict(trace.primary_failure), "call_ref": trace.primary_failure.get("call_ref") or call_ref}
                   if trace.primary_failure else records[0])
        secondary = ({**dict(trace.secondary_failure), "call_ref": trace.secondary_failure.get("call_ref") or call_ref}
                     if trace.secondary_failure else None)
        return replace(trace, failure_records=records, primary_failure=primary,
                       secondary_failure=secondary)
    if trace.status in {
            "READY", "PARTIAL_READY", "READY_PARTIAL", "UNDER_SPECIFIED",
            "NO_SUPPORTED_VIOLATION"}:
        return trace
    status = str(trace.status or "UNKNOWN")
    message = "; ".join(str(error) for error in trace.errors[:4])
    if status == "BUDGET_EXHAUSTED":
        code, failure_class, stage = "BUDGET_EXHAUSTED", "infrastructure", "orchestration"
    elif status in {"INVALID_OUTPUT", "FORMAT_INVALID"}:
        code, failure_class, stage = "INVALID_OUTPUT", "model_format", "front"
    elif status in {"INVALID_PLAN", "PLAN_INVALID"}:
        code, failure_class, stage = "INVALID_PLAN", "model_format", "planning"
    elif status in {"EVIDENCE_DELIVERY_INCOMPLETE", "DELIVERY_INCOMPLETE"} or any(
            token in message for token in ("REQUIRED_SOURCE_NOT_DELIVERED", "delivery", "DELIVERY")):
        code, failure_class, stage = "EVIDENCE_DELIVERY_INCOMPLETE", "evidence", "delivery"
    elif status == "EVIDENCE_GAP":
        code, failure_class, stage = "EVIDENCE_GAP", "evidence", "retrieval"
    elif status in {"RESPONSE_UNKNOWN"}:
        code, failure_class, stage = "RESPONSE_UNKNOWN", "unknown", "provider"
    elif status in {"INFRASTRUCTURE_ERROR", "ENVIRONMENT_ERROR"}:
        code, failure_class, stage = "INFRASTRUCTURE_ERROR", "infrastructure", "orchestration"
    else:
        return trace

    calls = [call for call in trace.model_calls if isinstance(call, Mapping)]
    latest = calls[-1] if calls else {}
    dispatch = latest.get("dispatch_state")
    if dispatch not in {"not_sent", "response_known", "response_unknown"}:
        dispatch = "response_unknown" if latest.get("error") and "response_content" not in latest else "not_sent"
    if dispatch == "response_unknown" and status not in {"BUDGET_EXHAUSTED", "EVIDENCE_GAP"}:
        code, failure_class, stage = "RESPONSE_UNKNOWN", "unknown", "provider"
    call_ref = None
    if journal is not None:
        recent = journal.dispatches[dispatch_start:]
        refs = recent[-1][1].call_refs if recent else ()
        if refs:
            ref = refs[-1]
            call_ref = f"{ref.run_id}:{ref.segment_index}:{ref.absolute_call_index}"
    admission = next((row['admission_failure'] for row in reversed(
        trace.candidate_set.context_audit if trace.candidate_set else ())
        if 'admission_failure' in row), None) if status == 'BUDGET_EXHAUSTED' else None
    record = make_failure(stage, code, failure_class=failure_class,
        dispatch_state=dispatch, message=message or code, call_ref=call_ref,
        snapshot_id=trace.snapshot_id, details={"status": status, **({"admission": admission} if admission else {})})
    return replace(trace, failure_records=(record,), primary_failure=record)



def required_source_prerequisites(candidate_set, *, helper_support=(), establishment_required=False):
    """One producer/consumer projection of current, already-validated source roles.

    The Ready boundary independently recomputes these entries rather than
    trusting a saved context or dropping its role-specific requirements.
    """
    from contractedge.diagnosis.evidence import reader_reference_contexts
    # READY binds what its producer actually declared. A historical trace did
    # not request this new review: do not silently add obligations on resume.
    # fit_diagnosis marks every current selected reader role before planning.
    reader_rows = reader_reference_contexts([u for u in candidate_set.candidates
        if 'SOURCE_READER:'+u['id'] in u.get('gap_role_keys',[])])
    rows = []
    for unit in candidate_set.candidates:
        contracts = field_contract_contexts([unit], ' '.join(unit.get('source_contract_fields', [])))
        readers = [row for row in reader_rows if row['source_evidence_ref']=='SRC-'+unit['id']]
        if not unit.get('precondition_review_required') and not contracts and not readers:
            continue
        reason = ('Review the source-supported exception-producing guard and caller prerequisites; literal match and local calls are locators, not causal proof'
            if any(role.startswith('PUBLIC_EXCEPTION:') for role in unit.get('gap_role_keys', [])) else
            'Review source-supported initialization, readiness and state preservation; lexical relevance only')
        if contracts:
            if not unit.get('precondition_review_required'):
                reason = 'Review the applicability of the supplied field/API source contract; this is source interpretation, not observed runtime readiness.'
            reason += ' Review these exact source-local field contracts before inferring consumer behavior; producing a value is not proof the consumer applies it: '+json.dumps(contracts,ensure_ascii=False,separators=(',',':'))
        if readers:
            reason += (' Review the actual reference publication to readers separately from starting or retiring an object. '
                'A wait after assigning the reader-visible reference does not cover reads before that wait. '
                'These are lexical windows, not an automatically supported causal relation: '
                +json.dumps(readers,ensure_ascii=False,separators=(',',':')))
        rows.append(dict(source_evidence_ref='SRC-'+unit['id'],reason=reason))
    for helper in helper_support:
        rows.append(dict(source_evidence_ref=helper['id'],reason=(
            'Review the exact local helper contract before choosing implementation: input/output identity, '
            'selection semantics, mutation and initialization/cleanup requirements. A new call path or changed '
            'guard can require changing earlier initialization as well as later cleanup. Link applicable '
            'conditions to existing checks or open obligations; do not treat lexical context as causal proof '
            'or an additional editable target.')))
    if establishment_required:
        def source_key(snapshot_id,path,loc,text,digest):
            if (not isinstance(snapshot_id,str) or not snapshot_id or not isinstance(path,str) or not path
                    or not isinstance(text,str) or not isinstance(loc,Mapping) or hashlib.sha256(text.encode()).hexdigest()!=digest
                    or type(loc.get('start_line')) is not int or type(loc.get('end_line')) is not int
                    or not 1<=loc['start_line']<=loc['end_line']):
                return None
            return (snapshot_id,path,loc['start_line'],loc['end_line'],digest)
        bindings={}
        def bind(eid,key):
            if eid in bindings and (key is None or bindings[eid] is None or bindings[eid]!=key):
                raise ValueError('Prerequisite source ID is ambiguous across physical source records: '+eid)
            bindings[eid]=key
        for unit in candidate_set.candidates:
            bind('SRC-'+unit['id'],source_key(unit.get('snapshot_id'),unit.get('path'),unit.get('locator',{}),
                unit.get('text'),unit.get('content_digest')))
        for helper in helper_support:
            loc=helper.get('locator',{})
            if not isinstance(loc,Mapping):loc={}
            bind(helper['id'],source_key(helper.get('snapshot_id'),loc.get('path'),loc,
                helper.get('excerpt'),helper.get('content_digest')))
        # Physical equality only. Keep every role rationale and all original
        # source evidence; this is neither semantic equivalence nor permission.
        unique={};combined=[]
        for index,row in enumerate(rows):
            eid=row['source_evidence_ref'];key=bindings.get(eid) or ('UNBOUND',index)
            if key not in unique:
                row=dict(row);unique[key]=row;combined.append(row)
            else:
                canonical=unique[key]
                if row['reason'] not in canonical['reason']:canonical['reason']+=' '+row['reason']
                if eid!=canonical['source_evidence_ref'] and eid not in canonical.get('source_alias_refs',[]):
                    canonical.setdefault('source_alias_refs',[]).append(eid)
        rows=combined
        for row in rows:
            row['establishment_required'] = True
    if len(rows)>12:
        raise BudgetExceeded('Planning prerequisite review exceeds the existing 12-record contract; '
            f'{len(rows)} > 12 records; no required contract was silently dropped',
            details={'dimension': 'prerequisite_records', 'actual': len(rows), 'limit': 12})
    return rows



def required_planning_source_support(method_input, candidate_set, snapshot_id, *, helper_support=()):
    # Selection is complete. A model citation is not a second selection gate:
    # planning and execution receive every selected current source body under
    # the same request limits, without another scan or an edit grant.
    records=[r for r in build_evidence_catalog(method_input,candidate_set,snapshot_id)
            if r.get('kind')=='SOURCE_SPAN']
    records.extend(helper_support)
    return [{**r,'delivery_required':True} for r in records]


def requirement_for_rediagnosis(requirement, prior, snapshot_id, input_digest):
    """Carry validated obligation identities with same-snapshot reassessment."""
    from copy import deepcopy
    result = deepcopy(requirement)
    if (result.get('lineage_required') and prior
            and prior.get('snapshot_id') == snapshot_id
            and prior.get('input_digest') == input_digest
            and prior.get('obligation_lineage')):
        result['prior_obligation_lineage'] = deepcopy(prior['obligation_lineage'])
    return result


def diagnosis_continuity(prior, snapshot_id, evidence_catalog, extension_gaps, *, input_digest,
                         planning_context=None):
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
    branches, omitted_branches = [], 0
    if (isinstance(planning_context, Mapping) and prior.get('run_id')
            and all(planning_context.get(k) == prior.get(k)
                    for k in ('run_id', 'snapshot_id', 'input_digest'))):
        open_rows = planning_context.get('open_obligations', [])
        from jsonschema import Draft202012Validator
        from contractedge.contracts.validation import _load_schema
        schema = _load_schema('ce.semantic_changeset.v1_1')
        open_validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/openObligation'})
        parents = {a['id']: a.get('outcome_id') for a in prior.get('obligation_atoms', [])}
        outcomes = {o['id'] for o in prior.get('requirement_spec', {}).get('outcomes', [])}
        opened, seen_open = {}, set()
        for obligation in open_rows if isinstance(open_rows, list) else []:
            if not isinstance(obligation, Mapping) or not isinstance(obligation.get('id'), str):
                continue
            oid = obligation['id']
            if oid in seen_open:
                opened.pop(oid, None)  # Duplicate IDs are ambiguous, never last-wins.
                continue
            seen_open.add(oid)
            if (open_validator.is_valid(obligation) and obligation['outcome_id'] in outcomes
                    and all(parents.get(ref) == obligation['outcome_id'] for ref in obligation['obligation_refs'])):
                opened[oid] = obligation
        atoms = {a['id'] for a in prior.get('obligation_atoms', [])}
        seen = set()
        reviews = planning_context.get('production_path_review', [])
        for row in reviews if isinstance(reviews, list) else []:
            if not isinstance(row, Mapping):
                continue
            eid = row.get('source_evidence_id')
            refs = row.get('open_obligation_refs', [])
            if (row.get('disposition') != 'UNRESOLVED' or row.get('change_refs') != []
                    or not isinstance(eid, str) or not isinstance(row.get('rationale'), str)
                    or not isinstance(refs, list) or not refs
                    or any(not isinstance(ref, str) or ref not in opened for ref in refs)
                    or any(not isinstance(opened[ref].get('obligation_refs', []), list)
                           or not all(isinstance(a, str) for a in opened[ref].get('obligation_refs', [])) for ref in refs)
                    or eid in seen):
                continue
            seen.add(eid)
            record = current.get(eid, {})
            original = previous.get(eid, {})
            text = record.get('excerpt')
            if (record.get('kind') != 'SOURCE_SPAN' or record.get('snapshot_id') != snapshot_id
                    or not isinstance(text, str)
                    or hashlib.sha256(text.encode()).hexdigest() != record.get('content_digest')
                    or any(original.get(k) != record.get(k) for k in
                           ('snapshot_id', 'kind', 'locator', 'content_digest', 'excerpt'))
                    or any(not set(opened[ref].get('obligation_refs', [])) <= atoms for ref in refs)):
                omitted_branches += 1
                continue
            concern = dict(source_evidence_ref=eid, snapshot_id=snapshot_id,
                source_content_digest=record['content_digest'], locator=dict(record['locator']),
                disposition='UNRESOLVED', rationale=row.get('rationale', ''),
                open_obligations=[dict(opened[ref]) for ref in refs])
            if len(branches) >= 4 or len(json.dumps(branches + [concern], ensure_ascii=False).encode()) > 8000:
                omitted_branches += 1
                continue
            branches.append(concern)
    if not claims and not omitted and not branches and not omitted_branches:
        return None
    result = {'snapshot_id': snapshot_id, 'prior_diagnosis_digest': canonical_digest(prior),
        'claims_for_reassessment': claims, 'omitted_claim_count': omitted,
        'extension_gaps': [dict(g) for g in extension_gaps[:8]],
        'instruction': 'Prior claims are not trusted facts or edit permission. Cite only exact current evidence_catalog records; unavailable IDs cannot support claims. '
            'Reassess the listed gaps and prior static inference. Missing final/live observation limits verification, not source-supported fault inference. '
            'Revise or withdraw claims when current evidence warrants it; explain in causal_explanation/evidence_gaps.summary. '
            'Missing repair helpers may be introduced only with evidenced caller/interface prerequisites and edit permission. '
            'Unobserved outcomes remain UNKNOWN. Do not automatically retain old verdicts.'}
    if branches or omitted_branches:
        result.update(planning_branch_assessments=branches,
            omitted_planning_branch_count=omitted_branches,
            planning_context_digest=canonical_digest(planning_context),
            planning_branch_interpretation='Unverified same-snapshot planning concerns, not new evidence, '
                'supported violations or edit permission. Reassess each branch against the current source, '
                'public observations and obligation scope. Bind participating branches to appropriate semantic '
                'units only when justified; explain remaining uncertainty or irrelevance. Do not narrow a broad '
                'obligation to one branch merely because the earlier supported diagnosis covered only that branch.')
    return result



def run_front_half(method_input: MethodInput | Mapping[str, Any], workspace: str | Path,
                   provider: ModelProvider, config: Mapping[str, Any],
                   token_counter: Callable[[str], int] | None, *,
                   semantic_reviewer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
                   execution_feedback: Mapping[str, Any] | None = None,
                   public_observations: tuple[Mapping[str, Any], ...] = (),
                   journal=None, attempt_id="front", inherited_source_context=None) -> RunTrace:
    # A malformed or mixed source-material contract is a configuration error,
    # raised before any acquisition or model call rather than a run outcome.
    source_context_contract(config)
    if not isinstance(provider, BudgetedProvider):
        if token_counter is None:
            raise ValueError("an explicit model tokenizer/token counter is required")
        provider = BudgetedProvider(provider, token_counter,
            max_tokens=int(config.get('max_total_tokens', 32_000)),
            max_seconds=float(config.get('max_elapsed_seconds', 1_200)),
            max_calls=int(config.get('max_model_calls', 24)),
            reconcile_observed_output=config.get('reconcile_observed_output', False),
            reconcile_observed_input=config.get('reconcile_observed_input', False))
        if journal is not None:
            journal.attach(provider)
            provider.on_update = journal.update
    dispatch_start = len(journal.dispatches) if journal is not None else 0
    if journal is not None:
        install_monitor(provider, journal)
        journal.primary_error = None
    products = {}
    retrieval_rounds = []
    trace = _run_front_half_impl(method_input, workspace, provider, config, token_counter,
        semantic_reviewer=semantic_reviewer, execution_feedback=execution_feedback,
        public_observations=public_observations, journal=journal, attempt_id=attempt_id,
        products=products, inherited_source_context=inherited_source_context, retrieval_rounds=retrieval_rounds)
    # One final projection covers every deliberate early return as well as
    # exceptions. Do not make each validation branch copy acquisition history.
    trace = replace(trace, retrieval_rounds=tuple(retrieval_rounds))
    trace = _front_failure_projection(trace, journal, dispatch_start)
    if journal is not None:
        from contractedge.orchestration.recorder import preserve_error
        def record_final_front():
            inputs = tuple(journal.recorder.capture_value(value, kind, snapshot_id=trace.snapshot_id)
                for value, kind in (
                    (asdict(method_input) if isinstance(method_input, MethodInput) else dict(method_input), "METHOD_INPUT"),
                    (dict(config), "FRONT_CONFIG"), (execution_feedback, "EXECUTION_FEEDBACK"),
                    (list(public_observations), "PUBLIC_OBSERVATIONS")))
            return journal.final_front(trace, attempt_id=attempt_id,
                                       products=products, input_refs=inputs)
        preserve_error(journal.primary_error, record_final_front)
    return trace


def _run_front_half_impl(method_input: MethodInput | Mapping[str, Any], workspace: str | Path,
                   provider: ModelProvider, config: Mapping[str, Any],
                   token_counter: Callable[[str], int] | None, *,
                   semantic_reviewer=None, execution_feedback=None,
                   public_observations=(), journal=None, attempt_id="front", products=None,
                   inherited_source_context=None, retrieval_rounds=None) -> RunTrace:
    started = time.monotonic()
    parsed = method_input if isinstance(method_input, MethodInput) else parse_method_input(method_input)
    run_id = str(config.get("run_id") or uuid.uuid4())
    front_call_start = len(provider.calls)
    front_token_start = provider.reserved_tokens
    products = {} if products is None else products

    call_annotations = {}
    retrieval_rounds = [] if retrieval_rounds is None else retrieval_rounds

    def front_calls():
        # Stage annotations are a view over immutable canonical call facts,
        # not another usage ledger or a mutation of already sealed records.
        return [{**call, **call_annotations.get(index, {})}
                for index, call in enumerate(provider.calls[front_call_start:], front_call_start)]

    def current_annotations():
        if len(provider.calls) <= front_call_start:
            raise RuntimeError('Front annotation has no actual model call')
        return call_annotations.setdefault(len(provider.calls) - 1, {})

    def annotate_gap_delivery(diagnosis):
        projection = front_calls()[-1]
        _annotate_gap_delivery_call(projection, diagnosis)
        if 'gap_delivery' in projection:
            current_annotations().update(gap_delivery=projection['gap_delivery'],
                                         request_metadata=projection['request_metadata'])
    # Keep the development default large enough for one bounded diagnosis
    # correction plus planning. Formal profiles declare an explicit budget;
    # this fallback is only for legacy/synthetic callers that omit it.
    max_total_tokens = int(config.get("max_total_tokens", 32_000))
    configured_reserve = config.get("post_planning_reserve_tokens", 0)
    if type(configured_reserve) is not int:
        raise ValueError("post_planning_reserve_tokens must be an integer")
    post_planning_reserve_tokens = configured_reserve
    if post_planning_reserve_tokens < 0:
        raise ValueError("post_planning_reserve_tokens must be nonnegative")
    # A resumed/reassessment round may inherit less than the configured floor;
    # in that case no new planning request is admitted and the caller receives
    # the normal BUDGET_EXHAUSTED projection.
    post_planning_reserve_tokens = min(post_planning_reserve_tokens, max_total_tokens)
    max_elapsed_seconds = float(config.get("max_elapsed_seconds", 1_200))
    max_model_calls = int(config.get('max_model_calls', 24))
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
    planning_helpers = []
    active_sources = None
    context = None
    separate_truncation_allowance = config.get("planning_truncation_separate_allowance", False)
    if type(separate_truncation_allowance) is not bool:
        raise ValueError("planning_truncation_separate_allowance must be boolean")
    generation_recovery_scope = config.get('planning_generation_recovery_scope', 'prompt_version')
    if generation_recovery_scope not in ('prompt_version', 'front_half'):
        raise ValueError('planning_generation_recovery_scope must be prompt_version or front_half')
    engine = getattr(journal, "engine", None)
    policy = engine.policy if engine is not None else PolicyLedger(config)
    max_format_retries = policy.limit("format_correction")
    per_evidence_context_correction = config.get('diagnosis_format_retry_per_context', False)
    if type(per_evidence_context_correction) is not bool:
        raise ValueError('diagnosis_format_retry_per_context must be boolean')
    reconcile_observed_output = config.get('reconcile_observed_output', False)
    if type(reconcile_observed_output) is not bool:
        raise ValueError('reconcile_observed_output must be boolean')
    reconcile_observed_input = config.get('reconcile_observed_input', False)
    if type(reconcile_observed_input) is not bool:
        raise ValueError('reconcile_observed_input must be boolean')
    compact_planning_context = config.get('compact_planning_context', False)
    if type(compact_planning_context) is not bool:
        raise ValueError('compact_planning_context must be boolean')

    def record_call(value):
        """Project stage metadata without changing sealed dispatch facts."""
        protected = {'request', 'request_metadata', 'response_content', 'response_metadata',
            'prompt_tokens_reserved', 'output_tokens_reserved', 'dispatch_state', 'elapsed_seconds',
            'error', 'provider_error_metadata'}
        current_annotations().update({key: value for key, value in value.items() if key not in protected})

    def diagnosis_gate(diagnosis):
        payload_ref = None
        call_refs = ()
        if journal is not None:
            payload_ref = journal.recorder.capture_value(diagnosis.raw, "FINAL_DIAGNOSIS",
                                                          snapshot_id=snapshot_id)
            dispatch = products['diagnosis'].source_for(diagnosis.raw)
            call_refs = dispatch.call_refs if dispatch is not None else ()
        result = StageResult("diagnosis", front_status(diagnosis.status), payload_ref,
                             call_refs=call_refs, snapshot_id=snapshot_id)
        live_snapshot = compute_snapshot(source_root).snapshot_id
        return (engine.step(result, current_snapshot=live_snapshot) if engine is not None else
                transition(RepairState(live_snapshot), result))

    def planning_generation_mode(request):
        """Keep an observed recovery in its configured scope, never across runs."""
        origin = recovered_generation_modes.get(request.prompt_version)
        origin_prompt = request.prompt_version
        if origin is None and generation_recovery_scope == 'front_half' and recovered_generation_modes:
            origin_prompt, origin = next(iter(recovered_generation_modes.items()))
        if origin is None:
            return request
        provenance = ({'origin_prompt_version': origin_prompt} if generation_recovery_scope == 'front_half' else {})
        return replace(request, thinking='disabled', metadata={**request.metadata,
            'planning_generation_policy': {
                'mode': 'disabled', 'reason': 'COMPLETED_REASONING_STARVED_JSON_RECOVERY',
                'origin_stage': origin,
                'scope': ('CURRENT_FRONT_HALF' if generation_recovery_scope == 'front_half'
                          else 'CURRENT_FRONT_HALF_PROMPT_VERSION'), **provenance}})

    def complete_recorded(request, stage: str, *, correction_scopes=()):
        nonlocal active_sources, context, run_context_wire
        draft_sources = request.metadata.get('source_context')
        ledger_start = len(provider.calls)
        for name, scope_name in correction_scopes:
            if not policy.available(name, scope_name):
                raise ValueError('Existing policy allowance exhausted: ' + name + '/' + scope_name)
        request_tokens(request)
        previous_timeout = getattr(provider, 'timeout_seconds', None)
        response = None
        dispatch_error = None
        quote = None
        receipt = None
        try:
            remaining = max_elapsed_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise BudgetExceeded('Model call cannot start after the run deadline')
            if previous_timeout is not None:
                provider.timeout_seconds = min(previous_timeout, remaining)
            monitor = getattr(provider, '_semantic_delivery_monitor', None)
            # Correction/recovery stage names may differ; the request dialect
            # determines which selected material the actual builder adopts.
            planning = request.prompt_version.startswith('changeset.')
            selected_public = tuple(record for record in run_input.public_evidence
                if record.get('kind') == 'PUBLIC_OBSERVATION')
            selected_evidence = (selected_public if planning else
                tuple(build_evidence_catalog(run_input, candidate_set, snapshot_id, source_context=draft_sources)))
            semantic = delivery_context(parsed, consumer=stage, snapshot_id=snapshot_id,
                diagnosis=diagnosis_wire, changeset=changeset_payload, feedback=execution_feedback,
                necessary_support=([{**r, 'delivery_required':True} for r in source_context_records(draft_sources,kind='SOURCE_SPAN')]
                                   if planning and draft_sources is not None else ()),
                prerequisites=(required_source_prerequisites(candidate_set,helper_support=planning_helpers,establishment_required=True)
                               if planning and candidate_set is not None else ()),
                evidence=selected_evidence, mandatory_refs=selected_evidence,
                declared_artifacts=run_input.declared_artifacts,
                feedback_required=execution_feedback is not None and not planning,
                source_requirements=planning)
            with monitor.scope(semantic) if monitor is not None else nullcontext():
                quote = request_quote(request)
                response = complete_quoted(provider, request, quote)
            if time.monotonic() - started >= max_elapsed_seconds:
                raise BudgetExceeded('Model response exceeded the run deadline')
        except Exception as exc:
            dispatch_error = exc
            if isinstance(exc, SemanticDeliveryError):
                # Admission attempts are in semantic_delivery receipts, never in
                # this compatibility model-call view without a real dispatch.
                raise
            received = getattr(exc, 'response', None)
            if response is None and isinstance(received, ModelResponse):
                response = received
            current_calls = provider.calls[ledger_start:]
            dispatch_state = (recorded_dispatch_state(current_calls[-1]) if current_calls
                              else provider.failure_dispatch_state(exc) or 'not_sent')
            exc._front_failure_dispatch_state = dispatch_state
            if time.monotonic() - started >= max_elapsed_seconds and not isinstance(exc, BudgetExceeded):
                deadline = BudgetExceeded('Model call exceeded the run deadline')
                deadline._front_failure_dispatch_state = dispatch_state
                dispatch_error = deadline
                raise deadline from exc
            raise
        finally:
            from contractedge.orchestration.recorder import preserve_error
            current_calls = provider.calls[ledger_start:]
            if draft_sources is not None and current_calls:
                dispatched = [recorded_dispatch_state(call) for call in current_calls]
                state = ('response_unknown' if 'response_unknown' in dispatched else
                         'response_known' if 'response_known' in dispatched else 'not_sent')
                committed = preserve_error(dispatch_error, lambda: source_context_commit(active_sources, draft_sources, state))
                if committed is not None and state != 'not_sent':
                    active_sources = committed
                    context = replace(context, retrieval_config={**context.retrieval_config,
                        **request.metadata.get('source_context_retrieval', {}),
                        'source_context':active_sources})
                    run_context_wire = asdict(context)
            for index in range(ledger_start, len(provider.calls)):
                call_annotations.setdefault(index, {})['stage'] = stage
            if any(recorded_dispatch_state(call) in {'response_known', 'response_unknown'}
                   for call in current_calls):
                for name, scope_name in correction_scopes:
                    preserve_error(dispatch_error, lambda name=name, scope_name=scope_name:
                                   policy.consume(name, scope_name))
            if previous_timeout is not None:
                provider.timeout_seconds = previous_timeout
            if journal is not None:
                primary = dispatch_error
                receipt = preserve_error(primary, lambda: journal.record_dispatch(stage,
                    attempt_id=attempt_id + ".call-" + str(ledger_start),
                    snapshot_id=snapshot_id, call_start=ledger_start,
                    status="ADMISSION_REJECTED" if isinstance(primary, SemanticDeliveryError)
                           else "ERROR" if primary is not None else "RESPONSE_RECEIVED"))
                if primary is not None:
                    primary._front_failure_dispatch_receipt = receipt
        if journal is not None:
            return journal.completed_response(response, receipt)
        from contractedge.orchestration.recorder import RecordedResponse
        return RecordedResponse(response.content, response.metadata)

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

    def admit_front_request(request):
        quote = request_quote(request)
        require_context(quote, int(config['max_context_tokens']), config.get('model_context_tokens'),
                        label='Front ' + request.prompt_version + ' request')
        provider.require_admission(quote,
            token_limit=front_token_start + max_total_tokens,
            call_limit=provider.prior_calls + front_call_start + max_model_calls,
            deadline=started + max_elapsed_seconds, reserve_after=post_planning_reserve_tokens)

    def recover_truncated_planning(response, request, stage):
        """Recover known reasoning-starved JSON once within all shared budgets.

        This is a new logged model request, not an HTTP replay or hand repair.
        The identical source/evidence/schema and all shared budgets remain bound.
        """
        nonlocal format_retries_used, truncation_recoveries_used
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
        admit_front_request(retry)
        corrections = (("truncation_recovery", attempt_id),) + (
            () if separate_truncation_allowance else (("format_correction", "diagnosis:" + attempt_id),))
        result = complete_recorded(retry, 'PLANNING_TRUNCATION_FORMAT_RETRY', correction_scopes=corrections)
        truncation_recoveries_used = policy.used("truncation_recovery", attempt_id)
        format_retries_used = policy.used("format_correction", "diagnosis:" + attempt_id)
        record_call({'stage': 'PLANNING_TRUNCATION_FORMAT_RETRY', 'prompt_version': retry.prompt_version,
            'request_metadata': dict(retry.metadata), 'response_metadata': dict(result.metadata),
            'response_content': result.content, 'elapsed_seconds': time.monotonic() - started,
            'prompt_tokens_reserved': count, 'output_tokens_reserved': retry.max_output_tokens,
            'format_retry_index': format_retries_used,
            'truncation_recovery_index': truncation_recoveries_used})
        if not _output_was_truncated(result):
            recovered_generation_modes[request.prompt_version] = stage
        return result

    def planning_source_draft(fitted, method, current_context, *, source_reservations=()):
        draft = source_context_for_candidates(active_sources, method, fitted, current_context.snapshot_id)
        if source_reservations:
            draft = source_context_offer(draft, source_reservations, producer='extension')
        helpers = []; audit = []
        helper_bytes = max(0,min(int(config.get('max_execution_support_bytes',24000)),24000))
        if config.get('helper_preconditions_enabled',False):
            from contractedge.execution.helper_context import planning_helper_source_support
            helpers,audit = planning_helper_source_support(snapshot,fitted.candidates,
                max_bytes=helper_bytes,source_context=active_sources)
        draft = source_context_offer(draft,helpers,producer='planning_helper')
        prerequisites = required_source_prerequisites(fitted,helper_support=helpers,
            establishment_required=True)
        support = [{**r,'delivery_required':True} for r in source_context_records(draft,kind='SOURCE_SPAN')
                   if r['snapshot_id'] == current_context.snapshot_id]
        dependency_bytes = sum(len(r['excerpt'].encode()) for r in
            source_context_records(draft, kind='PUBLIC_DEPENDENCY_DECLARATION')
            if r.get('current_source_snapshot') == current_context.snapshot_id)
        support_bytes = dependency_bytes + sum(len(r['excerpt'].encode()) for r in support)
        support_limit = int(config.get('max_execution_support_bytes',24000))
        # Source context v2 has no second source quota for already-adopted
        # bodies: planning and execution check their final complete requests.
        if draft.get('version') != 2 and support_bytes > support_limit:
            raise BudgetExceeded('Adopted planning source exceeds execution support admission bound: '
                f'{support_bytes} > {support_limit} bytes', details={
                    'dimension': 'execution_support_bytes', 'actual': support_bytes, 'limit': support_limit})
        return draft,helpers,audit,prerequisites,support

    def admission_error(fitted):
        return next((row['admission_failure']['message'] for row in reversed(fitted.context_audit)
                     if 'admission_failure' in row), 'Complete diagnosis request cannot be admitted')

    def fit_diagnosis(candidate_set, method, requirement, context, *, prior=None, extension_gaps=(),
                      planning_context=None, source_reservations=()):
        """Fit the complete serialized request, including fixed prompt overhead, in context."""
        fitted = candidate_set
        contracts = field_contract_contexts(fitted.candidates, parsed.requirement_text)
        by_unit = {}
        for record in contracts:
            by_unit.setdefault(record['candidate_id'], []).append(record['field_name'])
        from contractedge.diagnosis.evidence import reader_reference_contexts
        reader_ids={row['candidate_id'] for row in reader_reference_contexts(fitted.candidates)}
        contract_ids=set(by_unit)|reader_ids
        fitted = replace(fitted, candidates=tuple(
            {**u, **({'source_contract_fields':by_unit[u['id']]} if u['id'] in by_unit else {}),
             'gap_role_keys': list(dict.fromkeys([*u.get('gap_role_keys', []),
                *(('SOURCE_CONTRACT:'+u['id'],) if u['id'] in by_unit else ()),
                *(('SOURCE_READER:'+u['id'],) if u['id'] in reader_ids else ())]))}
            if u['id'] in contract_ids else u for u in fitted.candidates))
        required_endpoint_ids=list(dict.fromkeys([*fitted.required_endpoint_ids,
            *[c['id'] for c in fitted.candidates if requires_gap_delivery(c)]]))
        while True:
            if not fitted.candidates:
                return fitted,None,0
            draft_sources = source_context_for_candidates(active_sources, method, fitted, context.snapshot_id)
            if source_reservations:
                draft_sources = source_context_offer(draft_sources, source_reservations, producer='extension')
            draft_context = replace(context, retrieval_config={**context.retrieval_config,
                'source_context':draft_sources})
            request = build_diagnosis_request(method, requirement, fitted, draft_context,
                                              scope_resolution=scope)
            request = replace(request, metadata={**request.metadata,'source_context':draft_sources})
            continuity = diagnosis_continuity(prior, context.snapshot_id,
                build_evidence_catalog(method, fitted, context.snapshot_id, source_context=draft_sources), extension_gaps,
                input_digest=context.input_digest, planning_context=planning_context)
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
            # The smaller initial allowance is not a second global context cap.
            # Evidence extensions and validated feedback add state on
            # rediagnosis; fit both in the existing total, reserving output.
            total_limit = min(int(config['max_context_tokens']),
                int(config.get('model_context_tokens') or config['max_context_tokens']))
            initial_context = execution_feedback is None and not fitted.extension_used
            # Legacy synthetic callers declare only an input cap. Preserve
            # that admission contract; real profiles declare a model window.
            reserve_output = execution_feedback is not None or config.get('model_context_tokens') is not None
            v2_sources = active_sources.get('version') == 2
            if v2_sources:
                # One request boundary for initial, extension and feedback
                # diagnosis: the configured input cap and the model window
                # with output reserved. No smaller first-diagnosis allowance.
                window = int(config.get('model_context_tokens') or config['max_context_tokens'])
                first_limit = min(fitted.max_context_tokens, window - request.max_output_tokens)
            else:
                first_limit = (min(fitted.max_context_tokens,
                    int(config.get('initial_diagnosis_context_tokens', fitted.max_context_tokens)))
                    if initial_context else
                    min(fitted.max_context_tokens, total_limit - (request.max_output_tokens if reserve_output else 0)))
            if count <= first_limit:
                try:
                    planning_source_draft(fitted, method, draft_context,
                        source_reservations=source_reservations)
                except BudgetExceeded as exc:
                    fitted = replace(fitted, context_audit=fitted.context_audit + ({
                        'admission_failure': {**exc.details, 'message': str(exc)}},))
                    before_ids = tuple(fitted.selected_ids)
                    fitted = trim_context_bundle(fitted,request_tokens=count,limit=first_limit,
                        adopted_ids=active_sources['candidate_ids'],token_counter=token_counter,
                        reason='EXECUTION_SUPPORT_CAPACITY')
                    if tuple(fitted.selected_ids) == before_ids:
                        return fitted,None,count
                    continue
                if config.get('gap_role_delivery_enabled'):
                    from contractedge.retrieval.gap_delivery import audit_gap_delivery
                    receipt=audit_gap_delivery([{'required_unit_ids':required_endpoint_ids,
                                                 'available_unit_ids': list(dict.fromkeys(
                                                     [*fitted.selected_ids, *fitted.dropped_ids]))}],
                        list(fitted.candidates),
                        json.dumps([dict(m) for m in request.messages],ensure_ascii=False),
                        drop_reasons=dict(fitted.drop_reasons or {}))
                    request=replace(request,metadata={**request.metadata,'gap_delivery':receipt})
                    if receipt['status']=='INCOMPLETE':
                        fitted=replace(fitted,context_audit=fitted.context_audit+({'gap_delivery':receipt},))
                        return fitted,None,count
                audit = {'request_tokens': count, 'input_limit': first_limit,
                         'reserved_output_tokens': request.max_output_tokens,
                         'context_stage': ('INITIAL_DIAGNOSIS' if initial_context else
                             'FEEDBACK_REDIAGNOSIS' if execution_feedback is not None else 'EVIDENCE_REDIAGNOSIS'),
                         'total_context_limit': total_limit,
                         'selected_ids': list(fitted.selected_ids),
                         'surviving_reference_edges': len(fitted.edges),
                         'drop_reasons': dict(fitted.drop_reasons or {})}
                fitted = replace(fitted, context_tokens=count,
                                 context_audit=fitted.context_audit + (audit,))
                request = replace(request, metadata={**request.metadata, 'context_audit': list(fitted.context_audit)})
                return fitted, request, count
            fitted = replace(fitted, context_audit=fitted.context_audit + ({'admission_failure': {
                'dimension': 'request_input_tokens', 'actual': count, 'limit': first_limit,
                **({'reserved_output_tokens': request.max_output_tokens, 'sent': False} if v2_sources else {}),
                'message': f'Complete diagnosis input exceeds request allowance: {count} > {first_limit}'}},))
            if not fitted.candidates or v2_sources:
                # v2: prepared bodies are not cut to fit; an over-window
                # request is refused unsent with its exact size recorded.
                return fitted, None, count
            before_ids = tuple(fitted.selected_ids)
            fitted = trim_context_bundle(fitted, request_tokens=count, limit=first_limit,
                adopted_ids=active_sources['candidate_ids'],token_counter=token_counter)
            if tuple(fitted.selected_ids) == before_ids:
                # trim_context_bundle deliberately retains an all-protected
                # required set. Stop rather than looping forever; the caller
                # projects this as a bounded BUDGET_EXHAUSTED result.
                return fitted, None, count

    def evidence_delivery_failure(fitted, diagnosis=None, changeset=None):
        for record in reversed(fitted.context_audit):
            receipt=record.get('gap_delivery',{})
            if receipt.get('status')=='INCOMPLETE':
                if engine is not None:
                    engine.step(StageResult('delivery', 'DELIVERY_INCOMPLETE',
                        journal.recorder.capture_value(receipt, 'SOURCE_DELIVERY', snapshot_id=snapshot_id),
                        snapshot_id=snapshot_id))
                return RunTrace(run_id,parsed.case_id,parsed.input_digest,snapshot.snapshot_id,
                    'EVIDENCE_DELIVERY_INCOMPLETE',requirement,scope,fitted,diagnosis,changeset,tuple(front_calls()),
                    ('REQUIRED_SOURCE_NOT_DELIVERED: '+json.dumps(receipt,ensure_ascii=False),),
                    structural_validation,semantic_review,tuple(retrieval_rounds),run_context_wire)
        return None
    def continue_with_evidence(diagnosis, gaps, *, trigger, planning_context=None):
        """One acquisition/fit/reassessment route, sharing the existing owners."""
        nonlocal candidate_set, requirement, diagnosis_wire, format_retries_used, changeset_payload
        reassessed = False

        def stop(status, message, *, validation_status=None):
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                status, requirement, scope, candidate_set, diagnosis.raw,
                None if reassessed else planning_context,
                tuple(front_calls()), (message,), validation_status or structural_validation, semantic_review,
                tuple(retrieval_rounds), run_context_wire)

        def no_progress(message):
            # A failed local lookup is not a new diagnosis. Planning keeps
            # ownership of its validated status and existing correction count.
            return diagnosis, stop('EVIDENCE_GAP', message), not reassessed

        def material(record):
            # Alias IDs or new role annotations cannot manufacture new evidence.
            return canonical_digest({key: record.get(key) for key in
                ('kind', 'snapshot_id', 'current_source_snapshot', 'locator', 'content_digest', 'excerpt')})

        while gaps:
            if not policy.available('evidence_extension', attempt_id):
                return no_progress('Existing bounded evidence acquisition allowance exhausted')
            # An acquisition is a bounded local lookup, including an empty one.
            # It never refunds a failed attempt or creates a model-call budget.
            acquisition = policy.consume('evidence_extension', attempt_id)
            gap_units, audit = extract_gap_extension_units(snapshot, scope.seed_paths, units,
                gaps, max_files=max(0, int(config.get('max_gap_search_files', 2000))),
                max_units=max(0, int(config.get('max_gap_units', 40))),
                max_lines=int(config.get('max_lines_per_unit', 80)),
                max_declaration_lines=int(config.get('max_gap_declaration_lines', 512)))
            facts, absence_audit = collect_absence_search_evidence(snapshot, gaps,
                max_files=max(0, int(config.get('max_absence_search_files', 2000))),
                max_bytes=max(0, int(config.get('max_absence_search_bytes', 64 * 1024 * 1024))))
            audit['absence_search'] = absence_audit
            by_id = {u['id']: u for u in units}
            extra = [by_id[i] for i in candidate_set.dropped_ids if i in by_id] + gap_units
            if config.get('gap_role_delivery_enabled'):
                from contractedge.retrieval.gap_delivery import locate_gap_roles
                roles, role_audit = locate_gap_roles(snapshot, gaps,
                    dict(max_new_files=4, seed_units=units, seed_declarations=declaration_index))
                extra.extend(roles)
                facts = (*facts, *role_audit.get('static_search_evidence', ()))
                audit['role_locations'] = role_audit
            round_record = {'round_id': acquisition + 1, 'extension_used': False,
                            'trigger': trigger, 'gap_search': audit}
            retrieval_rounds.append(round_record)
            extra = materialize_complete_units(snapshot, extra, artifacts=parsed.declared_artifacts,
                                               adopted_ids=active_sources['candidate_ids'])
            from contractedge.source.dependency import gap_dependency_support
            try:
                # Normalize aliases and mandatory endpoints with the existing
                # selector before offering space to external declarations.
                # This is a local feasibility draft, not another acquisition
                # or adopted selection; optional source reserves no space.
                local_proposed = extend_once(candidate_set, gaps, extra,
                    max_context_tokens=int(config['max_context_tokens']), token_counter=token_counter,
                    allow_reextension=bool(candidate_set.extension_used), source_context=active_sources)
            except ValueError as exc:
                round_record['reason'] = str(exc)
                return no_progress('No bounded evidence extension is available: ' + str(exc))
            dependency_count, dependency_bytes = source_extension_dependency_allowance(
                local_proposed, active_sources)
            dependency_count = min(4, dependency_count)
            prior_dependencies = source_context_records(active_sources, kind='PUBLIC_DEPENDENCY_DECLARATION')
            dependency_records, dependency_audit = gap_dependency_support(snapshot, gaps,
                candidate_set.candidates, case_id=parsed.case_id,
                public_execution_capabilities=config.get('public_execution_capabilities') or {},
                max_records=dependency_count, max_bytes=dependency_bytes,
                already_supplied=lambda row: any(same_evidence_content(row, old)
                    for old in prior_dependencies))
            audit['public_dependency_lookup'] = dependency_audit
            if not extra and not facts and not dependency_records:
                round_record['reason'] = 'no additional bounded source or search facts'
                return no_progress(round_record['reason'])
            try:
                prior_dependency_keys = {source_context_key(r) for r in prior_dependencies}
                new_dependencies = {source_context_key(r): r for r in dependency_records
                                    if source_context_key(r) not in prior_dependency_keys}
                if (len(new_dependencies) > dependency_count or
                        sum(len(r['excerpt'].encode()) for r in new_dependencies.values()) > dependency_bytes):
                    raise ValueError('Dependency reader exceeded its offered source allowance')
                proposed = (extend_once(candidate_set, gaps, extra,
                    max_context_tokens=int(config['max_context_tokens']), token_counter=token_counter,
                    allow_reextension=bool(candidate_set.extension_used), source_context=active_sources,
                    source_reservations=dependency_records) if dependency_records else local_proposed)
                proposed = replace(proposed, supplemental_evidence=tuple(merge_evidence_records(
                    [*candidate_set.supplemental_evidence, *facts])))
            except ValueError as exc:
                round_record['reason'] = str(exc)
                return no_progress('No bounded evidence extension is available: ' + str(exc))
            next_requirement = requirement_for_rediagnosis(requirement, diagnosis.raw,
                snapshot.snapshot_id, parsed.input_digest)
            proposed, request, count = fit_diagnosis(proposed, run_input, next_requirement, context,
                prior=diagnosis.raw, extension_gaps=gaps, planning_context=planning_context,
                source_reservations=dependency_records)
            if request is None and new_dependencies:
                # These are still unsent offers. They must not turn a viable
                # local acquisition into a stop at the final request/support
                # bound. Refit the original local draft once; active_sources
                # retains every previously adopted body, including older APIs.
                round_record['withheld_unsent_dependency_ids'] = sorted(
                    row['id'] for row in new_dependencies.values())
                round_record['dependency_admission_reason'] = (
                    'Optional unsent APIs did not fit the complete request or execution support')
                local_proposed = replace(local_proposed, supplemental_evidence=tuple(merge_evidence_records(
                    [*candidate_set.supplemental_evidence, *facts])))
                proposed, request, count = fit_diagnosis(local_proposed, run_input, next_requirement, context,
                    prior=diagnosis.raw, extension_gaps=gaps, planning_context=planning_context)
            failure = evidence_delivery_failure(proposed, diagnosis.raw,
                None if reassessed else planning_context)
            if failure is not None:
                return diagnosis, failure, False
            if request is None:
                candidate_set = proposed
                return diagnosis, stop('BUDGET_EXHAUSTED', admission_error(proposed)), False
            old_material = {material(r) for r in source_context_records(active_sources)}
            new_material = [r for r in source_context_records(request.metadata['source_context'])
                            if material(r) not in old_material]
            if not new_material:
                round_record['reason'] = 'no new material in the fitted delivery context'
                return no_progress(round_record['reason'])
            admit_front_request(request)
            candidate_set, requirement = proposed, next_requirement
            round_record.update(extension_used=True, selected_ids=list(proposed.selected_ids),
                                dropped_ids=list(proposed.dropped_ids))
            response = complete_recorded(request, 'DIAGNOSIS_EXTENSION')
            stage = 'DIAGNOSIS_EXTENSION'
            correction_scope = ('diagnosis-extension:' if per_evidence_context_correction else 'diagnosis:') + attempt_id
            while True:
                record_call({'stage': stage, 'prompt_version': request.prompt_version,
                    'extension_used': True, 'trigger': trigger,
                    'semantic_call_index': acquisition + 1,
                    'elapsed_seconds': time.monotonic() - started,
                    'prompt_tokens_reserved': count, 'output_tokens_reserved': request.max_output_tokens})
                response = recover_truncated_planning(response, request, stage)
                if _output_was_truncated(response):
                    return diagnosis, stop('BUDGET_EXHAUSTED', 'Evidence rediagnosis output reached its existing generation limit'), False
                body = {}
                try:
                    body = parse_diagnosis_response(response.content)
                    body, binding = _bind_diagnosis_envelope(body, run_input, context, requirement, candidate_set)
                    diagnosis = validate_diagnosis(body, run_input, context, candidate_set)
                    products['diagnosis'] = response.produced(diagnosis.raw)
                    diagnosis_wire = diagnosis.raw
                    reassessed = True
                    # Accepted reassessment supersedes the earlier plan even
                    # if a later admission/transport exception exits this helper.
                    changeset_payload = None
                    current_annotations()['runner_binding'] = binding
                    annotate_gap_delivery(diagnosis.raw)
                    break
                except (ValueError, ContractError) as exc:
                    if not policy.available('format_correction', correction_scope):
                        return diagnosis, stop('INVALID_OUTPUT', str(exc), validation_status='FAIL'), False
                    validation_feedback = _format_validation_feedback(exc, body if isinstance(body, Mapping) else {})
                    current_annotations()['validation_error'] = str(exc)
                    current_annotations()['validation_feedback'] = validation_feedback
                    request = replace(request, messages=_append_diagnosis_retry_turn(
                        request.messages, response.content, validation_feedback), metadata={**request.metadata,
                        'format_retry_index': policy.used('format_correction', correction_scope) + 1,
                        'correction_allowance': ('FRESH_EVIDENCE_CONTEXT_ONCE' if per_evidence_context_correction else 'SHARED_FORMAT_CORRECTION')})
                    request = planning_generation_mode(request)
                    count = request_tokens(request)
                    admit_front_request(request)
                    response = complete_recorded(request, 'DIAGNOSIS_FORMAT_RETRY',
                        correction_scopes=(('format_correction', correction_scope),))
                    format_retries_used = policy.used('format_correction', 'diagnosis:' + attempt_id)
                    stage = 'DIAGNOSIS_FORMAT_RETRY'
            gaps = list(diagnosis.evidence_gaps) if diagnosis.status == 'EVIDENCE_GAP' else []
        return diagnosis, None, False

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
                            None, None, None, None, None, tuple(front_calls()),
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
                            requirement, None, None, None, None, tuple(front_calls()), ("public requirement is under-specified",))
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
        # Observation-limit prose remains capability context. Only registered
        # API descriptors and task/configuration hints guide source discovery.
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
            from contractedge.retrieval.candidates import _terms
            terms = []
            for token in re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", parsed.requirement_text):
                if (_terms(token) and token.casefold() not in stopwords
                        and token.casefold() not in known_terms and token not in terms):
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
                unit['anchor_priority'] = max(int(unit.get('anchor_priority', 0)), 2)
        seen_inputs = set()
        for unit in input_units:
            if unit['artifact_id'] not in seen_inputs:
                unit['anchor_priority'] = 1
                seen_inputs.add(unit['artifact_id'])
        units.extend(input_units)
        units = materialize_complete_units(snapshot, units, artifacts=parsed.declared_artifacts)
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
            necessary_units = materialize_complete_units(snapshot, [*lifecycle_units, *exception_units])
            if necessary_units:
                candidate_set = build_candidate_set(parsed.requirement_text, parsed.public_observable_text,
                    [*units, *necessary_units], build_direct_reference_edges([*units, *necessary_units]),
                    top_n=int(config.get('top_n', 12)), max_context_tokens=int(config['max_context_tokens']),
                    token_counter=token_counter)
                candidate_set = replace(candidate_set,
                    required_endpoint_ids=tuple(u['id'] for u in necessary_units))
        retrieval_rounds[:] = [{
            "round_id": 1, "seed_paths": list(scope.seed_paths),
            "probe_entrypoint_resolution": entrypoint_audit,
            "selected_ids": list(candidate_set.selected_ids), "dropped_ids": list(candidate_set.dropped_ids),
            "scope_extension_paths": list(scope.extension_paths), "extension_used": False,
        }]
        context = RunContext(run_id, parsed.case_id,
                             int(execution_feedback['round_id']) + 1 if execution_feedback else 0,
                             snapshot.snapshot_id,
                             execution_feedback['from_snapshot'] if execution_feedback else None, parsed.input_digest,
                             # Development/fixture callers historically
                             # omitted a model and received the harmless
                             # ``UNCONFIGURED`` prompt identity.  Preserve
                             # that compatibility; an explicitly malformed
                             # model still fails through model_config.
                             model_config=(
                                 {"model": "UNCONFIGURED",
                                  "provider": config.get("provider", "configured"),
                                  "schema_in_prompt": config.get("schema_in_prompt", True)}
                                 if config.get("model") is None else
                                 model_config(config.get("model"),
                                              provider=config.get("provider"),
                                              schema_in_prompt=config.get("schema_in_prompt", True))),
            retrieval_config={"top_n": candidate_set.top_n,
                             # Keep the exact public artifact contracts
                             # available to planning.  Diagnosis receives
                             # artifact-derived source units, but a planner
                             # also needs the literal external key/flag names
                             # when it binds a configuration hand-off.  This
                             # is input context, not an edit permission.
                             "declared_artifacts": [dict(item) for item in parsed.declared_artifacts],
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
        active_sources = source_context_restart(inherited_source_context,
            dict(run_id=run_id,case_id=parsed.case_id,input_digest=parsed.input_digest,front_id=attempt_id),
            execution_feedback, evidence_extensions=policy.limit('evidence_extension'), initial_records=candidate_set.top_n,
            version=source_context_contract(config))
        run_context_wire = asdict(context)
        if not candidate_set.selected_ids:
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id, "EVIDENCE_GAP",
                            requirement, scope, candidate_set, None, None, tuple(front_calls()),
                            ("no source candidates resolved",), run_context=run_context_wire)

        candidate_set, diagnosis_request, prompt_tokens = fit_diagnosis(
            candidate_set, run_input, requirement, context)
        failure=evidence_delivery_failure(candidate_set)
        if failure is not None:return failure
        if diagnosis_request is None:
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                            "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None,
                            tuple(front_calls()), (admission_error(candidate_set),),
                            retrieval_rounds=tuple(retrieval_rounds), run_context=run_context_wire)
        admit_front_request(diagnosis_request)
        diagnosis_response = complete_recorded(diagnosis_request, "DIAGNOSIS")
        record_call({"stage": "DIAGNOSIS", "prompt_version": diagnosis_request.prompt_version,
                      "request_metadata": dict(diagnosis_request.metadata),
                      "response_metadata": dict(diagnosis_response.metadata),
                      "response_content": diagnosis_response.content,
                      "elapsed_seconds": time.monotonic() - started,
                      "prompt_tokens_reserved": prompt_tokens,
                      "output_tokens_reserved": diagnosis_request.max_output_tokens})
        diagnosis_response = recover_truncated_planning(diagnosis_response, diagnosis_request, 'DIAGNOSIS')
        if _output_was_truncated(diagnosis_response):
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                            "BUDGET_EXHAUSTED", requirement, scope, candidate_set, None, None, tuple(front_calls()),
                            ("Diagnosis output reached the configured generation token limit",),
                            run_context=run_context_wire)
        diagnosis_payload: Mapping[str, Any] = {}
        diagnosis_retry_messages = tuple(diagnosis_request.messages)
        try:
            diagnosis_payload = parse_diagnosis_response(diagnosis_response.content)
            diagnosis_payload, runner_binding = _bind_diagnosis_envelope(
                diagnosis_payload, run_input, context, requirement, candidate_set)
            diagnosis = validate_diagnosis(diagnosis_payload, run_input, context, candidate_set)
            products['diagnosis'] = diagnosis_response.produced(diagnosis.raw)
            diagnosis_wire = diagnosis.raw
            current_annotations()["runner_binding"] = runner_binding
            annotate_gap_delivery(diagnosis.raw)
        except (ValueError, ContractError) as exc:
            validation_error: Exception = exc
            while True:
                if format_retries_used >= max_format_retries:
                    return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                                    "INVALID_OUTPUT", requirement, scope, candidate_set, None, None,
                                    tuple(front_calls()), (str(validation_error),), "FAIL",
                                    run_context=run_context_wire)
                validation_feedback = _format_validation_feedback(validation_error, diagnosis_payload)
                current_annotations()["validation_error"] = str(validation_error)
                current_annotations()["validation_feedback"] = validation_feedback
                diagnosis_retry_messages = _append_diagnosis_retry_turn(
                    diagnosis_retry_messages, diagnosis_response.content, validation_feedback)
                feedback = replace(diagnosis_request, messages=diagnosis_retry_messages,
                    metadata={**dict(diagnosis_request.metadata), "format_retry_index": format_retries_used + 1})
                feedback = planning_generation_mode(feedback)
                feedback_tokens = request_tokens(feedback)
                admit_front_request(feedback)
                diagnosis_response = complete_recorded(feedback, "DIAGNOSIS_FORMAT_RETRY",
                    correction_scopes=(("format_correction", "diagnosis:" + attempt_id),))
                format_retries_used = policy.used("format_correction", "diagnosis:" + attempt_id)
                record_call({"stage": "DIAGNOSIS_FORMAT_RETRY", "prompt_version": feedback.prompt_version,
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
                                    tuple(front_calls()), ("Diagnosis format-retry output reached its token limit",),
                                    "FAIL", run_context=run_context_wire)
                try:
                    diagnosis_payload = parse_diagnosis_response(diagnosis_response.content)
                    diagnosis_payload, runner_binding = _bind_diagnosis_envelope(
                        diagnosis_payload, run_input, context, requirement, candidate_set)
                    diagnosis = validate_diagnosis(diagnosis_payload, run_input, context, candidate_set)
                    products['diagnosis'] = diagnosis_response.produced(diagnosis.raw)
                    diagnosis_wire = diagnosis.raw
                    current_annotations()["runner_binding"] = runner_binding
                    annotate_gap_delivery(diagnosis.raw)
                    break
                except (ValueError, ContractError) as retry_exc:
                    validation_error = retry_exc
        structural_validation = "PASS"
        semantic_review = {"decision": "UNCERTAIN", "method": "NOT_CONFIGURED",
                           "rationale": "Deterministic validation passed; no independent semantic reviewer was configured."}
        if diagnosis.status == "EVIDENCE_GAP" and diagnosis.evidence_gaps:
            diagnosis, failure, _ = continue_with_evidence(diagnosis, list(diagnosis.evidence_gaps),
                trigger='DIAGNOSIS_EVIDENCE_GAP')
            if failure is not None:
                return failure
        diagnosis_decision = diagnosis_gate(diagnosis)
        if diagnosis_decision.action != "PLAN":
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                            diagnosis.status if diagnosis_decision.action == "STOP" else "EVIDENCE_GAP",
                            requirement, scope, candidate_set, diagnosis.raw, None, tuple(front_calls()),
                            () if diagnosis_decision.action == "STOP" else ("DIAGNOSIS_SNAPSHOT_STALE",),
                            structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)

        if config.get('method_mode') == 'without_changeset':
            return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                            'READY', requirement, scope, candidate_set, diagnosis.raw, None,
                            tuple(front_calls()), (), structural_validation, semantic_review,
                            tuple(retrieval_rounds), run_context_wire)

        feedback: list[Mapping[str, Any]] = []
        changeset_payload = None
        validation = None
        change_attempt = -1
        planning_corrections_used = 0
        previous_changeset_attempt = None
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
                        tuple(front_calls()), errors, structural_validation, semantic_review,
                        tuple(retrieval_rounds), run_context_wire)
            if config.get('production_path_review_enabled', False):
                context = replace(context, retrieval_config={**context.retrieval_config,
                    'production_path_frontier': production_frontier(candidate_set.candidates, parsed.requirement_text)})
                run_context_wire = asdict(context)
            helper_enabled=config.get('helper_preconditions_enabled',False)
            helper_bytes=(None if active_sources.get('version')==2 else
                max(0,min(int(config.get('max_execution_support_bytes',24000)),24000)))
            draft_sources,planning_helpers,helper_audit,source_prerequisites,source_support = planning_source_draft(
                candidate_set,run_input,context)
            # Corrections retain the same selected bodies. The projection may
            # share exact duplicates; the final request must fit with its draft.
            planning_compact_context = compact_planning_context
            request_context = replace(context, retrieval_config={**context.retrieval_config,
                    'planning_helper_contracts':bool(helper_enabled),
                    'planning_helper_max_bytes':helper_bytes,
                    'compact_planning_context': planning_compact_context,
                    'planning_helper_audit':helper_audit,
                    'precondition_establishment_required':True,
                    'source_context':draft_sources,
                    'necessary_source_support':source_support,
                    'source_prerequisites':source_prerequisites})
            changeset_request = build_changeset_request(diagnosis, request_context, feedback,
                previous_attempt=previous_changeset_attempt if feedback else None)
            changeset_request = replace(changeset_request, metadata={**changeset_request.metadata,
                'planning_format_retry_index': planning_corrections_used + bool(feedback),
                'source_context_retrieval':dict(request_context.retrieval_config)})
            changeset_request = planning_generation_mode(changeset_request)
            prompt_tokens = request_tokens(changeset_request)
            admit_front_request(changeset_request)
            changeset_response = complete_recorded(changeset_request,
                "SEMANTIC_CHANGESET" if change_attempt == 0 else "CHANGESET_REVISION",
                correction_scopes=(("format_correction", "planning:" + attempt_id),) if feedback else ())
            planning_corrections_used = policy.used("format_correction", "planning:" + attempt_id)
            record_call({"stage": "SEMANTIC_CHANGESET" if change_attempt == 0 else "CHANGESET_REVISION",
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
                                None, tuple(front_calls()), ("ChangeSet output reached the configured generation token limit",),
                                structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)
            identity_bound = False
            changeset_payload = None
            validation = None
            previous_changeset_attempt = (dict(run_id=run_id, snapshot_id=snapshot.snapshot_id,
                input_digest=parsed.input_digest, diagnosis_digest=canonical_digest(diagnosis.raw),
                response_content=changeset_response.content, state='REJECTED_PLAN_DRAFT')
                if isinstance(changeset_response.content, str) and changeset_response.content.strip() else None)
            try:
                changeset_payload = parse_json_response(changeset_response.content)
                changeset_payload, changeset_binding = bind_changeset_envelope(
                    changeset_payload, diagnosis, run_id, snapshot.snapshot_id, parsed.input_digest)
                identity_bound = True
                current_annotations()["runner_binding"] = changeset_binding
                changeset_payload = adapt_model_changeset(changeset_payload, diagnosis, snapshot)
            except ValueError as exc:
                feedback = _changeset_retry_feedback(exc)
                current_annotations()["validation_error"] = str(exc)
                current_annotations()["validation_feedback"] = feedback
                if not identity_bound or not _safe_planning_gaps(changeset_payload):
                    continue
                validation = ValidationResult(False, (ValidationIssue('INVALID_MODEL_PLAN', '', str(exc)),))
            else:
                validation = validate_semantic_changeset(changeset_payload, diagnosis, snapshot,
                policy=(config.get('public_execution_capabilities') or {}).get('editable_policy'),
                    frontier=context.retrieval_config.get('production_path_frontier'),
                    prerequisites=context.retrieval_config.get('source_prerequisites'), capabilities=consumer_capabilities,
                    source_support=context.retrieval_config.get('necessary_source_support'))
            finally:
                # A retained rejected draft still has a real source response;
                # binding provenance does not turn it into a valid plan. No
                # object means no product, even if a dispatch was attempted.
                if changeset_payload is not None:
                    products['planning'] = changeset_response.produced(changeset_payload)
            feedback = compact_validation_feedback(validation.errors,
                include_codes={'PRECONDITION_ESTABLISHMENT_INVALID',
                               'PRECONDITION_ESTABLISHMENT_MISSING'})
            if feedback:
                current_annotations()['validation_feedback'] = feedback
            planning_gaps = _safe_planning_gaps(changeset_payload)
            extension_trigger = 'CHANGESET_NEEDS_EVIDENCE'
            binding_errors = {'PRODUCTION_BRANCH_CHANGE_INVALID', 'PRODUCTION_BRANCH_TARGET_UNAVAILABLE'}
            # A plan cannot create a missing diagnosis unit by rewriting its
            # action text. Reuse the one existing acquisition/reassessment;
            # never auto-create units or expand edit permissions here.
            if (config.get('gap_role_delivery_enabled') and not planning_gaps
                    and policy.available('evidence_extension', attempt_id)
                    and feedback and {e['code'] for e in feedback} <= binding_errors
                    and any(e['code'] == 'PRODUCTION_BRANCH_TARGET_UNAVAILABLE' for e in feedback)):
                planning_gaps = production_branch_rebinding_gaps(changeset_payload, diagnosis,
                    context.retrieval_config.get('production_path_frontier', []))
                if planning_gaps:
                    extension_trigger = 'CHANGESET_TARGET_BINDING_GAP'
                    current_annotations()['target_binding_reassessment'] = planning_gaps
            if ((changeset_payload.get('status') == 'NEEDS_EVIDENCE'
                    or extension_trigger == 'CHANGESET_TARGET_BINDING_GAP') and planning_gaps
                    and policy.available('evidence_extension', attempt_id)):
                diagnosis, failure, no_reassessment = continue_with_evidence(diagnosis, planning_gaps,
                    trigger=extension_trigger, planning_context=changeset_payload)
                if failure is not None and not no_reassessment:
                    return failure
                if not no_reassessment:
                    diagnosis_decision = diagnosis_gate(diagnosis)
                    if diagnosis_decision.action != "PLAN":
                        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                            diagnosis.status if diagnosis_decision.action == "STOP" else "EVIDENCE_GAP",
                            requirement, scope, candidate_set, diagnosis.raw, None, tuple(front_calls()),
                            () if diagnosis_decision.action == "STOP" else ("DIAGNOSIS_SNAPSHOT_STALE",),
                            structural_validation, semantic_review, tuple(retrieval_rounds), run_context_wire)
                    # The next plan is based on a new diagnosis. Previous plan
                    # errors do not describe it, but any used correction stays used.
                    feedback = []
                    previous_changeset_attempt = None
                    continue
            if validation.valid:
                break
            feedback = compact_validation_feedback(validation.errors)
        assert validation is not None and changeset_payload is not None
        semantic_review = _semantic_review_result(semantic_reviewer, parsed, requirement,
                                                  diagnosis.raw, changeset_payload, candidate_set)
        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot.snapshot_id,
                        str(changeset_payload["status"]), requirement, scope, candidate_set,
                        diagnosis.raw, changeset_payload, tuple(front_calls()), (), structural_validation, semantic_review,
                        tuple(retrieval_rounds), run_context_wire)
    except Exception as exc:
        if journal is not None:
            journal.primary_error = exc
        dispatch_state = getattr(exc, '_front_failure_dispatch_state', None)
        mapped_status, failure = classify_exception(exc, stage="front",
            dispatch_state=dispatch_state)
        # Keep the established front public terminal for transport/runtime
        # failures, while retaining the more precise engine failure record.
        status = mapped_status if mapped_status in {
            "BUDGET_EXHAUSTED", "INVALID_OUTPUT", "INVALID_PLAN",
            "EVIDENCE_DELIVERY_INCOMPLETE"} else "INFRASTRUCTURE_ERROR"
        return RunTrace(run_id, parsed.case_id, parsed.input_digest, snapshot_id,
                        status,
                        requirement, scope, candidate_set, diagnosis_wire, changeset_payload,
                        tuple(front_calls()), (f"{type(exc).__name__}: {exc}",),
                        run_context=run_context_wire, retrieval_rounds=tuple(retrieval_rounds), failure_records=(failure,),
                        primary_failure=failure)
