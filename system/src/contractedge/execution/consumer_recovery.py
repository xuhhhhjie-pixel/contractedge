"""Rebuild consumer authority by validating the original accepted input again."""
from pathlib import Path

from contractedge.contracts.validation import canonical_digest
from contractedge.execution.consumer import ConsumerContext,ExecutionInput,build_consumer,validate_consumer,PlanRejected
from contractedge.source.snapshot import compute_snapshot


def recovery_consumer(consumer_id,backend,*,consumer=None,executor=None):
    if consumer is None:
        if consumer_id=='contractedge.modify_only_consumer.v1':
            from contractedge.execution.consumers.direct import ModifyOnlyConsumer
            consumer=ModifyOnlyConsumer()
        else:
            consumer=build_consumer(backend,executor=executor)
    if validate_consumer(consumer).consumer_id!=consumer_id:
        raise PlanRejected('Recovery consumer identity differs')
    return consumer


def reaccept(consumer,authority,source,policy,method_input,*,plan):
    capabilities=validate_consumer(consumer);snapshot=compute_snapshot(source)
    context_value=authority['context']
    if (authority['consumer_id']!=capabilities.consumer_id or authority['intent_kind'] not in capabilities.supported_intents
            or authority['snapshot_id']!=snapshot.snapshot_id or authority['input_digest']!=method_input.input_digest
            or Path(context_value['source_root']).resolve()!=Path(source).resolve()
            or context_value['policy']!=dict(policy) or authority['payload']!=plan
            or context_value['requirement']!=method_input.requirement_text
            or context_value['declared_artifacts']!=list(method_input.declared_artifacts)):
        raise PlanRejected('Recovered consumer source/input/policy/plan authority differs')
    context=ConsumerContext(Path(source),policy,context_value['config'],method_input.requirement_text,
        tuple(method_input.declared_artifacts),tuple(context_value['observations']),context_value['feedback'],
        context_value['previous_attempt'],prior_execution=context_value['prior_execution'],rebind=context_value['rebind'])
    item=ExecutionInput(authority['consumer_id'],authority['intent_kind'],snapshot.snapshot_id,
        method_input.input_digest,plan,tuple(authority['target_paths']),context,authority['operation_coverage'])
    accepted=consumer.accept(item,snapshot,policy)
    accepted.verify(capabilities.consumer_id)
    if (accepted.execution_digest!=authority['execution_digest'] or accepted.acceptance!=authority['acceptance']
            or accepted.acceptance_digest!=authority['acceptance_digest']
            or canonical_digest(authority['acceptance'])!=authority['acceptance_digest']):
        raise PlanRejected('Recovered acceptance no longer matches original validated authority')
    return accepted
