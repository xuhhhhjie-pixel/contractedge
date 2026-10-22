import hashlib,json,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from tests.test_contracts import valid_method_input
from tests.test_orchestration import PipelineFixtureProvider
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from contractedge.contracts.validation import canonical_digest


class FinalFrontContract(unittest.TestCase):
    def test_new_prompt_contract_runs_through_diagnosis_plan_and_identity_binding(self):
        class Provider(PipelineFixtureProvider):
            def complete_json(self,request):
                original=request.prompt_version
                legacy=original.replace('diagnosis.v1.1.26','diagnosis.v1.1.24').replace('diagnosis.v1.1.25','diagnosis.v1.1.24').replace('changeset.v1.1.9','changeset.v1.1.8')
                response=super().complete_json(replace(request,prompt_version=legacy))
                body=json.loads(response.content)
                if original.startswith('diagnosis.'):
                    public=json.loads(request.messages[1]['content']);clauses=public['requirement_spec']['clauses']
                    atom=body['obligation_atoms'][0];atom['source_refs']=[clauses[0]['source_ref']];atom['source_quote']=clauses[0]['text']
                    body['clause_treatments']=[{'source_ref':c['source_ref'],'disposition':'OBLIGATION','obligation_refs':['A1'],'rationale':'Explicit desired behavior'} for c in clauses]
                else:body['preservation_impact']=[]
                return ModelResponse(json.dumps(body),{})
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'templates').mkdir();text='apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n';(root/'templates/serviceaccount.yaml').write_text(text)
            provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(text.encode()).hexdigest()}
            m=valid_method_input();m['scope_hints']=[{'kind':'path','value':'templates/serviceaccount.yaml','raw_text':'templates/serviceaccount.yaml','origin_ref':'public'}];m['initial_snapshot_id']=compute_snapshot(root).snapshot_id
            m['public_evidence']=[{'id':'OBS','snapshot_id':m['initial_snapshot_id'],'kind':'PUBLIC_OBSERVATION',
                'locator':{'path':'public/observation.json'},'excerpt':'Rendered default name',
                'content_digest':hashlib.sha256(b'Rendered default name').hexdigest(),'origin':'Public test fixture'}]
            for r in m['public_evidence']:r['snapshot_id']=m['initial_snapshot_id']
            m.pop('input_digest',None);m['input_digest']=canonical_digest(m)
            trace=run_front_half(m,root,provider,{'model':{'model':'fixture'},'max_context_tokens':24000,
                'max_total_tokens':80000,'requirement_traceability_enabled':True},token_counter=lambda s:len(s.split()))
            self.assertEqual(trace.status,'READY',trace.errors)
            atom=trace.diagnosis['obligation_atoms'][0]
            self.assertTrue(atom['stable_obligation_key'].startswith('OB-'))
            self.assertEqual(trace.changeset['postconditions'][0]['stable_obligation_keys'],[atom['stable_obligation_key']])
