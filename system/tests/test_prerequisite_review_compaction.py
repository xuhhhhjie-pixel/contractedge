"""Keep physical-source review exact and schema correction bounded/actionable."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.orchestration.runner import required_source_prerequisites
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.changeset.prompt import build_changeset_request
from contractedge.contracts.models import RunContext
from tests.test_plan_precondition_coverage import fixture,review


def sources():
    text='func Start() { go consume() }\n'
    digest=hashlib.sha256(text.encode()).hexdigest()
    unit=dict(id='RUN',path='worker.go',snapshot_id='snap-0',locator=dict(start_line=1,end_line=1),
        text=text,content_digest=digest,precondition_review_required=True,gap_role_keys=['PUBLIC_LIFECYCLE:RUN'])
    helper=dict(id='HELPER-RUN',kind='SOURCE_SPAN',snapshot_id='snap-0',
        locator=dict(path='worker.go',start_line=1,end_line=1),excerpt=text,content_digest=digest)
    return unit,helper


def test_same_exact_source_is_reviewed_once_without_losing_role_reasons():
    unit,helper=sources();before=deepcopy((unit,helper))
    rows=required_source_prerequisites(SimpleNamespace(candidates=[unit]),helper_support=[helper],establishment_required=True)
    assert len(rows)==1
    assert rows[0]['source_evidence_ref']=='SRC-RUN'
    assert rows[0]['source_alias_refs']==['HELPER-RUN']
    assert 'initialization' in rows[0]['reason'] and 'input/output' in rows[0]['reason']
    assert (unit,helper)==before


@pytest.mark.parametrize('alter',['snapshot','path','span','body','bad_digest'])
def test_nearby_or_unverified_sources_are_not_silently_collapsed(alter):
    unit,helper=sources()
    if alter=='snapshot':helper['snapshot_id']='other'
    if alter=='path':helper['locator']['path']='other.go'
    if alter=='span':helper['locator']['start_line']=2;helper['locator']['end_line']=2
    if alter=='body':
        helper['excerpt']='func Start() { consume() }\n'
        helper['content_digest']=hashlib.sha256(helper['excerpt'].encode()).hexdigest()
    if alter=='bad_digest':helper['content_digest']='f'*64
    rows=required_source_prerequisites(SimpleNamespace(candidates=[unit]),helper_support=[helper],establishment_required=True)
    assert len(rows)==2


def test_historical_projection_does_not_gain_new_alias_rules():
    unit,helper=sources()
    rows=required_source_prerequisites(SimpleNamespace(candidates=[unit]),helper_support=[helper])
    assert [r['source_evidence_ref'] for r in rows]==['SRC-RUN','HELPER-RUN']


def test_overlong_real_shape_reports_count_and_duplicate_without_echoing_payload():
    diagnosis,plan=fixture()
    plan['precondition_review']=[{**review(),'rationale':'DO_NOT_ECHO_LARGE_PAYLOAD '*30} for _ in range(13)]
    result=validate_semantic_changeset(plan,diagnosis,None,prerequisites=[dict(source_evidence_ref='EV2')])
    assert not result.valid
    message=' '.join(e.message for e in result.errors)
    assert len(message)<1200
    assert '13' in message and '12' in message and 'EV2' in message
    assert 'DO_NOT_ECHO_LARGE_PAYLOAD' not in message
    assert 'SCHEMA_INVALID' in {e.code for e in result.errors}


def test_in_range_duplicate_feedback_names_exact_sources_to_consolidate():
    diagnosis,plan=fixture();plan['precondition_review']=[review(),review()]
    result=validate_semantic_changeset(plan,diagnosis,None,prerequisites=[dict(source_evidence_ref='EV2')])
    error=next(e for e in result.errors if e.code=='PRECONDITION_REVIEW_INCOMPLETE')
    assert 'duplicate' in error.message.lower() and 'EV2' in error.message


def test_model_schema_caps_at_supplied_unique_source_count():
    diagnosis,_=fixture();context=RunContext('r','c',0,'snap-0',None,'input',
        retrieval_config={'source_prerequisites':[dict(source_evidence_ref='EV2',reason='Inspect current source')]})
    request=build_changeset_request(diagnosis,context)
    prop=request.output_schema['properties']['precondition_review']
    assert prop['minItems']==prop['maxItems']==1


def test_safe_quote_errors_are_returned_with_cardinality_on_first_correction():
    diagnosis,plan=fixture()
    row=review();row['establishment']=dict(mode='CURRENT_SOURCE',source_quote='invented()',
        change_refs=[],mechanism='Unsupported quote')
    plan['precondition_review']=[deepcopy(row) for _ in range(13)]
    text='initialize()'
    support=[dict(id='EV2',kind='SOURCE_SPAN',snapshot_id=diagnosis.snapshot_id,excerpt=text,
        content_digest=hashlib.sha256(text.encode()).hexdigest(),locator=dict(path='worker.go',start_line=1,end_line=1))]
    result=validate_semantic_changeset(plan,diagnosis,None,prerequisites=[dict(source_evidence_ref='EV2')],source_support=support)
    codes={e.code for e in result.errors}
    assert {'SCHEMA_INVALID','PRECONDITION_REVIEW_INCOMPLETE','PRECONDITION_ESTABLISHMENT_INVALID'}<=codes
    assert len([e for e in result.errors if e.code=='PRECONDITION_ESTABLISHMENT_INVALID'])==1


def test_same_id_cannot_hide_distinct_physical_sources():
    unit,helper=sources();other=deepcopy(helper);other['locator']['path']='other.go'
    with pytest.raises(ValueError,match='ambiguous'):
        required_source_prerequisites(SimpleNamespace(candidates=[unit]),helper_support=[helper,other],establishment_required=True)


def test_shape_invalid_establishment_is_not_unsafely_semantically_inspected():
    diagnosis,plan=fixture();row=review();row['establishment']=dict(mode=[],source_quote='x')
    plan['precondition_review']=[row]
    result=validate_semantic_changeset(plan,diagnosis,None,prerequisites=[dict(source_evidence_ref='EV2')])
    assert not result.valid and any(e.code=='SCHEMA_INVALID' for e in result.errors)
