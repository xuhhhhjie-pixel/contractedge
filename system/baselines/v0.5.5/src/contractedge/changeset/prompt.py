from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from contractedge.contracts.models import DiagnosisPacket, RunContext
from contractedge.contracts.validation import canonical_digest
from contractedge.model.provider import ModelRequest
from contractedge.model.schema import constrained_output_schema
from contractedge.contracts.permissions import target_permission_error


CHANGESET_SYSTEM_PROMPT = """Produce one ContractEdge SemanticChangeSet v1.1 JSON object. Plan only changes that restore supported planned violations. Every change must cite a supported violation and postcondition. Preserve already satisfied outcomes and list unresolved obligations. Existing targets must use current snapshot paths/digests. A missing target may be NEW only when an EXPECTED_MISSING role and evidence justify it. Read-only source evidence does not grant edit permission: existing_target_paths contains only publicly editable targets. Do not write patch code or claim that any edit has been applied."""
CHANGESET_SYSTEM_PROMPT += " In this model-facing schema, each target uses target_kind. For target_kind EXISTING, emit source_path and unit_refs from the same entry in existing_target_paths; every unit in that change must resolve to that one source_path. If a supported repair needs edits in multiple files, create separate changes, one per source_path, and link each change to the relevant supported violation and postcondition. Do not combine units from different files in one target. Do not invent a path, locator, snapshot ID, or digest because the runner resolves those from the current source snapshot. For target_kind NEW, emit parent_path, proposed_path, expected_role, anchor_unit_refs, missing_unit_refs, and justification_refs. Do not mix fields from these two forms. The runner translates your selector into the canonical v1.1 target before validation."
CHANGESET_SYSTEM_PROMPT += " Trace each proposed setting or field through its production consumer to the generated or runtime outcome, using the supplied evidence and supported relation chain. Do not add an unused value or declaration whose consumer path is absent from the supplied evidence. State a postcondition at the contract boundary that the change is intended to restore; do not treat merely adding a configuration key as sufficient when no consuming template or code is evidenced."
CHANGESET_SYSTEM_PROMPT += " Every planned postcondition must cite the atomic obligation IDs it restores, as well as its relation_refs. Preserve or leave open each atom not repaired this round by citing that atom in the matching preserve_constraints or open_obligations entry. Do not treat a test-only edit as restoration of a production relation; tests may be supplementary changes but cannot replace the production change or its postcondition."
CHANGESET_SYSTEM_PROMPT += " If planning needs additional evidence, return NEEDS_EVIDENCE with no executable changes and explicit open obligations. When you can locate the missing evidence, include evidence_gaps with id, summary and locator_hint.path, locator_hint.symbol or locator_hint.search_term. This shares the single Diagnosis extension allowance; it does not create a new round or replenish budgets. Otherwise omit evidence_gaps or return an empty array."
CHANGESET_SYSTEM_PROMPT += " Make every action specific enough for a separate executor: identify the production consumer and intended binding/state change in semantic_action, and preserve the requirement trigger/scope in intent. Use depends_on only for real prerequisites. Do not restate 'fix configuration' as the entire action or embed full implementation code. Each P must state the actual observable boundary in planned_check/check_scope. Set verification_kind to PUBLIC_STRUCTURE, PUBLIC_BEHAVIOR, BUILD_ONLY, NOT_PUBLICLY_OBSERVABLE or UNKNOWN according to the declared public capabilities. A behavior P is not BUILD_ONLY merely because only builds are available. Unobservable behavior remains open to verification but does not prevent an otherwise supported executable change. Do not invent a new probe, command or hidden fixture. Preserve all supported atoms not repaired by this change."

CHANGESET_SYSTEM_PROMPT += " Review each atom against its original wording and trigger/object/preservation scope before planning. For each already evidenced production branch relevant to the same obligation, state in semantic_action/intent whether it is changed, remains valid with supporting rationale, or remains unresolved; do not equate an exact target with complete branch coverage. A broad owning-function obligation cannot silently narrow to one validator or one execution mode. No whole-repository branch enumeration is required. For declared-input equality use verification_kind=DECLARED_INPUT_EQUALITY and comparison_requirements binding every required artifact_id/input_pointer/output_pointer before execution. Use one P per scenario/resource when needed. Bindings contain paths, never expected values; incomplete or unavailable bindings remain UNKNOWN. Use verification_scope with scenario/resource identities when supplied by public artifacts; do not guess missing identities. Apply the appropriate verification kind to preservation and open obligations too."

