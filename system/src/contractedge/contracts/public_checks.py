"""Compatibility projection of the shared trusted public-check catalog.

Planning and execution use one producer/scenario validator. This projection
retains the local planner interface without creating a second authority.
"""
from contractedge.contracts.execution_capabilities import public_behavior_check_catalog


def public_check_catalog(capabilities):
    return {row['id']: {
        'scenario': (row.get('verification_scope') or {}).get('scenario'),
        'registered': row['source_kind'] == 'REGISTERED_PUBLIC_CHECK',
        'scenario_members': row.get('covered_scenarios'),
    } for row in public_behavior_check_catalog(capabilities)}
