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
from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
from contractedge.contracts.public_checks import public_check_catalog


from contractedge.contracts.pointers import JSON_POINTER_GUIDANCE

CHANGESET_SYSTEM_PROMPT = """Produce one ContractEdge SemanticChangeSet v1.1 JSON object. Plan only changes that restore supported planned violations. Every change must cite a supported violation and postcondition. Preserve already satisfied outcomes and list unresolved obligations. Existing targets must use current snapshot paths/digests. A missing target may be NEW only when an EXPECTED_MISSING role and evidence justify it. Read-only source evidence does not grant edit permission: existing_target_paths contains only publicly editable targets. Do not write patch code or claim that any edit has been applied."""
CHANGESET_SYSTEM_PROMPT += " In this model-facing schema, each target uses target_kind. For target_kind EXISTING, emit source_path and unit_refs from the same entry in existing_target_paths; every unit in that change must resolve to that one source_path. If a supported repair needs edits in multiple files, create separate changes, one per source_path, and link each change to the relevant supported violation and postcondition. Do not combine units from different files in one target. Do not invent a path, locator, snapshot ID, or digest because the runner resolves those from the current source snapshot. For target_kind NEW, emit parent_path, proposed_path, expected_role, anchor_unit_refs, missing_unit_refs, and justification_refs. Do not mix fields from these two forms. The runner translates your selector into the canonical v1.1 target before validation."
CHANGESET_SYSTEM_PROMPT += " Trace each proposed setting or field through its production consumer to the generated or runtime outcome, using the supplied evidence and supported relation chain. Do not add an unused value or declaration whose consumer path is absent from the supplied evidence. State a postcondition at the contract boundary that the change is intended to restore; do not treat merely adding a configuration key as sufficient when no consuming template or code is evidenced."
CHANGESET_SYSTEM_PROMPT += " Every planned postcondition must cite the atomic obligation IDs it restores, as well as its relation_refs. Preserve or leave open each atom not repaired this round by citing that atom in the matching preserve_constraints or open_obligations entry. Do not treat a test-only edit as restoration of a production relation; tests may be supplementary changes but cannot replace the production change or its postcondition."
CHANGESET_SYSTEM_PROMPT += " outcome_coverage.preserve_required contains outcome IDs whose already satisfied parts must have explicit preserve_constraints entries. open_obligations do not substitute for those preservation constraints. For an outcome containing both failed and passing predicates, preserve the supported passing predicates, plan the failed ones, and retain genuinely unverified residuals as open; do not delete a preservation requirement to satisfy validation. If no changes are planned, preservation_impact must use GLOBAL with empty change_refs rather than referring to absent changes."
CHANGESET_SYSTEM_PROMPT += " If planning needs additional evidence, return NEEDS_EVIDENCE with no executable changes and explicit open obligations. When you can locate the missing evidence, include evidence_gaps with id, summary and locator_hint.path, locator_hint.symbol or locator_hint.search_term. This shares the single Diagnosis extension allowance; it does not create a new round or replenish budgets. Otherwise omit evidence_gaps or return an empty array."
CHANGESET_SYSTEM_PROMPT += " Make every action specific enough for a separate executor: identify the production consumer and intended binding/state change in semantic_action, and preserve the requirement trigger/scope in intent. Use depends_on only for real prerequisites. Do not restate 'fix configuration' as the entire action or embed full implementation code. When reordering a production block, identify where every referenced input is initialized and remains in scope. Place the block only after those evidenced prerequisites and before the dependent behavior; a source line naming one prerequisite does not prove all inputs are available. Preserve error handling and guards when describing the move. Each P must state the actual observable boundary in planned_check/check_scope. Set verification_kind to PUBLIC_STRUCTURE, PUBLIC_BEHAVIOR, BUILD_ONLY, NOT_PUBLICLY_OBSERVABLE or UNKNOWN according to the declared public capabilities. A behavior P is not BUILD_ONLY merely because only builds are available. Unobservable behavior remains open to verification but does not prevent an otherwise supported executable change. Do not invent a new probe, command or hidden fixture. Preserve all supported atoms not repaired by this change."

CHANGESET_SYSTEM_PROMPT += " Review each atom against its original wording and trigger/object/preservation scope before planning. For each already evidenced production branch relevant to the same obligation, state in semantic_action/intent whether it is changed, remains valid with supporting rationale, or remains unresolved; do not equate an exact target with complete branch coverage. A broad owning-function obligation cannot silently narrow to one validator or one execution mode. No whole-repository branch enumeration is required. For declared-input equality use verification_kind=DECLARED_INPUT_EQUALITY and comparison_requirements binding every required artifact_id/input_pointer/output_pointer before execution. Use one P per scenario/resource when needed. Bindings contain paths, never expected values; incomplete or unavailable bindings remain UNKNOWN. Use verification_scope with scenario/resource identities when supplied by public artifacts; do not guess missing identities. Apply the appropriate verification kind to preservation and open obligations too."