COVERAGE_PROMPT = " For each behavior_check_refs binding explicitly set check_coverage: ALL_OF only if those checks together cover the complete concrete statement; NECESSARY_ONLY if failure of a required instance disproves it but success does not exhaust all inputs, branches or modes; SUPPORTING only if a failed result would not contradict the statement (for example an unrelated control). Incomplete mode coverage alone is NECESSARY_ONLY, not SUPPORTING. Bind the named necessary checks even when a public result was not selected; unobserved results stay UNKNOWN. Explain coverage_rationale using the public check descriptions. Narrow statements or leave UNKNOWN rather than claiming broad sufficiency."

IMPACT_PROMPT = " When preservation_impact is requested, account for every preserve_id exactly once: CHANGES lists all concrete change_refs that may influence it with a rationale; GLOBAL has an empty list and explains why the constraint is global. These are possible impact associations, never proof of causality or permission to skip preservation checks."

FRONTIER_PROMPT = " production_path_frontier is a bounded lexical sibling inventory, not a proven call graph or new edit permission. Review each supplied source_evidence_id exactly once in production_path_review: MODIFY with change_refs targeting that actual branch, PRESERVE with a concrete reason, OUT_OF_SCOPE with a concrete relevance explanation, or UNRESOLVED with real open_obligation_refs. Do not silently omit another owning-function branch or relabel it repaired because a neighbouring branch is changed. Inspect the exact source text; shared names alone do not prove relevance. Unrepresented or truncated source remains a coverage limit."



@lru_cache(maxsize=1)
def _output_schema() -> Mapping[str, Any]:
    path = Path(__file__).resolve().parents[1] / "contracts" / "schema" / "semantic_changeset.v1_1.json"
    return constrained_output_schema(json.loads(path.read_text(encoding="utf-8")))


def _unit_ids_by_source_path(raw: Mapping[str, Any]) -> dict[str, set[str]]:
    evidence_by_id = {str(item["id"]): item for item in raw.get("evidence_refs", [])
                      if isinstance(item, Mapping) and item.get("id")}
    unit_ids_by_path: dict[str, set[str]] = {}
    for unit in raw.get("units", []):
        if not isinstance(unit, Mapping) or not unit.get("id") or unit.get("existence") != "PRESENT":
            continue
        for evidence_id in unit.get("locator_refs", []):
            record = evidence_by_id.get(str(evidence_id))
            locator = record.get("locator") if isinstance(record, Mapping) and record.get("kind") == "SOURCE_SPAN" else None
            path = locator.get("path") if isinstance(locator, Mapping) else None
            if isinstance(path, str) and path:
                unit_ids_by_path.setdefault(path, set()).add(str(unit["id"]))
    return unit_ids_by_path


