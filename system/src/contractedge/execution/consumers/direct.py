"""A MODIFY-only consumer with its own writes/replacements dialect.

Semantic inputs remain model-authored. Relation/requirement modes deliver source
inventory and their real input only; literal edits are not backfilled as C/P.
"""
from __future__ import annotations
from dataclasses import replace
import json
from typing import Mapping
from contractedge.contracts.permissions import target_permission_error
from contractedge.contracts.repair_intent import (SemanticChangeSetIntent, DirectPlanIntent,
    RelationOnlyIntent, RequirementOnlyIntent, intent_identity)
from contractedge.contracts.validation import canonical_digest
from contractedge.execution.consumer import (ConsumerCapabilities, ExecutionInput, AcceptedInput)
from contractedge.execution.literal import (PlanRejected, LiteralTarget, safe_target,
    validate_literal_targets, apply_literal_edits)
from contractedge.model.admission import quote_request, context_fits, complete_quoted
from contractedge.model.provider import ModelRequest
from contractedge.source.snapshot import compute_snapshot
from jsonschema import Draft202012Validator


class ModifyOnlyConsumer:
    ID='contractedge.modify_only_consumer.v1'
    PROMPT='modify-only-literal-execution.v1'

    def describe(self):
        return ConsumerCapabilities(self.ID,('SEMANTIC_CHANGESET','DIRECT_PLAN','RELATION_ONLY','REQUIREMENT_ONLY'),
            ('MODIFY',),(self.PROMPT,),received_response_recovery=True)

    def prepare(self, intent, context):
        if getattr(intent,'kind',None) not in self.describe().supported_intents:
            raise PlanRejected('MODIFY-only consumer rejects unsupported intent')
        if context.previous_input is not None and (context.previous_input.consumer_id!=self.ID
                or context.previous_input.intent_kind!=intent.kind):
            raise PlanRejected('Previous input belongs to a different consumer or intent')
        if intent.kind!='SEMANTIC_CHANGESET' and context.previous_attempt and (
                context.previous_attempt.get('consumer_id')!=self.ID
                or context.previous_attempt.get('intent_kind')!=intent.kind):
            raise PlanRejected('Correction input lacks matching consumer/mode provenance')
        snapshot_id,input_digest=intent_identity(intent)
        snapshot=compute_snapshot(context.source)
        if context.rebind and context.previous_input is not None:
            if context.previous_input.consumer_id!=self.ID:
                raise PlanRejected('Cannot rebind another consumer input')
            snapshot_id=snapshot.snapshot_id
        elif snapshot.snapshot_id!=snapshot_id:
            raise PlanRejected('Repair intent snapshot is stale')
        writes=[]
        if isinstance(intent,SemanticChangeSetIntent):
            original={'changeset':dict(intent.changeset),'diagnosis':intent.diagnosis,
                      'source_support':list(intent.source_support)}
            for change in intent.changeset.get('changes',[]):
                target=change['target']
                if change['operation']!='MODIFY' or target.get('kind')!='EXISTING':
                    raise PlanRejected('MODIFY-only consumer rejects unsupported operation: '+change['operation'])
                if not context.rebind and (target.get('snapshot_id')!=snapshot_id
                        or target.get('content_digest')!=snapshot.file_digests.get(target['path'])):
                    raise PlanRejected('Semantic target identity mismatch')
                writes.append({'key':change['id'],'file':target['path'],'kind':'MODIFY',
                    'before_digest':snapshot.file_digests.get(target['path']),
                    'purpose':change['intent'],'after':list(change['depends_on'])})
        elif isinstance(intent,DirectPlanIntent):
            original=dict(intent.plan)
            if (set(original)-{'schema_version','snapshot_id','input_digest','goal','steps'}
                    or original.get('schema_version')!='ce.direct_plan.v1'):
                raise PlanRejected('DirectPlan must use its independent strict contract')
            for step in original.get('steps',[]):
                if not isinstance(step,Mapping) or set(step)!={'id','path','operation','intent','depends_on'}:
                    raise PlanRejected('DirectPlan step fields are invalid')
                if step['operation']!='MODIFY':
                    raise PlanRejected('MODIFY-only consumer rejects unsupported operation: '+step['operation'])
                writes.append({'key':step['id'],'file':step['path'],'kind':'MODIFY',
                    'before_digest':snapshot.file_digests.get(step['path']),
                    'purpose':step['intent'],'after':list(step['depends_on'])})
        elif isinstance(intent,RelationOnlyIntent):
            original={'diagnosis':dict(intent.diagnosis)}
        else:
            original={'requirement':context.requirement}
        applicable=isinstance(intent,(SemanticChangeSetIntent,DirectPlanIntent))
        if applicable and not writes:
            raise PlanRejected('Planned intent has no executable writes')
        # With no authored edit plan, deliver the same presemantic public scope.
        # These are permitted source files, not invented semantic targets/steps.
        paths=sorted({row['file'] for row in writes}) if applicable else sorted(
            path for path in snapshot.file_digests if target_permission_error(path,context.policy) is None)
        if not paths:
            raise PlanRejected('Consumer has no editable source input')
        payload={'schema_version':'ce.modify_input.v1','snapshot_id':snapshot_id,'input_digest':input_digest,
            'intent_kind':intent.kind,'model_authored_input':original,'writes':writes,
            'source_inventory':paths,'semantic_history_is_fresh':not context.rebind}
        return ExecutionInput(self.ID,intent.kind,snapshot_id,input_digest,payload,tuple(paths),context,
            'APPLICABLE' if applicable else 'NOT_APPLICABLE_NO_AUTHORED_OPERATION_PLAN')

    def _targets(self,item):
        return tuple(LiteralTarget(row['key'],row['file'],row['kind'],row['before_digest'])
                     for row in item.payload['writes'])

    def accept(self, execution_input, snapshot, policy):
        item=execution_input
        if item.consumer_id!=self.ID or item.intent_kind not in self.describe().supported_intents:
            raise PlanRejected('Input does not belong to MODIFY-only consumer')
        if item.snapshot_id!=snapshot.snapshot_id or item.payload['snapshot_id']!=item.snapshot_id:
            raise PlanRejected('Consumer input snapshot is stale')
        targets=self._targets(item)
        # Source-only modes have no planned operations; path validation still
        # applies before dispatch and response targets are validated separately.
        inventory=tuple(LiteralTarget(path,path,'MODIFY',snapshot.file_digests.get(path)) for path in item.target_paths)
        validate_literal_targets(item.context.source,policy,item.snapshot_id,targets or inventory,('MODIFY',))
        order=[];remaining={target.key for target in targets}
        if len(remaining)!=len(targets):
            raise PlanRejected('Write IDs must be unique')
        dependencies={row['key']:set(row['after']) for row in item.payload['writes']}
        while remaining:
            ready=sorted(key for key in remaining if dependencies[key]<=set(order))
            if not ready:
                raise PlanRejected('Write dependencies are missing or cyclic')
            order.extend(ready);remaining.difference_update(ready)
        acceptance={'schema_version':'ce.consumer_acceptance.v1','consumer_id':self.ID,'decision':'ACCEPT',
            'plan_digest':canonical_digest(item.payload),'snapshot_id':item.snapshot_id,'input_digest':item.input_digest,
            'operation_order':order,'scope_policy':dict(policy),'supported_operations':['MODIFY'],
            'operation_coverage':item.operation_coverage}
        return AcceptedInput(item,acceptance,item.digest())

    def execute(self, accepted_input, budget_port):
        accepted_input.verify(self.ID)
        item=accepted_input.execution_input;context=item.context
        policy=accepted_input.acceptance['scope_policy']
        self.accept(item,compute_snapshot(context.source),policy)
        files=[];used=0
        for path in item.target_paths:
            text=safe_target(context.source,path).read_text(encoding='utf-8')
            size=len(text.encode());used+=size
            if size>1024*1024 or used>4*1024*1024:
                raise PlanRejected('Source inventory exceeds consumer byte limit')
            files.append({'file':path,'text':text})
        properties={'key':{'type':'string'},'file':{'type':'string','enum':list(item.target_paths)},
            'before':{'type':'string','minLength':1},'after':{'type':'string'}}
        if item.payload['writes']:
            properties['key']['enum']=[row['key'] for row in item.payload['writes']]
        schema={'type':'object','properties':{'replacements':{'type':'array','minItems':1,'maxItems':128,
            'items':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}}},
            'required':['replacements'],'additionalProperties':False}
        payload={'repair_input':item.payload,'requirement':context.requirement,
            'current_files':files,'declared_artifacts':list(context.declared_artifacts),
            'public_observations':list(context.observations),'previous_attempt':context.previous_attempt}
        # No Full feedback or retained semantic plan is injected into ablations.
        # D2 supplies a mode-specific projection rather than deleting fields here.
        if item.intent_kind=='SEMANTIC_CHANGESET':
            payload['feedback']=context.feedback
        request=ModelRequest(model=str(budget_port.config['model']),prompt_version=self.PROMPT,
            messages=({'role':'system','content':'Implement the actual repair input with literal replacements in current_files. Respect its existing conditions and preservation obligations. No shell or other file operation is available. Return JSON replacements only. Source inventory grants edit permission, and is not an authored semantic plan.'},
                      {'role':'user','content':json.dumps(payload,ensure_ascii=False)}),
            max_output_tokens=int(budget_port.config.get('execution_max_output_tokens',8192)),output_schema=schema)
        quote=quote_request(budget_port.provider,request,budget_port.token_counter)
        if not context_fits(quote,int(budget_port.config['max_context_tokens']),budget_port.config.get('model_context_tokens')):
            raise PlanRejected('Execution request exceeds the full context budget')
        response=complete_quoted(budget_port.provider,request,quote)
        trial=budget_port.trial_session
        response_id=trial.record_response(request,response) if trial is not None else None
        return self.consume_received_response(request,response,accepted_input,budget_port,response_id=response_id)

    def consume_received_response(self,request,response,accepted_input,budget_port,*,response_id=None):
        """Deterministic replay of already received bytes; never invokes provider."""
        if not isinstance(accepted_input,AcceptedInput):
            raise PlanRejected('Received-response recovery requires validated consumer input and authority')
        accepted_input.verify(self.ID)
        item=accepted_input.execution_input;context=item.context
        policy=accepted_input.acceptance['scope_policy']
        self.accept(item,compute_snapshot(context.source),policy)
        if budget_port.trial_session is not None:
            if not response_id:
                raise PlanRejected('Durable response identity is required')
            budget_port.trial_session.consume_response(response_id)
        schema=request.output_schema
        if response.metadata.get('finish_reason') in {'length','max_tokens'}:
            raise PlanRejected('Literal response was truncated')
        body=json.loads(response.content)
        errors=list(Draft202012Validator(schema).iter_errors(body))
        if errors:
            raise PlanRejected('Literal response schema invalid: '+errors[0].message)
        edits=[{'change_id':row['key'],'path':row['file'],'operation':'MODIFY',
                'old_text':row['before'],'new_text':row['after']} for row in body['replacements']]
        targets=self._targets(item)
        if not targets:
            snapshot=compute_snapshot(context.source)
            keyed={}
            for row in body['replacements']:
                current=LiteralTarget(row['key'],row['file'],'MODIFY',snapshot.file_digests.get(row['file']))
                if row['key'] in keyed and keyed[row['key']]!=current:
                    raise PlanRejected('Literal key cannot name multiple files')
                keyed[row['key']]=current
            targets=tuple(keyed.values())
        facts=apply_literal_edits(context.source,policy,{'edits':edits},snapshot_id=item.snapshot_id,
            targets=targets,operation_order=accepted_input.acceptance['operation_order'] or None,
            require_behavior_change=self._behavior_change_required(item),
            supported_operations=('MODIFY',),preflight=lambda:self.accept(item,compute_snapshot(context.source),policy),
            trial_session=budget_port.trial_session)
        current=facts['snapshot'];counts=facts['per_change']
        result={'schema_version':'ce.concrete_execution.v1','consumer_id':self.ID,'status':'APPLIED',
            'acceptance':dict(accepted_input.acceptance),'from_snapshot':item.snapshot_id,'to_snapshot':current.snapshot_id,
            'changed_paths':sorted(facts['changed']),'change_ids':sorted(counts),'unchanged_change_ids':[],
            'file_digests':{path:current.file_digests[path] for path in facts['changed']},
            'operation_coverage':([{'change_id':t.key,'path':t.path,'edit_count':counts[t.key],
                'disposition':'EDITED_SEMANTICS_UNVERIFIED'} for t in targets]
                if item.operation_coverage=='APPLICABLE' else []),
            'operation_coverage_status':item.operation_coverage,
            'literal_edit_trace':[dict(row) for row in body['replacements']],
            'semantic_edit_trace':[],
            'semantic_faithfulness':'NOT_PROVEN_BY_EDIT_COVERAGE; requires public observation',
            'hidden_evaluation':'NOT_AVAILABLE_TO_METHOD',
            'model_response':{'content':response.content,'metadata':dict(response.metadata)}}
        if budget_port.config.get('helper_preconditions_enabled') and item.intent_kind=='SEMANTIC_CHANGESET':
            from contractedge.execution.preconditions import validate_helper_preconditions
            original=item.payload['model_authored_input']
            helpers=[*original['source_support'],*(original.get('diagnosis') or {}).get('evidence_refs',[])]
            result['helper_preconditions']=validate_helper_preconditions(original['changeset'],
                {path:safe_target(context.source,path).read_text() for path in facts['changed']},helpers)
        if budget_port.trial_session is not None:
            budget_port.trial_session.record('RESPONSE_CONSUME_COMPLETED',response_id=response_id,
                                             snapshot_id=result['to_snapshot'])
        return result

    @staticmethod
    def _behavior_change_required(item):
        from contractedge.execution.code_change import behavior_change_required
        return item.intent_kind=='SEMANTIC_CHANGESET' and behavior_change_required(
            item.context.feedback,item.snapshot_id)
