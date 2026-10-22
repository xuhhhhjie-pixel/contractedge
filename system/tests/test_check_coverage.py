import unittest
import hashlib
from contractedge.execution.support import support_error


def obs(key,status):
    return {'kind':'PUBLIC_OBSERVATION','locator':{'path':'check.json'},'origin':'Trusted test fixture',
            'content_digest':hashlib.sha256(('{"status":"'+status+'"}').encode()).hexdigest(),'id':key,'evidence_kind':'PUBLIC_BEHAVIOR','scenario':'trigger','snapshot_id':'S1',
            'excerpt':'{"status":"'+status+'"}', 'behavior_check':{'id':key,'status':status,'probe_sha256':'a'*64}}


class CheckCoverageTests(unittest.TestCase):
    def obligation(self,coverage='ALL_OF'):
        return {'verification_kind':'PUBLIC_BEHAVIOR','verification_scope':{'scenario':'trigger'},
                'behavior_check_refs':['a','b'],'check_coverage':coverage,'coverage_rationale':'Each named check is a necessary part of this concrete predicate.'}

    def test_one_necessary_failure_does_not_need_every_other_citation(self):
        self.assertIsNone(support_error(self.obligation(),{'status':'FAIL','evidence':[{'observation_id':'a'}]},[obs('a','FAIL'),obs('b','PASS')]))

    def test_necessary_only_cannot_establish_success(self):
        claim={'status':'PASS','evidence':[{'observation_id':'a'},{'observation_id':'b'}]}
        self.assertEqual(support_error(self.obligation('NECESSARY_ONLY'),claim,[obs('a','PASS'),obs('b','PASS')]),'CHECK_COVERAGE_INSUFFICIENT')

    def test_related_support_cannot_establish_failure(self):
        self.assertEqual(support_error(self.obligation('SUPPORTING'),{'status':'FAIL','evidence':[{'observation_id':'a'}]},[obs('a','FAIL')]),'CHECK_COVERAGE_INSUFFICIENT')

    def test_all_of_pass_needs_all_checks_and_failed_scope_is_not_usable(self):
        ob=self.obligation();claim={'status':'PASS','evidence':[{'observation_id':'a'}]}
        self.assertEqual(support_error(ob,claim,[obs('a','PASS'),obs('b','PASS')]),'METHOD_BEHAVIOR_CHECKS_INCOMPLETE')
        a=obs('a','FAIL');a['scenario']='control'
        self.assertEqual(support_error(ob,{'status':'FAIL','evidence':[{'observation_id':'a'}]},[a]),'OBSERVATION_SCENARIO_MISMATCH')

    def test_deterministic_results_are_from_bound_fresh_observations(self):
        from contractedge.execution import support
        self.assertTrue(hasattr(support,'deterministic_check_result'),'Bound probe aggregation is not implemented')
        claim=support.deterministic_check_result(self.obligation(),[obs('a','PASS'),obs('b','PASS')],snapshot_id='S1')
        self.assertEqual(claim['status'],'PASS')
        self.assertIsNone(support.deterministic_check_result(self.obligation(),[obs('a','PASS'),obs('b','PASS')],snapshot_id='S2'))

    def test_all_bound_checks_resolve_without_model_call(self):
        from contractedge.execution.review import ModelPublicReviewer
        class NeverCall:
            def complete_json(self,request):raise AssertionError('Mechanical aggregation should not require a model')
        p={'postconditions':[{'id':'P','statement':'Both concrete checks hold',**self.obligation()}]}
        r=ModelPublicReviewer(NeverCall(),len,{'model':'fixture'}).review('Public requirement',p,
            {'status':'EXECUTION_PASS','snapshot_id':'S1','observations':[obs('a','PASS'),obs('b','PASS')]})
        self.assertEqual(r['decision'],'PASS')
        self.assertEqual(r['method'],'BOUND_CHECK_AGGREGATION')

    def test_new_traceability_plan_cannot_omit_probe_coverage(self):
        from tests.test_contracts import valid_diagnosis,valid_method_input,valid_plan
        from contractedge.contracts.validation import validate_diagnosis,parse_method_input,canonical_digest
        from contractedge.changeset.validate import validate_semantic_changeset
        d=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
        d.raw['requirement_spec']['traceability_required']=True
        p=valid_plan();p['diagnosis_digest']=canonical_digest(d.raw);p['postconditions'][0]['behavior_check_refs']=['p:x']
        r=validate_semantic_changeset(p,d,None)
        self.assertIn('CHECK_COVERAGE_REQUIRED',[e.code for e in r.errors])

    def test_unobservable_sibling_cannot_hide_mechanical_failure_or_disable_retry(self):
        from contractedge.execution.review import ModelPublicReviewer
        from contractedge.orchestration.feedback import make_feedback
        class NeverCall:
            def complete_json(self,request):raise AssertionError('Only unresolved check is explicitly unobservable')
        p={'status':'READY_PARTIAL','changes':[{'id':'C','postcondition_refs':['P']}],
            'postconditions':[{'id':'P','statement':'Both concrete checks hold',**self.obligation()}],
            'open_obligations':[{'id':'U','statement':'Outside public boundary','verification_kind':'NOT_PUBLICLY_OBSERVABLE'}]}
        observations=[obs('a','FAIL'),obs('b','PASS')]
        r=ModelPublicReviewer(NeverCall(),len,{'model':'fixture'}).review('Public requirement',p,
            {'status':'EXECUTION_PASS','snapshot_id':'S1','observations':observations})
        self.assertEqual(next(c for c in r['checks'] if c['id']=='P')['status'],'FAIL')
        f=make_feedback(case_id='C',run_id='r',input_digest='a'*64,round_id=0,from_snapshot='S0',to_snapshot='S1',patch_digest='b'*64,
            execution_status='EXECUTION_PASS',observations=observations,plan=p,review=r)
        self.assertEqual(f['route'],'EXECUTE')
