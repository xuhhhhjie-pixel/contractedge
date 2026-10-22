import hashlib,json,tempfile,unittest
from pathlib import Path
from contractedge.contracts.validation import canonical_digest
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_method_input
from tests.test_planning_evidence_extension import PlanningGapProvider


class InvalidPlanGapProvider(PlanningGapProvider):
    def __init__(self): super().__init__();self.revision_feedback=None
    def complete_json(self,request):
        if request.prompt_version.startswith('semantic-changeset') and self.plan_calls:
            self.revision_feedback=request.messages
        response=super().complete_json(request)
        if self.plan_calls==1 and not request.prompt_version.startswith('diagnosis.'):
            body=json.loads(response.content);body['planned_violation_ids']=['V1']
            return ModelResponse(json.dumps(body),{})
        return response


class PlanningGapCorrectionTests(unittest.TestCase):
    def test_invalid_first_plan_can_supply_safe_gap_before_only_revision(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'templates').mkdir();source=root/'templates/serviceaccount.yaml'
            source.write_text('apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n')
            provider=InvalidPlanGapProvider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(source.read_bytes()).hexdigest()}
            value=valid_method_input();value['scope_hints']=[{'kind':'path','value':'templates/serviceaccount.yaml','raw_text':'templates/serviceaccount.yaml','origin_ref':'public.scope'}]
            value['initial_snapshot_id']=compute_snapshot(root).snapshot_id
            text='The rendered name is default instead of selected.'
            value['public_evidence']=[{'id':'OBS1','kind':'PUBLIC_OBSERVATION','snapshot_id':value['initial_snapshot_id'],
                'locator':{'path':'observation.txt'},'content_digest':hashlib.sha256(text.encode()).hexdigest(),'excerpt':text,'origin':'public fixture'}]
            value.pop('input_digest');value['input_digest']=canonical_digest(value)
            trace=run_front_half(value,root,provider,{'model':{'model':'fixture'},'max_context_tokens':12000,
                'max_output_tokens':1024,'max_total_tokens':40000},lambda s:len(s.split()))
            self.assertEqual(trace.status,'READY',trace.errors)
            self.assertEqual(provider.diagnosis_calls,2)
            self.assertEqual(provider.plan_calls,2)
            self.assertTrue(trace.candidate_set.extension_used)
            self.assertEqual([c['stage'] for c in trace.model_calls],['DIAGNOSIS','SEMANTIC_CHANGESET','DIAGNOSIS_EXTENSION','CHANGESET_REVISION'])
            first=trace.model_calls[1]
            self.assertIn('validation_feedback',first)
            self.assertTrue(first['validation_feedback'])

    def test_missing_status_is_a_correctable_plan_error_not_infrastructure(self):
        class MissingStatus(PlanningGapProvider):
            def complete_json(self,request):
                response=super().complete_json(request)
                if self.plan_calls==1 and not request.prompt_version.startswith('diagnosis.'):
                    body=json.loads(response.content);body.pop('status',None)
                    return ModelResponse(json.dumps(body),{})
                return response
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'templates').mkdir();source=root/'templates/serviceaccount.yaml'
            source.write_text('apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n')
            provider=MissingStatus();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(source.read_bytes()).hexdigest()}
            value=valid_method_input();value['scope_hints']=[{'kind':'path','value':'templates/serviceaccount.yaml','raw_text':'templates/serviceaccount.yaml','origin_ref':'public.scope'}]
            value['initial_snapshot_id']=compute_snapshot(root).snapshot_id
            text='The rendered name is default instead of selected.'
            value['public_evidence']=[{'id':'OBS1','kind':'PUBLIC_OBSERVATION','snapshot_id':value['initial_snapshot_id'],
                'locator':{'path':'observation.txt'},'content_digest':hashlib.sha256(text.encode()).hexdigest(),'excerpt':text,'origin':'public fixture'}]
            value.pop('input_digest');value['input_digest']=canonical_digest(value)
            trace=run_front_half(value,root,provider,{'model':{'model':'fixture'},'max_context_tokens':12000,
                'max_output_tokens':1024,'max_total_tokens':40000},lambda s:len(s.split()))
            self.assertEqual(trace.status,'READY',trace.errors)
            self.assertEqual(provider.plan_calls,2)
            self.assertEqual(provider.diagnosis_calls,1)
            self.assertEqual([c['stage'] for c in trace.model_calls],['DIAGNOSIS','SEMANTIC_CHANGESET','CHANGESET_REVISION'])
