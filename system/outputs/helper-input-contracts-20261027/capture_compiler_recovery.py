"""Zero-call capture of the actual native recovery provider boundary."""
from dataclasses import asdict
import importlib.util
from pathlib import Path
import json

FILE=Path(__file__).with_name('compiler_feedback_recovery.py')
spec=importlib.util.spec_from_file_location('recovery_component',FILE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
import contractedge.execution.topomen_agent as native_module

class Captured(BaseException):
    pass


def main():
    config=m.read(m.CONFIG);provider,counter=m._build_provider(config);money_before=provider.money_budget.summary()
    source=Path(m.read(m.ROOT/'workspace/workspace.json')['source_root']);before=m.compute_snapshot(source).snapshot_id
    request,quote=m.request_for(source,config,provider,counter)
    assert json.loads(json.dumps(asdict(request)))==m.read(m.ROOT/'expected-request.json')
    seen=[]
    def capture(actual_provider,actual_request,actual_quote):
        assert asdict(actual_request)==asdict(request) and actual_quote.request_digest==quote.request_digest
        wire=json.loads(actual_quote.prepared.body)
        assert wire['messages'][1]['content']==actual_request.messages[1]['content']
        seen.append(actual_quote.request_digest)
        raise Captured()
    previous=native_module.complete_quoted;native_module.complete_quoted=capture
    plan,payload=m.component_inputs();executor=m.TopoMenAgentExecutor(provider,counter,config)
    try:
        try:executor.execute(plan,source,payload['acceptance']['scope_policy'],requirement=payload['requirement'],
            declared_artifacts=payload['declared_artifacts'],observations=payload['public_observations'],
            previous_attempt=m.recovery_attempt()[0])
        except Captured:pass
    finally:native_module.complete_quoted=previous
    assert seen==[quote.request_digest] and executor.last_trace['backend']['agent_class'].endswith('.DefaultAgent')
    assert before==m.compute_snapshot(source).snapshot_id and provider.money_budget.summary()==money_before
    result=dict(status='NATIVE_COMPILER_RECOVERY_INPUT_CAPTURE_PASS',api_calls=0,source_unchanged=True,
        actual_native_request_equals_registered=True,adapter_body_equals_request=True,
        only_payload_change='previous_execution_attempt',historical_trial_snapshot=m.read(m.FAILED/'after.json')['snapshot_id'],
        restored_current_snapshot=before,public_failure_kind='CANDIDATE_BUILD_FAILURE',
        request_digest=quote.request_digest,total_token_bound=quote.total_tokens,context_cap=config['max_context_tokens'],
        thinking=request.thinking,formal_ready=False)
    m.write_json(m.OUT/'COMPILER_RECOVERY_INPUT_CAPTURE.json',result)
    print(json.dumps(result))

if __name__=='__main__':main()
