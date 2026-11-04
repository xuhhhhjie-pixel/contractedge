import copy,json,unittest
from types import SimpleNamespace
from contractedge.changeset.prompt import build_changeset_request
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.validation import validate_diagnosis,parse_method_input
from tests.test_contracts import valid_diagnosis,valid_method_input,valid_plan


class PlanningEditPermissionTests(unittest.TestCase):
    def test_read_only_evidence_is_not_offered_as_editable_target(self):
        raw=valid_diagnosis()
        context=SimpleNamespace(run_id=raw['run_id'],case_id='CASE',snapshot_id=raw['snapshot_id'],input_digest=raw['input_digest'],
            model_config={'model':'fixture'},budget={'max_output_tokens':1000},
            retrieval_config={'public_execution_capabilities':{'editable_policy':{'allowed_paths':['pkg/*'],'forbidden_paths':[]}}})
        request=build_changeset_request(SimpleNamespace(raw=raw),context)
        payload=json.loads(request.messages[1]['content'])
        self.assertEqual(payload['existing_target_paths'],[])
        # Read-only supporting evidence remains available for understanding.
        self.assertEqual(payload['diagnosis_packet'],raw)
        target=request.output_schema['properties']['changes']['items']['properties']['target']['properties']
        self.assertEqual(target['source_path']['enum'],[])

    def test_scope_rejection_happens_in_plan_validation_before_consumer(self):
        packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
        result=validate_semantic_changeset(valid_plan(),packet,None,
            policy={'allowed_paths':['pkg/*'],'forbidden_paths':[]})
        self.assertFalse(result.valid)
        self.assertIn('TARGET_OUTSIDE_EDITABLE_SCOPE',[e.code for e in result.errors])

    def test_forbidden_paths_override_allow_and_legacy_unspecified_scope_remains_compatible(self):
        packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
        self.assertTrue(validate_semantic_changeset(valid_plan(),packet,None).valid)
        result=validate_semantic_changeset(valid_plan(),packet,None,
            policy={'allowed_paths':['**'],'forbidden_paths':['templates/*']})
        self.assertFalse(result.valid)
        self.assertIn('TARGET_FORBIDDEN',[e.code for e in result.errors])

    def test_observation_boundary_path_can_remain_unchanged(self):
        packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
        # A source path named by an observation limit is not another required
        # modification, including when replayed under the old context name.
        self.assertTrue(validate_semantic_changeset(valid_plan(),packet,None).valid)
        result=validate_semantic_changeset(valid_plan(),packet,None,
            boundary_contract_critical_paths=['pkg/injector/patcher/sidecar_container.go'])
        self.assertTrue(result.valid,result.errors)
        self.assertEqual(result.value['changes'],valid_plan()['changes'])

    def test_boundary_critical_path_does_not_force_edits_in_evidence_request(self):
        packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
        plan=valid_plan()
        plan.update(status='NEEDS_EVIDENCE',planned_violation_ids=[],changes=[],postconditions=[],
            open_obligations=[{'id':'G1','outcome_id':'O1','obligation_refs':['A1'],
                'statement':'Render the selected ServiceAccount',
                'reason':'The production hand-off needs additional source evidence'}],
            outcome_coverage=[{'outcome_id':'O1','repair_supported':[],
                'preserve_required':[],'unresolved':['O1']}],
            evidence_gaps=[{'id':'G1','summary':'Inspect the production hand-off',
                'locator_hint':{'path':'pkg/injector/patcher/sidecar_container.go'}}])
        self.assertTrue(validate_semantic_changeset(plan,packet,None).valid)
        result=validate_semantic_changeset(plan,packet,None,
            boundary_contract_critical_paths=['pkg/injector/patcher/sidecar_container.go'])
        self.assertTrue(result.valid,result.errors)
        self.assertEqual(result.value['changes'],[])

    def test_partial_ready_plan_does_not_invent_edits_for_observation_scope(self):
        packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
        plan=valid_plan()
        plan.update(status='READY_PARTIAL',
            open_obligations=[{'id':'G1','outcome_id':'O1','obligation_refs':['A1'],
                'statement':'Confirm the selected ServiceAccount at runtime',
                'reason':'The source repair still needs independent behavior verification'}])
        plan['outcome_coverage'][0]['unresolved']=['O1']
        self.assertTrue(validate_semantic_changeset(plan,packet,None).valid)
        result=validate_semantic_changeset(plan,packet,None,
            boundary_contract_critical_paths=['pkg/injector/patcher/sidecar_container.go'])
        self.assertTrue(result.valid,result.errors)
        self.assertEqual(result.value['changes'],plan['changes'])

    def test_named_observation_path_still_cannot_bypass_editable_policy(self):
        packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
        plan=valid_plan()
        result=validate_semantic_changeset(plan,packet,None,
            policy={'allowed_paths':['pkg/*'],'forbidden_paths':[]},
            boundary_contract_critical_paths=[plan['changes'][0]['target']['path']])
        self.assertFalse(result.valid)
        self.assertIn('TARGET_OUTSIDE_EDITABLE_SCOPE',[e.code for e in result.errors])

    def test_observation_scope_does_not_waive_plan_reference_contracts(self):
        packet=validate_diagnosis(valid_diagnosis(),parse_method_input(valid_method_input()),None)
        for field,invalid_ref in [('violation_refs','V404'),('postcondition_refs','P404')]:
            with self.subTest(field=field):
                plan=valid_plan()
                plan['changes'][0][field]=[invalid_ref]
                result=validate_semantic_changeset(plan,packet,None,
                    boundary_contract_critical_paths=['unrelated/observer.go'])
                self.assertFalse(result.valid)
                self.assertTrue(result.errors)

    def test_scope_paths_are_serialized_as_read_only_without_expanding_targets(self):
        raw=valid_diagnosis()
        extra='unrelated/observer.go'
        for context_key in ('observation_scope_paths','boundary_contract_critical_paths'):
            with self.subTest(context_key=context_key):
                context=SimpleNamespace(run_id=raw['run_id'],case_id='CASE',
                    snapshot_id=raw['snapshot_id'],input_digest=raw['input_digest'],
                    model_config={'model':'fixture'},budget={'max_output_tokens':1000},
                    retrieval_config={context_key:[extra],
                        'public_execution_capabilities':{
                            'task_observation_limit':extra+' is not executed or claimed.',
                            'editable_policy':{'allowed_paths':['templates/*'],'forbidden_paths':[]}}})
                request=build_changeset_request(SimpleNamespace(raw=raw),context)
                payload=json.loads(request.messages[1]['content'])
                self.assertEqual(payload['observation_scope']['paths'],[extra])
                self.assertIn('Read-only',payload['observation_scope']['interpretation'])
                self.assertNotIn('boundary_contract',payload)
                self.assertNotIn(extra,[t['path'] for t in payload['existing_target_paths']])
                target=request.output_schema['properties']['changes']['items']['properties']['target']['properties']
                self.assertNotIn(extra,target['source_path']['enum'])
                self.assertIn('not executed or claimed',request.messages[0]['content'])
                self.assertNotIn('include an EXISTING MODIFY change',request.messages[0]['content'])

    def test_runner_keeps_observation_prose_out_of_source_scope(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from contractedge.contracts.validation import canonical_digest
        from contractedge.diagnosis.prompt import build_diagnosis_request
        from contractedge.orchestration.runner import run_front_half
        from contractedge.source.snapshot import compute_snapshot
        from tests.test_planning_evidence_extension import front_fixture

        class Captured(BaseException):
            pass

        class Provider:
            request=None
            def complete_json(self,request):
                self.request=request
                raise Captured()

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            value,_=front_fixture(root)
            (root/'worker.py').write_text('def select_account():\n    return "default"\n')
            value['scope_hints'].append(dict(kind='path',value='worker.py',
                raw_text='worker.py',origin_ref='public.scope'))
            snapshot=compute_snapshot(root).snapshot_id
            value['initial_snapshot_id']=snapshot
            value['public_evidence'][0]['snapshot_id']=snapshot
            value.pop('input_digest');value['input_digest']=canonical_digest(value)
            provider=Provider()
            with patch('contractedge.orchestration.runner.build_diagnosis_request',
                       wraps=build_diagnosis_request) as build:
                with self.assertRaises(Captured):
                    run_front_half(value,root,provider,dict(model={'model':'fixture'},
                        max_context_tokens=24000,max_output_tokens=1024,max_total_tokens=100000,
                        public_execution_capabilities={
                            'task_observation_limit':'templates/serviceaccount.yaml is not executed or claimed.'}),
                        lambda text:len(text.split()))
            payload=json.loads(provider.request.messages[1]['content'])
            retrieval=build.call_args.args[3].retrieval_config
            self.assertNotIn('observation_scope_paths',retrieval)
            self.assertEqual(payload['public_execution_capabilities']['task_observation_limit'],
                'templates/serviceaccount.yaml is not executed or claimed.')
            self.assertNotIn('boundary_contract_critical_paths',retrieval)
            self.assertIn('worker.py',[
                item['path'] for item in payload['candidate_set']['selected_candidates']])
