from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from contractedge.contracts.models import DiagnosisPacket, Snapshot
from contractedge.contracts.validation import canonical_digest


def _project_explicit_open_records(result, diagnosis):
    """Only add conservative summary flags from already explicit open records.

    Never infer an obligation, remove UNKNOWN, create missing coverage rows or
    promote PARTIAL to READY. Source/reference errors remain validation errors.
    """
    audit={'added_unresolved':[], 'model_status':result.get('status')}
    opened=result.get('open_obligations'); coverage=result.get('outcome_coverage')
    if not isinstance(opened,list) or not opened or not isinstance(coverage,list):return audit
    outcomes={r['id'] for r in diagnosis.raw['requirement_spec']['outcomes']}
    atoms={a['id']:a['outcome_id'] for a in diagnosis.raw['obligation_atoms']}
    if any(not isinstance(r,dict) or not isinstance(r.get('outcome_id'),str) or r['outcome_id'] not in outcomes
           or not isinstance(r.get('obligation_refs'),list) or not r['obligation_refs']
           or any(not isinstance(a,str) or atoms.get(a)!=r['outcome_id'] for a in r['obligation_refs']) for r in opened):return audit
    if (any(not isinstance(r,dict) or not isinstance(r.get('outcome_id'),str)
            or not isinstance(r.get('unresolved'),list) for r in coverage)
            or len({r.get('outcome_id') for r in coverage})!=len(coverage)):return audit
    by_outcome={r.get('outcome_id'):r for r in coverage}
    if not {r['outcome_id'] for r in opened} <= set(by_outcome):return audit
    for record in opened:
        oid=record['outcome_id'];row=by_outcome[oid]
        if oid not in row['unresolved']:
            row['unresolved'].append(oid);audit['added_unresolved'].append(oid)
    if result.get('status')=='READY':result['status']='READY_PARTIAL'
    audit['bound_status']=result.get('status')
    return audit


