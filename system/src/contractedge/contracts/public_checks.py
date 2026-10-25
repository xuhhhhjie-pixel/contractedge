"""Trusted public check declarations shared by planning and plan admission.

This catalog describes available evidence, never results or semantic coverage.
It does not fill in a model's missing bindings.
"""
from collections.abc import Mapping
import re


def _name(value):
    return isinstance(value, str) and bool(value.strip())


def public_check_catalog(capabilities):
    catalog = {}

    def add(identifier, scenario, *, registered=False, members=None):
        if not _name(identifier) or identifier in catalog:
            raise ValueError('Public check IDs must be nonempty and unique across declaration sources')
        if scenario is not None and not _name(scenario):
            raise ValueError('Public check scenario must be a nonempty string')
        catalog[identifier] = {'scenario': scenario, 'registered': registered,
                               'scenario_members': members}

    # Preserve the existing method-owned probe protocol and its declared scope.
    for probe in capabilities.get('method_behavior_probes', []):
        for identifier in probe['checks']:
            add(probe['id'] + ':' + identifier, probe.get('scenario'))
    if 'registered_public_checks' not in capabilities:
        return catalog
    registration = capabilities['registered_public_checks']
    if (not isinstance(registration, Mapping)
            or registration.get('schema_version') != 'ce.public_process_check_capabilities.v1'
            or not _name(registration.get('probe_id'))
            or not re.fullmatch('[a-f0-9]{64}', str(registration.get('observer_sha256', '')))
            or not isinstance(registration.get('checks'), list) or not registration['checks']):
        raise ValueError('Invalid registered public check declaration')
    for check in registration['checks']:
        if not isinstance(check, Mapping):
            raise ValueError('Registered public checks must be objects')
        members = check.get('scenario_members')
        if (not _name(check.get('scenario')) or not _name(check.get('statement'))
                or not _name(check.get('scope')) or not isinstance(members, list) or not members
                or not all(_name(member) for member in members) or len(set(members)) != len(members)):
            raise ValueError('Registered check requires explicit finite scenario and member coverage')
        identifier = check.get('id')
        if not _name(identifier) or not identifier.startswith(registration['probe_id'] + ':'):
            raise ValueError('Registered check ID must belong to its declared observer')
        add(identifier, check['scenario'], registered=True, members=list(members))
    return catalog
