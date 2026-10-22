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
