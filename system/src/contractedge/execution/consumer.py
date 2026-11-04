"""The engine-facing consumer port; payload syntax belongs to each adapter."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable
from contractedge.contracts.repair_intent import RepairIntent
from contractedge.contracts.validation import canonical_digest
from contractedge.execution.literal import PlanRejected
from contractedge.model.budget import BudgetExceeded


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
    source_context: Mapping | None = None


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


def execution_review_mapping(result, *, max_characters=None):
    """Complete ordered edit transaction bound to its original/result snapshots.

    Literal steps may consume or produce intermediate text absent from either
    endpoint. Direct receipts retain response order, so restore execution order
    using the same accepted operation order as the atomic literal executor.
    """
    used = 0
    mapping = []
    literal_rows = result.get('literal_edit_trace', [])
    order = {key: index for index, key in enumerate(
        (result.get('acceptance') or {}).get('operation_order', ()))}
    if order:
        literal_rows = sorted(literal_rows, key=lambda row: order.get(row['key'], len(order)))
    rows=result.get('semantic_edit_trace') or [
        {'literal_edit_id':row['key'],'path':row['file'],
         'literal_edits':[{'old_text':row['before'],'new_text':row['after']}],
         'semantic_status':'NOT_PROVEN; actual literal edit only'}
        for row in literal_rows]
    snapshots = {key: result.get(key) for key in ('from_snapshot', 'to_snapshot')}
    if rows and any(not isinstance(value, str) or not value for value in snapshots.values()):
        raise ValueError('Execution review edits require both original and result snapshot identities')
    for row in rows:
        if any(key in row and row[key] != value for key, value in snapshots.items()):
            raise ValueError('Execution review row conflicts with the actual edit snapshot identities')
        item = {k: v for k, v in row.items() if k != 'literal_edits'}
        item.update(snapshots)
        spans = []
        for edit in row.get('literal_edits', []):
            span = {}
            for key in ('old_text', 'new_text'):
                raw = edit[key]
                used += len(raw)
                if max_characters is not None and used > max(0, max_characters):
                    raise BudgetExceeded('Complete execution review context exceeds the explicit character limit: '
                        f'required_characters_at_least={used}, allowed_characters={max(0, max_characters)}')
                span[key] = raw
            span['truncated'] = False
            spans.append(span)
        item['literal_edits'] = spans
        mapping.append(item)
    return mapping

