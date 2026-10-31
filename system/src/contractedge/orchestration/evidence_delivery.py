"""Select public observations without silently losing required current failures."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from contractedge.contracts.delivery import Delivery
from contractedge.contracts.validation import canonical_digest
from contractedge.diagnosis.operation_consistency import operation_facts


def _reference(record):
    excerpt = record.get("excerpt", "")
    digest = hashlib.sha256(excerpt.encode()).hexdigest() if isinstance(excerpt, str) else None
    return {"id": record.get("id"), "snapshot_id": record.get("snapshot_id"),
            "content_digest": digest}


def _declared_failure(record):
    return (record.get("evidence_kind") == "PUBLIC_BEHAVIOR"
            and isinstance(record.get("behavior_check"), Mapping)
            and record["behavior_check"].get("status") == "FAIL")


def _verified_failure(record):
    check, locator = record["behavior_check"], record.get("locator", {})
    if (not check.get("probe_sha256") or not record.get("content_digest")
            or locator.get("truncated") is not False or locator.get("whole_file_read") is not True
            or record.get("structured_complete") is False):
        return False
    try:
        body = json.loads(record["excerpt"])
    except (KeyError, TypeError, ValueError):
        return False
    return (isinstance(body, dict) and body.get("status") == "FAIL"
            and isinstance(body.get("observed"), dict)
            and isinstance(body.get("statement"), str) and bool(body["statement"])
            and isinstance(body.get("probe_id"), str) and isinstance(body.get("id"), str)
            and check.get("id") == body["probe_id"] + ":" + body["id"])


def _selection_order(observations, query):
    # Keep the previous selector's ordering, including complete-record priority.
    terms = set(re.findall(r"[a-z][a-z0-9_-]{3,}", query.casefold()))
    ranked = sorted(observations,
        key=lambda o: (-sum(term in o["excerpt"].casefold() for term in terms), o["id"]))
    headers = [o for o in ranked if o["locator"].get("start_byte") == 0
               and o["locator"]["path"].endswith(".render-overview.json")]
    pod_records = [o for o in ranked if ".pod-spec-" in o["locator"]["path"]]
    identity_terms = set(re.findall(r"[a-z0-9]+", query.casefold()))

    def pod_identity_priority(record):
        try:
            resource = json.loads(record["excerpt"])["resource"]
            kind = str(resource.get("kind", "")).casefold()
            name_terms = set(re.findall(r"[a-z0-9]+", str(resource.get("name", "")).casefold()))
        except (ValueError, KeyError, TypeError):
            return (0, 0)
        return (-int(kind in identity_terms or kind + "s" in identity_terms),
                -len(name_terms & identity_terms))

    pod_records.sort(key=pod_identity_priority)
    inventories = [o for o in ranked if ".render-kind-" in o["locator"]["path"]]

    def relevant_kind(record):
        try:
            kind = str(json.loads(record["excerpt"])["kind"]).casefold()
        except (ValueError, KeyError, TypeError):
            return False
        return kind in identity_terms or kind + "s" in identity_terms

    relevant_inventories = [o for o in inventories if relevant_kind(o)]
    structured = [o for o in ranked if o.get("structured_complete") or o.get("behavior_check")]
    # operation_facts requires a real snapshot; missing identities are never relabelled.
    operation_records = [o for o in observations if isinstance(o.get("snapshot_id"), str)]
    failed_operations = {o["observation_id"] for o in operation_facts(operation_records)
                         if o["status"] == "FAILED"}
    failures = [o for o in structured
                if o.get("behavior_check", {}).get("status") == "FAIL" or o["id"] in failed_operations]
    return headers + relevant_inventories + failures + pod_records + structured + ranked


def select_observations_delivery(observations, query: str, token_counter, max_tokens: int = 4000,
                                 *, snapshot_id=None, mandatory_refs=()) -> Delivery:
    """Retain complete records and account for every input and required reference.

    A missing current failure blocks delivery rather than changing the legacy
    ranking to fit it. An explicit snapshot never turns old evidence into new
    evidence. No snapshot preserves the previous cross-snapshot selection.
    """
    if type(max_tokens) is not int or max_tokens < 0:
        raise ValueError("Observation token budget must be a nonnegative integer")
    records = list(observations)
    references, errors, by_id, duplicate_indices = [], [], {}, set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            references.append({"id": None, "snapshot_id": None, "content_digest": None})
            errors.append({"code": "OBSERVATION_INVALID", "input_index": index})
            continue
        reference = _reference(record)
        references.append(reference)
        locator = record.get("locator")
        if (not isinstance(record.get("id"), str) or not record["id"]
                or not isinstance(record.get("excerpt"), str)
                or not isinstance(locator, Mapping) or not isinstance(locator.get("path"), str)):
            errors.append({"code": "OBSERVATION_INVALID", "input_index": index, "ref": reference})
            continue
        if record.get("content_digest") is not None and record["content_digest"] != reference["content_digest"]:
            code = "UNVERIFIABLE_FAILED_OBSERVATION" if _declared_failure(record) else "OBSERVATION_DIGEST_MISMATCH"
            errors.append({"code": code, "input_index": index, "ref": reference,
                           "claimed_content_digest": record["content_digest"]})
        identifier = record["id"]
        if identifier in by_id:
            previous_index = by_id[identifier]
            if canonical_digest(records[previous_index]) != canonical_digest(record):
                errors.append({"code": "OBSERVATION_ID_COLLISION", "input_index": index,
                               "previous_input_index": previous_index, "ref": reference})
            else:
                duplicate_indices.add(index)
        else:
            by_id[identifier] = index

    mandatory = []
    for supplied in mandatory_refs:
        if isinstance(supplied, str):
            index = by_id.get(supplied)
            reference = references[index] if index is not None else None
        elif (isinstance(supplied, Mapping) and isinstance(supplied.get("id"), str)
              and all(k in supplied for k in ("id", "snapshot_id", "content_digest"))):
            index = by_id.get(supplied.get("id"))
            reference = references[index] if index is not None else None
            if reference is not None and dict(supplied) != reference:
                reference = None
        else:
            reference = None
        if reference is None:
            errors.append({"code": "MANDATORY_REF_INVALID", "reference": supplied})
        elif reference not in mandatory:
            mandatory.append(reference)

    current = [r for index, r in enumerate(records)
               if not errors and index not in duplicate_indices and isinstance(r, Mapping)
               and (snapshot_id is None or r.get("snapshot_id") == snapshot_id)]
    for record in current:
        if _declared_failure(record):
            reference = _reference(record)
            if not _verified_failure(record):
                errors.append({"code": "UNVERIFIABLE_FAILED_OBSERVATION", "ref": reference})
            elif reference not in mandatory:
                mandatory.append(reference)
    valid_operation_records = [r for r in current if isinstance(r.get("snapshot_id"), str)
                               and r.get("content_digest") == _reference(r)["content_digest"]]
    if not errors:
        failed_ids = {fact["observation_id"] for fact in operation_facts(valid_operation_records)
                      if fact["status"] == "FAILED"}
        for record in current:
            reference = _reference(record)
            if record["id"] in failed_ids and reference not in mandatory:
                mandatory.append(reference)

    selected_records, selected_rows, omitted_rows = [], [], []
    used, selected_ids = 0, set()
    if not errors:
        for record in _selection_order(current, query):
            if record["id"] in selected_ids:
                continue
            cost = token_counter(record["excerpt"])
            if type(cost) is not int or cost < 0:
                raise ValueError("Observation token counter must return nonnegative integers")
            if used + cost <= max_tokens:
                selected_records.append(record)
                selected_ids.add(record["id"])
                used += cost
        selected_rows = [{"ref": _reference(record), "input_index": by_id[record["id"]],
                          "reason": "SELECTED_COMPLETE_RECORD"} for record in selected_records]
    for index, reference in enumerate(references):
        record = records[index]
        if errors:
            reason = "DELIVERY_REJECTED"
        elif index in duplicate_indices:
            reason = "DUPLICATE_IDENTICAL"
        elif snapshot_id is not None and record.get("snapshot_id") != snapshot_id:
            reason = "MISSING_SNAPSHOT" if record.get("snapshot_id") is None else "STALE_SNAPSHOT"
        elif record["id"] not in selected_ids:
            reason = "TOKEN_BUDGET"
        else:
            continue
        omitted_rows.append({"ref": reference, "input_index": index, "reason": reason})
    delivered_refs = [row["ref"] for row in selected_rows]
    delivered_mandatory = [ref for ref in mandatory if ref in delivered_refs]
    missing = [ref for ref in mandatory if ref not in delivered_refs]
    receipt = {"schema_version": "ce.evidence_delivery.v1",
        "status": "REJECTED" if errors else "INCOMPLETE" if missing else "COMPLETE",
        "snapshot_id": snapshot_id, "input_refs": references,
        "input_records_digest": canonical_digest(records),
        "query_digest": hashlib.sha256(query.encode()).hexdigest(),
        "selected": selected_rows, "omitted": omitted_rows,
        "mandatory_refs": mandatory,
        "mandatory_coverage": {"required": mandatory, "delivered": delivered_mandatory, "missing": missing},
        "errors": errors, "budget": {"max_tokens": max_tokens, "used_tokens": used},
        "records_digest": canonical_digest(selected_records)}
    receipt["receipt_digest"] = canonical_digest(receipt)
    return Delivery(records=tuple(selected_records), receipt=receipt)


def annotate_delivery_citations(delivery: Delivery, review: Mapping | None) -> Delivery:
    """Add the observed review citations without treating citation as proof.

    Delivery is established before the reviewer runs.  This second receipt
    projection records whether the reviewer actually cited each delivered
    record; it never changes the selected records or their mandatory status.
    """
    cited = set()
    if isinstance(review, Mapping):
        for check in review.get("checks", ()):
            if not isinstance(check, Mapping):
                continue
            for evidence in check.get("evidence", ()):
                if isinstance(evidence, Mapping) and isinstance(evidence.get("observation_id"), str):
                    cited.add(evidence["observation_id"])
    receipt = dict(delivery.receipt)
    receipt["citation_status"] = {
        str(row.get("ref", {}).get("id")): ("CITED" if row.get("ref", {}).get("id") in cited else "NOT_CITED")
        for row in receipt.get("selected", ()) if isinstance(row, Mapping)}
    receipt["model_citation_status"] = "CITED" if cited else "NOT_CITED"
    receipt["receipt_digest"] = canonical_digest({key: value for key, value in receipt.items()
        if key != "receipt_digest"})
    return Delivery(records=delivery.records, receipt=receipt)
