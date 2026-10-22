"""Capabilities of the actual source-repair consumer, independent of wire syntax."""
from __future__ import annotations

from typing import Mapping

SOURCE_CONSUMER_ID = 'contractedge.topomen_source_consumer.v1'
SOURCE_OPERATIONS = ('MODIFY', 'CREATE')
SOURCE_BACKENDS = frozenset({'direct', 'topomen_agent'})


def resolve_execution_capabilities(capabilities: Mapping | None = None, *, backend: str | None = None) -> dict:
    """Bind trusted backend limits and permit only explicit narrowing by configuration.

    Absent declarations use the known direct source consumer; unknown explicit
    backends/consumers fail. The general ChangeSet schema still permits REMOVE.
    """
    if capabilities is not None and not isinstance(capabilities, Mapping):
        raise ValueError('Execution capabilities must be a mapping')
    result = dict(capabilities or {})
    declared_backend = result.get('execution_backend')
    selected = backend if backend is not None else (declared_backend or 'direct')
    if not isinstance(selected, str) or selected not in SOURCE_BACKENDS:
        raise ValueError('Unknown execution backend: ' + str(selected))
    if declared_backend is not None and declared_backend != selected:
        raise ValueError('Execution capability backend conflicts with selected backend')
    if result.get('consumer_id', SOURCE_CONSUMER_ID) != SOURCE_CONSUMER_ID:
        raise ValueError('Unknown execution consumer')
    operations = result.get('supported_operations', SOURCE_OPERATIONS)
    if (not isinstance(operations, (list, tuple)) or not operations
            or any(not isinstance(op, str) for op in operations)
            or len(operations) != len(set(operations))):
        raise ValueError('Supported operations must be a nonempty unique list')
    if not set(operations) <= set(SOURCE_OPERATIONS):
        raise ValueError('Configured operations exceed actual source consumer capabilities')
    result.update(consumer_id=SOURCE_CONSUMER_ID, execution_backend=selected,
                  supported_operations=[op for op in SOURCE_OPERATIONS if op in operations])
    return result
