"""Adopted bodies survive real helper selection and consumer handoffs."""
import copy
import hashlib
import json

import pytest

from contractedge.diagnosis.evidence import source_context_new, source_context_offer
from contractedge.execution.helper_context import planning_helper_source_support
from contractedge.execution.consumer import BudgetPort, ConsumerContext
from contractedge.execution.consumers.topomen import TopoMenRepairConsumer
from contractedge.execution.literal import PlanRejected
from contractedge.model.budget import BudgetExceeded
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot
from tests.test_repair_consumers import CONFIG, POLICY, LiteralProvider, count, semantic, source
from contractedge.execution.topomen import build_execution_request
from contractedge.diagnosis.evidence import source_context_commit
from tests.test_execution_adopted_material_delivery import REQUIREMENT, inspect
from contractedge.orchestration.semantic_delivery import require_delivery, SemanticDeliveryError
from tests.test_repair_execution import bound_plan


def context(input_digest='input'):
    return source_context_new(dict(run_id='run', case_id='case', input_digest=input_digest, front_id='front'))


def helpers(tmp_path):
    (tmp_path/'go.mod').write_text('module demo\n')
    for directory in ('app', 'a', 'z'):
        (tmp_path/directory).mkdir()
    (tmp_path/'z/z.go').write_text('package z\n'+''.join(f'func F{i}() {{}}\n' for i in range(1, 9)))
    (tmp_path/'a/a.go').write_text('package a\nfunc Earlier() {}\n')
    base='package app\nimport "demo/z"\nfunc Base() { '+ '; '.join(f'z.F{i}()' for i in range(1, 9))+' }\n'
    added='package app\nimport "demo/a"\nfunc Added() { a.Earlier() }\n'
    (tmp_path/'app/base.go').write_text(base)
    (tmp_path/'app/added.go').write_text(added)
    snap=compute_snapshot(tmp_path)
    b=[u for u in parse_source_units('app/base.go', base, snap.snapshot_id) if u.get('symbol')=='Base']
    a=[u for u in parse_source_units('app/added.go', added, snap.snapshot_id) if u.get('symbol')=='Added']
    return snap, b, a


def test_new_seed_cannot_evict_eighth_adopted_helper(tmp_path):
    snap, base, added=helpers(tmp_path)
    old, _=planning_helper_source_support(snap, base)
    assert len(old)==8
    active=source_context_offer(context(), old, producer='planning_helper')
    before=copy.deepcopy(active)
    actual, _=planning_helper_source_support(snap, base+added, source_context=active)
    assert actual==old
    assert active==before


def test_adopted_helper_budget_cannot_be_silently_reduced(tmp_path):
    snap, base, added=helpers(tmp_path)
    old, _=planning_helper_source_support(snap, base)
    active=source_context_offer(context(), old, producer='planning_helper')
    with pytest.raises(BudgetExceeded):
        planning_helper_source_support(snap, base+added, max_bytes=1, source_context=active)


def test_helper_append_uses_remaining_quota_and_never_recharges_old(tmp_path):
    snap, base, added=helpers(tmp_path)
    first, _=planning_helper_source_support(snap, added)
    active=source_context_offer(context(), first, producer='planning_helper')
    actual, _=planning_helper_source_support(snap, added+base, source_context=active)
    assert actual[:len(first)]==first and len(actual)==8
    assert len({r['id'] for r in actual})==8
    committed=source_context_offer(active, actual, producer='planning_helper')
    repeat, _=planning_helper_source_support(snap, base+added, source_context=committed)
    assert repeat==actual


def test_consumer_passes_context_per_call_without_changing_accepted_plan(tmp_path):
    source(tmp_path)
    provider=LiteralProvider()
    consumer=TopoMenRepairConsumer()
    item=consumer.prepare(semantic(tmp_path), ConsumerContext(tmp_path, POLICY, CONFIG, 'Restore selected name.'))
    accepted=consumer.accept(item, compute_snapshot(tmp_path), POLICY)
    active=context(item.input_digest)
    plan=copy.deepcopy(item.payload)
    consumer.execute(accepted, BudgetPort(provider, count, CONFIG, source_context=active))
    assert item.payload==plan
    assert provider.requests[0].metadata['source_context']['binding']==active['binding']
    assert provider.requests[0].metadata['source_context']['targets']
    assert active['targets']==[]  # only orchestration commits actual dispatch


