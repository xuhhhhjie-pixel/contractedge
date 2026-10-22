import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from contractedge.contracts.validation import canonical_digest
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_method_input
from tests.test_orchestration import PipelineFixtureProvider


class PlanningGapProvider(PipelineFixtureProvider):
    def __init__(self):
        super().__init__()
        self.diagnosis_calls = 0
        self.plan_calls = 0

    def complete_json(self, request):
        response = super().complete_json(request)
        if request.prompt_version.startswith('diagnosis.'):
            self.diagnosis_calls += 1
            return response
        self.plan_calls += 1
        if self.plan_calls == 1:
            body = json.loads(response.content)
            body.update(status='NEEDS_EVIDENCE', planned_violation_ids=[], changes=[], postconditions=[],
                open_obligations=[{'id': 'G1', 'outcome_id': 'O1', 'obligation_refs': ['A1'],
                    'statement': 'Render the selected ServiceAccount', 'reason': 'Check whether a dedicated optional template exists'}],
                outcome_coverage=[{'outcome_id': 'O1', 'repair_supported': [], 'preserve_required': [], 'unresolved': ['O1']}],
                evidence_gaps=[{'id': 'G1', 'summary': 'Check optional template inventory',
                                'locator_hint': {'path': 'templates/optional-serviceaccount.yaml'}}])
            return ModelResponse(json.dumps(body), {'provider': 'planning-gap-fixture'})
        return response


class PlanningEvidenceExtensionTests(unittest.TestCase):
    def test_changeset_gap_uses_the_same_single_extension_and_second_plan_allowance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'templates').mkdir()
            source = root / 'templates/serviceaccount.yaml'
            source.write_text('apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n')
            provider = PlanningGapProvider()
            provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(source.read_bytes()).hexdigest()}
            value = valid_method_input()
            value['scope_hints'] = [{'kind': 'path', 'value': 'templates/serviceaccount.yaml',
                'raw_text': 'templates/serviceaccount.yaml', 'origin_ref': 'public.scope'}]
            value['initial_snapshot_id'] = compute_snapshot(root).snapshot_id
            excerpt = 'The rendered name is default instead of selected.'
            value['public_evidence'] = [{'id': 'OBS1', 'kind': 'PUBLIC_OBSERVATION',
                'snapshot_id': value['initial_snapshot_id'], 'locator': {'path': 'observation.txt'},
                'content_digest': hashlib.sha256(excerpt.encode()).hexdigest(), 'excerpt': excerpt, 'origin': 'public fixture'}]
            value.pop('input_digest'); value['input_digest'] = canonical_digest(value)
            trace = run_front_half(value, root, provider,
                {'model': {'model': 'fixture'}, 'max_context_tokens': 12000,
                 'max_output_tokens': 1024, 'max_total_tokens': 40000}, lambda text: len(text.split()))
            self.assertEqual(trace.status, 'READY', trace.errors)
            self.assertEqual(provider.diagnosis_calls, 2)
            self.assertEqual(provider.plan_calls, 2)
            self.assertTrue(trace.candidate_set.extension_used)
            self.assertEqual(trace.retrieval_rounds[-1]['trigger'], 'CHANGESET_NEEDS_EVIDENCE')
            self.assertEqual([c['stage'] for c in trace.model_calls],
                ['DIAGNOSIS', 'SEMANTIC_CHANGESET', 'DIAGNOSIS_EXTENSION', 'CHANGESET_REVISION'])


def front_fixture(root):
    (root/'templates').mkdir();source=root/'templates/serviceaccount.yaml'
    source.write_text('apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n')
    value=valid_method_input();value['scope_hints']=[dict(kind='path',value='templates/serviceaccount.yaml',
        raw_text='templates/serviceaccount.yaml',origin_ref='public.scope')]
    value['initial_snapshot_id']=compute_snapshot(root).snapshot_id;excerpt='The rendered name is default instead of selected.'
    value['public_evidence']=[dict(id='OBS1',kind='PUBLIC_OBSERVATION',snapshot_id=value['initial_snapshot_id'],
        locator={'path':'observation.txt'},content_digest=hashlib.sha256(excerpt.encode()).hexdigest(),excerpt=excerpt,origin='public fixture')]
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    return value,source


def test_planning_extension_invokes_role_location_and_delivers_full_declared_endpoint(tmp_path):
    from unittest.mock import patch
    from contractedge.retrieval.gap_delivery import locate_gap_roles
    value,source=front_fixture(tmp_path)
    (tmp_path/'main.go').write_text('package main\nfunc reloadConfig() { state.Reset() }\n')
    (tmp_path/'api').mkdir();(tmp_path/'api/groups.go').write_text('package api\nfunc getGroupsHandler() { serve(state.Groups()) }\n')
    value['scope_hints']+= [dict(kind='path',value=p,raw_text=p,origin_ref='public.scope') for p in ['main.go','api/groups.go']]
    value['initial_snapshot_id']=compute_snapshot(tmp_path).snapshot_id;value['public_evidence'][0]['snapshot_id']=value['initial_snapshot_id']
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    class Provider(PlanningGapProvider):
        def complete_json(self,request):
            response=super().complete_json(request)
            if request.prompt_version.startswith('changeset.') and self.plan_calls==1:
                body=json.loads(response.content);body['evidence_gaps']=[dict(id='G1',summary='reload affecting groups API',locator_hint={'path':'main.go','symbol':'reloadConfig'})]
                return ModelResponse(json.dumps(body),{})
            if request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls==2:
                payload=json.loads(request.messages[1]['content'])
                assert any(e.get('locator',{}).get('path')=='api/groups.go' and 'state.Groups' in e.get('excerpt','') for e in payload['evidence_catalog'])
            if request.prompt_version.startswith('changeset.'):
                body=json.loads(response.content)
                for change in body.get('changes',[]):
                    change['target']['content_digest']=hashlib.sha256((tmp_path/change['target']['path']).read_bytes()).hexdigest()
                return ModelResponse(json.dumps(body),{})
            return response
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(source.read_bytes()).hexdigest()}
    with patch('contractedge.retrieval.gap_delivery.locate_gap_roles',wraps=locate_gap_roles) as lookup:
        trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},max_context_tokens=24000,
            max_output_tokens=1024,max_total_tokens=100000,gap_role_delivery_enabled=True),lambda t:len(t.split()))
    assert trace.status=='READY',trace.errors
    assert lookup.call_count==1
    assert trace.retrieval_rounds[-1]['gap_search']['role_locations']['inspected_files']<=4


def test_planning_gap_gets_existing_single_correction_for_its_fresh_context(tmp_path):
    value,source=front_fixture(tmp_path)
    class Provider(PlanningGapProvider):
        def complete_json(self,request):
            response=super().complete_json(request)
            if request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls in {1,3}:
                body=json.loads(response.content);body['violations'][0]['support_basis']='INVALID'
                return ModelResponse(json.dumps(body),{})
            return response
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(source.read_bytes()).hexdigest()}
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},max_context_tokens=24000,
        max_output_tokens=1024,max_total_tokens=100000,max_format_retries=1,diagnosis_format_retry_per_context=True),lambda t:len(t.split()))
    assert trace.status=='READY',trace.errors
    assert provider.diagnosis_calls==4 and provider.plan_calls==2
    assert trace.candidate_set.extension_used
