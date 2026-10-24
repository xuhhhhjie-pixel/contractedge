"""Bind an actual front invocation to its persisted inputs and dispatch ledger."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path

from contractedge.contracts.validation import canonical_digest


_INPUT_FILES = {
    "method_input": "front.method_input.json",
    "config": "front.config.json",
    "observations": "front.public_observations.json",
    "feedback": "front.execution_feedback.json",
}
_ARTIFACT_FILES = (*_INPUT_FILES.values(), "front.replay.json")


def build_front_delivery(directory, method_input, provider, start, evidence_delivery=None) -> dict:
    """Describe existing bytes and actual calls without reconstructing legacy requests.

    The caller persists this sidecar after the replay inputs, including on a
    failed front attempt. A delivery receipt describes selector delivery only;
    it does not establish that a model adopted evidence or repaired behavior.
    Missing artifacts or inconsistent MethodInput identity raise to the caller,
    which must retain any original front exception.
    """
    directory = Path(directory)
    if is_dataclass(method_input) and not isinstance(method_input, type):
        method_wire = asdict(method_input)
    elif isinstance(method_input, Mapping):
        method_wire = dict(method_input)
    else:
        raise TypeError("Front MethodInput must be a dataclass instance or mapping")
    calls = provider.calls
    if type(start) is not int or not 0 <= start <= len(calls):
        raise ValueError("Front delivery call range must reference the actual ledger")

    artifacts, values = {}, {}
    for name in _ARTIFACT_FILES:
        content = (directory / name).read_bytes()
        artifacts[name] = hashlib.sha256(content).hexdigest()
        if name in _INPUT_FILES.values():
            values[name] = json.loads(content)
    effective = {key: values[name] for key, name in _INPUT_FILES.items()}
    if canonical_digest(effective["method_input"]) != canonical_digest(method_wire):
        raise ValueError("Persisted front MethodInput differs from the invocation input")

    receipt = getattr(evidence_delivery, "receipt", evidence_delivery)
    if receipt is not None and not isinstance(receipt, Mapping):
        raise TypeError("Evidence delivery must contain a receipt mapping")
    if receipt is not None:
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
        if receipt.get("receipt_digest") != canonical_digest(unsigned):
            raise ValueError("Evidence delivery receipt digest does not match its content")
        observations = effective["observations"]
        if receipt.get("records_digest") != canonical_digest(observations):
            raise ValueError("Evidence delivery records digest differs from persisted observations")
        expected_refs = [
            {"id": record.get("id"), "snapshot_id": record.get("snapshot_id"),
             "content_digest": hashlib.sha256(record["excerpt"].encode()).hexdigest()}
            for record in observations
        ]
        supplied_refs = [row.get("ref") for row in receipt.get("selected", [])]
        if supplied_refs != expected_refs:
            raise ValueError("Evidence delivery selected references differ from actual records")
        snapshot_id = receipt.get("snapshot_id")
        if snapshot_id is not None and any(record.get("snapshot_id") != snapshot_id for record in observations):
            raise ValueError("Evidence delivery snapshot differs from actual records")
    requests = []
    for index, call in enumerate(calls[start:], start=start):
        request_digest = call.get("request_digest")
        if "request_digest" not in call and isinstance(call.get("request"), Mapping):
            request_digest = canonical_digest(call["request"])
        transport = call.get("transport_request")
        transport_digest = call.get("transport_body_sha256")
        if transport_digest is None and isinstance(transport, Mapping):
            transport_digest = transport.get("body_sha256")
        requests.append({
            "call_index": index,
            "request_digest": request_digest,
            "transport_body_sha256": transport_digest,
            "prompt_version": call.get("prompt_version"),
            "prompt_tokens_reserved": call.get("prompt_tokens_reserved"),
            "output_tokens_reserved": call.get("output_tokens_reserved"),
        })
    return {
        "schema_version": "ce.front_delivery.v1",
        "evidence_delivery_receipt_digest": receipt.get("receipt_digest") if receipt is not None else None,
        "delivery_interpretation": (
            "Selector delivery and actual request/artifact identity only; "
            "not evidence of model adoption, semantic correctness or behavior repair."
        ),
        "model_call_range": [start, len(calls)],
        "requests": requests,
        "artifacts": artifacts,
        "effective_front_input_sha256": canonical_digest(effective),
    }
