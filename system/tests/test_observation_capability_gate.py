import hashlib
import json
import unittest

from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.provider import FixtureProvider
from contractedge.orchestration.feedback import make_feedback


def record(text='{"value":true}', kind='PUBLIC_STRUCTURE', scenario='trigger'):
    return {'id': 'OBS', 'kind': 'PUBLIC_OBSERVATION', 'snapshot_id': 'S1',
            'locator': {'path': 'raw/trigger.pod-spec-0000.json'}, 'excerpt': text,
            'content_digest': hashlib.sha256(text.encode()).hexdigest(), 'origin': 'trusted runner fixture',
            'evidence_kind': kind, 'scenario': scenario}


def claim(quote='true', comparisons=None):
    c={'id': 'P', 'status': 'PASS', 'rationale': 'Claim',
       'evidence': [{'observation_id': 'OBS', 'quote': quote}]}
    if comparisons is not None: c['comparisons']=comparisons
    return c


def plan(kind='UNKNOWN', **extra):
    return {'postconditions': [{'id': 'P', 'statement': 'Selected behavior', 'verification_kind': kind, **extra}]}


def feedback(p, c, obs):
    return make_feedback(case_id='CASE', run_id='run', input_digest='a'*64, round_id=0,
        from_snapshot='S0', to_snapshot='S1', patch_digest='b'*64,
        execution_status='EXECUTION_PASS', observations=[obs], plan=p,
        review={'decision': 'PASS', 'checks': [c], 'failure_kind': 'UNKNOWN'})


