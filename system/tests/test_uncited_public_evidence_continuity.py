"""A diagnostic citation choice must not erase available current observations."""
from dataclasses import replace
import hashlib
import pytest
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import parse_method_input
from contractedge.orchestration.runner import _bind_diagnosis_envelope
from contractedge.retrieval.candidates import CandidateSet
from tests.test_contracts import valid_method_input


def fixture(snapshot='snap-0'):
    text='{"operation":"update","error":"injected","status":"PASS"}'
    record=dict(id='OBS-control',snapshot_id=snapshot,kind='PUBLIC_OBSERVATION',
        locator={'path':'raw/control.json'},excerpt=text,
        content_digest=hashlib.sha256(text.encode()).hexdigest(),origin='Public runtime')
    method=replace(parse_method_input(valid_method_input()),public_evidence=(record,))
    context=RunContext('run',method.case_id,0,'snap-0',None,method.input_digest)
    return method,context,CandidateSet((),(),(),{},(),0,10000,12),record


def test_uncited_current_public_fact_survives_without_creating_semantic_claim():
    method,context,candidates,record=fixture()
    raw={'evidence_refs':[],'status':'EVIDENCE_GAP','violations':[]}
    body,audit=_bind_diagnosis_envelope(raw,method,context,{},candidates)
    assert body['evidence_refs']==[record]
    assert body['status']=='EVIDENCE_GAP' and body['violations']==[]
    assert raw['evidence_refs']==[]
    assert audit['retained_uncited_public_evidence_ids']==['OBS-control']


def test_retaining_current_observations_does_not_duplicate_a_real_citation():
    method,context,candidates,record=fixture()
    body,audit=_bind_diagnosis_envelope({'evidence_refs':['OBS-control']},method,context,{},candidates)
    assert body['evidence_refs']==[record]
    assert audit['retained_uncited_public_evidence_ids']==[]
    with pytest.raises(ValueError,match='unknown catalog ID'):
        _bind_diagnosis_envelope({'evidence_refs':['FORGED']},method,context,{},candidates)


def test_uncited_old_snapshot_is_not_rebound_as_current_observation():
    method,context,candidates,_=fixture('old-snapshot')
    with pytest.raises(ValueError,match='current snapshot'):
        _bind_diagnosis_envelope({'evidence_refs':[]},method,context,{},candidates)
