"""Small, engine-owned failure vocabulary.

The model never has to emit these values.  They describe what the control
plane actually observed so that a transport failure, a rejected model output
and an infrastructure failure cannot be folded into one ``UNKNOWN`` value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


FAILURE_CLASSES = frozenset({
    "model_format", "evidence", "execution", "infrastructure", "unknown",
})
DISPATCH_STATES = frozenset({
    "not_sent", "response_known", "response_unknown",
})


@dataclass(frozen=True)
class FailureRecord:
    """Engine-only failure fact; never part of a model output contract."""

    stage: str
    code: str
    failure_class: str = "unknown"
    dispatch_state: str = "not_sent"
    snapshot_id: str | None = None
    call_ref: str | None = None
    retryable: bool = False
    secondary: bool = False
    message: str | None = None
    details: Mapping[str, Any] | None = None

    def __post_init__(self):
        if self.failure_class not in FAILURE_CLASSES:
            raise ValueError("unsupported failure class: " + str(self.failure_class))
        if self.dispatch_state not in DISPATCH_STATES:
            raise ValueError("unsupported dispatch state: " + str(self.dispatch_state))

    @property
    def primary(self) -> bool:
        return not self.secondary

    def to_wire(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "stage": str(self.stage), "code": str(self.code),
            "failure_class": self.failure_class, "dispatch_state": self.dispatch_state,
            "retryable": bool(self.retryable), "secondary": bool(self.secondary),
        }
        if self.message:
            record["message"] = str(self.message)[:2000]
        if self.call_ref is not None:
            record["call_ref"] = str(self.call_ref)
        if self.snapshot_id is not None:
            record["snapshot_id"] = str(self.snapshot_id)
        if self.details:
            record["details"] = dict(self.details)
        return record


def make_failure(
    stage: str,
    code: str,
    *,
    failure_class: str = "unknown",
    dispatch_state: str = "not_sent",
    retryable: bool = False,
    message: str | None = None,
    call_ref: str | None = None,
    snapshot_id: str | None = None,
    secondary: bool = False,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a bounded JSON failure record.

    ``stage``, ``failure_class`` and ``dispatch_state`` are deliberately the
    only categorical dimensions.  More specific information belongs in
    ``code`` or bounded details and is not added to the model protocol.
    """
    return FailureRecord(stage=stage, code=code, failure_class=failure_class,
        dispatch_state=dispatch_state, retryable=retryable, secondary=secondary,
        message=message, call_ref=call_ref, snapshot_id=snapshot_id,
        details=details).to_wire()


def classify_exception(exc: BaseException, *, stage: str = "unknown",
                       dispatch_state: str | None = None) -> tuple[str, dict[str, Any]]:
    """Map known control-plane exceptions to a public status and a record.

    A generic ``RuntimeError`` remains ``INTERRUPTED`` because the controller
    cannot prove whether its effect happened.  Generic contract/value errors
    are deterministic method failures and therefore must not be counted as an
    interruption.  Callers can pass the provider dispatch state when a request
    was sent but its response was not observed.
    """
    name = type(exc).__name__
    message = str(exc)
    lower = message.casefold()
    dispatch = dispatch_state or getattr(exc, "dispatch_state", None)
    if dispatch not in DISPATCH_STATES:
        dispatch = "not_sent"

    if name in {"BudgetExceeded", "MoneyBudgetExceeded"} or "budget" in lower and "exceed" in lower:
        return "BUDGET_EXHAUSTED", make_failure(stage, "BUDGET_EXHAUSTED",
            failure_class="infrastructure", dispatch_state=dispatch, message=message)
    if name in {"PlanRejected", "ContractError", "SemanticDeliveryError"}:
        if name == "PlanRejected":
            return "INVALID_PLAN", make_failure(stage, "INVALID_PLAN",
                failure_class="model_format", dispatch_state=dispatch, message=message)
        if name == "SemanticDeliveryError":
            return "EVIDENCE_DELIVERY_INCOMPLETE", make_failure(stage, "EVIDENCE_DELIVERY_INCOMPLETE",
                failure_class="evidence", dispatch_state=dispatch, message=message)
        return "INVALID_OUTPUT", make_failure(stage, "INVALID_CONTRACT",
            failure_class="model_format", dispatch_state=dispatch, message=message)
    if name in {"ProviderResponseError", "TimeoutError", "ConnectionError", "HTTPError"}:
        unknown = dispatch == "response_unknown" or name in {"TimeoutError", "ConnectionError"}
        status = "RESPONSE_UNKNOWN" if unknown else "INVALID_OUTPUT"
        return status, make_failure(stage, status, failure_class="unknown" if unknown else "model_format",
            dispatch_state="response_unknown" if unknown else "response_known", message=message)
    if name in {"RuntimeRejected", "OSError", "FileNotFoundError", "PermissionError"}:
        return "INFRASTRUCTURE_ERROR", make_failure(stage, "INFRASTRUCTURE_ERROR",
            failure_class="infrastructure", dispatch_state=dispatch, message=message)
    if name == "RuntimeError" and any(token in lower for token in
                                      ("unavailable", "install the ", "docker", "runtime dependency", "no such file")):
        return "INFRASTRUCTURE_ERROR", make_failure(stage, "INFRASTRUCTURE_ERROR",
            failure_class="infrastructure", dispatch_state=dispatch, message=message)
    if name in {"ValueError", "TypeError", "KeyError", "JSONDecodeError", "UnicodeDecodeError"}:
        return "INVALID_OUTPUT", make_failure(stage, "INVALID_OUTPUT",
            failure_class="model_format", dispatch_state=dispatch, message=message)
    return "INTERRUPTED", make_failure(stage, "INTERRUPTED", failure_class="unknown",
        dispatch_state=dispatch, message=message, retryable=False,
        details={"exception_type": name})


def project_terminal_status(primary: Mapping[str, Any] | None,
                            secondary: Mapping[str, Any] | None = None,
                            *, default: str = "COMPLETE") -> str:
    """Choose a terminal status without allowing cleanup to erase the root."""
    if primary and primary.get("code"):
        return str(primary["code"])
    if secondary and secondary.get("code"):
        return "INFRASTRUCTURE_ERROR"
    return default
