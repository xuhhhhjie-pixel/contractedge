import unittest

from contractedge.orchestration.feedback import make_feedback


def feedback(plan, checks, **extra):
    from tests.test_optimization_contract_flow import observation
    record = observation()
    checks = [{**c, 'evidence': [{'observation_id': record['id'], 'quote': record['excerpt']}]} for c in checks]
    return make_feedback(case_id='CASE', run_id='run', input_digest='a' * 64,
        round_id=0, from_snapshot='S0', to_snapshot='S1', patch_digest='b' * 64,
        execution_status='EXECUTION_PASS', observations=[record], plan=plan,
        review={'decision': 'PASS' if all(c['status'] == 'PASS' for c in checks) else 'FAIL',
                'checks': checks, 'failure_kind': 'DIAGNOSIS'}, **extra)


class RetainedFeedbackTests(unittest.TestCase):
    def test_previous_pass_cannot_disappear_when_new_plan_only_repairs_other_postcondition(self):
        from contractedge.orchestration.feedback import review_plan_with_retained
        first = feedback({'postconditions': [{'id': 'P1', 'statement': 'Keep selector'},
                                             {'id': 'P2', 'statement': 'Restore affinity'}]},
                         [{'id': 'P1', 'status': 'PASS'}, {'id': 'P2', 'status': 'FAIL'}])
        new_plan = {'postconditions': [{'id': 'P2', 'statement': 'Restore affinity'}]}
        effective = review_plan_with_retained(new_plan, first)
        retained_id = 'preserve:' + effective['preserve_constraints'][0]['id']
        second = feedback(effective, [{'id': 'P2', 'status': 'PASS'}])
        self.assertEqual(second['semantic_outcome'], 'UNKNOWN')
        self.assertIn(retained_id, [p['id'] for p in second['pending_obligations']])
        third = feedback(review_plan_with_retained(new_plan, second),
                         [{'id': 'P2', 'status': 'PASS'}, {'id': retained_id, 'status': 'FAIL'}])
        self.assertEqual(third['semantic_outcome'], 'PUBLIC_REVIEW_FAIL')
        self.assertTrue(any(c['statement'] == 'Keep selector' for c in third['retained_constraints']))
        final_plan = review_plan_with_retained(new_plan, third)
        fourth = feedback(final_plan, [{'id': 'P2', 'status': 'PASS'}, {'id': retained_id, 'status': 'PASS'}])
        self.assertEqual(fourth['semantic_outcome'], 'PUBLIC_REVIEW_PASS')

    def test_passed_preservation_constraint_also_becomes_a_persistent_candidate(self):
        from contractedge.orchestration.feedback import review_plan_with_retained
        first = feedback({'preserve_constraints': [{'id': 'K1', 'statement': 'No disabled target Jobs', 'outcome_id': 'O1'}]},
                         [{'id': 'preserve:K1', 'status': 'PASS'}])
        effective = review_plan_with_retained({'postconditions': []}, first)
        self.assertEqual(effective['preserve_constraints'][0]['statement'], 'No disabled target Jobs')
        self.assertEqual(effective['preserve_constraints'][0]['requirement_refs'], ['O1'])
