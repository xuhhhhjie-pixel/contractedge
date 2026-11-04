"""References and their lexical windows are not replacement evidence records."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from contractedge.diagnosis.evidence import field_contract_contexts
from contractedge.model.admission import quote_request
from contractedge.model.provider import ModelRequest
from contractedge.orchestration.semantic_delivery import (
    SemanticDeliveryError, delivery_context, inspect_delivery, require_delivery,
)


TEXT='Preserve the configured timeout.'
SOURCE='type Config struct {\n\t// Timeout controls waiting.\n\tTimeout string `json:"timeout,omitempty"`\n\tOther bool `json:"other,omitempty"`\n}\n'
UNIT=dict(id='CU-config',path='config.go',kind='type',snapshot_id='s0',text=SOURCE,
          content_digest=sha256(SOURCE.encode()).hexdigest(),
          locator=dict(start_line=40,end_line=44,column_start=1,start_char=0,end_char=len(SOURCE)))
RECORD=dict(id='SRC-CU-config',kind='SOURCE_SPAN',snapshot_id='s0',
            locator=dict(path='config.go',**UNIT['locator']),excerpt=SOURCE,
            content_digest=UNIT['content_digest'],delivery_required=True)


class Prepared:
    def prepare_request(self,request):
        body=json.dumps(dict(messages=list(request.messages))).encode()
        return SimpleNamespace(body=body,metadata={},overhead='',output_schema=None,wire_schema=None,
            audit_record=lambda:dict(body_sha256=sha256(body).hexdigest()))

    def complete_prepared_json(self,request,prepared):
        raise AssertionError('A delivery regression must not call a model')


def inspect(body,*,frame=None):
    request=ModelRequest(model='fixture',prompt_version='reference-record-regression',
        messages=({'role':'user','content':json.dumps(dict(requirement_text=TEXT,**body))},),
        max_output_tokens=32)
    context=frame or delivery_context(dict(requirement_text=TEXT),consumer='DIAGNOSIS',
                                     snapshot_id='s0',necessary_support=[RECORD])
    result=inspect_delivery(request,quote_request(Prepared(),request,len),context)
    assert result['request_projection']['authority']=='PREPARED_MESSAGES_SCHEMA_TOOLS'
    return result


def classification(report,identifier=RECORD['id']):
    return next(row['classification'] for row in report['subjects'] if row['id']==identifier)


@pytest.mark.parametrize('own_field',['producer','kind','excerpt','content_digest'])
def test_real_field_context_producer_does_not_replace_its_full_source_record(own_field):
    references=field_contract_contexts([UNIT],TEXT)
    assert len(references)==1 and references[0]['locator']!=RECORD['locator']
    assert references[0]['source_content_digest']==RECORD['content_digest']
    assert references[0]['excerpt'] in RECORD['excerpt']
    if own_field=='kind':references[0]['kind']='DERIVED_FIELD_WINDOW'
    elif own_field=='excerpt':references[0]['excerpt']='A reference-local explanatory excerpt.'
    elif own_field=='content_digest':references[0]['content_digest']=sha256(b'local projection').hexdigest()
    report=inspect(dict(evidence_catalog=[RECORD],source_field_contract_contexts=references))
    assert classification(report)=='FULL_ORIGINAL'
    require_delivery(report)


@pytest.mark.parametrize('reference_key',['source_evidence_ref','change_id'])
def test_reference_with_its_own_values_does_not_override_a_canonical_evidence_body(reference_key):
    reference={reference_key:RECORD['id'],'kind':'OTHER_ROLE','locator':{'path':'other.go'},
               'excerpt':'Reference-local text','content_digest':'local'}
    report=inspect(dict(evidence_catalog=[RECORD],references=[reference]))
    assert classification(report)=='FULL_ORIGINAL'
    require_delivery(report)


@pytest.mark.parametrize('reference_key',['source_evidence_ref','change_id'])
def test_a_reference_cannot_supply_source_body_even_with_all_other_original_fields(reference_key):
    reference=deepcopy(RECORD);reference[reference_key]=reference.pop('id')
    report=inspect(dict(references=[reference]))
    assert classification(report)=='IDENTITY_ONLY'
    with pytest.raises(SemanticDeliveryError):require_delivery(report)


@pytest.mark.parametrize('fault',['locator','kind','snapshot','excerpt','digest'])
@pytest.mark.parametrize('incomplete',[False,True])
def test_real_same_id_damage_is_detected_even_beside_a_complete_original(fault,incomplete):
    damaged=deepcopy(RECORD)
    if incomplete:damaged.pop('excerpt' if fault=='kind' else 'kind')
    if fault=='locator':damaged['locator']['start_line']+=1
    elif fault=='kind':damaged['kind']='STATIC_SEARCH'
    elif fault=='snapshot':damaged['snapshot_id']='different'
    elif fault=='excerpt':damaged['excerpt']='changed\n'
    else:damaged['content_digest']='0'*64
    report=inspect(dict(evidence_catalog=[RECORD,damaged]))
    assert classification(report)==('CONTENT_DRIFT' if fault in {'excerpt','digest'} else 'IDENTITY_DRIFT')
    with pytest.raises(SemanticDeliveryError):require_delivery(report)


@pytest.mark.parametrize('missing',['excerpt','kind','locator'])
def test_incomplete_same_id_body_alone_does_not_prove_complete_delivery(missing):
    incomplete=deepcopy(RECORD);incomplete.pop(missing)
    report=inspect(dict(evidence_catalog=[incomplete],references=field_contract_contexts([UNIT],TEXT)))
    assert classification(report) not in {'FULL_ORIGINAL','EXPLICIT_SOURCE_REREAD'}
    with pytest.raises(SemanticDeliveryError):require_delivery(report)


def change_frame():
    change=dict(id='C1',operation='REPLACE',semantic_action='USE_CONFIGURED_TIMEOUT',intent=TEXT)
    projection=dict(change_id=change['id'],**{key:value for key,value in change.items() if key!='id'})
    frame=delivery_context(dict(requirement_text=TEXT),consumer='EXECUTION',snapshot_id='s0',
        changeset=dict(changes=[change]),plan=dict(operations=[projection]))
    return change,projection,frame


def test_change_record_and_registered_projection_keep_existing_conflict_reporting():
    change,projection,frame=change_frame()
    assert classification(inspect(dict(changes=[change]),frame=frame),'C1')=='FULL_ORIGINAL'
    assert classification(inspect(dict(operations=[projection]),frame=frame),'C1')=='EXPLICIT_SUMMARY'
    changed=deepcopy(projection);changed['semantic_action']='DIFFERENT_PROJECTION'
    assert classification(inspect(dict(operations=[changed]),frame=frame),'C1')=='CONTENT_DRIFT'
    assert classification(inspect(dict(changes=[change],operations=[changed]),frame=frame),'C1')=='CONTENT_DRIFT'


def test_changed_real_change_record_is_still_reported_as_drift():
    change,_,frame=change_frame();damaged={**change,'semantic_action':'DIFFERENT_ACTION'}
    assert classification(inspect(dict(changes=[change,damaged]),frame=frame),'C1')=='CONTENT_DRIFT'