def test_legacy_custom_executor_rejected_only_when_context_is_required(tmp_path):
    source(tmp_path)
    class Legacy:
        def execute(self, plan, source, policy, **kwargs):
            return {'legacy': True}
    consumer=TopoMenRepairConsumer(executor=Legacy())
    item=consumer.prepare(semantic(tmp_path), ConsumerContext(tmp_path, POLICY, CONFIG, 'Restore selected name.'))
    accepted=consumer.accept(item, compute_snapshot(tmp_path), POLICY)
    assert consumer.execute(accepted, BudgetPort(None, count, CONFIG))=={'legacy': True}
    with pytest.raises(PlanRejected, match='source context'):
        consumer.execute(accepted, BudgetPort(None, count, CONFIG, source_context=context(item.input_digest)))


def execution_helpers(tmp_path):
    helpers(tmp_path)
    source(tmp_path)
    snap=compute_snapshot(tmp_path)
    plan=bound_plan(tmp_path)
    plan['operations'][0]['target'].update(path='app/base.go', content_digest=snap.file_digests['app/base.go'],
        locator={'start_line':3, 'end_line':3})
    config=dict(CONFIG, execution_helper_support_enabled=True, max_execution_support_bytes=24000)
    active=context(plan['input_digest'])
    request=build_execution_request(plan, tmp_path, {'allowed_paths':['app/**']}, len, config,
        requirement=REQUIREMENT, source_context=active)
    return plan, config, active, request


def test_execution_retains_eighth_helper_and_previous_target_after_revised_target(tmp_path):
    plan, config, active, request=execution_helpers(tmp_path)
    require_delivery(inspect(request, plan, tmp_path))
    committed=source_context_commit(active, request.metadata['source_context'], 'response_known')
    first=json.loads(request.messages[1]['content'])
    assert len(first['read_only_source_support'])==8
    assert first['adopted_source_context']=={'records':[], 'targets':[]}
    later=copy.deepcopy(plan)
    snap=compute_snapshot(tmp_path)
    later['operations'][0]['target'].update(path='app/added.go', content_digest=snap.file_digests['app/added.go'])
    second=build_execution_request(later, tmp_path, {'allowed_paths':['app/**']}, len, config,
        requirement=REQUIREMENT, source_context=committed)
    body=json.loads(second.messages[1]['content'])
    assert body['read_only_source_support']==first['read_only_source_support']
    assert len(second.metadata['source_context']['producer_refs']['execution_helper'])==8
    assert body['adopted_source_context']['targets'][0]['content']==(tmp_path/'app/base.go').read_text()
    require_delivery(inspect(second, later, tmp_path))
    def drop(body):
        body['adopted_source_context']['targets'].clear()
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(second, later, tmp_path, drop))


def test_snapshot_change_keeps_historical_helpers_without_relabelling(tmp_path):
    from contractedge.execution.consumers.topomen import rebind_execution_plan
    plan, config, active, request=execution_helpers(tmp_path)
    committed=source_context_commit(active, request.metadata['source_context'], 'response_unknown')
    old_snapshot=plan['snapshot_id']
    with (tmp_path/'app/base.go').open('a') as stream:
        stream.write('// New source snapshot after an edit.\n')
    rebound=rebind_execution_plan(plan, tmp_path, {})
    later=build_execution_request(rebound, tmp_path, {'allowed_paths':['app/**']}, len, config,
        requirement=REQUIREMENT, source_context=committed)
    body=json.loads(later.messages[1]['content'])
    assert body['read_only_source_support']==[]
    assert len(body['adopted_source_context']['records'])==8
    assert all(r['snapshot_id']==old_snapshot for r in body['adopted_source_context']['records'])
    require_delivery(inspect(later, rebound, tmp_path))
    def lose(body):
        body['adopted_source_context']['records'].pop()
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(later, rebound, tmp_path, lose))


