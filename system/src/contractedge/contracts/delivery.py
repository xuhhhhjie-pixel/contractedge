"""Wire-neutral result of a bounded evidence projection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Delivery:
    """Selected complete records and an auditable, content-bound receipt.

    COMPLETE describes delivery of required evidence, not semantic correctness.
    INCOMPLETE and REJECTED must not authorize a downstream model request.
    """

    records: tuple[dict[str, Any], ...]
    receipt: dict[str, Any]
