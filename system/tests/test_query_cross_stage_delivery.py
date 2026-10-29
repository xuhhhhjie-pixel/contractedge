"""A directory/multiword extension must survive Ready and executor projection."""
import hashlib,json
from pathlib import Path

import pytest

from contractedge.changeset.topomen import translate_for_topomen
from contractedge.contracts.validation import parse_method_input
from contractedge.execution.topomen import build_execution_request,PlanRejected
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import validate_ready_trace
from contractedge.orchestration.runner import run_front_half
from tests.test_planning_evidence_extension import PlanningGapProvider,front_fixture


def test_query_located_dependency_is_preserved_through_ready_and_execution(tmp_path):
    value,template=front_fixture(tmp_path)
    (tmp_path/'worker').mkdir();dependency=tmp_path/'worker/replay.go'
    dependency.write_text('package worker\nfunc Replay() { compare(QueueStateTokenName, ExternalQueueCursor) }\n')
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.contracts.validation import canonical_digest
    value['initial_snapshot_id']=compute_snapshot(tmp_path).snapshot_id
    value['public_evidence'][0]['snapshot_id']=value['initial_snapshot_id']
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    class Provider(PlanningGapProvider):
        def complete_json(self,request):
            response=super().complete_json(request)
            if request.prompt_version.startswith('changeset.') and self.plan_calls==1:
                body=json.loads(response.content);body['evidence_gaps']=[dict(id='G1',summary='Inspect explicit input prerequisite',
                    locator_hint=dict(path='worker',search_term='QueueStateTokenName ExternalQueueCursor comparison'))]
                return ModelResponse(json.dumps(body),{})
            if request.prompt_version.startswith('changeset.') and self.plan_calls==2:
                body=json.loads(request.messages[1]['content'])
                assert any('QueueStateTokenName' in r['excerpt'] for r in body['read_only_source_support'])
            return response
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(template.read_bytes()).hexdigest()}
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},gap_role_delivery_enabled=True,
        max_context_tokens=24000,max_output_tokens=1024,max_total_tokens=100000),lambda s:len(s.split())).to_wire()
    assert trace['status']=='READY',trace['errors']
    _,context=validate_ready_trace(trace,parse_method_input(value),tmp_path)
    support=context.retrieval_config['necessary_source_support']
    assert any(r['locator']['path']=='worker/replay.go' for r in support)
    plan=translate_for_topomen(trace['changeset'],'contractedge.topomen_plan.v1',diagnosis=trace['diagnosis'],source_support=support)
    cfg=dict(model='fixture',max_context_tokens=50000,execution_max_output_tokens=1024)
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda s:len(s.split()),cfg,requirement=value['requirement_text'])
    body=json.loads(request.messages[1]['content'])
    assert any('QueueStateTokenName' in r['excerpt'] for r in body['read_only_source_support'])
    assert {r['path'] for r in body['current_target_files']}=={'templates/serviceaccount.yaml'}
    # The dependency stays read-only and must be re-read after source changes.
    dependency.write_text(dependency.read_text().replace('QueueStateTokenName','ReplacementStateToken'))
    with pytest.raises(PlanRejected):
        build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda s:len(s.split()),cfg,requirement=value['requirement_text'])