def bind_changeset_envelope(payload: Mapping[str, Any], diagnosis: DiagnosisPacket,
                            run_id: str, snapshot_id: str, input_digest: str
                            ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind non-semantic run identity and the exact validated Diagnosis digest."""
    result = deepcopy(dict(payload))
    expected = {"schema_version": "ce.semantic_changeset.v1_1", "run_id": run_id,
                "snapshot_id": snapshot_id, "input_digest": input_digest,
                "diagnosis_digest": canonical_digest(diagnosis.raw)}
    mismatched = {key: result[key] for key, value in expected.items()
                  if key in result and result[key] != value}
    result.update(expected)
    from contractedge.requirement.normalize import bind_plan_obligations
    bind_plan_obligations(result, diagnosis.raw)
    projection=_project_explicit_open_records(result,diagnosis)
    return result, {"owner": "runner", "model_mismatched_fields": sorted(mismatched),
                   "explicit_open_projection":projection,
                   "model_supplied_fields": sorted(key for key in expected if key in payload)}


def _existing_target(unit_refs: list[str], diagnosis: DiagnosisPacket,
                     snapshot: Snapshot, source_path_hint: str | None = None) -> dict[str, Any]:
    raw = diagnosis.raw
    units = {str(unit["id"]): unit for unit in raw.get("units", [])
             if isinstance(unit, Mapping) and unit.get("id")}
    evidence = {str(item["id"]): item for item in raw.get("evidence_refs", [])
                if isinstance(item, Mapping) and item.get("id")}
    if not unit_refs or len(unit_refs) != len(set(unit_refs)):
        raise ValueError("MODEL_EXISTING_TARGET_UNITS_INVALID: choose one or more distinct SemanticUnit IDs")
    source_records = []
    for unit_id in unit_refs:
        unit = units.get(unit_id)
        if unit is None or unit.get("existence") != "PRESENT":
            raise ValueError(f"MODEL_EXISTING_TARGET_UNIT_INVALID: {unit_id}")
        for evidence_id in unit.get("locator_refs", []):
            record = evidence.get(str(evidence_id))
            if isinstance(record, Mapping) and record.get("kind") == "SOURCE_SPAN":
                locator = record.get("locator")
                if isinstance(locator, Mapping) and isinstance(locator.get("path"), str):
                    source_records.append(record)
    paths = {str(record["locator"]["path"]) for record in source_records}
    if source_path_hint is not None:
        if source_path_hint not in paths:
            raise ValueError('MODEL_EXISTING_TARGET_PATH_MISMATCH: source_path must occur in selected SemanticUnit source evidence')
        source_records=[r for r in source_records if r['locator']['path']==source_path_hint]
        paths={source_path_hint}
    if len(paths) != 1:
        raise ValueError("MODEL_EXISTING_TARGET_PATH_AMBIGUOUS: referenced SemanticUnits must locate one current source file")
    path = next(iter(paths))
    if source_path_hint is not None and source_path_hint != path:
        raise ValueError(f"MODEL_EXISTING_TARGET_PATH_MISMATCH: selected units resolve to {path!r}, not {source_path_hint!r}")
    digest = snapshot.file_digests.get(path)
    if not isinstance(digest, str):
        raise ValueError(f"MODEL_EXISTING_TARGET_NOT_IN_SNAPSHOT: {path}")
    starts = [record["locator"].get("start_line") for record in source_records
              if isinstance(record["locator"].get("start_line"), int)]
    ends = [record["locator"].get("end_line") for record in source_records
            if isinstance(record["locator"].get("end_line"), int)]
    locator = {"start_line": min(starts), "end_line": max(ends)} if starts and ends else {}
    return {"kind": "EXISTING", "path": path, "locator": locator,
            "unit_refs": sorted(set(unit_refs)), "snapshot_id": snapshot.snapshot_id,
            "content_digest": digest}


def adapt_model_changeset(payload: Mapping[str, Any], diagnosis: DiagnosisPacket,
                          snapshot: Snapshot | None) -> dict[str, Any]:
    """Translate model target selectors into the canonical v1.1 ChangeTarget contract."""
    result = deepcopy(dict(payload))
    changes = result.get("changes")
    if not isinstance(changes, list):
        return result
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or not isinstance(change.get("target"), dict):
            continue
        target = change["target"]
        if "target_kind" not in target:
            continue  # fixture/legacy adapter already supplied the canonical v1.1 form
        target_kind = target.get("target_kind")
        if target_kind == "EXISTING":
            if snapshot is None:
                raise ValueError("MODEL_EXISTING_TARGET_REQUIRES_SNAPSHOT")
            if any(key in target for key in ("parent_path", "proposed_path", "expected_role",
                                               "anchor_unit_refs", "missing_unit_refs", "justification_refs")):
                raise ValueError(f"MODEL_EXISTING_TARGET_HAS_NEW_FIELDS at changes[{index}].target")
            unit_refs = target.get("unit_refs")
            if not isinstance(unit_refs, list) or any(not isinstance(item, str) for item in unit_refs):
                raise ValueError(f"MODEL_EXISTING_TARGET_UNITS_INVALID at changes[{index}].target")
            source_path = target.get("source_path")
            if source_path is not None and not isinstance(source_path, str):
                raise ValueError(f"MODEL_EXISTING_TARGET_PATH_INVALID at changes[{index}].target")
            change["target"] = _existing_target(unit_refs, diagnosis, snapshot, source_path)
        elif target_kind == "NEW":
            if "unit_refs" in target or "source_path" in target or any(key in target for key in ("path", "locator", "snapshot_id", "content_digest")):
                raise ValueError(f"MODEL_NEW_TARGET_HAS_EXISTING_FIELDS at changes[{index}].target")
            new_target = {key: value for key, value in target.items() if key != "target_kind"}
            new_target["kind"] = "NEW"
            change["target"] = new_target
        else:
            raise ValueError(f"MODEL_TARGET_KIND_INVALID at changes[{index}].target")
    return result
