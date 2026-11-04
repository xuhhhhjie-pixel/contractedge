"""Small helpers for the provider model configuration boundary.

The CLI accepts a flat ``model`` string, while recorded/replay configurations
store a model mapping (the mapping also carries provider prompt settings).
Both forms are valid inputs, but the wire ``ModelRequest.model`` field must
always be the actual model name.  Keeping this normalization in one module
prevents an accidental ``str({...})`` model identity from reaching a paid
provider or its money ledger.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def model_name(value: Any) -> str:
    """Return the canonical non-empty model name from a string or mapping."""
    if isinstance(value, Mapping):
        value = value.get("model")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("model configuration requires a non-empty model name")
    return value.strip()


def model_config(value: Any, *, provider: str | None = None,
                 schema_in_prompt: bool = True) -> dict[str, Any]:
    """Project either accepted model form to a canonical prompt config."""
    if isinstance(value, Mapping):
        result = dict(value)
        result["model"] = model_name(value)
    else:
        result = {"model": model_name(value)}
    if provider is not None:
        result.setdefault("provider", provider)
    result.setdefault("schema_in_prompt", schema_in_prompt)
    return result