def test_dependency_context_reuses_actual_pinned_api_without_locator_or_new_quota(tmp_path):
    from tests.test_compiler_dependency_context import request_fixture
    snap, feedback, root, plan, config=request_fixture(tmp_path)
    active=context(plan['input_digest'])
    first=build_execution_request(plan, root, {'allowed_paths':['main.go']}, len, config,
        requirement=REQUIREMENT, feedback=feedback, source_context=active)
    adopted=source_context_commit(active, first.metadata['source_context'], 'response_known')
    second=build_execution_request(plan, root, {'allowed_paths':['main.go']}, len, config,
        requirement=REQUIREMENT, feedback=feedback, source_context=adopted)
    assert len(second.metadata['source_context']['producer_refs']['execution_helper'])==1
    assert json.loads(second.messages[1]['content'])['compiler_api_context']==json.loads(first.messages[1]['content'])['compiler_api_context']
    require_delivery(inspect(second, plan, root))


def test_native_agent_carries_context_in_the_actual_budget_record(tmp_path):
    from tests.test_topomen_agent import AVAILABLE, source_fixture
    from contractedge.execution.topomen_agent import TopoMenAgentExecutor, source_identity
    from contractedge.development import public_demo
    from contractedge.model.budget import BudgetedProvider
    from contractedge.model.provider import FixtureProvider
    if not AVAILABLE:
        pytest.skip('Native TopoMen checkout/dependencies unavailable')
    target, plan, edits=source_fixture(tmp_path)
    active=context(plan['input_digest'])
    budget=BudgetedProvider(FixtureProvider(edits), len, max_tokens=200000, max_seconds=30)
    # Public workflow tests load the bundled source in this same process. Use
    # that identical root rather than requesting a second native provenance.
    native = public_demo.SYSTEM_ROOT / 'vendor/topomen'
    cfg=dict(model='fixture', max_context_tokens=100000, execution_max_output_tokens=1024,
        topomen_source_root=str(native), topomen_source_sha256=source_identity(native)['source_sha256'],
        execution_backend='topomen_agent')
    result=TopoMenAgentExecutor(budget, len, cfg).execute(plan, tmp_path, POLICY,
        requirement='Use selected name', source_context=active)
    assert result['status']=='APPLIED' and target.read_text()=='name: selected\n'
    assert len(budget.calls)==1
    recorded=budget.calls[0]['request_metadata']['source_context']
    assert recorded['binding']==active['binding'] and len(recorded['targets'])==1
    assert active['targets']==[]


def test_required_helper_reread_preserves_original_identity_across_two_continuations(tmp_path):
    from contractedge.execution.consumers.topomen import rebind_execution_plan
    plan, config, active, first=execution_helpers(tmp_path)
    record=copy.deepcopy(first.metadata['source_context']['records'][0])
    record['delivery_required']=True
    active=source_context_offer(active, [record], producer='planning_helper')
    plan['coordination_context']={'source_support':[record]}
    config=dict(config, execution_helper_support_enabled=False,
        max_execution_support_bytes=2*len(record['excerpt'].encode()))
    with (tmp_path/'app/base.go').open('a') as stream:
        stream.write('// continuation\n')
    rebound=rebind_execution_plan(plan, tmp_path, {})
    request=build_execution_request(rebound, tmp_path, {'allowed_paths':['app/**']}, len, config,
        requirement=REQUIREMENT, source_context=active)
    body=json.loads(request.messages[1]['content'])
    assert body['accepted_plan']['coordination_context']['source_support'][0]==record
    current=body['read_only_source_support'][0]
    assert current['id']==record['id'] and current['snapshot_id']==rebound['snapshot_id']!=record['snapshot_id']
    committed=source_context_commit(active, request.metadata['source_context'], 'response_known')
    again=build_execution_request(rebound, tmp_path, {'allowed_paths':['app/**']}, len, config,
        requirement=REQUIREMENT, source_context=committed)
    repeated=json.loads(again.messages[1]['content'])
    assert repeated['read_only_source_support']==[current]
    assert len(again.metadata['source_context']['producer_refs']['planning_helper'])==1
    assert len(again.metadata['source_context']['producer_refs']['reread'])==1
    require_delivery(inspect(again, rebound, tmp_path))
