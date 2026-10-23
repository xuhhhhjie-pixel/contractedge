"""The engine-facing consumer port; payload syntax belongs to each adapter."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable
from contractedge.contracts.repair_intent import RepairIntent
from contractedge.contracts.validation import canonical_digest
from contractedge.execution.literal import PlanRejected


@dataclass(frozen=True)
class ConsumerCapabilities:
    consumer_id: str
    supported_intents: tuple[str, ...]
    supported_operations: tuple[str, ...]
    execution_prompt_versions: tuple[str, ...]
    received_response_recovery: bool = False


@dataclass(frozen=True)
class ConsumerContext:
    source: Path
    policy: Mapping
    config: Mapping
    requirement: str
    declared_artifacts: tuple = ()
    observations: tuple = ()
    feedback: Mapping | None = None
    previous_attempt: Mapping | None = None
    previous_input: 'ExecutionInput | None' = None
    prior_execution: Mapping | None = None
    rebind: bool = False


@dataclass(frozen=True)
class ExecutionInput:
    consumer_id: str
    intent_kind: str
    snapshot_id: str
    input_digest: str
    payload: Mapping
    target_paths: tuple[str, ...]
    context: ConsumerContext = field(repr=False, compare=False)
    operation_coverage: str = 'APPLICABLE'

    def digest(self):
        return canonical_digest({'consumer_id':self.consumer_id,'intent_kind':self.intent_kind,
            'snapshot_id':self.snapshot_id,'input_digest':self.input_digest,'payload':self.payload,
            'target_paths':list(self.target_paths),'operation_coverage':self.operation_coverage,
            'source_root':str(self.context.source.resolve()),'scope_policy':self.context.policy,
            'requirement':self.context.requirement,'config':self.context.config})


@dataclass(frozen=True)
class AcceptedInput:
    execution_input: ExecutionInput
    acceptance: Mapping
    execution_digest: str
    acceptance_digest: str = field(init=False)

    def __post_init__(self):
        object.__setattr__(self,'acceptance_digest',canonical_digest(self.acceptance))

    def verify(self, consumer_id):
        if self.execution_input.consumer_id != consumer_id or self.execution_digest != self.execution_input.digest():
            raise PlanRejected('Accepted consumer input changed or belongs to another consumer')
        if self.acceptance_digest!=canonical_digest(self.acceptance):
            raise PlanRejected('Accepted consumer authority changed')


@dataclass(frozen=True)
class BudgetPort:
    provider: object
    token_counter: object
    config: Mapping
    trial_session: object = None


@runtime_checkable
class RepairConsumer(Protocol):
    def describe(self) -> ConsumerCapabilities: ...
    def prepare(self, intent: RepairIntent, context: ConsumerContext) -> ExecutionInput: ...
    def accept(self, execution_input: ExecutionInput, snapshot, policy: Mapping) -> AcceptedInput: ...
    def execute(self, accepted_input: AcceptedInput, budget_port: BudgetPort) -> Mapping: ...


def validate_consumer(consumer):
    if not isinstance(consumer, RepairConsumer):
        raise ValueError('Repair consumer must implement describe/prepare/accept/execute')
    capabilities=consumer.describe()
    if (not isinstance(capabilities,ConsumerCapabilities) or not capabilities.supported_intents
            or not capabilities.supported_operations
            or not set(capabilities.supported_operations) <= {'MODIFY','CREATE'}):
        raise ValueError('Invalid or unsupported consumer capabilities')
    return capabilities


def build_consumer(backend, *, executor=None, on_trace=None):
    """Backend construction is outside the repair loop and lazy-loads its dialect."""
    from contractedge.execution.consumers.topomen import TopoMenRepairConsumer
    return TopoMenRepairConsumer(backend=backend, executor=executor, on_trace=on_trace)


def execution_review_mapping(result, *, max_characters=24000):
    """Bounded actual edit context, never public runtime/behavior evidence."""
    remaining = max(0, max_characters)
    mapping = []
    rows=result.get('semantic_edit_trace') or [
        {'literal_edit_id':row['key'],'path':row['file'],
         'literal_edits':[{'old_text':row['before'],'new_text':row['after']}],
         'semantic_status':'NOT_PROVEN; actual literal edit only'}
        for row in result.get('literal_edit_trace',[])]
    for row in rows:
        item = {k: v for k, v in row.items() if k != 'literal_edits'}
        spans = []
        for edit in row.get('literal_edits', []):
            span = {}
            for key in ('old_text', 'new_text'):
                raw = edit[key]
                span[key] = raw[:remaining]
                remaining -= len(span[key])
            span['truncated'] = any(len(span[k]) < len(edit[k]) for k in ('old_text', 'new_text'))
            spans.append(span)
        item['literal_edits'] = spans
        mapping.append(item)
    return mapping

