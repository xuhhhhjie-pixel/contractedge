"""Capabilities of the actual source-repair consumer, independent of wire syntax."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Mapping

SOURCE_CONSUMER_ID = 'contractedge.topomen_source_consumer.v1'
SOURCE_OPERATIONS = ('MODIFY', 'CREATE')
SOURCE_BACKENDS = frozenset({'direct', 'topomen_agent'})


def public_behavior_check_catalog(capabilities: Mapping | None) -> list[dict]:
    """Project trusted capability descriptions without merging their authority.

    These are declared inputs, never check results or executable registrations.
    A full check ID must have one producer; duplicate IDs fail before dispatch.
    """
    if capabilities is not None and not isinstance(capabilities, Mapping):
        raise ValueError('Public check capabilities must be a mapping')
    capabilities = capabilities or {}
    result, identifiers = [], set()

    def add(identifier, source, producer, producer_sha, statement, verification_scope, **extra):
        if (not isinstance(identifier, str) or not identifier
                or identifier in identifiers or not isinstance(producer, str) or not producer
                or not isinstance(producer_sha, str)
                or not re.fullmatch(r'[0-9a-f]{64}', producer_sha)
                or not isinstance(statement, str) or not statement):
            raise ValueError('Invalid or conflicting public behavior check capability')
        if verification_scope is not None and (not isinstance(verification_scope, Mapping)
                or set(verification_scope) != {'scenario'} or not isinstance(verification_scope['scenario'], str)
                or not verification_scope['scenario']):
            raise ValueError('Public check scope must name its declared scenario')
        identifiers.add(identifier)
        result.append({'id': identifier, 'source_kind': source, 'producer_id': producer,
            'producer_sha256': producer_sha, 'statement': statement,
            'verification_scope': deepcopy(verification_scope), **deepcopy(extra)})

    probes = capabilities.get('method_behavior_probes', [])
    if not isinstance(probes, (list, tuple)):
        raise ValueError('Method behavior probe descriptions must be a list')
    for probe in probes:
        if (not isinstance(probe, Mapping) or not isinstance(probe.get('checks'), Mapping)
                or not isinstance(probe.get('id'), str)):
            raise ValueError('Invalid method behavior probe description')
        for key, statement in probe['checks'].items():
            if not isinstance(key, str) or not key:
                raise ValueError('Method check ID must be a nonempty string')
            add(probe['id'] + ':' + key, 'METHOD_BEHAVIOR_PROBE', probe['id'],
                probe.get('sha256'), statement,
                {'scenario': probe['scenario']} if probe.get('scenario') else None,
                boundary=probe.get('boundary'))
    registered = capabilities.get('registered_public_checks')
    if registered is not None:
        if (not isinstance(registered, Mapping)
                or registered.get('schema_version') != 'ce.public_process_check_capabilities.v1'
                or not isinstance(registered.get('probe_id'), str)
                or not registered['probe_id']
                or not isinstance(registered.get('checks'), list)):
            raise ValueError('Invalid registered public check descriptions')
        for check in registered['checks']:
            if (not isinstance(check, Mapping) or not isinstance(check.get('id'), str)
                    or not check['id'].startswith(registered['probe_id'] + ':')):
                raise ValueError('Registered check ID must bind its declared producer')
            members = check.get('covered_scenarios')
            if members is not None and (not isinstance(members, list) or not members
                    or any(not isinstance(value, str) or not value for value in members)
                    or len(set(members)) != len(members)):
                raise ValueError('Public scenario coverage must be explicit and unique')
            add(check['id'], 'REGISTERED_PUBLIC_CHECK', registered['probe_id'],
                registered.get('observer_sha256'), check.get('statement'),
                check.get('verification_scope'), scope=check.get('scope'),
                covered_scenarios=members)
    return result


def behavior_binding_error(obligation: Mapping, capabilities: Mapping | None = None) -> str | None:
    """Check declared binding metadata; never choose or fill a model's scope."""
    refs = obligation.get('behavior_check_refs')
    if not refs:
        return None
    if (not isinstance(refs, (list, tuple))
            or any(not isinstance(ref, str) or not ref for ref in refs)
            or len(set(refs)) != len(refs)):
        return 'METHOD_BEHAVIOR_BINDINGS_INVALID'
    if (obligation.get('check_coverage') not in {'ALL_OF', 'NECESSARY_ONLY', 'SUPPORTING'}
            or not isinstance(obligation.get('coverage_rationale'), str)
            or not obligation['coverage_rationale'].strip()):
        return 'CHECK_COVERAGE_REQUIRED'
    scope = obligation.get('verification_scope')
    if (not isinstance(scope, Mapping) or not isinstance(scope.get('scenario'), str)
            or not scope['scenario'].strip()):
        return 'VERIFICATION_SCOPE_UNDECLARED'
    if capabilities is not None:
        try:
            catalog = {row['id']: row for row in public_behavior_check_catalog(capabilities)}
        except ValueError:
            return 'INVALID_PUBLIC_CHECK_CAPABILITIES'
        if not set(refs) <= catalog.keys():
            return 'METHOD_BEHAVIOR_CHECK_UNDECLARED'
        if any(catalog[ref].get('verification_scope') != {'scenario': scope['scenario']}
               for ref in refs):
            return 'METHOD_BEHAVIOR_CHECK_SCOPE_MISMATCH'
    return None


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
    # Validate already-known public check identities before any front-half call.
    public_behavior_check_catalog(result)
    return result