def _case_output_schema(diagnosis: DiagnosisPacket, policy: Mapping | None = None, frontier=None, probe_ids=None) -> dict[str, Any]:
    schema = deepcopy(_output_schema())
    raw = diagnosis.raw
    units = [item for item in raw.get("units", []) if isinstance(item, Mapping) and item.get("id")]
    present_ids = sorted({str(item["id"]) for item in units if item.get("existence") == "PRESENT"})
    missing_ids = sorted({str(item["id"]) for item in units if item.get("existence") == "EXPECTED_MISSING"})
    outcome_ids = sorted({str(item["id"]) for item in raw.get("requirement_spec", {}).get("outcomes", [])
                          if isinstance(item, Mapping) and item.get("id")})
    relation_ids = sorted({str(item["id"]) for item in raw.get("expected_relations", [])
                           if isinstance(item, Mapping) and item.get("id")})
    obligation_ids = sorted({str(item["id"]) for item in raw.get("obligation_atoms", [])
                             if isinstance(item, Mapping) and item.get("id")})
    evidence_ids = sorted({str(item["id"]) for item in raw.get("evidence_refs", [])
                           if isinstance(item, Mapping) and item.get("id")})
    unit_ids_by_path = {path: ids for path, ids in _unit_ids_by_source_path(raw).items()
                        if target_permission_error(path, policy) is None}
    existing_unit_ids = sorted({unit_id for ids in unit_ids_by_path.values() for unit_id in ids})
    supported_violation_ids = sorted({str(item["id"]) for item in raw.get("violations", [])
                                       if isinstance(item, Mapping) and item.get("id")
                                       and item.get("causal_status") == "SUPPORTED"})
    preserve_ids = sorted({str(outcome_id) for row in raw.get("outcome_coverage", [])
                           if isinstance(row, Mapping) for outcome_id in row.get("preserve_required", [])})
    # An outcome may contain both broken atoms and already satisfied atoms.
    # Outcome-level REPAIR_SUPPORTED must not forbid preserving the latter.
    supported_relations = {str(item.get('relation_id')) for item in raw.get('violations', [])
                           if item.get('causal_status') == 'SUPPORTED'}
    repaired_atoms = {str(atom_id) for relation in raw.get('expected_relations', [])
                      if str(relation.get('id')) in supported_relations
                      for atom_id in relation.get('obligation_refs', [])}
    preserve_ids = sorted(set(preserve_ids) | {
        str(atom['outcome_id']) for atom in raw.get('obligation_atoms', [])
        if str(atom['id']) not in repaired_atoms})
    unresolved_ids = sorted({str(outcome_id) for row in raw.get("outcome_coverage", [])
                             if isinstance(row, Mapping) for outcome_id in row.get("unresolved", [])})
    properties = schema["properties"]
    if raw.get("requirement_spec", {}).get("traceability_required"):
        schema["required"].append("preservation_impact")
    else:
        properties.pop("preservation_impact", None)
    for kind in ['postconditions','preserve_constraints','open_obligations']:
        fields = properties[kind]['items']['properties']
        fields.pop('stable_obligation_keys',None)
        if not raw.get('requirement_spec',{}).get('traceability_required'):
            fields.pop('check_coverage',None);fields.pop('coverage_rationale',None)
        if probe_ids and raw.get('requirement_spec',{}).get('traceability_required'):
            properties[kind]['items']['required'].extend(['check_coverage','coverage_rationale'])
        if probe_ids:
            fields['behavior_check_refs']['items']['enum'] = list(probe_ids)
        else:
            fields.pop('behavior_check_refs', None)
    if frontier:
        properties['production_path_review']['items']['properties']['source_evidence_id']['enum'] = [r['source_evidence_id'] for r in frontier]
        schema['required'].append('production_path_review')
    else:
        properties.pop('production_path_review', None)
    properties["planned_violation_ids"]["items"]["enum"] = supported_violation_ids
    change = properties["changes"]["items"]["properties"]
    change["violation_refs"]["items"]["enum"] = supported_violation_ids
    target = change["target"]
    existing_selector = {
        "target_kind": {"type": "string", "enum": ["EXISTING"] if not missing_ids else ["EXISTING", "NEW"]},
        "source_path": {"type": "string", "enum": sorted(unit_ids_by_path)},
        "unit_refs": {"type": "array", "minItems": 1,
                      "items": {"type": "string", "enum": existing_unit_ids}, "maxItems": 32},
    }
    target["properties"] = existing_selector if not missing_ids else {
        **existing_selector,
        "parent_path": {"type": "string", "minLength": 1, "maxLength": 600},
        "proposed_path": {"type": "string", "minLength": 1, "maxLength": 600},
        "expected_role": {"type": "string", "minLength": 1, "maxLength": 600},
        "anchor_unit_refs": {"type": "array", "items": {"type": "string", "enum": present_ids}, "maxItems": 32},
        "missing_unit_refs": {"type": "array", "items": {"type": "string", "enum": missing_ids}, "maxItems": 32},
        "justification_refs": {"type": "array", "items": {"type": "string", "enum": evidence_ids}, "maxItems": 32},
    }
    target["required"] = ["target_kind", "source_path", "unit_refs"] if not missing_ids else ["target_kind"]
    properties["postconditions"]["items"]["properties"]["requirement_refs"]["items"]["enum"] = outcome_ids
    properties["postconditions"]["items"]["properties"]["obligation_refs"]["items"]["enum"] = obligation_ids
    properties["postconditions"]["items"]["properties"]["relation_refs"]["items"]["enum"] = relation_ids
    properties["preserve_constraints"]["items"]["properties"]["outcome_id"]["enum"] = preserve_ids
    properties["preserve_constraints"]["items"]["properties"]["obligation_refs"]["items"]["enum"] = obligation_ids
    properties["open_obligations"]["items"]["properties"]["outcome_id"]["enum"] = outcome_ids
    properties["open_obligations"]["items"]["properties"]["obligation_refs"]["items"]["enum"] = obligation_ids
    if not preserve_ids:
        properties["preserve_constraints"]["maxItems"] = 0
    if not outcome_ids:
        properties["open_obligations"]["maxItems"] = 0
    coverage = properties["outcome_coverage"]["items"]["properties"]
    coverage["outcome_id"]["enum"] = outcome_ids
    coverage["repair_supported"]["items"]["enum"] = supported_violation_ids
    coverage["preserve_required"]["items"]["enum"] = outcome_ids
    coverage["unresolved"]["items"]["enum"] = outcome_ids
    return schema


