"""Consumer replacement at the real host fixture boundary, no API or Docker."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from contractedge.changeset.topomen import translate_for_topomen
from contractedge.contracts.repair_intent import (SemanticChangeSetIntent, DirectPlanIntent,
    RelationOnlyIntent, RequirementOnlyIntent)
from contractedge.development import public_demo
from contractedge.execution.consumer import BudgetPort, ConsumerContext, execution_review_mapping
from contractedge.execution.consumers.topomen import TopoMenRepairConsumer
from contractedge.execution.consumers.direct import ModifyOnlyConsumer
from contractedge.execution.literal import PlanRejected
from contractedge.execution.topomen import ModelPlanExecutor
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_plan


CONFIG={'model':'offline-fixture','max_context_tokens':240000,'execution_max_output_tokens':4096}
POLICY={'allowed_paths':['templates/**']}


def count(text):
    return len(text.encode())


def source(root):
    (root/'templates').mkdir()
    (root/'templates/serviceaccount.yaml').write_text('name: default\n')
    return compute_snapshot(root)


def semantic(root):
    value=valid_plan();snap=compute_snapshot(root)
    value['snapshot_id']=snap.snapshot_id
    value['changes'][0]['target'].update(snapshot_id=snap.snapshot_id,
        content_digest=snap.file_digests['templates/serviceaccount.yaml'])
    return SemanticChangeSetIntent(value)


class LiteralProvider:
    def __init__(self,body=None):
        self.requests=[];self.body=body

    def complete_json(self,request):
        self.requests.append(request)
        if self.body is not None:
            return ModelResponse(json.dumps(self.body),{})
        if request.prompt_version=='topomen-source-execution.v1':
            body={'edits':[{'change_id':'C1','path':'templates/serviceaccount.yaml','operation':'MODIFY',
                'old_text':'name: default','new_text':'name: selected'}]}
        else:
            body={'replacements':[{'key':'C1','file':'templates/serviceaccount.yaml',
                'before':'name: default','after':'name: selected'}]}
        return ModelResponse(json.dumps(body),{})


def run_consumer(consumer,intent,root,provider,policy=POLICY,trial=None):
    context=ConsumerContext(root,policy,CONFIG,'Restore selected name.')
    item=consumer.prepare(intent,context)
    accepted=consumer.accept(item,compute_snapshot(root),policy)
    receipt=consumer.execute(accepted,BudgetPort(provider,count,CONFIG,trial))
    return item,accepted,receipt


def test_default_full_projection_request_and_receipt_remain_equal(tmp_path):
    old=tmp_path/'old';new=tmp_path/'new';old.mkdir();new.mkdir()
    source(old);source(new)
    old_provider,new_provider=LiteralProvider(),LiteralProvider()
    intent=semantic(old)
    legacy=translate_for_topomen(intent.changeset,'contractedge.topomen_plan.v1',diagnosis=None)
    expected=ModelPlanExecutor(old_provider,count,CONFIG).execute(legacy,old,POLICY,
        requirement='Restore selected name.')
    item,accepted,actual=run_consumer(TopoMenRepairConsumer(),intent,new,new_provider)
    assert item.payload==legacy
    assert old_provider.requests==new_provider.requests
    assert expected==actual
    assert accepted.acceptance==actual['acceptance']


@pytest.mark.parametrize('operation',['CREATE','REMOVE'])
def test_modify_consumer_rejects_known_operation_before_dispatch(tmp_path,operation):
    source(tmp_path);intent=semantic(tmp_path)
    value=copy.deepcopy(intent.changeset);value['changes'][0]['operation']=operation
    provider=LiteralProvider()
    with pytest.raises(PlanRejected,match='unsupported operation'):
        run_consumer(ModifyOnlyConsumer(),SemanticChangeSetIntent(value),tmp_path,provider)
    assert provider.requests==[]


def test_topomen_rejects_absent_plan_intent_before_dispatch(tmp_path):
    snap=source(tmp_path);provider=LiteralProvider()
    with pytest.raises(PlanRejected,match='unsupported intent'):
        run_consumer(TopoMenRepairConsumer(),RequirementOnlyIntent(snap.snapshot_id,'input'),tmp_path,provider)
    assert provider.requests==[]


@pytest.mark.parametrize('consumer',[TopoMenRepairConsumer,ModifyOnlyConsumer])
def test_consumers_cannot_widen_edit_permission_or_use_stale_snapshot(tmp_path,consumer):
    source(tmp_path);provider=LiteralProvider();intent=semantic(tmp_path)
    context=ConsumerContext(tmp_path,{'allowed_paths':['elsewhere/*']},CONFIG,'Restore selected name.')
    adapter=consumer();item=adapter.prepare(intent,context)
    with pytest.raises(PlanRejected,match='scope'):
        adapter.accept(item,compute_snapshot(tmp_path),context.policy)
    context=replace(context,policy=POLICY)
    item=adapter.prepare(intent,context);accepted=adapter.accept(item,compute_snapshot(tmp_path),POLICY)
    (tmp_path/'templates/serviceaccount.yaml').write_text('name: externally changed\n')
    with pytest.raises(PlanRejected,match='stale'):
        adapter.execute(accepted,BudgetPort(provider,count,CONFIG))
    assert provider.requests==[]


@pytest.mark.parametrize('consumer',[TopoMenRepairConsumer,ModifyOnlyConsumer])
def test_accepted_payload_mutation_fails_before_dispatch(tmp_path,consumer):
    source(tmp_path);provider=LiteralProvider();adapter=consumer()
    item=adapter.prepare(semantic(tmp_path),ConsumerContext(tmp_path,POLICY,CONFIG,'Restore selected name.'))
    accepted=adapter.accept(item,compute_snapshot(tmp_path),POLICY)
    item.payload['injected']='mutated after acceptance'
    with pytest.raises(PlanRejected,match='changed'):
        adapter.execute(accepted,BudgetPort(provider,count,CONFIG))
    assert provider.requests==[]


@pytest.mark.parametrize('consumer',[TopoMenRepairConsumer,ModifyOnlyConsumer])
def test_accepted_authority_cannot_be_widened_after_acceptance(tmp_path,consumer):
    source(tmp_path);provider=LiteralProvider();adapter=consumer()
    item=adapter.prepare(semantic(tmp_path),ConsumerContext(tmp_path,POLICY,CONFIG,'Restore selected name.'))
    accepted=adapter.accept(item,compute_snapshot(tmp_path),POLICY)
    accepted.acceptance['scope_policy']['allowed_paths']=['**']
    with pytest.raises(PlanRejected,match='authority changed'):
        adapter.execute(accepted,BudgetPort(provider,count,CONFIG))
    assert provider.requests==[]


def test_requirement_mode_rejects_foreign_correction_context_before_dispatch(tmp_path):
    snap=source(tmp_path);provider=LiteralProvider();adapter=ModifyOnlyConsumer()
    context=ConsumerContext(tmp_path,POLICY,CONFIG,'Restore selected name.',
        previous_attempt={'model_response':'FULL_PLAN_SENTINEL'})
    with pytest.raises(PlanRejected,match='provenance'):
        adapter.prepare(RequirementOnlyIntent(snap.snapshot_id,'input'),context)
    assert provider.requests==[]


@pytest.mark.parametrize('consumer',[TopoMenRepairConsumer,ModifyOnlyConsumer])
def test_raw_plan_restore_without_authority_is_explicitly_rejected(tmp_path,consumer):
    provider=LiteralProvider()
    with pytest.raises(PlanRejected,match='validated consumer input and authority'):
        consumer().consume_received_response(None,None,{},BudgetPort(provider,count,CONFIG))
    assert provider.requests==[]


def test_alternate_response_is_atomic_and_cannot_escape_scope(tmp_path):
    source(tmp_path)
    provider=LiteralProvider({'replacements':[
        {'key':'C1','file':'templates/serviceaccount.yaml','before':'name: default','after':'name: selected'},
        {'key':'C1','file':'templates/serviceaccount.yaml','before':'absent literal','after':'other'}]})
    with pytest.raises(PlanRejected,match='exactly once'):
        run_consumer(ModifyOnlyConsumer(),semantic(tmp_path),tmp_path,provider)
    assert (tmp_path/'templates/serviceaccount.yaml').read_text()=='name: default\n'
    provider=LiteralProvider({'replacements':[
        {'key':'C1','file':'../outside','before':'anything','after':'injected'}]})
    with pytest.raises(PlanRejected,match='schema invalid'):
        run_consumer(ModifyOnlyConsumer(),semantic(tmp_path),tmp_path,provider)
    assert not (tmp_path.parent/'outside').exists()


@pytest.mark.parametrize('kind',['direct_plan','relation','requirement'])
def test_modes_use_their_actual_inputs_without_full_backfill(tmp_path,kind):
    snap=source(tmp_path)
    if kind=='direct_plan':
        intent=DirectPlanIntent({'schema_version':'ce.direct_plan.v1','snapshot_id':snap.snapshot_id,
            'input_digest':'independent-input','goal':'selected name','steps':[{'id':'C1',
                'path':'templates/serviceaccount.yaml','operation':'MODIFY','intent':'Use selected name.',
                'depends_on':[]}]})
    elif kind=='relation':
        intent=RelationOnlyIntent({'snapshot_id':snap.snapshot_id,'input_digest':'independent-input',
            'expected_relations':[{'id':'RELATION_SENTINEL','condition':'enabled'}],
            'violations':[{'id':'VIOLATION_SENTINEL'}]})
    else:
        intent=RequirementOnlyIntent(snap.snapshot_id,'independent-input')
    provider=LiteralProvider()
    item,_,receipt=run_consumer(ModifyOnlyConsumer(),intent,tmp_path,provider)
    delivered=json.loads(provider.requests[0].messages[1]['content'])
    serialized=json.dumps(delivered)
    assert 'ce.semantic_changeset' not in serialized
    assert 'postconditions' not in serialized and 'planned_violation_ids' not in serialized
    assert ('RELATION_SENTINEL' in serialized)==(kind=='relation')
    assert delivered['repair_input']['writes']==item.payload['writes']
    if kind!='direct_plan':
        assert item.payload['writes']==[]
        assert receipt['operation_coverage']==[]
        assert receipt['operation_coverage_status'].startswith('NOT_APPLICABLE')
    else:
        assert receipt['operation_coverage_status']=='APPLICABLE'
    assert 'feedback' not in delivered
    assert receipt['semantic_edit_trace']==[]


def test_budget_is_owned_by_same_budgeted_provider(tmp_path):
    source(tmp_path);provider=LiteralProvider()
    budget=BudgetedProvider(provider,count,max_tokens=1,max_seconds=30,max_calls=1)
    with pytest.raises(BudgetExceeded):
        run_consumer(ModifyOnlyConsumer(),semantic(tmp_path),tmp_path,budget)
    assert provider.requests==[]
    assert (tmp_path/'templates/serviceaccount.yaml').read_text()=='name: default\n'


def test_alternate_consumer_forwards_durable_response_and_shared_edit_hooks(tmp_path):
    source(tmp_path)
    class Trial:
        def __init__(self): self.events=[]
        def record_response(self,request,response):
            self.events.append('response');return 'durable-response'
        def consume_response(self,response_id):
            assert response_id=='durable-response';self.events.append('consume')
        def record_edit_intent(self,changed):
            assert changed=={'templates/serviceaccount.yaml':'name: selected\n'}
            assert (tmp_path/'templates/serviceaccount.yaml').read_text()=='name: default\n'
            self.events.append('edit_intent')
        def record(self,event,**kwargs): self.events.append(event)
    trial=Trial()
    run_consumer(ModifyOnlyConsumer(),semantic(tmp_path),tmp_path,LiteralProvider(),trial=trial)
    assert trial.events==['response','consume','edit_intent','RESPONSE_CONSUME_COMPLETED']


def test_alternate_format_injects_into_unmodified_repair_host_loop(tmp_path):
    case='config-binding';public=public_demo.freeze_example(case,tmp_path)
    runner=public_demo.PublicExampleRunner(public,case)
    class Provider(public_demo.SyntheticExampleProvider):
        def complete_json(self,request):
            if request.prompt_version==ModifyOnlyConsumer.PROMPT:
                return ModelResponse(json.dumps({'replacements':[{'key':'C1','file':'app.py',
                    'before':'name = "default"','after':'name = config["selected_name"]'}]}),
                    {'provider':'independent-modify-only-fixture','zero_cost':True})
            return super().complete_json(request)
    provider=Provider(case,runner)
    config={**public_demo.default_config(case),'execution_backend':'direct','candidate_guard_enabled':True}
    report=repair_case(public,tmp_path/'unused-tools',case,tmp_path/'repair',provider,public_demo.count_tokens,
        config,public_runner=runner,consumer=ModifyOnlyConsumer())
    assert report['status']=='PUBLIC_REVIEW_COMPLETE',report['errors']
    output=tmp_path/'repair'
    assert (output/'candidate.patch').read_text().startswith('diff --git')
    execution=json.loads((output/'rounds/0/execution.json').read_text())
    projected=json.loads((output/'rounds/0/plan.json').read_text())
    calls=json.loads((output/'model_calls.json').read_text())
    assert projected['schema_version']=='ce.modify_input.v1' and 'operations' not in projected
    assert execution['consumer_id']==ModifyOnlyConsumer.ID
    assert [call['prompt_version'] for call in calls][-2:]==[
        ModifyOnlyConsumer.PROMPT,'public-postcondition-review.v2']
    assert report['initial_snapshot_id']!=report['final_snapshot_id']
    assert execution_review_mapping(execution)[0]['literal_edits'][0]['new_text']=='name = config["selected_name"]'
    broken=json.loads((output/'initial-public-observation.json').read_text())
    assert any(json.loads(row['excerpt'])['status']=='FAIL' for row in broken['observations'])
