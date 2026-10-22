import unittest
from tests.test_contracts import valid_diagnosis,valid_method_input,valid_plan
from contractedge.contracts.validation import validate_diagnosis,parse_method_input
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.orchestration.feedback import make_feedback


class PreservationImpactTests(unittest.TestCase):
    def plan(self):
        p=valid_plan();p['preserve_constraints']=[{'id':'KEEP','outcome_id':'O1','obligation_refs':['A1'],
          'statement':'Keep original field','current_satisfaction':'UNKNOWN','evidence_refs':[]}]
        p['preservation_impact']=[{'preserve_id':'KEEP','scope':'CHANGES','change_refs':['C1'],'rationale':'The same resource is modified.'}]
        return p

    def test_unknown_change_and_omitted_preservation_are_rejected(self):
        d=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None);p=self.plan()
        p['preservation_impact'][0]['change_refs']=['fake']
        result=validate_semantic_changeset(p,d,None)
        self.assertIn('PRESERVATION_IMPACT_INVALID',[r.code for r in result.errors])
        p['preservation_impact']=[]
        result=validate_semantic_changeset(p,d,None)
        self.assertIn('PRESERVATION_IMPACT_INCOMPLETE',[r.code for r in result.errors])

    def test_preservation_feedback_links_possible_influencing_changes(self):
        p=self.plan();f=make_feedback(case_id='X',run_id='r',input_digest='a'*64,round_id=0,
          from_snapshot='S0',to_snapshot='S1',patch_digest='b'*64,execution_status='EXECUTION_PASS',
          observations=[],plan=p,review={'decision':'UNKNOWN','checks':[]})
        link=next(r for r in f['coordination_links'] if r['check_id']=='preserve:KEEP')
        self.assertEqual(link['change_refs'],['C1'])
        self.assertIn('not',link['interpretation'])