class ObservationCapabilityGateTests(unittest.TestCase):
    def test_real_build_quote_cannot_clear_unmarked_runtime_obligation(self):
        obs=record('{"status":"EXECUTION_PASS"}', 'EXECUTION_ONLY')
        f=feedback(plan(),claim('EXECUTION_PASS'),obs)
        self.assertEqual(f['semantic_outcome'], 'UNKNOWN')
        self.assertEqual(len(f['pending_obligations']),1)
        self.assertEqual(f['retained_constraints'],[])

    def test_structure_cannot_prove_runtime_even_with_real_quote(self):
        self.assertEqual(feedback(plan('PUBLIC_BEHAVIOR'),claim(),record())['semantic_outcome'],'UNKNOWN')

    def test_unknown_observation_kind_fails_closed(self):
        obs=record(); obs.pop('evidence_kind')
        self.assertEqual(feedback(plan(),claim(),obs)['semantic_outcome'],'UNKNOWN')

    def test_correct_typed_structure_and_behavior_remain_usable(self):
        for kind in ['PUBLIC_STRUCTURE','PUBLIC_BEHAVIOR']:
            with self.subTest(kind=kind):
                f=feedback(plan(kind),claim(),record(kind=kind))
                self.assertEqual(f['semantic_outcome'],'PUBLIC_REVIEW_PASS')

    def test_wrong_scenario_is_rejected_even_when_values_equal(self):
        f=feedback(plan('PUBLIC_STRUCTURE',verification_scope={'scenario':'control'}),claim(),record())
        self.assertEqual(f['semantic_outcome'],'UNKNOWN')

    def test_field_equality_requires_comparison_and_original_input(self):
        p=plan('DECLARED_INPUT_EQUALITY',comparison_requirements=[{'artifact_id':'INPUT','input_pointer':'/value','output_pointer':'/value'}])
        self.assertEqual(feedback(p,claim(),record())['semantic_outcome'],'UNKNOWN')
        spec={'artifact_id':'INPUT','input_pointer':'/value','observation_id':'OBS','output_pointer':'/value'}
        p['declared_artifacts']=[{'id':'INPUT','content':'value: true\n'}]
        self.assertEqual(feedback(p,claim(comparisons=[spec]),record())['semantic_outcome'],'PUBLIC_REVIEW_PASS')
        p['declared_artifacts'][0]['content']='value: false\n'
        self.assertEqual(feedback(p,claim(comparisons=[spec]),record())['semantic_outcome'],'UNKNOWN')

    def test_metadata_quote_does_not_prove_rendered_field(self):
        self.assertEqual(feedback(plan(),claim('EXECUTION_PASS'),
            record('{"status":"EXECUTION_PASS"}','EXECUTION_ONLY'))['semantic_outcome'],'UNKNOWN')

    def test_reviewer_normalizes_unsupported_claim_without_asking_model_to_invent_evidence(self):
        provider=FixtureProvider({'checks':[claim('EXECUTION_PASS')], 'failure_kind':'UNKNOWN','rationale':'Claim'})
        reviewer=ModelPublicReviewer(provider,len,{'model':'fixture','max_context_tokens':20000})
        r=reviewer.review('Runtime behavior',plan(),{'status':'EXECUTION_PASS',
            'observations':[record('{"status":"EXECUTION_PASS"}','EXECUTION_ONLY')]})
        self.assertEqual(r['decision'],'UNKNOWN')
        self.assertEqual(r['checks'][0]['status'],'UNKNOWN')
        self.assertEqual(r['checks'][0]['submitted_status'],'PASS')

    def test_real_unequal_field_comparison_supports_fail_and_repair_route(self):
        p=plan('DECLARED_INPUT_EQUALITY',comparison_requirements=[{'artifact_id':'INPUT','input_pointer':'/value','output_pointer':'/value'}])
        p['declared_artifacts']=[{'id':'INPUT','content':'value: false\n'}]
        spec={'artifact_id':'INPUT','input_pointer':'/value','observation_id':'OBS','output_pointer':'/value'}
        c=claim(comparisons=[spec]);c['status']='FAIL'
        r=make_feedback(case_id='CASE',run_id='run',input_digest='a'*64,round_id=0,from_snapshot='S0',to_snapshot='S1',
            patch_digest='b'*64,execution_status='EXECUTION_PASS',observations=[record()],plan=p,
            review={'decision':'FAIL','checks':[c],'failure_kind':'EXECUTION'})
        self.assertEqual(r['semantic_outcome'],'PUBLIC_REVIEW_FAIL')
        self.assertEqual(r['route'],'EXECUTE')

    def test_irrelevant_equal_field_cannot_discharge_required_field_bindings(self):
        p=plan('DECLARED_INPUT_EQUALITY',comparison_requirements=[{'artifact_id':'INPUT','input_pointer':'/wanted','output_pointer':'/wanted'}])
        p['declared_artifacts']=[{'id':'INPUT','content':'wanted: true\nnoise: true\n'}]
        spec={'artifact_id':'INPUT','input_pointer':'/noise','observation_id':'OBS','output_pointer':'/noise'}
        r=feedback(p,claim(comparisons=[spec]),record('{"wanted":false,"noise":true}'))
        self.assertEqual(r['semantic_outcome'],'UNKNOWN')

    def test_equality_without_predeclared_bindings_cannot_choose_its_own_answer(self):
        p=plan('DECLARED_INPUT_EQUALITY');p['declared_artifacts']=[{'id':'INPUT','content':'value: true\n'}]
        spec={'artifact_id':'INPUT','input_pointer':'/value','observation_id':'OBS','output_pointer':'/value'}
        self.assertEqual(feedback(p,claim(comparisons=[spec]),record())['semantic_outcome'],'UNKNOWN')

    def test_replanning_same_obligation_cannot_drop_scope_and_field_bindings(self):
        from contractedge.orchestration.feedback import review_plan_with_retained
        bindings=[{'artifact_id':'INPUT','input_pointer':'/value','output_pointer':'/value'}]
        p=plan('DECLARED_INPUT_EQUALITY',comparison_requirements=bindings,verification_scope={'scenario':'control'})
        first=feedback(p,claim(),record())
        revised=review_plan_with_retained(plan(),first)
        current=revised['postconditions'][0]
        self.assertEqual(current.get('verification_kind'),'DECLARED_INPUT_EQUALITY')
        self.assertEqual(current.get('comparison_requirements'),bindings)
        self.assertEqual(current.get('verification_scope'),{'scenario':'control'})

    def test_resource_index_does_not_veto_a_separate_matching_resource_record(self):
        from copy import deepcopy
        target=record();target['resource_identity']={'kind':'Job','name':'target'}
        index=deepcopy(target);index['id']='INDEX';index.pop('resource_identity')
        p=plan('PUBLIC_STRUCTURE',verification_scope={'scenario':'trigger','resource':{'kind':'Job','name':'target'}})
        c=claim();c['evidence'].insert(0,{'observation_id':'INDEX','quote':'true'})
        r=make_feedback(case_id='CASE',run_id='run',input_digest='a'*64,round_id=0,from_snapshot='S0',to_snapshot='S1',
            patch_digest='b'*64,execution_status='EXECUTION_PASS',observations=[index,target],plan=p,
            review={'decision':'PASS','checks':[c],'failure_kind':'UNKNOWN'})
        self.assertEqual(r['semantic_outcome'],'PUBLIC_REVIEW_PASS')