def build_changeset_request(diagnosis: DiagnosisPacket, run_context: RunContext,
                            validation_feedback: list[Mapping[str, Any]] | None = None) -> ModelRequest:
    capabilities = getattr(run_context, 'retrieval_config', {}).get('public_execution_capabilities') or {}
    policy = capabilities.get('editable_policy')
    frontier = getattr(run_context, 'retrieval_config', {}).get('production_path_frontier', [])
    probe_ids = [p['id']+':'+cid for p in capabilities.get('method_behavior_probes',[]) for cid in p['checks']]
    editable_targets = {path: ids for path, ids in _unit_ids_by_source_path(diagnosis.raw).items()
                        if target_permission_error(path, policy) is None}
    user_payload = {
        "schema_version": "ce.semantic_changeset.v1_1",
        "output_schema": _case_output_schema(diagnosis, policy, frontier, probe_ids),
        "run_context": {"run_id": run_context.run_id, "case_id": run_context.case_id,
                        "snapshot_id": run_context.snapshot_id, "input_digest": run_context.input_digest},
        "diagnosis_packet": dict(diagnosis.raw),
        "production_path_frontier": frontier,
        "existing_target_paths": [{"path": path, "unit_ids": sorted(unit_ids)}
                                   for path, unit_ids in sorted(editable_targets.items())],
        "diagnosis_digest": canonical_digest(diagnosis.raw),
        "public_execution_capabilities": getattr(run_context, 'retrieval_config', {}).get('public_execution_capabilities'),
        "validation_feedback": list(validation_feedback or []),
        "output_contract": "Return only a SemanticChangeSet v1.1 JSON object; no markdown fences.",
    }
    if not run_context.model_config.get('schema_in_prompt', True):
        user_payload.pop('output_schema')
        user_payload['output_fields'] = list(_case_output_schema(diagnosis, policy, frontier, probe_ids)['properties'])
    system_prompt = CHANGESET_SYSTEM_PROMPT + (COVERAGE_PROMPT + IMPACT_PROMPT if diagnosis.raw.get('requirement_spec',{}).get('traceability_required') else '') + (FRONTIER_PROMPT if frontier else "")
    if probe_ids:
        system_prompt += " Registered method_behavior_probes are additional public method-owned observations, not frozen independent scorers. For obligations actually covered by these probe assertions choose PUBLIC_BEHAVIOR, set verification_scope.scenario to the registered method scenario and predeclare all necessary behavior_check_refs. Missing, failed or unrelated check results cannot establish PASS. Respect each probe boundary; do not infer external runtime coverage from local stand-ins."
    model = str(run_context.model_config.get("model", "UNCONFIGURED"))
    max_tokens = int(run_context.budget.get("max_output_tokens", 4096))
    output_schema = _case_output_schema(diagnosis, policy, frontier, probe_ids)
    return ModelRequest(model=model, prompt_version="changeset.v1.1.9" if diagnosis.raw.get("requirement_spec",{}).get("traceability_required") else "changeset.v1.1.8",
                        messages=({"role": "system", "content": system_prompt},
                                  {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True)}),
                        max_output_tokens=max_tokens, temperature=0.0,
                        metadata={"run_id": run_context.run_id, "snapshot_id": run_context.snapshot_id,
                                  "input_digest": run_context.input_digest,
                                  "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest()},
                        output_schema=output_schema)