CHANGESET_SYSTEM_PROMPT += " Before proposing reuse or transfer of runtime objects, inspect their ownership, cancellation, timer/channel lifetime, route/input binding and synchronization contracts in the supplied source. Transferring a map of pointers does not establish that the pointed-to objects remain usable under the replacement owner. If this repair strategy depends on a missing lifecycle contract, use the existing NEEDS_EVIDENCE extension with exact symbols rather than treating a proposed helper API as evidence that the strategy is safe. Preserve all source-supported behavior and retain unverified parts explicitly."

CHANGESET_SYSTEM_PROMPT += " " + JSON_POINTER_GUIDANCE

COVERAGE_PROMPT = " For each behavior_check_refs binding explicitly set check_coverage: ALL_OF only if those checks together cover the complete concrete statement; NECESSARY_ONLY if failure of a required instance disproves it but success does not exhaust all inputs, branches or modes; SUPPORTING only if a failed result would not contradict the statement (for example an unrelated control). Incomplete mode coverage alone is NECESSARY_ONLY, not SUPPORTING. Bind the named necessary checks even when a public result was not selected; unobserved results stay UNKNOWN. Explain coverage_rationale using the public check descriptions. Narrow statements or leave UNKNOWN rather than claiming broad sufficiency."

IMPACT_PROMPT = " When preservation_impact is requested, preserve_id refers to the id of an entry you declare in this plan's preserve_constraints, never its outcome_id or an outcome ID. Account for each such entry exactly once: CHANGES lists all concrete change_refs that may influence it with a rationale; GLOBAL has an empty list and explains why the constraint is global. If preserve_constraints is empty, preservation_impact must also be empty. These are possible impact associations, never proof of causality or permission to skip preservation checks."

FRONTIER_PROMPT = " production_path_frontier is a bounded lexical sibling inventory, not a proven call graph or new edit permission. Review each supplied source_evidence_id exactly once in production_path_review: MODIFY with change_refs targeting that actual branch, PRESERVE with a concrete reason, OUT_OF_SCOPE with a concrete relevance explanation, or UNRESOLVED with real open_obligation_refs. Do not silently omit another owning-function branch or relabel it repaired because a neighbouring branch is changed. Inspect the exact source text; shared names alone do not prove relevance. Unrepresented or truncated source remains a coverage limit. MODIFY has nonempty real change_refs and empty open_obligation_refs; incomplete runtime verification belongs in the plan open_obligations, without erasing it. UNRESOLVED describes unplanned branch intent with real open_obligation_refs and empty change_refs. PRESERVE and OUT_OF_SCOPE have both reference arrays empty."



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


def _case_output_schema(diagnosis: DiagnosisPacket, policy: Mapping | None = None, frontier=None, probe_ids=None, prerequisites=None, capabilities=None) -> dict[str, Any]:
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
    properties["changes"]["items"]["properties"]["operation"]["enum"] = resolve_execution_capabilities(capabilities)["supported_operations"]
    if prerequisites:
        schema['required'].append('precondition_review')
        properties['precondition_review']['minItems'] = len(prerequisites)
        properties['precondition_review']['items']['properties']['source_evidence_ref']['enum'] = [r['source_evidence_ref'] for r in prerequisites]
    else:
        properties.pop('precondition_review', None)
    properties.pop("obligation_lineage",None)
    if raw.get("requirement_spec", {}).get("traceability_required"):
        schema["required"].append("preservation_impact")
    else:
        properties.pop("preservation_impact", None)
    for kind in ['postconditions','preserve_constraints','open_obligations']:
        fields = properties[kind]['items']['properties']
        fields.pop('stable_obligation_keys',None)
        if not probe_ids and not raw.get('requirement_spec',{}).get('traceability_required'):
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
        if "preservation_impact" in properties:
            properties["preservation_impact"]["maxItems"] = 0
    if not outcome_ids:
        properties["open_obligations"]["maxItems"] = 0
    coverage = properties["outcome_coverage"]["items"]["properties"]
    coverage["outcome_id"]["enum"] = outcome_ids
    coverage["repair_supported"]["items"]["enum"] = supported_violation_ids
    coverage["preserve_required"]["items"]["enum"] = outcome_ids
    coverage["unresolved"]["items"]["enum"] = outcome_ids
    return schema


