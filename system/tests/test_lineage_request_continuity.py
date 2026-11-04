import copy
from types import SimpleNamespace
import pytest
from contractedge.diagnosis.prompt import _case_output_schema
from contractedge.orchestration.runner import requirement_for_rediagnosis


def requirement():
    return {'raw_text':'Keep behavior.','outcomes':[{'id':'O1','statement':'Keep behavior.'}], 'lineage_required':True,'prior_obligation_lineage':{}}


def test_initial_request_disallows_events_without_existing_parent_identities():
    schema=_case_output_schema(requirement(),[])
    assert schema['properties']['lineage_events']['maxItems']==0


def test_same_snapshot_validated_lineage_is_carried_with_real_parent_enum():
    r=requirement();ledger={'input_digest':'I','nodes':{'OB-one':{},'OB-two':{}},'bindings':{'A1':'OB-one'}}
    prior={'snapshot_id':'S','input_digest':'I','obligation_lineage':ledger}
    result=requirement_for_rediagnosis(r,prior,'S','I')
    assert result['prior_obligation_lineage']==ledger and r['prior_obligation_lineage']=={}
    schema=_case_output_schema(result,[])
    assert schema['properties']['lineage_events']['items']['properties']['parent_ids']['items']['enum']==['OB-one','OB-two']
    result['prior_obligation_lineage']['nodes'].clear()
    assert prior['obligation_lineage']['nodes']


@pytest.mark.parametrize('snap,identity',[('other','I'),('S','different')])
def test_wrong_identity_prior_cannot_enter_rediagnosis(snap,identity):
    r=requirement();p={'snapshot_id':snap,'input_digest':identity,'obligation_lineage':{'nodes':{'OB-wrong':{}}}}
    assert requirement_for_rediagnosis(r,p,'S','I')==r
