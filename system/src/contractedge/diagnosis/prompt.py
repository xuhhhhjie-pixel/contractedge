from __future__ import annotations

from contractedge.retrieval.frontier import production_frontier

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from contractedge.contracts.models import MethodInput, RunContext
from contractedge.contracts.evidence import supports_expected_missing_evidence
from contractedge.diagnosis.evidence import field_contract_contexts, build_evidence_catalog
from contractedge.model.provider import ModelRequest
from contractedge.model.schema import constrained_output_schema


SYSTEM_PROMPT = """You produce the semantic body of a ContractEdge DiagnosisPacket v1.1 as exactly one JSON object matching output_schema. Return only properties declared by that schema. Be concise: use short phrases, one sentence per relation or violation, and at most three causal steps. Never repeat a claim. The runner adds schema_version, run_id, snapshot_id, input_digest, the exact RequirementSpec, and canonical EvidenceRecords to the validated body; do not emit those fields. Do not include request-context fields such as method_input, candidate_set, run_context, evidence_catalog, allowed_evidence_ids, output_schema, or output_contract in the response. IDs in RequirementSpec.outcomes (for example O1) are outcome IDs: use them only in requirement_horizon, unverified_outcomes, requirement_refs, outcome_coverage keys, and outcome_id fields. Evidence IDs are separate: select an exact string from allowed_evidence_ids and use it only in evidence_refs, units.locator_refs, relation support_refs, and violation evidence_refs. `evidence_refs` is an array of evidence ID strings only, not EvidenceRecord objects. Never add a prefix, transform a CandidateUnit ID, or invent an evidence ID. The runner will attach the canonical EvidenceRecord for each selected ID. Do not create a semantic unit for the requirement itself; units describe participating configuration, templates, resources, controllers, or runtime components. In outcome_coverage, include each exact outcome ID as one object key, with one disposition: REPAIR_SUPPORTED, PRESERVE_REQUIRED, or UNRESOLVED. Do not omit, repeat, or invent an outcome key. Use only the supplied public requirement, public observations, evidence_catalog, allowed_evidence_ids, and selected source candidates. Keep facts, static observations, and causal inferences distinct. Preserve UNKNOWN when evidence does not establish presence or absence. Do not invent source facts or claim effects beyond the observed boundary. Unsupported causal paths must remain tentative or become evidence gaps. Each evidence gap must include a concise summary and a locatable path, symbol, or search_term so the runner can perform at most one bounded retrieval extension."""
SYSTEM_PROMPT += " The only valid requirement reference IDs are the exact IDs listed in RequirementSpec.outcomes; copy them verbatim. Never use case_id, run_id, a case label, or evidence ID as a requirement reference. The schema restricts these fields and outcome_coverage keys to the current case's outcome IDs."
SYSTEM_PROMPT += " For each outcome_coverage key, choose exactly one disposition: REPAIR_SUPPORTED, PRESERVE_REQUIRED, or UNRESOLVED. Do not emit violation IDs in outcome_coverage; the runner derives supported violation links from the ExpectedRelation to Violation chain. For PRESERVE_REQUIRED, provide preservation_status and preservation_evidence_refs. For UNRESOLVED, do not claim a supported repair or preservation. The runner converts this keyed representation into the v1.1 outcome_coverage array. Use READY only when every O has supported repair coverage and there are no unresolved outcomes or evidence gaps; use PARTIAL_READY when at least one O is supported and another O remains open; use EVIDENCE_GAP when no supported actionable violation exists."
SYSTEM_PROMPT += " If a Violation has causal_status SUPPORTED and its linked ExpectedRelation lists an O in requirement_refs, mark that O REPAIR_SUPPORTED; do not simultaneously describe that same O as unresolved. Use UNRESOLVED only when no supported Violation is linked to that O."
SYSTEM_PROMPT += " EXPECTED_MISSING requires direct inventory or observation evidence. Do not infer absence from a file, symbol, or resource missing from the selected candidates; selected-candidate omission is not proof that no implementation exists. Cite an allowed SOURCE_INVENTORY, STATIC_SEARCH, or PUBLIC_OBSERVATION record that directly supports absence. If no such supplied record exists, use existence=UNKNOWN and do not claim the role is missing."
SYSTEM_PROMPT += " Scope-resolution results describe only bounded retrieval. An unresolved hint, truncated/incomplete search, or capped match list is not evidence that a role or implementation is absent. Do not convert retrieval limits, zero selected candidates, or incomplete scope coverage into EXPECTED_MISSING; request locatable evidence when the unresolved scope matters."
SYSTEM_PROMPT += " Represent independent required outputs or independent broken edges as separate ExpectedRelations, each with its own concrete contract and supporting evidence; reuse the exact source outcome IDs when more than one relation serves one coarse outcome. Keep a genuinely shared constraint as one multi-endpoint relation when the contract is indivisible. Do not collapse distinct constraints into one generic relation, and do not split one shared constraint into duplicate relations merely to increase relation count."
SYSTEM_PROMPT += " Split independently required behaviors into separate atomic obligations A1, A2, and so on. Each atom must cite its exact parent outcome_id and one or more exact source_refs from that outcome. Every ExpectedRelation must cite obligation_refs for the specific atom(s) it implements; use multiple atom refs only for a genuinely indivisible shared constraint."
SYSTEM_PROMPT += " Model causal chains as separate direct edges between adjacent roles: input→consumer, consumer→generated/runtime state, and later observer edges are separate ExpectedRelations when distinct contracts exist. A relation with more than two distinct endpoint units is allowed only for one indivisible shared constraint; set relation_form=INDIVISIBLE_SHARED and give a concrete shared_constraint_reason explaining why it cannot be represented as adjacent direct edges. Do not encode a sequence of transformations or observations as one multi-endpoint relation. For ordinary one-source/one-target edges set relation_form=DIRECT_EDGE and shared_constraint_reason=null."
SYSTEM_PROMPT += " Assign each SemanticUnit a semantic_role_class: CONFIGURATION_INPUT for declared settings/API intent; PRODUCTION_CONSUMER for source code or templates that read/transform/reconcile it; GENERATED_STATE for a rendered/managed resource; RUNTIME_CONSUMER for the production process/component applying it; OBSERVED_STATE for the resulting state at the actual observation boundary; TEST_ARTIFACT for tests/verifiers; OTHER otherwise. Never use TEST_ARTIFACT as a relation endpoint or as a production role."
SYSTEM_PROMPT += " For each atom preserve the requirement's trigger and scope in its statement, and distinguish the desired effect from explicitly required preservation behavior without inventing either. Explain each supported break as expected contract, actual source/observation, deviation and affected atom. Static production evidence may support a causal break even when the public runner only builds; do not demand hidden runtime fixtures. A relation hypothesis guides a bounded search, never proves itself. Respect public_execution_capabilities: unobserved behavior remains unobserved. Optional locator_hint fields must be omitted when unknown, not set to null."
TRACEABILITY_PROMPT = " When traceability_required is true, quote each precise predicate verbatim in source_quote, use its clause source_ref, and classify every clause once in clause_treatments as OBLIGATION/BACKGROUND/UNCERTAIN with rationale and atom refs. Keep distinct predicates distinct even within one sentence. When one predicate quote covers different affected objects, copy the exact object name into scope_quote (for example create versus patch). Shared anchors never justify dropping atoms or narrowing obligations. Omit scope_quote unless splitting the same predicate by affected object; if used it must be a single exact word/name from the cited original clause, without explanation or parentheses. For every supported configuration outcome show adjacent input→production-consumer and production-consumer→generated/observed-state relations when evidenced. Input→template alone does not establish the output path; do not relabel code as observed state or invent an unsupported edge. Stable identity is runner-owned; never invent stable keys. A BACKGROUND label is a semantic claim requiring explanation, not automatic completeness proof."
TRACEABILITY_PROMPT += " If prior_obligation_lineage exists, use explicit CONTINUE/REANCHOR/SPLIT/MERGE events for changed identity mappings. If the prior derived interpretation is itself disputed by the original public requirement, use interpretation_disputed=true only on CONTINUE/REANCHOR and explain the source-grounded disagreement. This retains history and an unresolved raw requirement; it does not establish semantic equivalence, erase checks, waive the requirement or grant satisfaction."
FRONTIER_PROMPT = " production_path_frontier lists lexical sibling consumers from source actually supplied, not verified causal edges. Compare each against the raw scope, represent relevant branches in semantic roles/evidence and the production path, and retain uncertainty for unrelated or missing paths. A path through one branch does not establish coverage of a sibling. Never infer causality from matching names alone."
SYSTEM_PROMPT += " Keep blocking diagnosis gaps separate from post-repair verification limits. evidence_gaps request missing facts needed to establish the causal break or actionable target; unavailable public proof of a future final outcome is not by itself such a gap. When static evidence actually establishes a break, retain that supported diagnosis; later planning must explicitly leave unobservable behavior unverified. When the mechanism or target itself is unsupported, keep EVIDENCE_GAP. Never promote a claim merely to obtain an executable plan."

