"""TopoMen interchange and native Agent lifecycle behind RepairConsumer."""
from __future__ import annotations
import json
from typing import Mapping
from pathlib import Path
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.contracts.repair_intent import SemanticChangeSetIntent
from contractedge.contracts.validation import canonical_digest
from contractedge.execution.consumer import ConsumerCapabilities, ExecutionInput, AcceptedInput
from contractedge.execution.topomen import PlanRejected, accept_plan, operation_path
from contractedge.source.snapshot import compute_snapshot


class TopoMenRepairConsumer:
    def __init__(self, *, backend='direct', executor=None, on_trace=None):
        if backend not in {'direct','topomen_agent'}:
            raise ValueError('Unknown execution backend: '+str(backend))
        self.backend,self.executor,self.on_trace=backend,executor,on_trace
        self.trial_session=None

    def describe(self):
        return ConsumerCapabilities('contractedge.topomen_source_consumer.v1',
            ('SEMANTIC_CHANGESET',),('MODIFY','CREATE'),('topomen-source-execution.v1',),
            received_response_recovery=self.backend=='direct' and (
                self.executor is None or hasattr(self.executor,'consume_received_response')))

    @property
    def last_trace(self):
        return getattr(self.executor,'last_trace',None)

    def prepare(self, intent, context):
        if not isinstance(intent,SemanticChangeSetIntent):
            raise PlanRejected('TopoMen consumer rejects unsupported intent: '+str(getattr(intent,'kind',None)))
        if context.rebind and context.previous_input is not None:
            if context.previous_input.consumer_id!=self.describe().consumer_id:
                raise PlanRejected('Cannot rebind another consumer input')
            plan=rebind_execution_plan(context.previous_input.payload,context.source,context.feedback or {},
                                       prior_execution=context.prior_execution)
        else:
            plan=translate_for_topomen(intent.changeset,'contractedge.topomen_plan.v1',
                diagnosis=intent.diagnosis, public_capabilities=context.config.get('public_execution_capabilities'),
                source_support=list(intent.source_support))
        return ExecutionInput(self.describe().consumer_id,intent.kind,plan['snapshot_id'],plan['input_digest'],
            plan,tuple(sorted({operation_path(op) for op in plan['operations']})),context)

    def accept(self, execution_input, snapshot, policy):
        if execution_input.consumer_id!=self.describe().consumer_id or execution_input.intent_kind!='SEMANTIC_CHANGESET':
            raise PlanRejected('Input does not belong to TopoMen consumer')
        if snapshot.snapshot_id!=execution_input.snapshot_id:
            raise PlanRejected('Consumer input snapshot is stale')
        acceptance=accept_plan(execution_input.payload,execution_input.context.source,policy)
        return AcceptedInput(execution_input,acceptance,execution_input.digest())

    def execute(self, accepted_input, budget_port):
        accepted_input.verify(self.describe().consumer_id)
        item=accepted_input.execution_input;context=item.context
        # Recheck authority and snapshot at dispatch, including caller mutations.
        self.accept(item,compute_snapshot(context.source),accepted_input.acceptance['scope_policy'])
        if self.executor is None:
            if self.backend=='topomen_agent':
                from contractedge.execution.topomen_agent import TopoMenAgentExecutor
                self.executor=TopoMenAgentExecutor(budget_port.provider,budget_port.token_counter,budget_port.config,
                                                   on_trace=self.on_trace)
            else:
                from contractedge.execution.topomen import ModelPlanExecutor
                self.executor=ModelPlanExecutor(budget_port.provider,budget_port.token_counter,budget_port.config)
        # C2 owns durable request/response/edit hooks. Never emulate them here.
        self.executor.trial_session=budget_port.trial_session or self.trial_session
        return self.executor.execute(item.payload,context.source,accepted_input.acceptance['scope_policy'],
            requirement=context.requirement,declared_artifacts=context.declared_artifacts,
            observations=context.observations,feedback=context.feedback,previous_attempt=context.previous_attempt)


    def consume_received_response(self, request, response, accepted_input, budget_port, *, response_id=None):
        if not isinstance(accepted_input,AcceptedInput):
            raise PlanRejected('Received-response recovery requires validated consumer input and authority')
        accepted_input.verify(self.describe().consumer_id)
        if self.backend=='topomen_agent':
            raise PlanRejected('Native TopoMen received-response replay is unsupported; original response/reservations are retained')
        if self.executor is None:
            from contractedge.execution.topomen import ModelPlanExecutor
            self.executor=ModelPlanExecutor(None,None,budget_port.config)
        if not hasattr(self.executor,'consume_received_response'):
            raise PlanRejected('Selected executor does not support durable received-response recovery')
        item=accepted_input.execution_input
        return self.executor.consume_received_response(request,response,item.payload,item.context.source,
            accepted_input.acceptance['scope_policy'],trial_session=budget_port.trial_session,
            response_id=response_id)


def rebind_execution_plan(plan: Mapping, source: Path, feedback: Mapping, *, prior_execution=None) -> dict:
    """Keep semantic intent for EXECUTE retries, bind only fresh concrete targets."""
    snapshot = compute_snapshot(source)
    rebound = json.loads(json.dumps(plan))
    old_digest = canonical_digest(plan)
    rebound['snapshot_id'] = snapshot.snapshot_id
    for op in rebound['operations']:
        target = op['target']
        if target['kind'] == 'EXISTING':
            if target['path'] not in snapshot.file_digests:
                raise PlanRejected('Execution retry lost an existing target; rediagnosis required')
            target['snapshot_id'] = snapshot.snapshot_id
            target['content_digest'] = snapshot.file_digests[target['path']]
        elif (source / target['proposed_path']).exists():
            # CREATE already materialized; revise its concrete implementation.
            op['operation'] = 'MODIFY'
            op['target'] = {'kind': 'EXISTING', 'path': target['proposed_path'],
                            'snapshot_id': snapshot.snapshot_id,
                            'content_digest': snapshot.file_digests[target['proposed_path']],
                            'unit_refs': [], 'locator': {}}
    rebound['execution_continuation'] = {'prior_plan_digest': old_digest,
        'feedback_digest': canonical_digest(feedback), 'route': 'EXECUTE',
        'interpretation': 'Semantic intent retained; old diagnosis evidence is historical, not rebound as fresh facts.'}
    proof=prior_execution or {}
    if (proof.get('status')=='APPLIED' and proof.get('to_snapshot')==snapshot.snapshot_id
            and proof.get('from_snapshot')==plan['snapshot_id']
            and proof.get('acceptance',{}).get('plan_digest')==old_digest):
        edited={r['change_id'] for r in proof.get('semantic_edit_trace',[])
                if any(e.get('old_text')!=e.get('new_text') for e in r.get('literal_edits',[]))}
        eligible={}
        for op in rebound['operations']:
            target=op['target'];cid=op['change_id'];path=target.get('path')
            if (cid in edited and cid in proof.get('change_ids',[]) and target['kind']=='EXISTING'
                    and proof.get('file_digests',{}).get(path)==snapshot.file_digests.get(path)):
                eligible[cid]=dict(path=path,snapshot_id=snapshot.snapshot_id,content_digest=snapshot.file_digests[path])
        rebound['execution_continuation'].update(prior_applied_operations=eligible,
            prior_execution_digest=canonical_digest(proof),
            coverage_boundary='Previously applied source transaction, not semantic completion. All postconditions and preservation remain required.')
    return rebound


