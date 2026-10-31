from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml


class GroundTruthError(ValueError):
    pass


def load_private_gt(path: str | Path) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GroundTruthError(f"cannot read private GT: {exc}") from exc
    if not isinstance(value, Mapping):
        raise GroundTruthError("private GT must be a YAML mapping")
    return value


def _list_at(primary: Mapping[str, Any], nested: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = primary.get(key)
    if value is None:
        value = nested.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise GroundTruthError(f"{key} must be an array of objects")
    return [dict(item) for item in value]


def _canonicalize_gt_endpoints(relations: list[Mapping[str, Any]], units: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Resolve an exact unit-ID prefix in legacy descriptive endpoint strings."""
    unit_ids = sorted({str(unit["id"]) for unit in units if unit.get("id") is not None}, key=len, reverse=True)

    def canonical(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        for unit_id in unit_ids:
            if value == unit_id:
                return unit_id
            if value.startswith(unit_id) and len(value) > len(unit_id) \
                    and value[len(unit_id)] in " \t\r\n:([":
                return unit_id
        return value

    result = []
    for relation in relations:
        item = dict(relation)
        for key in ("source", "target", "source_unit_ids", "target_unit_ids"):
            if key in item:
                value = item[key]
                item[key] = [canonical(endpoint) for endpoint in value] if isinstance(value, list) else canonical(value)
        result.append(item)
    return result


def normalize_gt(private_case: Mapping[str, Any], release_id: str, *,
                release_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    metadata = release_metadata if isinstance(release_metadata, Mapping) else {}
    if private_case.get("case_id") is not None and metadata.get("case_id") not in {None, private_case.get("case_id")}:
        raise GroundTruthError("release metadata case_id does not match private GT")
    coordination = private_case.get("coordination")
    if not isinstance(coordination, Mapping):
        coordination = {}
    classification = private_case.get("classification")
    if not isinstance(classification, Mapping):
        classification = private_case.get("classification_boundary", {})
    if not isinstance(classification, Mapping):
        classification = {}
    primary_class = (classification.get("primary_class")
                     or private_case.get("primary_class")
                     or metadata.get("primary_class"))
    release_class = release_id.split("-", 1)[0]
    if primary_class is None and release_class in {"R1", "R2", "R3", "R4", "R5"}:
        primary_class = release_class
    requirement = private_case.get("requirement")
    requirement_text = requirement if isinstance(requirement, str) else (
        requirement.get("statement") if isinstance(requirement, Mapping) else None)
    if not isinstance(requirement_text, str):
        requirement_text = private_case.get("user_request")

    units = _list_at(private_case, coordination, "relevant_units")
    if not units:
        units = _list_at(private_case, coordination, "units")
    raw_relations = private_case.get("expected_relations", coordination.get("expected_relations", []))
    unstructured_relations = []
    if isinstance(raw_relations, list):
        relations = [dict(item) for item in raw_relations if isinstance(item, Mapping)]
        unstructured_relations = [item for item in raw_relations if not isinstance(item, Mapping)]
    else:
        relations = []
        unstructured_relations = [raw_relations]
    # Prose is retained for adjudication, never converted into invented endpoints/GT IDs.
    for key in ("expected_relation", "violated_relation", "broken_relation"):
        if key in private_case:
            unstructured_relations.append({key: private_case[key]})
    relations = _canonicalize_gt_endpoints(relations, units)
    violations = _list_at(private_case, coordination, "violated_relations")
    canonical_violations = []
    for item in violations:
        relation_id = item.get("relation_id", item.get("relation"))
        canonical_violations.append({**item, "relation_id": relation_id})

    policy = private_case.get("evaluation_policy")
    if not isinstance(policy, Mapping):
        policy = {}
    eligibility = private_case.get("full_recall_eligible", policy.get("full_recall_eligible"))
    if not isinstance(eligibility, bool):
        eligibility = None
    oracle_scope = private_case.get("oracle_scope")
    oracle_limit = private_case.get("oracle_limit")
    if oracle_limit is None:
        oracle_limit = private_case.get("classification_boundary", {}).get("not_observed") if isinstance(private_case.get("classification_boundary"), Mapping) else None
    oracle_boundary = metadata.get("oracle_boundary", private_case.get("oracle_boundary"))
    if oracle_limit is None and isinstance(oracle_boundary, str):
        oracle_limit = oracle_boundary
    ambiguity = []
    if eligibility is None:
        ambiguity.append("full recall eligibility is not explicitly stated")
    if not isinstance(policy.get("relation_precision"), bool):
        ambiguity.append("relation precision eligibility is not explicit")
    if not isinstance(policy.get("relation_recall"), bool):
        ambiguity.append("relation recall policy is not explicit")
    if unstructured_relations:
        ambiguity.append("unstructured relationship prose retained; endpoint/granularity adjudication required")
    return {
        "schema_version": "ce.normalized_gt.v1",
        "case_id": private_case.get("case_id") or metadata.get("case_id"), "release_id": release_id,
        "primary_class": primary_class,
        "fault_boundary": metadata.get("fault_boundary", private_case.get("fault_boundary")),
        "observed_depth": metadata.get("observed_depth", private_case.get("observed_depth")),
        "oracle_boundary": oracle_boundary,
        "requirement_text": requirement_text,
        "units": units,
        "expected_relations": relations,
        "violations": canonical_violations,
        "oracle_scope": oracle_scope,
        "oracle_limit": oracle_limit,
        "full_recall_eligible": eligibility,
        "precision_eligible": policy.get("relation_precision") if isinstance(policy.get("relation_precision"), bool) else None,
        "recall_eligible": policy.get("relation_recall") if isinstance(policy.get("relation_recall"), bool) else eligibility,
        "ambiguity": ambiguity,
        "unstructured_relation_annotations": unstructured_relations,
    }


def project_statuses(run_trace: Mapping[str, Any]) -> dict[str, str]:
    """Keep method, execution, public and independent-score states separate."""
    status = str(run_trace.get("status") or "NOT_RUN")
    feedback = run_trace.get("last_feedback") or run_trace.get("feedback") or {}
    semantic = run_trace.get("public_semantic_status")
    if semantic is None:
        semantic = feedback.get("semantic_outcome")
    build = feedback.get("build_state")
    if status in {"INFRASTRUCTURE_ERROR", "ENVIRONMENT_ERROR", "RESPONSE_UNKNOWN"}:
        execution = "ERROR"
    elif build == "PASSED" or status in {"PUBLIC_EXECUTION_COMPLETE", "PUBLIC_REVIEW_COMPLETE", "COMPLETE"}:
        execution = "COMPLETE"
    elif status == "NOT_RUN":
        execution = "NOT_RUN"
    else:
        execution = "INCOMPLETE"
    if semantic == "PUBLIC_REVIEW_PASS":
        public = "PASS"
    elif semantic == "PUBLIC_REVIEW_FAIL" or status == "BEHAVIOR_FAIL":
        public = "FAIL"
    elif status in {"PUBLIC_EXECUTION_COMPLETE", "PUBLIC_REVIEW_COMPLETE", "COMPLETE", "ROUND_LIMIT",
                    "EVIDENCE_GAP", "DELIVERY_INCOMPLETE", "OBSERVATION_INSUFFICIENT"} or semantic == "UNKNOWN":
        public = "UNKNOWN"
    else:
        public = "NOT_APPLICABLE"
    if status in {"INVALID_OUTPUT", "FORMAT_INVALID"}:
        method = "INVALID_OUTPUT"
    elif status in {"INVALID_PLAN", "PLAN_INVALID"}:
        method = "INVALID_PLAN"
    elif status in {"BUDGET_EXHAUSTED", "INFRASTRUCTURE_ERROR", "ENVIRONMENT_ERROR", "RESPONSE_UNKNOWN"}:
        method = status
    elif public == "UNKNOWN" and execution == "COMPLETE":
        method = "SEMANTIC_UNKNOWN"
    else:
        method = status
    independent = str(run_trace.get("independent_score_status") or "NOT_RUN")
    return {"method_status": method, "execution_status": execution,
            "public_status": public, "independent_score_status": independent}


def score_run(run_trace: Mapping[str, Any], normalized_gt: Mapping[str, Any]) -> dict[str, Any]:
    status = run_trace.get("status")
    projection = project_statuses(run_trace)
    precision_eligible = normalized_gt.get("precision_eligible")
    recall_eligible = normalized_gt.get("recall_eligible")
    record: dict[str, Any] = {
        "case_id": normalized_gt.get("case_id"), "release_id": normalized_gt.get("release_id"),
        "primary_class": normalized_gt.get("primary_class"),
        "fault_boundary": normalized_gt.get("fault_boundary"),
        "observed_depth": normalized_gt.get("observed_depth"),
        "oracle_boundary": normalized_gt.get("oracle_boundary"),
        "oracle_limit": normalized_gt.get("oracle_limit"),
        "status": status, "denominator_included": True, "failure_kind": None,
        **projection,
        "precision": None, "recall": None,
        "expected_relation_precision": None, "expected_relation_recall": None,
        "violation_precision": None, "violation_recall": None,
        "precision_eligible": precision_eligible, "recall_eligible": recall_eligible,
        "eligibility_status": _eligibility_status(precision_eligible, recall_eligible),
        "scoring_status": "SCORED",
        "ambiguous_relation_matches": [],
    }
    if status == "INFRASTRUCTURE_ERROR":
        record["failure_kind"] = "INFRASTRUCTURE"
        return record
    if status == "BUDGET_EXHAUSTED":
        record["failure_kind"] = "BUDGET"
        return record
    if status in {"INVALID_OUTPUT", "INVALID_PLAN", "FORMAT_INVALID", "PLAN_INVALID"}:
        record["failure_kind"] = "METHOD_OUTPUT"
        record["scoring_status"] = "METHOD_FAILURE"
        return record
    if (projection["method_status"] in {"SEMANTIC_UNKNOWN", "EVIDENCE_GAP", "DELIVERY_INCOMPLETE",
                                          "ROUND_LIMIT", "OBSERVATION_INSUFFICIENT"}
            or projection["public_status"] == "UNKNOWN"):
        record["scoring_status"] = "SEMANTIC_UNKNOWN"
        record["failure_kind"] = record["failure_kind"] or "SEMANTIC_UNKNOWN"
        return record
    diagnosis = run_trace.get("diagnosis") or {}
    predicted_relations = [item for item in diagnosis.get("expected_relations", [])
                           if isinstance(item, Mapping) and item.get("id")]
    predicted_units = {str(item.get("id")): item for item in diagnosis.get("units", [])
                       if isinstance(item, Mapping) and item.get("id")}
    reference_relations = [item for item in normalized_gt.get("expected_relations", [])
                           if isinstance(item, Mapping) and item.get("id")]
    reference_units = {str(item.get("id")): item for item in normalized_gt.get("units", [])
                       if isinstance(item, Mapping) and item.get("id")}
    relation_matches, ambiguous = _match_relations(
        predicted_relations, predicted_units, reference_relations, reference_units)
    record["ambiguous_relation_matches"] = ambiguous

    predicted_violation_ids = {
        str(item.get("relation_id")) for item in diagnosis.get("violations", [])
        if isinstance(item, Mapping) and item.get("relation_id") is not None
    }
    reference_violation_ids = {
        str(item.get("relation_id", item.get("relation")))
        for item in normalized_gt.get("violations", [])
        if isinstance(item, Mapping) and item.get("relation_id", item.get("relation")) is not None
    }
    predicted_violation_relations = [item for item in predicted_relations
                                     if str(item["id"]) in predicted_violation_ids]
    reference_violation_relations = [item for item in reference_relations
                                     if str(item["id"]) in reference_violation_ids]
    violation_matches = {
        predicted_id: reference_id for predicted_id, reference_id in relation_matches.items()
        if predicted_id in predicted_violation_ids and reference_id in reference_violation_ids
    }
    expected_counts = {"predicted": len(predicted_relations), "reference": len(reference_relations),
                       "matched": len(relation_matches)}
    violation_counts = {"predicted": len(predicted_violation_relations),
                        "reference": len(reference_violation_relations), "matched": len(violation_matches)}
    record["expected_relation_counts"] = expected_counts
    record["violation_relation_counts"] = violation_counts
    record["relation_matches"] = [{"predicted_relation_id": predicted, "reference_relation_id": reference}
                                   for predicted, reference in sorted(relation_matches.items())]
    ambiguous_predicted_ids = {str(item["predicted_relation_id"]) for item in ambiguous}
    ambiguous_reference_ids = {str(ref["relation_id"])
                               for item in ambiguous for ref in item.get("reference_candidates", [])}
    if ambiguous:
        record["scoring_status"] = "AMBIGUOUS_RELATION_MATCH"
    if precision_eligible is True and not ambiguous:
        record["expected_relation_precision"] = _ratio(expected_counts["matched"], expected_counts["predicted"], expected_counts["reference"])
    if recall_eligible is True and not ambiguous:
        record["expected_relation_recall"] = _ratio(expected_counts["matched"], expected_counts["reference"], expected_counts["predicted"])
    violation_ambiguity = bool(ambiguous_predicted_ids & predicted_violation_ids
                               or ambiguous_reference_ids & reference_violation_ids)
    if violation_ambiguity:
        record["scoring_status"] = "AMBIGUOUS_VIOLATION_MATCH"
    if precision_eligible is True and not violation_ambiguity:
        record["violation_precision"] = _ratio(violation_counts["matched"], violation_counts["predicted"], violation_counts["reference"])
    if recall_eligible is True and not violation_ambiguity:
        record["violation_recall"] = _ratio(violation_counts["matched"], violation_counts["reference"], violation_counts["predicted"])
    record["precision"] = record["violation_precision"]
    record["recall"] = record["violation_recall"]
    return record


def _eligibility_status(precision: Any, recall: Any) -> str:
    if precision is None and recall is None:
        return "PRECISION_AND_RECALL_UNADJUDICATED"
    if precision is None:
        return "PRECISION_UNADJUDICATED"
    if recall is None:
        return "RECALL_UNADJUDICATED"
    return "ELIGIBLE"


def _ratio(matched: int, denominator: int, opposite: int) -> float:
    if denominator:
        return matched / denominator
    return 1.0 if opposite == 0 else 0.0


_STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "between", "by", "for", "from", "in", "into",
    "is", "it", "of", "on", "or", "the", "to", "with", "must", "should", "shall",
    "required", "requirement", "expected", "relation", "contract", "value", "values",
    "source", "target", "object", "field", "binding", "realized", "realize", "correctly",
}


def _tokens(value: Any) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    tokens = {token.casefold() for token in re.findall(r"[A-Za-z0-9]+", text)}
    aliases = {"chosen": "selected", "flows": "flow", "flowing": "flow", "propagates": "propagate",
               "propagated": "propagate", "values": "value", "pods": "pod", "deployments": "deployment"}
    return {aliases.get(token, token) for token in tokens} - _STOPWORDS


def _similarity(left: Any, right: Any) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _unit_roles(endpoint: Any, units: Mapping[str, Mapping[str, Any]]) -> list[str]:
    values = endpoint if isinstance(endpoint, list) else [endpoint]
    roles: list[str] = []
    for value in values:
        item = units.get(str(value))
        if item is None:
            if isinstance(value, str) and value.strip():
                roles.append(value)
            continue
        role = item.get("role") or item.get("semantic_role") or item.get("kind")
        if isinstance(role, str) and role.strip():
            roles.append(role)
    return roles


def _endpoint_values(relation: Mapping[str, Any], side: str) -> Any:
    for key in (f"{side}_unit_ids", side):
        if key in relation:
            return relation[key]
    return None


def _endpoint_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    pairs = sorted(((_similarity(a, b), i, j) for i, a in enumerate(left) for j, b in enumerate(right)),
                   key=lambda item: (-item[0], item[1], item[2]))
    used_left: set[int] = set()
    used_right: set[int] = set()
    total = 0.0
    for score, i, j in pairs:
        if i not in used_left and j not in used_right:
            used_left.add(i)
            used_right.add(j)
            total += score
    return total / max(len(left), len(right))


def _relation_similarity(predicted: Mapping[str, Any], predicted_units: Mapping[str, Mapping[str, Any]],
                         reference: Mapping[str, Any], reference_units: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    source = _endpoint_similarity(_unit_roles(_endpoint_values(predicted, "source"), predicted_units),
                                  _unit_roles(_endpoint_values(reference, "source"), reference_units))
    target = _endpoint_similarity(_unit_roles(_endpoint_values(predicted, "target"), predicted_units),
                                  _unit_roles(_endpoint_values(reference, "target"), reference_units))
    contract = _similarity(predicted.get("expected_contract", predicted.get("expected_coordination", "")),
                           reference.get("expected_contract", reference.get("expected_coordination", "")))
    p_condition, r_condition = predicted.get("condition"), reference.get("condition")
    condition = _similarity(p_condition, r_condition) if p_condition and r_condition else (1.0 if not p_condition and not r_condition else 0.65)
    p_mechanism = predicted.get("mechanism", predicted.get("type", ""))
    r_mechanism = reference.get("mechanism", reference.get("type", ""))
    mechanism = 1.0 if _tokens(p_mechanism) & _tokens(r_mechanism) else 0.0
    score = source * 0.34 + target * 0.34 + contract * 0.20 + condition * 0.08 + mechanism * 0.04
    return {"score": score, "source": source, "target": target, "contract": contract,
            "condition": condition, "mechanism": mechanism}


def _match_relations(predicted: list[Mapping[str, Any]], predicted_units: Mapping[str, Mapping[str, Any]],
                     reference: list[Mapping[str, Any]], reference_units: Mapping[str, Mapping[str, Any]],
                     *, threshold: float = 0.48, ambiguity_margin: float = 0.06
                     ) -> tuple[dict[str, str], list[dict[str, Any]]]:
    scored: list[tuple[float, str, str, dict[str, float]]] = []
    ambiguous: list[dict[str, Any]] = []
    for pred in predicted:
        pid = str(pred["id"])
        candidates = []
        for ref in reference:
            rid = str(ref["id"])
            components = _relation_similarity(pred, predicted_units, ref, reference_units)
            scored.append((components["score"], pid, rid, components))
            if (components["score"] >= threshold and components["source"] >= 0.30
                    and components["target"] >= 0.30 and components["contract"] >= 0.18):
                candidates.append((components["score"], rid))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < ambiguity_margin:
            ambiguous.append({"predicted_relation_id": pid,
                              "reference_candidates": [{"relation_id": rid, "similarity": round(score, 4)}
                                                       for score, rid in candidates[:2]],
                              "reason": "top semantic matches are too close to choose reliably"})
    ambiguous_ids = {item["predicted_relation_id"] for item in ambiguous}
    matches: dict[str, str] = {}
    used_reference: set[str] = set()
    for score, pid, rid, components in sorted(scored, key=lambda item: (-item[0], item[1], item[2])):
        if (score < threshold or pid in ambiguous_ids or pid in matches or rid in used_reference
                or components["source"] < 0.30 or components["target"] < 0.30
                or components["contract"] < 0.18):
            continue
        matches[pid] = rid
        used_reference.add(rid)
    return matches, ambiguous
