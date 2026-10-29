from __future__ import annotations

from typing import Any, Mapping


SUPPORTED_CONSUMER_CONTRACTS = {"contractedge.topomen_plan.v1"}


def translate_for_topomen(changeset: Mapping[str, Any], consumer_version: str, *,
                          diagnosis: Mapping[str, Any] | None = None,
                          public_capabilities: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if consumer_version not in SUPPORTED_CONSUMER_CONTRACTS:
        raise ValueError(f"unsupported TopoMen interchange contract: {consumer_version!r}")
    operations = []
    for item in changeset.get("changes", []):
        operations.append({
            "change_id": item["id"], "target": dict(item["target"]),
            "operation": item["operation"], "semantic_action": item["semantic_action"],
            "intent": item["intent"], "violation_refs": list(item["violation_refs"]),
            "postcondition_refs": list(item["postcondition_refs"]),
            "depends_on": list(item["depends_on"]),
        })
    result = {
        "schema_version": consumer_version,
        "plan_id": changeset.get("diagnosis_digest", "") + ":" + str(changeset.get("run_id", "")),
        "run_id": changeset.get("run_id"), "snapshot_id": changeset.get("snapshot_id"),
        "input_digest": changeset.get("input_digest"), "goal": changeset.get("goal"),
        "planned_violation_ids": list(changeset.get("planned_violation_ids", [])),
        "operations": operations,
        "postconditions": [dict(item) for item in changeset.get("postconditions", [])],
        "preserve_constraints": [dict(item) for item in changeset.get("preserve_constraints", [])],
        "open_obligations": [dict(item) for item in changeset.get("open_obligations", [])],
        "production_path_review": [dict(item) for item in changeset.get("production_path_review", [])],
        "status": changeset.get("status"),
        "consumer_acceptance": "UNVERIFIED",
    }
    if 'preservation_impact' in changeset:
        result['preservation_impact'] = [dict(r) for r in changeset['preservation_impact']]
    if diagnosis is not None:
        violations = [v for v in diagnosis.get('violations', []) if v['id'] in changeset.get('planned_violation_ids', [])]
        relation_ids = {v['relation_id'] for v in violations}
        relations = [r for r in diagnosis.get('expected_relations', []) if r['id'] in relation_ids]
        support_ids = {ref for r in relations for ref in r.get('support_refs', [])}
        support_ids.update(ref for v in violations for ref in v.get('evidence_refs', []))
        result['coordination_context'] = {
            'diagnosis_snapshot': diagnosis.get('snapshot_id'),
            'obligation_atoms': [dict(a) for a in diagnosis.get('obligation_atoms', [])],
            'expected_relations': [dict(r) for r in relations],
            'violations': [dict(v) for v in violations],
            'source_support': [dict(e) for e in diagnosis.get('evidence_refs', [])
                               if e['id'] in support_ids and e.get('kind') == 'SOURCE_SPAN'],
            'interpretation': 'Validated diagnosis context; historical after source changes. Not a new edit permission.'}
    if public_capabilities is not None:
        result['public_execution_capabilities'] = dict(public_capabilities)
    return result