SYSTEM_PROMPT += " Before finalizing atoms, compare them back to the complete raw requirement: identify each trigger, affected object, required effect and explicit non-trigger/preservation clause. Keep the relevant original wording in each atom statement; source_refs alone do not establish semantic completeness. For production paths already visible in the supplied source, distinguish alternative relevant consumers/branches and state remaining uncertainty rather than generalizing one local function to all paths. Do not invent branches or demand all repository callers."



@lru_cache(maxsize=1)
def _output_schema() -> Mapping[str, Any]:
    path = Path(__file__).resolve().parents[1] / "contracts" / "schema" / "diagnosis_packet.v1_1.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _body_schema() -> Mapping[str, Any]:
    envelope = {"schema_version", "run_id", "snapshot_id", "input_digest", "requirement_spec"}
    schema = constrained_output_schema(_output_schema(), omit=envelope)
    schema["properties"]["evidence_refs"] = {"type": "array", "items": {"type": "string"}}
    return schema


def _case_output_schema(requirement: Mapping[str, Any], evidence_catalog: list[Mapping[str, Any]]) -> dict[str, Any]:
    schema = deepcopy(_body_schema())
    outcomes = requirement.get("outcomes", [])
    outcome_ids = sorted({str(item["id"]) for item in outcomes
                          if isinstance(item, Mapping) and isinstance(item.get("id"), str)})
    properties = schema["properties"]
    properties.pop('obligation_lineage',None)
    if not requirement.get('operation_consistency_required'):
        properties.pop('operation_assessments',None)
    if not requirement.get('lineage_required'):
        properties.pop('lineage_events',None)
    else:
        parents = sorted(requirement.get('prior_obligation_lineage', {}).get('nodes', {}))
        if parents:
            properties['lineage_events']['items']['properties']['parent_ids']['items']['enum'] = parents
        else:
            properties['lineage_events']['maxItems'] = 0
    if outcome_ids:
        statements = sorted({str(item["statement"]) for item in outcomes
                             if isinstance(item, Mapping) and isinstance(item.get("statement"), str)})
        if statements:
            properties["requirement_horizon"]["items"]["enum"] = statements
        properties["unverified_outcomes"]["items"]["enum"] = outcome_ids
        for name in ("units", "expected_relations"):
            properties[name]["items"]["properties"]["requirement_refs"]["items"]["enum"] = outcome_ids
        properties["outcome_coverage"] = {
            "type": "object",
            "properties": {
                outcome_id: {
                    "type": "object",
                    "required": ["disposition", "preservation_status", "preservation_evidence_refs"],
                    "properties": {
                        "disposition": {"type": "string", "enum": ["REPAIR_SUPPORTED", "PRESERVE_REQUIRED", "UNRESOLVED"]},
                        "preservation_status": {"type": "string", "enum": ["NOT_APPLICABLE", "OBSERVED", "INFERRED", "UNKNOWN"]},
                        "preservation_evidence_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
                    },
                    "additionalProperties": False,
                }
                for outcome_id in outcome_ids
            },
            "required": outcome_ids,
            "additionalProperties": False,
        }
    properties["units"]["items"]["properties"]["id"]["pattern"] = r"^U[1-9][0-9]*$"
    properties["obligation_atoms"]["items"]["properties"]["id"]["pattern"] = r"^A[1-9][0-9]*$"
    outcome_ids = sorted({str(item["id"]) for item in outcomes if isinstance(item, Mapping) and isinstance(item.get("id"), str)})
    properties["obligation_atoms"]["items"]["properties"]["outcome_id"]["enum"] = outcome_ids
    source_refs = sorted({str(ref) for item in outcomes if isinstance(item, Mapping)
                          for ref in item.get("source_refs", []) if isinstance(ref, str)})
    properties['obligation_atoms']['items']['properties'].pop('stable_obligation_key',None)
    if requirement.get('traceability_required'):
        source_refs=[c['source_ref'] for c in requirement.get('clauses',[])]
        import re
        properties['obligation_atoms']['items']['properties']['scope_quote']['enum']=sorted(set(re.findall(r'[A-Za-z_][A-Za-z0-9_-]*',requirement['raw_text'])))
        properties['obligation_atoms']['items']['required'].append('source_quote')
        schema['required'].append('clause_treatments')
        properties['clause_treatments']['items']['properties']['source_ref']['enum']=source_refs
    else:
        properties.pop('clause_treatments',None)
        properties['obligation_atoms']['items']['properties'].pop('source_quote',None)
        properties['obligation_atoms']['items']['properties'].pop('scope_quote',None)
    if source_refs:
        properties["obligation_atoms"]["items"]["properties"]["source_refs"]["items"]["enum"] = source_refs
    if not any(supports_expected_missing_evidence(item) for item in evidence_catalog):
        properties["units"]["items"]["properties"]["existence"]["enum"] = ["PRESENT", "UNKNOWN"]
    evidence_ids = [str(item["id"]) for item in evidence_catalog]
    allowed_evidence_ids = sorted(set(evidence_ids))
    if allowed_evidence_ids:
        properties["evidence_refs"]["items"]["enum"] = allowed_evidence_ids
        for name, field in (("units", "locator_refs"), ("expected_relations", "support_refs"),
                            ("violations", "evidence_refs")):
            properties[name]["items"]["properties"][field]["items"]["enum"] = allowed_evidence_ids
        outcome_coverage = properties["outcome_coverage"].get("properties", {})
        for outcome_schema in outcome_coverage.values():
            preservation_refs = outcome_schema.get("properties", {}).get("preservation_evidence_refs")
            if isinstance(preservation_refs, dict):
                preservation_refs["items"]["enum"] = allowed_evidence_ids
    return schema


