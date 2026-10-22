"""Interpret frozen producer operation results separately from returned fields.

An exception is a failure fact, not a causal diagnosis. Only a frozen explicit
negative-control contract can mechanically authorize an expected exception.
"""
import hashlib
import json


def operation_facts(observations):
    facts=[]
    for record in observations:
        if (not record.get('structured_complete') or not record.get('producer_manifest_sha256')
                or not record.get('source_file_sha256')):
            continue
        try:
            item=json.loads(record['excerpt'])
        except (KeyError,TypeError,ValueError):
            continue
        adapter=record.get('adapter_id'); exception=None
        if adapter in {'public-object-adoption-v1','public-object-adoption-operation-v2'} and isinstance(item,dict) and 'exception' in item:
            exception=item['exception']; complete=exception is None and isinstance(item.get('resource'),dict)
            status='FAILED' if exception is not None else 'SUCCEEDED' if complete else 'UNKNOWN'
        elif adapter=='public-oci-transport-v1' and isinstance(item,dict):
            errors={k:item[k] for k in ('setup_error','client_error','pull_error') if item.get(k)}
            exception=errors or None; complete=not errors and isinstance(item.get('requests'),list)
            status='FAILED' if errors else 'SUCCEEDED' if complete else 'UNKNOWN'
        else:
            continue
        identity=[record.get(k) for k in ('snapshot_id','producer_manifest_sha256','source_file_sha256','source_pointer','scenario')]
        facts.append(dict(operation_id='OP-'+hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24],
            observation_id=record['id'],snapshot_id=record['snapshot_id'],scenario=record.get('scenario'),
            resource_identity=record.get('resource_identity',{}),status=status,exception=exception,
            result_complete=complete,adapter_id=adapter,content_digest=record['content_digest']))
    return facts


def _expected(assessment, operation, context):
    refs={e['id']:e.get('excerpt','') for e in context.get('public_contract_evidence',[])}
    ref=assessment.get('contract_ref'); quote=assessment.get('contract_quote','')
    exception=operation.get('exception') or {}
    if (not quote or quote not in refs.get(ref,'') or assessment.get('scenario')!=operation.get('scenario')
            or assessment.get('exception_type')!=exception.get('type')):
        return False
    return any(all(e.get(k)==assessment.get(k) for k in ('operation_id','scenario','exception_type','contract_ref','contract_quote'))
               for e in context.get('operation_expectations',[]))


def validate_operation_claims(payload: dict, operations: list[dict], obligations: dict) -> list[dict]:
    assessments=payload.get('operation_assessments',[]);by_id={o['operation_id']:o for o in operations}
    coverage=payload.get('outcome_coverage',[])
    known_outcomes=set(coverage) if isinstance(coverage,dict) else {r.get('outcome_id') for r in coverage}
    errors=[];seen=set()
    for row in assessments:
        oid=row.get('operation_id'); op=by_id.get(oid)
        if not op or oid in seen:
            errors.append(dict(code='OPERATION_REFERENCE_INVALID',operation_id=oid));continue
        seen.add(oid)
        if known_outcomes and not set(row.get('outcome_refs',[]))<=known_outcomes:
            errors.append(dict(code='OPERATION_OUTCOME_INVALID',operation_id=oid))
        disposition=row.get('disposition')
        if disposition=='EXPECTED_EXCEPTION' and not _expected(row,op,obligations):
            errors.append(dict(code='EXPECTED_EXCEPTION_NOT_ESTABLISHED',operation_id=oid,
                reason='A quoted model interpretation is not a frozen negative-control contract; retain UNKNOWN or obtain public causal evidence'))
        elif disposition=='REQUIRES_SUCCESS' and op['status']!='SUCCEEDED' and row.get('satisfaction')=='SATISFIED':
            errors.append(dict(code='OPERATION_SUCCESS_CONFLICT',operation_id=oid))
        elif disposition=='UNRELATED':
            # No self-reported unrelated label can waive a failed operation that
            # is in scope of a global satisfaction claim. Scope must be explicit.
            if not row.get('scope_evidence_refs'):
                errors.append(dict(code='OPERATION_UNRELATED_UNSUPPORTED',operation_id=oid))
    if isinstance(coverage,dict):
        preserving=any(r.get('disposition')=='PRESERVE_REQUIRED' for r in coverage.values())
        preserved={k for k,r in coverage.items() if r.get('disposition')=='PRESERVE_REQUIRED'}
    else:
        preserving=any(r.get('preserve_current_satisfaction') or r.get('preserve_required') for r in coverage)
        preserved={r.get('outcome_id') for r in coverage if r.get('preserve_current_satisfaction') or r.get('preserve_required')}
    for op in operations:
        row=next((a for a in assessments if a.get('operation_id')==op['operation_id']),{})
        refs=set(row.get('outcome_refs',[]))
        separately_failed=(bool(refs) and refs<=known_outcomes and not refs&preserved
            and row.get('disposition')=='REQUIRES_SUCCESS' and row.get('satisfaction')!='SATISFIED')
        if preserving and op['status']!='SUCCEEDED' and not (
                separately_failed or row.get('disposition')=='EXPECTED_EXCEPTION' and _expected(row,op,obligations)):
            errors.append(dict(code='OPERATION_SUCCESS_CONFLICT',operation_id=op['operation_id'],
                reason='Global satisfaction cannot ignore a failed or incomplete public operation; identify scope or leave unresolved'))
    return errors


def validate_public_check_claims(payload, observations, snapshot_id):
    """A global satisfaction claim must not hide current registered failures.

    A predicate failure does not identify a production cause. Partial outcomes,
    unresolved evidence and explicitly unknown satisfaction stay permissible.
    Expected negative controls must pass their declared negative predicate.
    """
    rows=payload.get('outcome_coverage',[])
    if isinstance(rows,dict):
        satisfied=bool(rows) and all(r.get('disposition')=='PRESERVE_REQUIRED'
            and r.get('preservation_status') in {'OBSERVED','INFERRED'} for r in rows.values())
    else:
        satisfied=bool(rows) and all(r.get('preserve_required') and not r.get('unresolved')
            and not r.get('repair_supported') and r.get('preserve_current_satisfaction')
            and all(s.get('status') in {'OBSERVED','INFERRED'} for s in r['preserve_current_satisfaction']) for r in rows)
    if not satisfied:return []
    return [dict(code='PUBLIC_PREDICATE_SATISFACTION_CONFLICT',observation_id=o['id'],
                 check_id=o['behavior_check']['id'],scenario=o.get('scenario'),
                 reason='A current registered predicate failed. Explain its scope; retain UNKNOWN or establish a source-backed violation, never invent a causal repair.')
            for o in observations if o.get('snapshot_id')==snapshot_id and o.get('evidence_kind')=='PUBLIC_BEHAVIOR'
            and o.get('behavior_check',{}).get('status')=='FAIL' and o['behavior_check'].get('probe_sha256')]
