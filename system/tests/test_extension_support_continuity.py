import json
from types import SimpleNamespace

import pytest

from contractedge.execution.topomen import build_execution_request,PlanRejected
from contractedge.orchestration.runner import required_planning_source_support
from contractedge.retrieval.candidates import build_candidate_set
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.parsers import parse_source_units
from tests.test_repair_execution import bound_plan


def setup(root):
    (root/'templates').mkdir();(root/'templates/serviceaccount.yaml').write_text('name: default\n')
    (root/'state.go').write_text('package state\nfunc Subscribe() { replay() }\n')
    snapshot=compute_snapshot(root)
    unit=parse_source_units('state.go',(root/'state.go').read_text(),snapshot.snapshot_id)[0]
    unit.update(required_for_gap_delivery=True,gap_role_keys=['G1:anchor'])
    candidates=build_candidate_set('Subscribe',None,[unit],[],top_n=12,max_context_tokens=10000,token_counter=len)
    from dataclasses import replace
    candidates=replace(candidates,required_endpoint_ids=(unit['id'],))
    support=required_planning_source_support(SimpleNamespace(declared_artifacts=(),public_evidence=()),candidates,snapshot.snapshot_id)
    plan=bound_plan(root);plan['coordination_context']={'source_support':support}
    return plan,support


def test_non_lifecycle_gap_source_survives_planning_and_execution(tmp_path):
    plan,support=setup(tmp_path)
    assert len(support)==1 and support[0]['delivery_required']
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},len,
        dict(model='fixture',max_context_tokens=50000),requirement='Repair')
    payload=json.loads(request.messages[1]['content'])
    assert payload['read_only_source_support']==support


def test_non_lifecycle_required_source_cannot_silently_drop_at_support_budget(tmp_path):
    plan,_=setup(tmp_path)
    with pytest.raises(PlanRejected,match='source was not delivered'):
        build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},len,
            dict(model='fixture',max_context_tokens=50000,max_execution_support_bytes=1),requirement='Repair')
