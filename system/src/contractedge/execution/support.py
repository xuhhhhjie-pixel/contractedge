"""Public evidence capability checks shared by review and feedback acceptance.

Kinds are assigned by the observation producer, never by the review response.
They establish what can be observed, not that a semantic inference is correct.
"""
from collections.abc import Mapping
from contractedge.execution.comparison import compare_declared_input
from contractedge.contracts.execution_capabilities import behavior_binding_error, public_behavior_check_catalog

_UNSPECIFIED_SNAPSHOT = object()


def support_error(obligation, claim, observations, artifacts=(), *,
                  snapshot_id=_UNSPECIFIED_SNAPSHOT, capabilities=None):
    if obligation.get('interpretation_disputed'):
        return 'DISPUTED_REQUIREMENT_INTERPRETATION'
    required = obligation.get('verification_kind', 'UNKNOWN')
    if required in {'NOT_PUBLICLY_OBSERVABLE', 'BUILD_ONLY'}:
        return 'NO_PUBLIC_SEMANTIC_EVIDENCE'
    allowed = {'PUBLIC_BEHAVIOR'} if required == 'PUBLIC_BEHAVIOR' else {'PUBLIC_STRUCTURE', 'PUBLIC_BEHAVIOR'}
    by_id = {o['id']: o for o in observations}
    if len(by_id) != len(observations):
        return 'OBSERVATION_ID_AMBIGUOUS'
    citations = claim.get('evidence', [])
    eligible = []
    scope_error = None
    scope = obligation.get('verification_scope') or {}
    for citation in citations:
        record = by_id.get(citation.get('observation_id'))
        if not record or record.get('evidence_kind') not in allowed:
            continue
        if (snapshot_id is not _UNSPECIFIED_SNAPSHOT and snapshot_id is not None
                and record.get('snapshot_id') != snapshot_id):
            scope_error = 'OBSERVATION_SNAPSHOT_MISMATCH'
            continue
        if scope.get('scenario') and record.get('scenario') != scope['scenario']:
            scope_error = 'OBSERVATION_SCENARIO_MISMATCH'
            continue
        resource = scope.get('resource')
        if resource and (not isinstance(record.get('resource_identity'), Mapping)
                         or any(record['resource_identity'].get(k) != v for k, v in resource.items())):
            scope_error = 'OBSERVATION_RESOURCE_MISMATCH'
            continue
        eligible.append(record)
    if not eligible:
        return scope_error or 'OBSERVATION_CAPABILITY_INSUFFICIENT'
    if claim.get('status')=='PASS':
        from contractedge.diagnosis.operation_consistency import operation_facts
        snapshots={r.get('snapshot_id') for r in eligible}
        relevant=[r for r in observations if r.get('snapshot_id') in snapshots
            and (not scope.get('scenario') or r.get('scenario')==scope['scenario'])
            and (not scope.get('resource') or isinstance(r.get('resource_identity'),Mapping)
                and all(r['resource_identity'].get(k)==v for k,v in scope['resource'].items()))]
        if any(o['status']!='SUCCEEDED' for o in operation_facts(relevant)):
            return 'OPERATION_SUCCESS_CONFLICT'
    probe_records = [r for r in eligible if r.get('behavior_check')]
    refs = obligation.get('behavior_check_refs') or []
    if not isinstance(refs, (list, tuple)) or any(not isinstance(ref, str) for ref in refs):
        return 'METHOD_BEHAVIOR_BINDINGS_INVALID'
    bound = set(refs)
    if probe_records and not bound:
        return 'METHOD_BEHAVIOR_BINDINGS_MISSING'
    if bound:
        if len({r['behavior_check']['id'] for r in probe_records}) != len(probe_records):
            return 'METHOD_BEHAVIOR_CHECK_AMBIGUOUS'
        available = {r['behavior_check']['id']: r['behavior_check']['status'] for r in probe_records}
        coverage = obligation.get('check_coverage')
        if coverage == 'SUPPORTING' or (coverage == 'NECESSARY_ONLY' and claim.get('status') == 'PASS'):
            return 'CHECK_COVERAGE_INSUFFICIENT'
        necessary_failure = (coverage in {'ALL_OF', 'NECESSARY_ONLY'} and claim.get('status') == 'FAIL'
                             and any(available.get(k) == 'FAIL' for k in bound))
        if not bound <= available.keys() and not necessary_failure:
            return 'METHOD_BEHAVIOR_CHECKS_INCOMPLETE'
        statuses = [available[k] for k in bound if k in available]
        if ((claim.get('status')=='PASS' and any(v!='PASS' for v in statuses))
                or (claim.get('status')=='FAIL' and 'FAIL' not in statuses)):
            return 'METHOD_BEHAVIOR_RESULT_UNSUPPORTED'
        error = behavior_binding_error(obligation, capabilities)
        if error:
            return 'CHECK_COVERAGE_INSUFFICIENT' if error == 'CHECK_COVERAGE_REQUIRED' else error
        if snapshot_id is not _UNSPECIFIED_SNAPSHOT and (not isinstance(snapshot_id, str) or not snapshot_id):
            return 'OBSERVATION_SNAPSHOT_UNAVAILABLE'
        if capabilities is not None:
            catalog = {row['id']: row for row in public_behavior_check_catalog(capabilities)}
            if any(record['behavior_check'].get('probe_sha256') != catalog[record['behavior_check']['id']]['producer_sha256']
                   for record in probe_records if record['behavior_check']['id'] in bound):
                return 'METHOD_BEHAVIOR_PRODUCER_MISMATCH'
    comparisons = claim.get('comparisons', [])
    required_bindings = obligation.get('comparison_requirements', [])
    def binding(value):
        return tuple(value.get(k) for k in ('artifact_id', 'input_pointer', 'output_pointer'))
    if required == 'DECLARED_INPUT_EQUALITY' and not required_bindings:
        return 'PREDECLARED_COMPARISON_REQUIREMENTS_MISSING'
    if required_bindings and not {binding(v) for v in required_bindings} <= {binding(v) for v in comparisons}:
        return 'REQUIRED_FIELD_COMPARISON_INCOMPLETE'
    if comparisons:
        eligible_ids = {o['id'] for o in eligible}
        if any(c.get('observation_id') not in eligible_ids for c in comparisons):
            return 'COMPARISON_NOT_BOUND_TO_SUPPORTED_EVIDENCE'
        results = [compare_declared_input(c, artifacts, observations)['status'] for c in comparisons]
        if claim.get('status') == 'PASS' and any(v != 'EQUAL' for v in results):
            return 'PUBLIC_COMPARISON_UNSUPPORTED'
        if claim.get('status') == 'FAIL' and ('DIFFERENT' not in results or 'UNAVAILABLE' in results):
            return 'PUBLIC_COMPARISON_DOES_NOT_SUPPORT_FAILURE'
    return None


