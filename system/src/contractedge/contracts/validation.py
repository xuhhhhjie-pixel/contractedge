from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from .evidence import static_search_content_digest, supports_expected_missing_evidence
from .pointers import JSON_POINTER_GUIDANCE
from .models import (
    ContractError,
    DiagnosisPacket,
    MethodInput,
    RunContext,
    SemanticUnit,
    Snapshot,
    ValidationIssue,
    ValidationResult,
)

SCHEMA_DIR = Path(__file__).with_name("schema")
SCHEMA_FILES = {
    "ce.method_input.v1_1": "method_input.v1_1.json",
    "ce.requirement_spec.v1_1": "requirement_spec.v1_1.json",
    "ce.diagnosis_packet.v1_1": "diagnosis_packet.v1_1.json",
    "ce.semantic_changeset.v1_1": "semantic_changeset.v1_1.json",
    "ce.run_trace.v1_1": "run_trace.v1_1.json",
    "ce.execution_feedback.v1": "execution_feedback.v1.json",
}


def canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_schema(version: str) -> dict[str, Any]:
    filename = SCHEMA_FILES.get(version)
    if filename is None:
        raise ContractError("SCHEMA_VERSION_UNSUPPORTED", "schema_version", f"unsupported schema version {version!r}")
    try:
        return json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("SCHEMA_UNAVAILABLE", filename, str(exc)) from exc


def _schema_error_message(error) -> str:
    """Report the violated constraint without echoing a potentially huge value."""
    if error.validator in {'maxItems','minItems'} and isinstance(error.instance,list):
        direction='maximum' if error.validator=='maxItems' else 'minimum'
        message=f'Array has {len(error.instance)} items; {direction} is {error.validator_value}.'
        if list(error.absolute_path)[-1:]==['precondition_review']:
            refs=[r.get('source_evidence_ref') for r in error.instance if isinstance(r,Mapping)
                and isinstance(r.get('source_evidence_ref'),str)]
            repeated=[f'{ref[:100]} ({count})' for ref,count in Counter(refs).items() if count>1]
            if repeated:
                message+=' Duplicate source_evidence_ref: '+', '.join(repeated[:8])+'.'
                message+=' Consolidate all conditions for each source into one review, preserving every condition and check reference.'
        return message
    if error.validator in {'maxLength','minLength'} and isinstance(error.instance,str):
        return f'String length is {len(error.instance)}; {error.validator} is {error.validator_value}.'
    return error.message if len(error.message)<=600 else error.message[:600]+' [value echo omitted; consult the supplied schema]'


def _check_schema(payload: Any, version: str) -> None:
    schema = _load_schema(version)
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: tuple(str(x) for x in e.absolute_path))
    if errors:
        error = errors[0]
        path = ".".join(str(part) for part in error.absolute_path)
        additional = ["SCHEMA_INVALID at " + '.'.join(map(str, e.absolute_path)) + ': ' + _schema_error_message(e)[:220]
                      for e in errors[1:12]]
        pointer_hint = ('; ' + JSON_POINTER_GUIDANCE if any(
            e.validator == 'pattern' and list(e.absolute_path)[-1:] in [['input_pointer'], ['output_pointer']]
            for e in errors) else '')
        raise ContractError("SCHEMA_INVALID", path, _schema_error_message(error) + pointer_hint +
                            ('; additional independent errors: ' + '; '.join(additional) if additional else ''))