def build_changeset_request(diagnosis: DiagnosisPacket, run_context: RunContext,
                            validation_feedback: list[Mapping[str, Any]] | None = None, *,
                            previous_attempt: Mapping[str, Any] | None = None) -> ModelRequest:
    retrieval = getattr(run_context, 'retrieval_config', {})
    support = list(retrieval.get('necessary_source_support', []))
    prerequisites = list(retrieval.get('source_prerequisites', []))
    for record in support:
        if (record.get('snapshot_id') != run_context.snapshot_id or record.get('kind') != 'SOURCE_SPAN'
                or not isinstance(record.get('excerpt'), str)
                or hashlib.sha256(record['excerpt'].encode()).hexdigest() != record.get('content_digest')):
            raise ValueError('Planning source support must bind fresh exact source evidence')
    capabilities = resolve_execution_capabilities(getattr(run_context, 'retrieval_config', {}).get('public_execution_capabilities'))
    policy = capabilities.get('editable_policy')
    frontier = getattr(run_context, 'retrieval_config', {}).get('production_path_frontier', [])
    probe_ids = list(public_check_catalog(capabilities))
    editable_targets = {path: ids for path, ids in _unit_ids_by_source_path(diagnosis.raw).items()
                        if target_permission_error(path, policy) is None}
    user_payload = {
        "schema_version": "ce.semantic_changeset.v1_1",
        "output_schema": _case_output_schema(diagnosis, policy, frontier, probe_ids, prerequisites, capabilities),
        "run_context": {"run_id": run_context.run_id, "case_id": run_context.case_id,
                        "snapshot_id": run_context.snapshot_id, "input_digest": run_context.input_digest},
        "diagnosis_packet": dict(diagnosis.raw),
        "read_only_source_support": support,
        "source_prerequisites": prerequisites,
        "production_path_frontier": frontier,
        "existing_target_paths": [{"path": path, "unit_ids": sorted(unit_ids)}
                                   for path, unit_ids in sorted(editable_targets.items())],
        "diagnosis_digest": canonical_digest(diagnosis.raw),
        "public_execution_capabilities": capabilities,
        "validation_feedback": list(validation_feedback or []),
        "output_contract": "Return only a SemanticChangeSet v1.1 JSON object; no markdown fences.",
    }
    if retrieval.get('planning_helper_contracts') is True:
        user_payload['run_context']['planning_helper_contracts']={
            'enabled':True,'max_bytes':retrieval.get('planning_helper_max_bytes',24000)}
    if not run_context.model_config.get('schema_in_prompt', True):
        user_payload.pop('output_schema')
        user_payload['output_fields'] = list(_case_output_schema(diagnosis, policy, frontier, probe_ids, prerequisites, capabilities)['properties'])
    system_prompt = CHANGESET_SYSTEM_PROMPT + (COVERAGE_PROMPT + IMPACT_PROMPT if diagnosis.raw.get('requirement_spec',{}).get('traceability_required') else '') + (FRONTIER_PROMPT if frontier else "")
    system_prompt += (" Current execution consumer " + capabilities["consumer_id"] + " supports only "
                      + ", ".join(capabilities["supported_operations"])
                      + ". Do not propose unsupported operations; retain genuinely unresolved obligations instead.")
    if support:
        system_prompt += (' Explicitly plan each new local helper in an authorized owning file, including caller dependencies. '
            'A proposed helper implementation is not required to pre-exist: distinguish an evidenced defect and a planned '
            'implementation from an unknown existing ownership, input or lifecycle contract. Use NEEDS_EVIDENCE for '
            'missing contracts needed to justify the strategy, not merely because the repair code has not been written. '
            'External API availability/signatures may remain implementation uncertainties for compilation to check when '
            'the supplied source contract names the operation and the client binding is evidenced; never invent an '
            'existing method or claim runtime success. Keep behavior not publicly observable explicitly unverified. '
            'A caller edit cannot authorize unplanned library edits or new editable paths.')
        system_prompt += (' Read-only source support is fresh public implementation context, not an extra editable target '
            'or an automatically verified causal relation. Inspect initialization, readiness and state preservation '
            'before accepting a lifecycle handoff plan: starting an asynchronous call does not establish readiness. '
            'Record applicable prerequisites and how current postconditions, preservation or open obligations check them. '
            'If these sources contradict the accepted diagnosis or require new semantic units, use NEEDS_EVIDENCE '
            'with the exact source locator; do not silently invent supported units or broaden edit permissions. '
            'Unknown readiness remains unverified; do not propose a delay as proof of correctness.')
    if prerequisites:
        system_prompt += (' Review each source_prerequisites entry exactly once in precondition_review. '
            'Describe the actual requirement-relevant condition from the supplied source, not an assumed generic readiness rule. '
            'REQUIRED binds real postcondition/preservation IDs in verification_refs; finite checks retain their coverage limit. '
            'A runtime prerequisite with only BUILD_ONLY/UNKNOWN checks also needs a real open_obligation_ref. '
            'UNRESOLVED binds existing open_obligation_refs and does not claim the condition satisfied. '
            'NOT_APPLICABLE has empty reference arrays and a source-grounded relevance rationale. '
            'This is prerequisite accountability, not a static proof that the proposed code or synchronization is correct.')
    if probe_ids:
        system_prompt += " Registered method_behavior_probes are additional public method-owned observations, not frozen independent scorers. For obligations actually covered by these probe assertions choose PUBLIC_BEHAVIOR, set verification_scope.scenario to the registered method scenario and predeclare all necessary behavior_check_refs. Missing, failed or unrelated check results cannot establish PASS. Respect each probe boundary; do not infer external runtime coverage from local stand-ins."
    if capabilities.get('registered_public_checks'):
        system_prompt += (' registered_public_checks also declare available public observations. For each binding '
            'copy its exact registered check ID and scenario into behavior_check_refs and verification_scope.scenario. '
            'scenario_members limits the finite inputs covered by that predicate; it is not a different execution scenario. '
            'Checks may share a suite scenario while asserting different predicates. Do not infer scenario from a check ID, '
            'or bind unrelated checks. Declare check_coverage and coverage_rationale for every bound obligation. '
            'ALL_OF applies only to a concrete predicate fully covered by those checks; broader requirements retain '
            'NECESSARY_ONLY or unresolved obligations. No binding or PASS is supplied on your behalf.')
    model = str(run_context.model_config.get("model", "UNCONFIGURED"))
    max_tokens = int(run_context.budget.get("max_output_tokens", 4096))
    output_schema = _case_output_schema(diagnosis, policy, frontier, probe_ids, prerequisites, capabilities)
    messages = ({"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True)})
    draft_metadata = {}
    if previous_attempt is not None:
        identity = dict(run_id=run_context.run_id, snapshot_id=run_context.snapshot_id,
            input_digest=run_context.input_digest, diagnosis_digest=canonical_digest(diagnosis.raw))
        if (not isinstance(previous_attempt, Mapping) or not validation_feedback
                or any(previous_attempt.get(k) != v for k, v in identity.items())
                or not isinstance(previous_attempt.get('state'), str)
                or previous_attempt.get('state') not in {'REJECTED_PLAN_DRAFT', 'CURRENT_PLAN_FOR_REVISION'}
                or not isinstance(previous_attempt.get('response_content'), str)
                or not previous_attempt['response_content'].strip()):
            raise ValueError('Previous ChangeSet must bind the current run, input, snapshot and diagnosis, with revision feedback')
        content = previous_attempt['response_content']
        digest = hashlib.sha256(content.encode()).hexdigest()
        messages += ({'role': 'assistant', 'content': content}, {'role': 'user', 'content': json.dumps(dict(
            previous_attempt_state=previous_attempt['state'], previous_attempt_sha256=digest,
            binding=identity, validation_feedback=list(validation_feedback),
            instruction='Revise the preceding planning draft using the current schema, source and feedback. '
                'It is prior planning state, never new evidence or proof of behavior. '
                'Preserve unaffected valid fields and all requirement obligations; correct the cited errors without inventing support. '
                'Return one complete SemanticChangeSet with every required field. Do not emit this revision wrapper.'),
            ensure_ascii=False, sort_keys=True)})
        draft_metadata = dict(previous_changeset_sha256=digest, previous_changeset_state=previous_attempt['state'])
    return ModelRequest(model=model, prompt_version=("changeset.v1.1.14" if capabilities.get('registered_public_checks') else
                        "changeset.v1.1.13" if diagnosis.raw.get("requirement_spec",{}).get("traceability_required") else "changeset.v1.1.12"),
                        messages=messages,
                        max_output_tokens=max_tokens, temperature=0.0,
                        metadata={"run_id": run_context.run_id, "snapshot_id": run_context.snapshot_id,
                                  "input_digest": run_context.input_digest,
                                  "execution_capabilities_digest": canonical_digest(capabilities),
                                  "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
                                  **({'planning_helper_contracts':user_payload['run_context']['planning_helper_contracts']}
                                     if retrieval.get('planning_helper_contracts') is True else {}),
                                  **draft_metadata},
                        output_schema=output_schema)
