import json
from copy import deepcopy
from pathlib import Path

import pytest

from contractedge.contracts.validation import parse_method_input
from contractedge.orchestration.repair import validate_ready_trace


def saved_fixture():
    out=Path('outputs/reliability-closure-20261020')
    case=out/'runs/delivery-0.6.13-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    trace=json.loads((case/'rounds/0/front.trace.json').read_text())
    method=parse_method_input(json.loads((case/'method_input.json').read_text()))
    source=next((out/'public-source-alertmanager').iterdir())
    return trace,method,source


def test_actual_ready_plan_with_required_delivery_metadata_reaches_consumer_preflight():
    trace,method,source=saved_fixture()
    packet,context=validate_ready_trace(trace,method,source)
    assert packet.snapshot_id==trace['snapshot_id']
    assert context.retrieval_config['necessary_source_support']
    assert all(r['delivery_required'] for r in context.retrieval_config['necessary_source_support'])


@pytest.mark.parametrize('change',['remove','tamper','weaken'])
def test_actual_ready_plan_still_rejects_missing_or_forged_source_context(change):
    trace,method,source=saved_fixture();trace=deepcopy(trace)
    support=trace['run_context']['retrieval_config']['necessary_source_support']
    if change=='remove':support.pop()
    elif change=='tamper':support[0]['excerpt']+=' invented'
    else:support[0]['delivery_required']=False
    with pytest.raises(ValueError,match='context differs'):
        validate_ready_trace(trace,method,source)


def kopf_ready_fixture():
    case=Path('outputs/reliability-closure-20261020/runs/delivery-0.6.20-repeat-R3-KOPF-726-1-candidate/cases/R3-KOPF-726')
    trace=json.loads((case/'rounds/0/front.trace.json').read_text())
    method=parse_method_input(json.loads((case/'method_input.json').read_text()))
    return trace,method,Path('outputs/reliability-closure-20261020/kopf-closure-replay-v0620/S0')/method.source_root


def test_actual_exception_plan_passes_the_same_ready_consumer_contract():
    trace,method,source=kopf_ready_fixture()
    packet,context=validate_ready_trace(trace,method,source)
    assert packet.snapshot_id==trace['snapshot_id']
    assert 'exception-producing guard' in context.retrieval_config['source_prerequisites'][0]['reason']


@pytest.mark.parametrize('change',['missing','changed_reason','wrong_source'])
def test_exception_prerequisite_projection_cannot_be_removed_or_forged(change):
    trace,method,source=kopf_ready_fixture();trace=deepcopy(trace)
    entries=trace['run_context']['retrieval_config']['source_prerequisites']
    if change=='missing':entries.clear()
    elif change=='changed_reason':entries[0]['reason']='Claim unconditional success'
    else:entries[0]['source_evidence_ref']='SRC-forged'
    with pytest.raises(ValueError,match='context differs'):
        validate_ready_trace(trace,method,source)


def test_shared_prerequisite_projection_distinguishes_roles_without_adding_requirements():
    from types import SimpleNamespace
    from contractedge.orchestration.runner import required_source_prerequisites
    candidates=SimpleNamespace(candidates=[
        dict(id='exception',precondition_review_required=True,gap_role_keys=['PUBLIC_EXCEPTION:exception']),
        dict(id='lifecycle',precondition_review_required=True,gap_role_keys=['PUBLIC_LIFECYCLE:lifecycle']),
        dict(id='ordinary',gap_role_keys=[]),
    ])
    rows=required_source_prerequisites(candidates)
    assert [r['source_evidence_ref'] for r in rows]==['SRC-exception','SRC-lifecycle']
    assert 'exception-producing guard' in rows[0]['reason']
    assert 'initialization, readiness' in rows[1]['reason']
    assert required_source_prerequisites(SimpleNamespace(candidates=[]))==[]