def build_diagnosis_request(method_input: MethodInput, requirement: Mapping[str, Any],
                            candidates: Any, run_context: RunContext, *,
                            scope_resolution: Any | None = None) -> ModelRequest:
    if hasattr(candidates, "candidates"):
        selected = list(candidates.candidates)
        selected_ids = list(candidates.selected_ids)
        dropped_ids = list(candidates.dropped_ids)
        token_count = candidates.context_tokens
    else:
        selected = list(candidates.get("candidates", ()))
        selected_ids = list(candidates.get("selected_ids", ()))
        dropped_ids = list(candidates.get("dropped_ids", ()))
        token_count = candidates.get("context_tokens")
    evidence_catalog = build_evidence_catalog(method_input, candidates, run_context.snapshot_id)
    output_schema = _case_output_schema(requirement, evidence_catalog)
    candidate_summaries = []
    for item in selected:
        summary = {key: item[key] for key in (
            "id", "path", "kind", "symbol", "declared_symbols", "references", "parser_kind", "truncated",
            "origin_kind", "mutable", "artifact_id"
        ) if key in item}
        summary["evidence_ref_id"] = f"SRC-{item['id']}"
        candidate_summaries.append(summary)

    scope_summary = None
    if scope_resolution is not None:
        audits = []
        for item in scope_resolution.search_audit:
            matched_paths = list(item.get("matched_paths", ()))
            audits.append({key: item[key] for key in (
                "kind", "query", "origin_ref", "match_mode", "query_terms",
                "files_inspected", "bytes_inspected", "search_complete", "truncated",
                "matches_total", "match_cap_applied", "coverage_incomplete",
                "indexed_files_total", "indexed_bytes_total",
            ) if key in item})
            audits[-1]["matched_path_count"] = len(matched_paths)
            audits[-1]["matched_paths_preview"] = matched_paths[:8]
            audits[-1]["matched_paths_preview_truncated"] = len(matched_paths) > 8
        scope_summary = {
            "resolved_seed_path_count": len(scope_resolution.seed_paths),
            "resolved_seed_paths_preview": list(scope_resolution.seed_paths[:32]),
            "resolved_seed_paths_preview_truncated": len(scope_resolution.seed_paths) > 32,
            "unresolved_hints": [dict(item) for item in scope_resolution.unresolved],
            "search_audit": audits,
            "extension_paths": list(scope_resolution.extension_paths[:32]),
            "extension_paths_preview_truncated": len(scope_resolution.extension_paths) > 32,
        }

    from contractedge.diagnosis.operation_consistency import operation_facts
    operations=operation_facts(method_input.public_evidence)
    if operations and 'operation_assessments' in output_schema['properties']:
        output_schema['properties']['operation_assessments']['items']['properties']['scope_evidence_refs']['items']['enum'] = sorted(r['id'] for r in evidence_catalog)
    public = {
        "schema_version": "ce.diagnosis_packet.v1_1",
        "output_schema": output_schema,
        "evidence_catalog": evidence_catalog,
        "operation_facts": operations,
        "failed_public_predicates": [dict(observation_id=o['id'],check_id=o['behavior_check']['id'],
            scenario=o.get('scenario'),status='FAIL') for o in method_input.public_evidence
            if o.get('snapshot_id')==run_context.snapshot_id and o.get('evidence_kind')=='PUBLIC_BEHAVIOR'
            and o.get('behavior_check',{}).get('status')=='FAIL'],
        "allowed_evidence_ids": [item["id"] for item in evidence_catalog],
        "run_context": {"run_id": run_context.run_id, "case_id": method_input.case_id,
                         "snapshot_id": run_context.snapshot_id, "input_digest": method_input.input_digest},
        "method_input": {"case_id": method_input.case_id, "release_id": method_input.release_id,
                         "requirement_text": method_input.requirement_text,
                         "public_observable_text": method_input.public_observable_text,
                         "scope_hints": [dict(item) for item in method_input.scope_hints],
                         "public_evidence": [{'id':item['id']} for item in method_input.public_evidence],
                         "declared_artifacts": [{key: item[key] for key in ("id", "path", "media_type", "content_digest")}
                                                for item in method_input.declared_artifacts]},
        "requirement_spec": dict(requirement),
        "public_execution_capabilities": run_context.retrieval_config.get('public_execution_capabilities'),
        "production_path_frontier": (production_frontier(selected, method_input.requirement_text)
            if run_context.retrieval_config.get('production_path_review_enabled', False) else []),
        "candidate_set": {"selected_ids": selected_ids, "dropped_ids": dropped_ids,
                          "context_tokens": token_count,
                          "selected_candidates": candidate_summaries},
        "scope_resolution": scope_summary,
        "output_contract": "Return only a DiagnosisPacket v1.1 JSON object; no markdown fences.",
    }
    if not run_context.model_config.get('schema_in_prompt', True):
        # Native constrained decoding receives the complete schema separately.
        # Keep facts once in the evidence catalog instead of duplicating long
        # observation excerpts in MethodInput and again in the same request.
        public.pop('output_schema')
        public['output_fields'] = list(output_schema['properties'])
    system_prompt = SYSTEM_PROMPT + (TRACEABILITY_PROMPT if requirement.get('traceability_required') else '') + (FRONTIER_PROMPT if public["production_path_frontier"] else "")
    if requirement.get('operation_consistency_required') and operations:
        system_prompt += ' Registered operation_facts are runner-owned. Process exit success and returned object fields do not prove operation success. Address every failed operation relevant to your satisfaction judgment in operation_assessments. A failed operation requires causal source evidence for repair, otherwise leave unresolved and request a precise source gap; never claim the whole outcome satisfied by citing partial object fields. An EXPECTED_EXCEPTION requires an established public negative-control contract; no such contract means uncertainty, not permission to ignore failure. Do not invent causal violations merely to pass validation.'
        system_prompt += ' Every UNRELATED assessment, including a successful control operation, needs non-empty scope_evidence_refs copied from allowed_evidence_ids supporting the distinct scope; otherwise use UNKNOWN. Merely labeling an operation unrelated cannot waive a failure.'
    if requirement.get('lineage_required'):
        system_prompt += ' Prior obligation IDs are immutable. Use lineage_events CONTINUE or REANCHOR with parent_ids and current atom_refs to preserve identity across changed prose/quotes. SPLIT records one parent and at least two children; MERGE records a conjunction view, never erases parents. Leave events empty for initial registration or exact unchanged anchors. Original definitions and unresolved residuals remain live; do not assert equivalence solely from wording.'
    contracts = field_contract_contexts(selected, method_input.requirement_text)
    if contracts:
        public['source_field_contract_contexts'] = contracts
        public['source_contract_review_instruction'] = 'Review these exact field/API comments before inferring consumer effects. Producing a request field does not prove the consumer applies it. Recheck applicability, update restrictions and ignored inputs; these excerpts are not runtime observations or an automatic verdict.'
    content = json.dumps(public, ensure_ascii=False, sort_keys=True)
    model = str(run_context.model_config.get("model", "UNCONFIGURED"))
    output_budget = int(run_context.budget.get("max_output_tokens", 4096))
    return ModelRequest(model=model, prompt_version="diagnosis.v1.1.26" if requirement.get("traceability_required") else "diagnosis.v1.1.24",
                        messages=({"role": "system", "content": system_prompt},
                                  {"role": "user", "content": content}),
                        max_output_tokens=output_budget, temperature=0.0,
                        metadata={"run_id": run_context.run_id, "snapshot_id": run_context.snapshot_id,
                                  "input_digest": method_input.input_digest,
                                  "schema_in_prompt": run_context.model_config.get('schema_in_prompt', True),
                                  "selected_ids": selected_ids, "dropped_ids": dropped_ids},
                                  output_schema=output_schema)
