"""Mode-specific inputs. Missing semantic stages are never reconstructed here."""
from __future__ import annotations
from dataclasses import dataclass
from typing import ClassVar, Mapping, Union


@dataclass(frozen=True)
class SemanticChangeSetIntent:
    changeset: Mapping
    diagnosis: Mapping | None = None
    source_support: tuple[Mapping, ...] = ()
    kind: ClassVar[str] = 'SEMANTIC_CHANGESET'


@dataclass(frozen=True)
class DirectPlanIntent:
    """An independently authored ce.direct_plan.v1; no E/V contract required."""
    plan: Mapping
    kind: ClassVar[str] = 'DIRECT_PLAN'


@dataclass(frozen=True)
class RelationOnlyIntent:
    """The actual diagnosis is retained; C/P and a step list are absent."""
    diagnosis: Mapping
    kind: ClassVar[str] = 'RELATION_ONLY'


@dataclass(frozen=True)
class RequirementOnlyIntent:
    snapshot_id: str
    input_digest: str
    kind: ClassVar[str] = 'REQUIREMENT_ONLY'


RepairIntent = Union[SemanticChangeSetIntent, DirectPlanIntent, RelationOnlyIntent, RequirementOnlyIntent]


def intent_identity(intent: RepairIntent) -> tuple[str, str]:
    if isinstance(intent, SemanticChangeSetIntent):
        value = intent.changeset
    elif isinstance(intent, DirectPlanIntent):
        value = intent.plan
    elif isinstance(intent, RelationOnlyIntent):
        value = intent.diagnosis
    elif isinstance(intent, RequirementOnlyIntent):
        return intent.snapshot_id, intent.input_digest
    else:
        raise ValueError('Unsupported repair intent type')
    return value['snapshot_id'], value['input_digest']