def diagnosis_preflight_issues(payload: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
    """Collect independent shape/edge-form errors without trusting malformed refs.

    No payload is repaired and no causal labels are inferred. Cross-reference,
    evidence and coverage validation still runs strictly after shape validation.
    """
    issues = []
    schema = _load_schema('ce.diagnosis_packet.v1_1')
    for error in sorted(Draft202012Validator(schema).iter_errors(payload),
                        key=lambda e: tuple(map(str, e.absolute_path))):
        issues.append(ValidationIssue('SCHEMA_INVALID', '.'.join(map(str, error.absolute_path)), error.message[:300]))
        if len(issues) >= 12:
            break
    relations = payload.get('expected_relations', [])
    if isinstance(relations, list):
        for index, relation in enumerate(relations):
            if not isinstance(relation, Mapping):
                continue
            source, target = relation.get('source_unit_ids'), relation.get('target_unit_ids')
            if not (isinstance(source, list) and isinstance(target, list)
                    and all(isinstance(x, str) for x in source + target)):
                continue
            if relation.get('relation_form') == 'DIRECT_EDGE' and len(set(source + target)) > 2:
                issues.append(ValidationIssue('MULTI_ENDPOINT_RELATION_REQUIRES_SHARED_JUSTIFICATION',
                    f'expected_relations[{index}].relation_form',
                    'Represent adjacent contracts as separate direct edges, or justify one genuinely indivisible shared contract; do not invent support.'))
            if relation.get('relation_form') == 'INDIVISIBLE_SHARED' and not (
                    isinstance(relation.get('shared_constraint_reason'), str) and relation['shared_constraint_reason'].strip()):
                issues.append(ValidationIssue('SHARED_RELATION_JUSTIFICATION_MISSING',
                    f'expected_relations[{index}].shared_constraint_reason', 'An indivisible shared contract needs an evidence-backed explanation.'))
    assessments = payload.get('operation_assessments', [])
    if isinstance(assessments, list):
        for index, row in enumerate(assessments):
            if not isinstance(row, Mapping) or row.get('disposition') != 'UNRELATED':
                continue
            refs = row.get('scope_evidence_refs')
            if not (isinstance(refs, list) and refs
                    and all(isinstance(ref, str) and ref for ref in refs)):
                issues.append(ValidationIssue('OPERATION_UNRELATED_UNSUPPORTED',
                    f'operation_assessments[{index}].scope_evidence_refs',
                    'UNRELATED needs non-empty scope_evidence_refs citing actual supplied evidence for the different scope; otherwise retain UNKNOWN. Do not invent citations.'))
    return tuple(issues[:20])


def _require_unique(items: list[Mapping[str, Any]], field: str, path: str) -> set[str]:
    values = [str(item.get(field, "")) for item in items]
    if len(values) != len(set(values)):
        raise ContractError("DUPLICATE_ID", path, f"{field} values must be unique")
    return set(values)


def _require_refs(refs: list[str] | tuple[str, ...], allowed: set[str], path: str) -> None:
    missing = sorted(set(refs) - allowed)
    if missing:
        raise ContractError("REFERENCE_NOT_FOUND", path, f"unknown references: {', '.join(missing)}")


def parse_method_input(payload: Mapping[str, Any]) -> MethodInput:
    if not isinstance(payload, Mapping):
        raise ContractError("OBJECT_REQUIRED", "", "MethodInput must be a JSON object")
    version = payload.get("schema_version")
    if version not in SCHEMA_FILES:
        raise ContractError("SCHEMA_VERSION_UNSUPPORTED", "schema_version", f"unsupported schema version {version!r}")
    if version != "ce.method_input.v1_1":
        raise ContractError("SCHEMA_VERSION_MISMATCH", "schema_version", "expected ce.method_input.v1_1")
    _check_schema(payload, version)
    expected_digest = canonical_digest({key: value for key, value in payload.items() if key != "input_digest"})
    if payload["input_digest"] != expected_digest:
        raise ContractError("INPUT_DIGEST_MISMATCH", "input_digest", "input digest does not match canonical public input fields")
    artifact_ids: set[str] = set()
    artifact_paths: set[str] = set()
    for index, artifact in enumerate(payload["declared_artifacts"]):
        if artifact["id"] in artifact_ids or artifact["path"] in artifact_paths:
            raise ContractError("DUPLICATE_ID", f"declared_artifacts[{index}]", "artifact IDs and paths must be unique")
        artifact_ids.add(artifact["id"])
        artifact_paths.add(artifact["path"])
        if not _safe_relative_path(artifact["path"]):
            raise ContractError("UNSAFE_ARTIFACT_PATH", f"declared_artifacts[{index}].path", "declared artifact path must be normalized and relative")
        digest = hashlib.sha256(artifact["content"].encode("utf-8")).hexdigest()
        if digest != artifact["content_digest"]:
            raise ContractError("ARTIFACT_DIGEST_MISMATCH", f"declared_artifacts[{index}].content_digest", "digest must match exact UTF-8 content")
    return MethodInput(
        schema_version=version,
        case_id=payload["case_id"],
        release_id=payload["release_id"],
        original_broken_revision=payload["original_broken_revision"],
        archive_path=payload["archive_path"],
        archive_sha256=payload["archive_sha256"],
        source_root=payload["source_root"],
        initial_snapshot_id=payload["initial_snapshot_id"],
        requirement_text=payload["requirement_text"],
        public_observable_text=payload["public_observable_text"],
        scope_hints=tuple(dict(item) for item in payload["scope_hints"]),
        public_evidence=tuple(dict(item) for item in payload["public_evidence"]),
        declared_artifacts=tuple(dict(item) for item in payload["declared_artifacts"]),
        input_digest=payload["input_digest"],
    )


def validate_run_trace(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractError("OBJECT_REQUIRED", "", "RunTrace must be a JSON object")
    version = payload.get("schema_version")
    if version != "ce.run_trace.v1_1":
        raise ContractError("SCHEMA_VERSION_UNSUPPORTED", "schema_version", f"unsupported RunTrace version {version!r}")
    _check_schema(payload, version)
    context = payload.get("run_context")
    if context is not None:
        if context.get("run_id") != payload["run_id"] or context.get("case_id") != payload["case_id"]:
            raise ContractError("RUN_CONTEXT_MISMATCH", "run_context", "trace and run context identities must match")
        if context.get("input_digest") != payload["input_digest"]:
            raise ContractError("INPUT_DIGEST_MISMATCH", "run_context.input_digest", "trace context must bind the public input")
        if payload["snapshot_id"] is not None and context.get("snapshot_id") != payload["snapshot_id"]:
            raise ContractError("SNAPSHOT_MISMATCH", "run_context.snapshot_id", "trace and run context snapshots must match")
    return dict(payload)


def validate_diagnosis(
    payload: Mapping[str, Any], method_input: MethodInput, run_context: RunContext | None
) -> DiagnosisPacket:
    if not isinstance(payload, Mapping):
        raise ContractError("OBJECT_REQUIRED", "", "DiagnosisPacket must be a JSON object")
    version = payload.get("schema_version")
    if version != "ce.diagnosis_packet.v1_1":
        raise ContractError("SCHEMA_VERSION_UNSUPPORTED", "schema_version", f"unsupported schema version {version!r}")
    issues = diagnosis_preflight_issues(payload)
    if issues:
        first, *rest = issues
        raise ContractError(first.code, first.path, first.message +
                            ('; additional independent errors: ' + '; '.join(
                                f'{i.code} at {i.path}: {i.message}' for i in rest) if rest else ''))
    _check_schema(payload, version)
    if payload["input_digest"] != method_input.input_digest:
        raise ContractError("INPUT_DIGEST_MISMATCH", "input_digest", "diagnosis does not belong to this public input")
    expected_snapshot = run_context.snapshot_id if run_context else method_input.initial_snapshot_id
    if payload["snapshot_id"] != expected_snapshot:
        raise ContractError("SNAPSHOT_MISMATCH", "snapshot_id", "diagnosis must analyze the current run snapshot")
    if run_context:
        for key, value in (("run_id", run_context.run_id), ("input_digest", run_context.input_digest)):
            if payload.get(key) != value:
                raise ContractError("RUN_CONTEXT_MISMATCH", key, f"expected {value!r}")

    requirement = payload["requirement_spec"]
    _check_schema(requirement, requirement.get("schema_version"))
    if requirement["raw_text"] != method_input.requirement_text:
        raise ContractError("REQUIREMENT_TEXT_CHANGED", "requirement_spec.raw_text", "raw requirement text must be preserved exactly")

    units = payload["units"]
    outcomes = requirement["outcomes"]
    relations = payload["expected_relations"]
    violations = payload["violations"]
    evidence = payload["evidence_refs"]
    coverage = payload["outcome_coverage"]
    obligation_atoms = payload["obligation_atoms"]
    unit_ids = _require_unique(units, "id", "units")
    outcome_ids = _require_unique(outcomes, "id", "requirement_spec.outcomes")
    obligation_ids = _require_unique(obligation_atoms, "id", "obligation_atoms")
    relation_ids = _require_unique(relations, "id", "expected_relations")
    violation_ids = _require_unique(violations, "id", "violations")
    evidence_ids = _require_unique(evidence, "id", "evidence_refs")
    for index, assessment in enumerate(payload.get('operation_assessments', [])):
        _require_refs(assessment.get('scope_evidence_refs', []), evidence_ids,
                      f'operation_assessments[{index}].scope_evidence_refs')
    _require_unique(payload["evidence_gaps"], "id", "evidence_gaps")
    _require_unique(coverage, "outcome_id", "outcome_coverage")
    evidence_by_id = {record["id"]: record for record in evidence}

    outcome_by_id = {str(item["id"]): item for item in outcomes}
    atoms_by_outcome: dict[str, list[Mapping[str, Any]]] = {}
    for index, atom in enumerate(obligation_atoms):
        outcome_id = str(atom["outcome_id"])
        _require_refs([outcome_id], outcome_ids, f"obligation_atoms[{index}].outcome_id")
        source_refs = set(atom["source_refs"])
        allowed_source_refs = set(outcome_by_id[outcome_id].get("source_refs", []))
        if not source_refs or source_refs - allowed_source_refs:
            raise ContractError("OBLIGATION_SOURCE_MISMATCH", f"obligation_atoms[{index}].source_refs",
                                "atomic obligation source_refs must be a non-empty subset of its parent outcome source_refs")
        atoms_by_outcome.setdefault(outcome_id, []).append(atom)
    if set(atoms_by_outcome) != outcome_ids:
        raise ContractError("OBLIGATION_COVERAGE_INCOMPLETE", "obligation_atoms",
                            f"every coarse outcome needs at least one atomic obligation; missing {sorted(outcome_ids-set(atoms_by_outcome))}")

    for index, unit in enumerate(units):
        _require_refs(unit["requirement_refs"], outcome_ids, f"units[{index}].requirement_refs")
        _require_refs(unit["locator_refs"], evidence_ids, f"units[{index}].locator_refs")
        if unit["existence"] == "EXPECTED_MISSING":
            cited = [evidence_by_id[ref] for ref in unit["locator_refs"] if ref in evidence_by_id]
            if not any(supports_expected_missing_evidence(record) for record in cited):
                raise ContractError(
                    "MISSING_UNIT_UNSUPPORTED", f"units[{index}].locator_refs",
                    "EXPECTED_MISSING requires a complete exact absence search, inventory, or explicit absent observation",
                )
    for index, relation in enumerate(relations):
        _require_refs(relation["source_unit_ids"], unit_ids, f"expected_relations[{index}].source_unit_ids")
        _require_refs(relation["target_unit_ids"], unit_ids, f"expected_relations[{index}].target_unit_ids")
        endpoint_ids = set(relation["source_unit_ids"]) | set(relation["target_unit_ids"])
        if len(endpoint_ids) < 2:
            raise ContractError("RELATION_NEEDS_TWO_ROLES", f"expected_relations[{index}]",
                                "a coordination relation must connect at least two semantic units")
        if relation["relation_form"] == "DIRECT_EDGE" and len(endpoint_ids) > 2:
            raise ContractError("MULTI_ENDPOINT_RELATION_REQUIRES_SHARED_JUSTIFICATION",
                                f"expected_relations[{index}].relation_form",
                                "a relation with more than two endpoints must declare an indivisible shared contract")
        shared_reason = relation["shared_constraint_reason"]
        if relation["relation_form"] == "INDIVISIBLE_SHARED" and not (isinstance(shared_reason, str) and shared_reason.strip()):
            raise ContractError("SHARED_RELATION_JUSTIFICATION_MISSING",
                                f"expected_relations[{index}].shared_constraint_reason",
                                "an indivisible shared relation requires a concrete reason why the constraint cannot be split into direct edges")
        if relation["relation_form"] == "DIRECT_EDGE" and shared_reason is not None:
            raise ContractError("SHARED_RELATION_JUSTIFICATION_UNEXPECTED",
                                f"expected_relations[{index}].shared_constraint_reason",
                                "DIRECT_EDGE must set shared_constraint_reason to null")
        unit_by_id = {unit["id"]: unit for unit in units}
        roles = {unit_by_id[uid]["role"] for uid in endpoint_ids}
        if len(roles) < 2:
            raise ContractError("RELATION_ROLE_COLLAPSE", f"expected_relations[{index}]",
                                "relation endpoints must represent distinct semantic roles; file identity may be shared")
        _require_refs(relation["requirement_refs"], outcome_ids, f"expected_relations[{index}].requirement_refs")
        _require_refs(relation["obligation_refs"], obligation_ids, f"expected_relations[{index}].obligation_refs")
        related_atoms = [next(atom for atom in obligation_atoms if atom["id"] == atom_id)
                         for atom_id in relation["obligation_refs"]]
        if {atom["outcome_id"] for atom in related_atoms} - set(relation["requirement_refs"]):
            raise ContractError("OBLIGATION_PARENT_MISMATCH", f"expected_relations[{index}].obligation_refs",
                                "relation obligation refs must belong to one of its requirement_refs")
        _require_refs(relation["support_refs"], evidence_ids, f"expected_relations[{index}].support_refs")
        if any(unit_by_id[uid]["semantic_role_class"] == "TEST_ARTIFACT" for uid in endpoint_ids):
            raise ContractError("TEST_ARTIFACT_RELATION_ENDPOINT", f"expected_relations[{index}]",
                                "tests and verifiers cannot be production coordination relation endpoints")
    for index, violation in enumerate(violations):
        _require_refs([violation["relation_id"]], relation_ids, f"violations[{index}].relation_id")
        _require_refs(violation["evidence_refs"], evidence_ids, f"violations[{index}].evidence_refs")
    for index, item in enumerate(coverage):
        _require_refs([item["outcome_id"]], outcome_ids, f"outcome_coverage[{index}].outcome_id")
        _require_refs(item["repair_supported"], violation_ids, f"outcome_coverage[{index}].repair_supported")
        _require_refs(item["preserve_required"], outcome_ids, f"outcome_coverage[{index}].preserve_required")
        _require_refs(item["unresolved"], outcome_ids, f"outcome_coverage[{index}].unresolved")
        satisfaction = item["preserve_current_satisfaction"]
        satisfaction_by_outcome = {entry["outcome_id"]: entry for entry in satisfaction}
        if len(satisfaction_by_outcome) != len(satisfaction):
            raise ContractError("DUPLICATE_ID", f"outcome_coverage[{index}].preserve_current_satisfaction",
                                "current satisfaction must be unique per preserved outcome")
        _require_refs(list(satisfaction_by_outcome), outcome_ids,
                      f"outcome_coverage[{index}].preserve_current_satisfaction")
        if set(satisfaction_by_outcome) != set(item["preserve_required"]):
            raise ContractError("PRESERVE_SATISFACTION_MISSING", f"outcome_coverage[{index}].preserve_current_satisfaction",
                                "each preserve_required outcome needs exactly one current_satisfaction record")
        for sat_index, entry in enumerate(satisfaction):
            _require_refs(entry["evidence_refs"], evidence_ids,
                          f"outcome_coverage[{index}].preserve_current_satisfaction[{sat_index}].evidence_refs")
            if entry["status"] in {"OBSERVED", "INFERRED"} and not entry["evidence_refs"]:
                raise ContractError("PRESERVE_EVIDENCE_MISSING", f"outcome_coverage[{index}].preserve_current_satisfaction[{sat_index}]",
                                    "OBSERVED/INFERRED satisfaction requires evidence")
        categories = int(bool(item["repair_supported"])) + int(bool(item["preserve_required"])) + int(bool(item["unresolved"]))
        if categories > 1:
            raise ContractError("OUTCOME_CATEGORY_CONFLICT", f"outcome_coverage[{index}]",
                                "split repaired, preserved and unresolved behavior into distinct outcomes")
    relation_by_id = {str(item["id"]): item for item in relations}
    graph_issues = []
    for atom_id in sorted(obligation_ids):
        if not any(atom_id in relation["obligation_refs"] for relation in relations):
            graph_issues.append(ValidationIssue("OBLIGATION_RELATION_MISSING", "expected_relations",
                                f"atomic obligation {atom_id} is not represented by an ExpectedRelation"))

    for unit_index, unit in enumerate(units):
        if unit["semantic_role_class"] == "PRODUCTION_CONSUMER" and unit["existence"] == "PRESENT":
            cited = [evidence_by_id[ref] for ref in unit["locator_refs"] if ref in evidence_by_id]
            if not any(item["kind"] == "SOURCE_SPAN" for item in cited):
                graph_issues.append(ValidationIssue("PRODUCTION_CONSUMER_UNEVIDENCED", f"units[{unit_index}].locator_refs",
                                    "a production consumer must cite a source span in the current snapshot"))

    def has_consumer_path(outcome_id: str) -> bool:
        config_ids = {unit["id"] for unit in units if unit["semantic_role_class"] == "CONFIGURATION_INPUT"
                      and outcome_id in unit["requirement_refs"]}
        terminal_ids = {unit["id"] for unit in units if unit["semantic_role_class"] in
                        {"GENERATED_STATE", "RUNTIME_CONSUMER", "OBSERVED_STATE"}
                        and outcome_id in unit["requirement_refs"]}
        if not config_ids or not terminal_ids:
            return True
        graph: dict[str, set[str]] = {}
        for relation in relations:
            if outcome_id not in relation["requirement_refs"]:
                continue
            for source_id in relation["source_unit_ids"]:
                graph.setdefault(source_id, set()).update(relation["target_unit_ids"])
        consumer_ids = {unit["id"] for unit in units if unit["semantic_role_class"] == "PRODUCTION_CONSUMER"
                        and outcome_id in unit["requirement_refs"]}
        for source_id in config_ids:
            stack = [(source_id, False, frozenset({source_id}))]
            while stack:
                current, saw_consumer, visited = stack.pop()
                if current in terminal_ids and saw_consumer:
                    return True
                for target_id in graph.get(current, ()):
                    if target_id not in visited:
                        stack.append((target_id, saw_consumer or target_id in consumer_ids,
                                      visited | {target_id}))
        return False

    supported_outcomes = {outcome_id for violation in violations if violation["causal_status"] == "SUPPORTED"
                          for outcome_id in relation_by_id[violation["relation_id"]]["requirement_refs"]}
    for outcome_id in sorted(supported_outcomes):
        if not has_consumer_path(outcome_id):
            graph_issues.append(ValidationIssue("PRODUCTION_PATH_INCOMPLETE", "expected_relations",
                                f"supported configuration outcome {outcome_id} lacks an evidenced path through a production consumer to its generated/runtime/observed state"))
    if graph_issues:
        first, *rest = graph_issues
        raise ContractError(first.code, first.path, first.message +
            ('; additional independent errors: ' + '; '.join(f'{i.code} at {i.path}: {i.message}' for i in rest) if rest else ''))
    for index, item in enumerate(coverage):
        outcome_id = item["outcome_id"]
        supported_violations = {
            str(violation["id"]) for violation in violations
            if violation["causal_status"] == "SUPPORTED"
            and outcome_id in relation_by_id[violation["relation_id"]]["requirement_refs"]
        }
        if supported_violations and not supported_violations.issubset(set(item["repair_supported"])):
            raise ContractError("OUTCOME_SUPPORTED_VIOLATION_UNCOVERED", f"outcome_coverage[{index}]",
                                f"supported violations {sorted(supported_violations)} linked through ExpectedRelation must be marked repair_supported")
    covered_outcomes = {item["outcome_id"] for item in coverage}
    if covered_outcomes != outcome_ids:
        raise ContractError("OUTCOME_COVERAGE_INCOMPLETE", "outcome_coverage",
                            f"missing outcomes: {sorted(outcome_ids-covered_outcomes)}")
    for index, record in enumerate(evidence):
        if record["snapshot_id"] != expected_snapshot:
            raise ContractError("SNAPSHOT_MISMATCH", f"evidence_refs[{index}].snapshot_id", "evidence belongs to another snapshot")
        if record["kind"] == "STATIC_SEARCH":
            expected_digest = static_search_content_digest(record["snapshot_id"], record["locator"], record["excerpt"])
            if record["content_digest"] != expected_digest:
                raise ContractError("EVIDENCE_DIGEST_MISMATCH", f"evidence_refs[{index}].content_digest",
                                    "static-search digest must bind its exact query, scope, results and snapshot")

    supported = [v for v in violations if v["causal_status"] == "SUPPORTED"]
    if any(v["support_basis"] == "OBSERVED_ONLY" for v in supported):
        raise ContractError("CAUSAL_CLAIM_UNSUPPORTED", "violations",
                            "an observed outcome alone cannot support a causal violation")
    unresolved = any(item["unresolved"] for item in coverage)
    if payload["status"] == "READY" and (not supported or unresolved or payload["evidence_gaps"]):
        raise ContractError("STATUS_INCONSISTENT", "status", "READY requires a supported violation and no unresolved outcome/evidence gap")
    if payload["status"] == "PARTIAL_READY" and not supported:
        raise ContractError("STATUS_INCONSISTENT", "status", "PARTIAL_READY requires at least one supported violation")
    if payload["status"] == "PARTIAL_READY" and not unresolved:
        raise ContractError("STATUS_INCONSISTENT", "status", "PARTIAL_READY requires at least one unresolved outcome")
    if payload["status"] == "NO_SUPPORTED_VIOLATION" and supported:
        raise ContractError("STATUS_INCONSISTENT", "status", "NO_SUPPORTED_VIOLATION cannot contain a supported violation")

    typed_units = tuple(
        SemanticUnit(u["id"], u["role"], u["kind"], u["semantic_role_class"], u["existence"], tuple(u["locator_refs"]), tuple(u["requirement_refs"]))
        for u in units
    )
    return DiagnosisPacket(
        schema_version=version,
        run_id=payload["run_id"],
        snapshot_id=payload["snapshot_id"],
        input_digest=payload["input_digest"],
        requirement_spec=dict(requirement),
        obligation_atoms=tuple(dict(item) for item in obligation_atoms),
        units=typed_units,
        expected_relations=tuple(dict(item) for item in relations),
        violations=tuple(dict(item) for item in violations),
        outcome_coverage=tuple(dict(item) for item in coverage),
        evidence_refs=tuple(dict(item) for item in evidence),
        evidence_gaps=tuple(dict(item) for item in payload["evidence_gaps"]),
        status=payload["status"],
        requirement_horizon=tuple(payload["requirement_horizon"]),
        observed_boundary=payload["observed_boundary"],
        inferred_causal_path=tuple(payload["inferred_causal_path"]),
        unverified_outcomes=tuple(payload["unverified_outcomes"]),
        raw=dict(payload),
    )


def _issue(code: str, path: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=path, message=message)


def validate_changeset(
    payload: Mapping[str, Any], diagnosis: DiagnosisPacket, snapshot: Snapshot | None
) -> ValidationResult:
    try:
        if not isinstance(payload, Mapping):
            raise ContractError("OBJECT_REQUIRED", "", "SemanticChangeSet must be a JSON object")
        version = payload.get("schema_version")
        if version != "ce.semantic_changeset.v1_1":
            raise ContractError("SCHEMA_VERSION_UNSUPPORTED", "schema_version", f"unsupported schema version {version!r}")
        _check_schema(payload, version)
    except ContractError as exc:
        return ValidationResult(False, (_issue(exc.code, exc.path, exc.message),))

    issues: list[ValidationIssue] = []
    current_snapshot = snapshot.snapshot_id if snapshot else diagnosis.snapshot_id
    if payload["snapshot_id"] != current_snapshot or diagnosis.snapshot_id != current_snapshot:
        issues.append(_issue("SNAPSHOT_MISMATCH", "snapshot_id", "plan, diagnosis and current snapshot must match"))
    if payload["input_digest"] != diagnosis.input_digest:
        issues.append(_issue("INPUT_DIGEST_MISMATCH", "input_digest", "plan and diagnosis input digests must match"))
    if payload["run_id"] != diagnosis.run_id:
        issues.append(_issue("RUN_CONTEXT_MISMATCH", "run_id", "plan and diagnosis run IDs must match"))
    if payload["diagnosis_digest"] != canonical_digest(diagnosis.raw):
        issues.append(_issue("DIAGNOSIS_DIGEST_MISMATCH", "diagnosis_digest", "plan must bind the exact diagnosis packet"))

    unit_by_id = {unit.id: unit for unit in diagnosis.units}
    violation_by_id = {item["id"]: item for item in diagnosis.violations}
    relation_by_id = {item["id"]: item for item in diagnosis.expected_relations}
    atom_by_id = {str(item["id"]): item for item in diagnosis.obligation_atoms}
    outcome_ids = {item["id"] for item in diagnosis.requirement_spec["outcomes"]}
    planned = set(payload["planned_violation_ids"])
    if planned - set(violation_by_id):
        issues.append(_issue("REFERENCE_NOT_FOUND", "planned_violation_ids", f"unknown violations: {sorted(planned-set(violation_by_id))}"))
    for vid in planned & set(violation_by_id):
        violation = violation_by_id[vid]
        if violation["causal_status"] != "SUPPORTED" or violation["support_basis"] == "OBSERVED_ONLY":
            issues.append(_issue("NEEDS_EVIDENCE", f"violations.{vid}", "causal target is not supported by source evidence"))

    change_ids = [change["id"] for change in payload["changes"]]
    change_set = set(change_ids)
    if len(change_set) != len(change_ids):
        issues.append(_issue("DUPLICATE_ID", "changes", "change IDs must be unique"))
    _check_dependency_cycles(payload["changes"], issues)
    postconditions = {item["id"] for item in payload["postconditions"]}
    covered_violations: set[str] = set()
    new_target_paths: dict[str, str] = {}
    for index, change in enumerate(payload["changes"]):
        path = f"changes[{index}]"
        refs = set(change["violation_refs"])
        if not refs:
            issues.append(_issue("CHANGE_WITHOUT_VIOLATION", f"{path}.violation_refs", "each change must restore a supported planned violation"))
        if refs - planned:
            issues.append(_issue("REFERENCE_NOT_FOUND", f"{path}.violation_refs", f"references not planned: {sorted(refs-planned)}"))
        covered_violations.update(refs)
        if not change["postcondition_refs"]:
            issues.append(_issue("CHANGE_WITHOUT_POSTCONDITION", f"{path}.postcondition_refs", "each change needs at least one check obligation"))
        if set(change["postcondition_refs"]) - postconditions:
            issues.append(_issue("REFERENCE_NOT_FOUND", f"{path}.postcondition_refs", "postcondition reference does not exist"))
        if set(change["depends_on"]) - change_set:
            issues.append(_issue("REFERENCE_NOT_FOUND", f"{path}.depends_on", "dependency change does not exist"))
        target = change["target"]
        if target["kind"] == "EXISTING":
            if not _safe_relative_path(target["path"]):
                issues.append(_issue("UNSAFE_TARGET_PATH", f"{path}.target.path", "existing target path must be normalized and relative"))
            if change["operation"] == "CREATE":
                issues.append(_issue("TARGET_OPERATION_MISMATCH", f"{path}.operation", "EXISTING target cannot use CREATE"))
            if target["snapshot_id"] != current_snapshot:
                issues.append(_issue("SNAPSHOT_MISMATCH", f"{path}.target.snapshot_id", "existing target is from another snapshot"))
            for unit_id in target["unit_refs"]:
                unit = unit_by_id.get(unit_id)
                if unit is None:
                    issues.append(_issue("REFERENCE_NOT_FOUND", f"{path}.target.unit_refs", f"unknown unit {unit_id!r}"))
                elif unit.existence != "PRESENT":
                    issues.append(_issue("TARGET_NOT_PRESENT", f"{path}.target.unit_refs", f"unit {unit_id!r} is {unit.existence}"))
            if snapshot is not None:
                actual_digest = snapshot.file_digests.get(target["path"])
                if actual_digest is None:
                    issues.append(_issue("TARGET_NOT_PRESENT", f"{path}.target.path", "existing path is absent from current snapshot"))
                elif actual_digest != target["content_digest"]:
                    issues.append(_issue("CONTENT_DIGEST_MISMATCH", f"{path}.target.content_digest", "existing target content changed"))
        else:
            if (not _safe_relative_path(target["proposed_path"])
                    or not _safe_relative_path(target["parent_path"], allow_dot=True)):
                issues.append(_issue("UNSAFE_TARGET_PATH", f"{path}.target.proposed_path", "new target paths must be normalized and relative"))
            else:
                expected_parent = PurePosixPath(target["proposed_path"]).parent.as_posix()
                if target["parent_path"] != expected_parent:
                    issues.append(_issue("NEW_TARGET_PARENT_MISMATCH", f"{path}.target.parent_path",
                                         "parent_path must be the immediate parent of proposed_path"))
            if change["operation"] != "CREATE":
                issues.append(_issue("TARGET_OPERATION_MISMATCH", f"{path}.operation", "NEW target must use CREATE"))
            if target["proposed_path"] in (snapshot.file_digests if snapshot else {}):
                issues.append(_issue("TARGET_PATH_CONFLICT", f"{path}.target.proposed_path", "new target path already exists"))
            if target["proposed_path"] in new_target_paths:
                issues.append(_issue("TARGET_PATH_CONFLICT", f"{path}.target.proposed_path",
                                     f"new target conflicts with change {new_target_paths[target['proposed_path']]!r}"))
            else:
                new_target_paths[target["proposed_path"]] = change["id"]
            _require_new_target_refs(target, unit_by_id, diagnosis, snapshot, path, issues)

    if covered_violations != planned:
        issues.append(_issue("VIOLATION_COVERAGE_INCOMPLETE", "changes", f"uncovered planned violations: {sorted(planned-covered_violations)}"))

    repaired_atoms: set[str] = set()
    for index, condition in enumerate(payload["postconditions"]):
        _require_refs_for_result(condition["requirement_refs"], outcome_ids, f"postconditions[{index}].requirement_refs", issues)
        _require_refs_for_result(condition["relation_refs"], set(relation_by_id), f"postconditions[{index}].relation_refs", issues)
        _require_refs_for_result(condition["obligation_refs"], set(atom_by_id),
                                 f"postconditions[{index}].obligation_refs", issues)
        relation_atoms = {atom_id for relation_id in condition["relation_refs"]
                          if relation_id in relation_by_id
                          for atom_id in relation_by_id[relation_id]["obligation_refs"]}
        for atom_id in condition["obligation_refs"]:
            atom = atom_by_id.get(atom_id)
            if atom and atom["outcome_id"] not in condition["requirement_refs"]:
                issues.append(_issue("OBLIGATION_PARENT_MISMATCH", f"postconditions[{index}].obligation_refs",
                                     f"{atom_id} does not belong to the cited requirement outcomes"))
            if atom_id not in relation_atoms:
                issues.append(_issue("OBLIGATION_RELATION_MISMATCH", f"postconditions[{index}].obligation_refs",
                                     f"{atom_id} is not represented by a cited relation"))
        repaired_atoms.update(atom_id for atom_id in condition["obligation_refs"] if atom_id in atom_by_id)
    preserve_by_id: dict[str, Mapping[str, Any]] = {}
    preserve_outcomes: set[str] = set()
    for index, constraint in enumerate(payload["preserve_constraints"]):
        if constraint["id"] in preserve_by_id:
            issues.append(_issue("DUPLICATE_ID", f"preserve_constraints[{index}].id", "preserve constraint IDs must be unique"))
        preserve_by_id[constraint["id"]] = constraint
        _require_refs_for_result([constraint["outcome_id"]], outcome_ids, f"preserve_constraints[{index}].outcome_id", issues)
        _require_refs_for_result(constraint["obligation_refs"], set(atom_by_id),
                                 f"preserve_constraints[{index}].obligation_refs", issues)
        for atom_id in constraint["obligation_refs"]:
            if atom_id in atom_by_id and atom_by_id[atom_id]["outcome_id"] != constraint["outcome_id"]:
                issues.append(_issue("OBLIGATION_PARENT_MISMATCH", f"preserve_constraints[{index}].obligation_refs",
                                     f"{atom_id} does not belong to outcome {constraint['outcome_id']}"))
        repaired_atoms.update(atom_id for atom_id in constraint["obligation_refs"] if atom_id in atom_by_id)
        preserve_outcomes.add(constraint["outcome_id"])
        _require_refs_for_result(constraint["evidence_refs"], {item["id"] for item in diagnosis.evidence_refs},
                                 f"preserve_constraints[{index}].evidence_refs", issues)
        if constraint["current_satisfaction"] in {"OBSERVED", "INFERRED"} and not constraint["evidence_refs"]:
            issues.append(_issue("PRESERVE_EVIDENCE_MISSING", f"preserve_constraints[{index}]", "OBSERVED/INFERRED preservation requires evidence"))
    open_by_id: dict[str, Mapping[str, Any]] = {}
    open_outcomes: set[str] = set()
    for index, obligation in enumerate(payload["open_obligations"]):
        if obligation["id"] in open_by_id:
            issues.append(_issue("DUPLICATE_ID", f"open_obligations[{index}].id", "open obligation IDs must be unique"))
        open_by_id[obligation["id"]] = obligation
        _require_refs_for_result([obligation["outcome_id"]], outcome_ids, f"open_obligations[{index}].outcome_id", issues)
        _require_refs_for_result(obligation["obligation_refs"], set(atom_by_id),
                                 f"open_obligations[{index}].obligation_refs", issues)
        for atom_id in obligation["obligation_refs"]:
            if atom_id in atom_by_id and atom_by_id[atom_id]["outcome_id"] != obligation["outcome_id"]:
                issues.append(_issue("OBLIGATION_PARENT_MISMATCH", f"open_obligations[{index}].obligation_refs",
                                     f"{atom_id} does not belong to outcome {obligation['outcome_id']}"))
        repaired_atoms.update(atom_id for atom_id in obligation["obligation_refs"] if atom_id in atom_by_id)
        open_outcomes.add(obligation["outcome_id"])

    all_atoms = set(atom_by_id)
    if repaired_atoms != all_atoms:
        issues.append(_issue("OBLIGATION_NOT_COVERED", "postconditions/preserve_constraints/open_obligations",
                             f"uncovered atomic obligations: {sorted(all_atoms-repaired_atoms)}"))
    for violation_id in planned & set(violation_by_id):
        relation = relation_by_id.get(violation_by_id[violation_id]["relation_id"], {})
        relation_atoms = set(relation.get("obligation_refs", []))
        repaired_for_relation = {atom_id for condition in payload["postconditions"]
                                 if relation.get("id") in condition.get("relation_refs", [])
                                 for atom_id in condition.get("obligation_refs", [])}
        if not relation_atoms.issubset(repaired_for_relation):
            issues.append(_issue("PLANNED_VIOLATION_OBLIGATION_UNCOVERED", f"violations.{violation_id}",
                                 f"planned violation requires postconditions for atoms {sorted(relation_atoms-repaired_for_relation)}"))
    for index, coverage in enumerate(payload["outcome_coverage"]):
        outcome_id = coverage["outcome_id"]
        if outcome_id not in outcome_ids:
            issues.append(_issue("REFERENCE_NOT_FOUND", f"outcome_coverage[{index}].outcome_id", f"unknown outcome {outcome_id!r}"))
        if set(coverage["repair_supported"]) - planned:
            issues.append(_issue("REFERENCE_NOT_FOUND", f"outcome_coverage[{index}].repair_supported", "only planned violations may be marked repaired"))
        if set(coverage["preserve_required"]) - outcome_ids or set(coverage["unresolved"]) - outcome_ids:
            issues.append(_issue("REFERENCE_NOT_FOUND", f"outcome_coverage[{index}]", "outcome coverage contains unknown IDs"))
        if not (coverage["repair_supported"] or coverage["preserve_required"] or coverage["unresolved"]):
            issues.append(_issue("OUTCOME_NOT_COVERED", f"outcome_coverage[{index}]", "each outcome must be repaired, preserved, or open"))
        if set(coverage["preserve_required"]) - preserve_outcomes:
            issues.append(_issue("PRESERVE_CONSTRAINT_MISSING", f"outcome_coverage[{index}].preserve_required", "preserved outcomes need explicit preservation constraints"))
        if set(coverage["unresolved"]) - open_outcomes:
            issues.append(_issue("OPEN_OBLIGATION_MISSING", f"outcome_coverage[{index}].unresolved", "unresolved outcomes need explicit open obligations"))

    covered_outcomes = [item["outcome_id"] for item in payload["outcome_coverage"]]
    if len(covered_outcomes) != len(set(covered_outcomes)):
        issues.append(_issue("DUPLICATE_ID", "outcome_coverage", "each requirement outcome must have exactly one coverage record"))
    missing_outcomes = outcome_ids - set(covered_outcomes)
    if missing_outcomes:
        issues.append(_issue("OUTCOME_NOT_COVERED", "outcome_coverage", f"missing outcomes: {sorted(missing_outcomes)}"))

    if payload["status"] == "READY_PARTIAL" and not payload["open_obligations"]:
        issues.append(_issue("STATUS_INCONSISTENT", "status", "READY_PARTIAL requires open obligations"))
    if payload["status"] == "READY_PARTIAL" and not any(c["unresolved"] for c in payload["outcome_coverage"]):
        issues.append(_issue("STATUS_INCONSISTENT", "status", "READY_PARTIAL requires an unresolved outcome"))
    if payload["status"] == "READY" and (payload["open_obligations"] or any(c["unresolved"] for c in payload["outcome_coverage"])):
        issues.append(_issue("STATUS_INCONSISTENT", "status", "READY requires all outcomes resolved or preserved"))
    if payload['status'] == 'READY' and payload.get('evidence_gaps'):
        issues.append(_issue('STATUS_INCONSISTENT', 'evidence_gaps', 'READY cannot contain unresolved planning evidence gaps'))
    if payload["status"] in {"READY", "READY_PARTIAL"} and not planned:
        issues.append(_issue("STATUS_INCONSISTENT", "planned_violation_ids", "ready plans require a supported planned violation"))
    if payload["status"] == "NEEDS_EVIDENCE":
        if planned:
            issues.append(_issue("STATUS_INCONSISTENT", "planned_violation_ids",
                "NEEDS_EVIDENCE: planned_violation_ids must be empty; supported diagnosis violations are not yet executable repairs. Keep unsupported repair obligations open and name the missing evidence; do not invent changes to cover these IDs."))
        if payload["changes"]:
            issues.append(_issue("STATUS_INCONSISTENT", "changes",
                "NEEDS_EVIDENCE: changes must be empty. Retain unresolved obligations and evidence gaps, or use a ready status only when the existing evidence supports an executable plan."))

    return ValidationResult(not issues, tuple(issues), dict(payload))


def _require_refs_for_result(refs: list[str], allowed: set[str], path: str, issues: list[ValidationIssue]) -> None:
    missing = sorted(set(refs) - allowed)
    if missing:
        issues.append(_issue("REFERENCE_NOT_FOUND", path, f"unknown references: {', '.join(missing)}"))


def _require_new_target_refs(
    target: Mapping[str, Any], unit_by_id: Mapping[str, SemanticUnit], diagnosis: DiagnosisPacket,
    snapshot: Snapshot | None,
    path: str, issues: list[ValidationIssue]
) -> None:
    evidence_ids = {item["id"] for item in diagnosis.evidence_refs}
    missing_roles: list[SemanticUnit] = []
    for unit_id in target["anchor_unit_refs"]:
        unit = unit_by_id.get(unit_id)
        if unit is None:
            issues.append(_issue("REFERENCE_NOT_FOUND", f"{path}.target.anchor_unit_refs", f"unknown unit {unit_id!r}"))
        elif unit.existence != "PRESENT":
            issues.append(_issue("NEW_TARGET_ANCHOR_INVALID", f"{path}.target.anchor_unit_refs", f"anchor {unit_id!r} is not present"))
    for unit_id in target["missing_unit_refs"]:
        unit = unit_by_id.get(unit_id)
        if unit is None:
            issues.append(_issue("REFERENCE_NOT_FOUND", f"{path}.target.missing_unit_refs", f"unknown unit {unit_id!r}"))
        elif unit.existence != "EXPECTED_MISSING":
            issues.append(_issue("NEW_TARGET_MISSING_ROLE_INVALID", f"{path}.target.missing_unit_refs", f"unit {unit_id!r} is not EXPECTED_MISSING"))
        else:
            missing_roles.append(unit)
    if missing_roles and not any(unit.role.strip().casefold() == target["expected_role"].strip().casefold() for unit in missing_roles):
        issues.append(_issue("NEW_TARGET_ROLE_MISMATCH", f"{path}.target.expected_role",
                             "expected_role must match a referenced EXPECTED_MISSING unit"))
    elif not missing_roles:
        issues.append(_issue("NEW_TARGET_MISSING_ROLE_INVALID", f"{path}.target.missing_unit_refs",
                             "NEW targets require an EXPECTED_MISSING semantic role"))
    _require_refs_for_result(target["justification_refs"], evidence_ids, f"{path}.target.justification_refs", issues)
    if snapshot is None:
        issues.append(_issue("TARGET_PARENT_UNCONFIRMED", f"{path}.target.parent_path", "NEW target parent must be checked against a source snapshot"))
    else:
        directories = {"."}
        for file_path in snapshot.file_digests:
            parent = PurePosixPath(file_path).parent
            while parent != PurePosixPath("."):
                directories.add(parent.as_posix())
                parent = parent.parent
        if target["parent_path"] not in directories:
            issues.append(_issue("TARGET_PARENT_NOT_PRESENT", f"{path}.target.parent_path", "NEW target parent does not exist in the current snapshot"))


def _safe_relative_path(value: str, *, allow_dot: bool = False) -> bool:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        return False
    if value == ".":
        return allow_dot
    return path.as_posix() == value and bool(path.parts) and all(part not in {"", ".", ".."} for part in path.parts)


def _check_dependency_cycles(changes: list[Mapping[str, Any]], issues: list[ValidationIssue]) -> None:
    graph = {str(item["id"]): set(item["depends_on"]) for item in changes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dependency in graph.get(node, set()):
            if dependency in graph and visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    if any(visit(node) for node in sorted(graph)):
        issues.append(_issue("DEPENDENCY_CYCLE", "changes", "change dependencies must form a directed acyclic graph"))
