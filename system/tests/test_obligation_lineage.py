import unittest
from contractedge.requirement.normalize import normalize_requirement
from contractedge.orchestration.feedback import review_plan_with_retained, _constraint_identity


class ObligationLineageTests(unittest.TestCase):
    def test_markdown_condition_and_clause_offsets_are_original(self):
        raw='# Requirement\n\nWhen enabled, bind config without consuming it. Later functions retain config.\n'
        n=normalize_requirement({'requirement_text':raw})
        self.assertEqual(n['trigger'],'When enabled')
        self.assertIn('clauses',n)
        self.assertEqual(len(n['clauses']),2)
        for c in n['clauses']:
            _,a,b=c['source_ref'].split(':')
            self.assertEqual(raw[int(a):int(b)],c['text'])

    def test_same_clause_different_predicates_have_distinct_stable_keys(self):
        from contractedge.requirement import normalize
        self.assertTrue(hasattr(normalize,'bind_obligation_sources'),'Source-grounded identity binding is absent')
        raw='Bind config without consuming it.';n=normalize_requirement({'requirement_text':raw});ref=n['clauses'][0]['source_ref']
        atoms=[{'id':'A1','outcome_id':'O1','statement':'Bind config','source_refs':[ref],'source_quote':'Bind config'},
               {'id':'A2','outcome_id':'O1','statement':'Do not consume','source_refs':[ref],'source_quote':'without consuming it'}]
        payload={'obligation_atoms':atoms};normalize.bind_obligation_sources(payload,n)
        self.assertNotEqual(atoms[0]['stable_obligation_key'],atoms[1]['stable_obligation_key'])
        key=atoms[0]['stable_obligation_key'];atoms[0]['statement']='Supply the configuration';normalize.bind_obligation_sources(payload,n)
        self.assertEqual(atoms[0]['stable_obligation_key'],key)
        atoms[0]['source_quote']='invented'
        with self.assertRaises(ValueError):normalize.bind_obligation_sources(payload,n)

    def test_paraphrase_cannot_erase_old_binding(self):
        prior={'retained_constraints':[{'statement':'Keep configuration','source_snapshot':'S1','stable_obligation_keys':['stable-a'],
            'verification_kind':'PUBLIC_BEHAVIOR','behavior_check_refs':['p:owner','p:final'],'check_coverage':'ALL_OF'}]}
        plan={'postconditions':[],'open_obligations':[], 'preserve_constraints':[{'id':'P','statement':'Retain config',
            'stable_obligation_keys':['stable-a'],'behavior_check_refs':['p:owner'],'verification_kind':'PUBLIC_BEHAVIOR'}]}
        result=review_plan_with_retained(plan,prior)
        self.assertEqual(len(result['preserve_constraints']),1)
        self.assertEqual(set(result['preserve_constraints'][0]['behavior_check_refs']),{'p:owner','p:final'})

    def test_distinct_lineage_cannot_be_merged_by_shared_outcome(self):
        a={'statement':'Retain config','requirement_refs':['O1'],'stable_obligation_keys':['a']}
        b={**a,'stable_obligation_keys':['b']}
        self.assertNotEqual(_constraint_identity(a),_constraint_identity(b))

    def test_shared_identity_retains_historical_semantics_not_only_checks(self):
        previous={'retained_constraints':[{'statement':'retain config and workload','source_snapshot':'S0',
            'verification_kind':'PUBLIC_STRUCTURE','stable_obligation_keys':['A']}]}
        current={'postconditions':[{'id':'P','statement':'retain config','verification_kind':'PUBLIC_STRUCTURE',
            'stable_obligation_keys':['A']}],'preserve_constraints':[],'open_obligations':[]}
        review=review_plan_with_retained(current,previous)
        self.assertIn('retain config and workload',review['postconditions'][0]['statement'])

    def test_first_feedback_preserves_both_same_atom_postcondition_statements(self):
        from contractedge.orchestration.feedback import make_feedback
        from tests.test_observation_capability_gate import record
        observation=record('config workload','PUBLIC_STRUCTURE');observation['snapshot_id']='S1'
        conditions=[{'id':'P1','statement':'retain config','stable_obligation_keys':['A']},
                    {'id':'P2','statement':'retain workload','stable_obligation_keys':['A']}]
        checks=[{'id':p['id'],'status':'PASS','evidence':[{'observation_id':observation['id'],'quote':p['statement'].split()[-1]}]} for p in conditions]
        f=make_feedback(case_id='C',run_id='r',input_digest='a'*64,round_id=0,from_snapshot='S0',to_snapshot='S1',patch_digest='b'*64,
            execution_status='EXECUTION_PASS',observations=[observation],plan={'postconditions':conditions},review={'decision':'PASS','checks':checks})
        texts=' '.join(p['statement'] for p in f['retained_constraints'])
        self.assertIn('retain config',texts);self.assertIn('retain workload',texts)

    def test_paraphrase_of_necessary_predicate_keeps_failure_usable(self):
        from contractedge.execution.support import support_error
        old={'statement':'Retain configuration','source_snapshot':'S0','stable_obligation_keys':['a'],
            'verification_kind':'PUBLIC_BEHAVIOR','behavior_check_refs':['a'],'check_coverage':'NECESSARY_ONLY'}
        new={**old,'id':'P','statement':'Keep configuration'}
        review=review_plan_with_retained({'postconditions':[new],'preserve_constraints':[],'open_obligations':[]},
            {'retained_constraints':[old]})
        condition=review['postconditions'][0]
        self.assertEqual(condition['check_coverage'],'NECESSARY_ONLY')
        self.assertIsNone(support_error(condition,{'status':'FAIL','evidence':[{'observation_id':'a'}]},
            [{'id':'a','evidence_kind':'PUBLIC_BEHAVIOR','behavior_check':{'id':'a','status':'FAIL'}}]))

    def test_shared_source_quote_can_distinguish_literal_object_scopes(self):
        from contractedge.requirement.normalize import bind_obligation_sources
        n=normalize_requirement({'requirement_text':'Both create and patch Jobs retain config.'});ref=n['clauses'][0]['source_ref']
        atoms=[{'id':'A1','outcome_id':'O1','statement':'create retains config','source_refs':[ref],
            'source_quote':n['raw_text'],'scope_quote':'create'},
            {'id':'A2','outcome_id':'O1','statement':'patch retains config','source_refs':[ref],
            'source_quote':n['raw_text'],'scope_quote':'patch'}]
        bind_obligation_sources({'obligation_atoms':atoms},n)
        self.assertNotEqual(atoms[0]['stable_obligation_key'],atoms[1]['stable_obligation_key'])
