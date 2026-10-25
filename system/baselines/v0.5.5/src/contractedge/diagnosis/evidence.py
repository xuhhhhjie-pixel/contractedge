from __future__ import annotations

from typing import Any, Mapping

from contractedge.contracts.models import MethodInput
from contractedge.retrieval.candidates import CandidateSet


def build_evidence_catalog(method_input: MethodInput, candidates: CandidateSet | Mapping[str, Any],
                           snapshot_id: str) -> list[dict[str, Any]]:
    selected = list(candidates.candidates) if hasattr(candidates, "candidates") else list(candidates.get("candidates", ()))
    records = []
    artifacts = {a['id']: a for a in method_input.declared_artifacts}
    for item in selected:
        record = {"id": f"SRC-{item['id']}", "snapshot_id": snapshot_id,
                  "kind": "SOURCE_SPAN", "locator": {"path": str(item['path']), **dict(item['locator'])},
                  "content_digest": str(item['content_digest']), "excerpt": str(item['text']),
                  "origin": f"selected candidate {item['id']}"}
        if item.get('origin_kind') == 'DECLARED_ARTIFACT':
            artifact = artifacts[item['artifact_id']]
            start = sum(len(line) for line in artifact['content'].splitlines(keepends=True)[:item['locator']['start_line'] - 1])
            end = start + len(item['text'])
            if artifact['content'][start:end] != item['text']:
                raise ValueError('Declared artifact unit does not match its exact source span')
            record.update(kind='DECLARED_ARTIFACT', locator={
                'path': f"cases/{method_input.case_id}/{artifact['path']}",
                'artifact_id': artifact['id'], 'artifact_content_digest': artifact['content_digest'],
                'start_char': start, 'end_char': end}, origin='Immutable public input span')
        records.append(record)
    if hasattr(candidates, "supplemental_evidence"):
        records.extend(dict(item) for item in candidates.supplemental_evidence)
    records.extend(dict(item) for item in method_input.public_evidence)
    return records


def bind_evidence_ids(evidence_refs: Any, catalog: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(evidence_refs, list):
        raise ValueError("evidence_refs must be an array")
    by_id = {str(item["id"]): dict(item) for item in catalog}
    bound = []
    seen = set()
    for index, reference in enumerate(evidence_refs):
        if isinstance(reference, Mapping):
            # Fixture and legacy responses already carry the canonical records.
            record_id = reference.get("id")
            if record_id not in by_id or dict(reference) != by_id[record_id]:
                raise ValueError(f"evidence_refs[{index}] is not an exact catalog record")
            record = dict(reference)
        elif isinstance(reference, str):
            if reference not in by_id:
                raise ValueError(f"evidence_refs[{index}] cites unknown catalog ID {reference!r}")
            record = by_id[reference]
        else:
            raise ValueError(f"evidence_refs[{index}] must be a catalog ID")
        if record["id"] in seen:
            raise ValueError(f"evidence_refs[{index}] duplicates catalog ID {record['id']!r}")
        seen.add(record["id"])
        bound.append(record)
    return bound