def deterministic_check_result(obligation, observations, *, snapshot_id, capabilities=None):
    """Aggregate only explicit all-of/necessary probe bindings on fresh records.

    Binding relevance/sufficiency remains a predeclared semantic claim. This
    function neither chooses bindings nor accepts status strings from raw logs.
    """
    coverage = obligation.get('check_coverage')
    if coverage not in {'ALL_OF', 'NECESSARY_ONLY'} or not obligation.get('behavior_check_refs'):
        return None
    bound = set(obligation['behavior_check_refs'])
    records = [o for o in observations if o.get('snapshot_id') == snapshot_id
               and o.get('behavior_check', {}).get('id') in bound and o.get('excerpt')]
    available = {o['behavior_check']['id']: o['behavior_check']['status'] for o in records}
    status = ('FAIL' if any(available.get(k) == 'FAIL' for k in bound) else
              'PASS' if coverage == 'ALL_OF' and bound <= available.keys()
              and all(available[k] == 'PASS' for k in bound) else None)
    if status is None:
        return None
    claim = {'status':status, 'rationale':'Deterministic aggregation of predeclared '+coverage+' check bindings; semantic coverage remains declared, not independently proven.',
             'evidence':[{'observation_id':o['id'], 'quote':o['excerpt']} for o in records],
             'decision_method':'BOUND_CHECK_AGGREGATION'}
    return claim if support_error(obligation,claim,observations,
                                 snapshot_id=snapshot_id, capabilities=capabilities) is None else None
