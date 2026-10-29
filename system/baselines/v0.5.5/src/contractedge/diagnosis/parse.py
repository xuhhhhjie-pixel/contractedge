from __future__ import annotations

import json
from typing import Any


def parse_json_response(content: str) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model response is empty")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model response is not strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value


def parse_diagnosis_response(content: str) -> dict[str, Any]:
    """Accept a single conventional outer `diagnosis` envelope, then validate strictly."""
    value = parse_json_response(content)
    if set(value) == {"diagnosis"} and isinstance(value.get("diagnosis"), dict):
        return value["diagnosis"]
    return value
