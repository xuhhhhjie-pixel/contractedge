"""Bounded delivery of immutable public inputs, independent of source retrieval."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from contractedge.diagnosis.evidence import merge_evidence_records
from contractedge.model.budget import BudgetExceeded


DECLARED_ARTIFACT_CONTENT_LIMIT = 64 * 1024
DECLARED_ARTIFACT_GUIDANCE = (
    " declared_artifacts are immutable read-only public inputs. Use supplied content verbatim for "
    "configuration keys, annotation names, flags, paths and literal values. Input delivery does not "
    "prove that production consumes the input, grant edit permission or add evidence IDs; production "
    "bindings still require the supplied source evidence."
)


def project_declared_artifacts(artifacts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Preserve whole exact contents within the existing aggregate UTF-8 cap.

    Share only exact duplicates. Reject the complete selected input set when
    its local cap is exceeded; final serialized-request admission still applies.
    """
    projected = merge_evidence_records(artifacts)
    if any(not isinstance(item.get('content'), str) for item in projected):
        raise ValueError('Declared public artifacts require complete string content')
    content_bytes = sum(len(item['content'].encode('utf-8')) for item in projected)
    if content_bytes > DECLARED_ARTIFACT_CONTENT_LIMIT:
        raise BudgetExceeded('Declared public input exceeds the complete-content budget: '
            f'required_bytes={content_bytes}, allowed_bytes={DECLARED_ARTIFACT_CONTENT_LIMIT}')
    return projected
