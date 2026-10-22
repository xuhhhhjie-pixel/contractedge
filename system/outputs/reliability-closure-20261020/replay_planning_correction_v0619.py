"""Replay actual saved front responses through the real native execution boundary, zero HTTP."""
import argparse
import json
from pathlib import Path

from contractedge.cli import _token_counter
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case


class Captured(BaseException):
    pass


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
    origin=out/'runs/delivery-0.6.18-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    parser=argparse.ArgumentParser();parser.add_argument('--output-name',default='planning-correction-saved-replay-v0619');args=parser.parse_args()
    dest=out/args.output_name;dest.mkdir(exist_ok=False)
    read=lambda p:json.loads(p.read_text())
    calls=read(origin/'model_calls.json');old=read(origin/'rounds/0/front.trace.json')
    config=read(root/'configs/deepseek-reliability-closure-v6-budget150.json')
    config['run_id']=old['run_id']
    benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    class ReplayPublic(BenchmarkPublicRunner):
        def observe(self,workspace,output):
            assert self._reads==0,'No candidate was executed in this transport replay'
            self._reads+=1
            return read(origin/'broken-public/observation.json')
        _reads=0
    public=ReplayPublic(benchmark/'public',benchmark/'tools',lock_root='/tmp/contractedge-system-native-locks',
        timeout_seconds=2400,query=read(origin/'method_input.json')['requirement_text'],method_probe_profiles=config['method_probe_profiles'])
    class Replay:
        index=0
        request=None
        def complete_json(self,request):
            if self.index==len(calls):
                self.request=request;raise Captured()
            assert self.index<len(calls)
            saved=calls[self.index];self.index+=1
            assert saved['prompt_version']==request.prompt_version
            return ModelResponse(saved['response_content'],saved['response_metadata'])
    provider=Replay()
    try:
        result=repair_case(benchmark/'public',benchmark/'tools','RW-ALERTMANAGER-4705',dest/'case',
            provider,_token_counter(config['tokenizer']),config,public_runner=public)
    except Captured:
        assert provider.request is not None
        assert provider.index==4
        assert provider.request.prompt_version.startswith('changeset.')
    else:raise AssertionError(('Planning correction not reached',result['status'],result['errors']))
    body=json.loads(provider.request.messages[1]['content'])
    assert any(f['code']=='PRESERVE_CONSTRAINT_MISSING' for f in body['validation_feedback'])
    assert provider.request.metadata['planning_format_retry_index']==1
    assert sum(_token_counter(config['tokenizer'])(m['content']) for m in provider.request.messages)+provider.request.max_output_tokens<=config['max_context_tokens']
    from contractedge.source.snapshot import compute_snapshot
    workspace=dest/'case/workspace'
    prepared=read(workspace/'workspace.json')
    initial=read(origin/'method_input.json')['initial_snapshot_id']
    current=compute_snapshot(Path(prepared['source_root'])).snapshot_id
    assert current==initial
    exported=public.export(workspace,dest/'unchanged.patch',dest/'unchanged-export')
    assert exported['patch_bytes']==0
    receipt=dest/'source-unchanged.json'
    receipt.write_text(json.dumps(dict(initial_snapshot_id=initial,final_snapshot_id=current,patch=exported),indent=2)+'\n')
    record=dict(scope='ZERO_HTTP_ACTUAL_FOUR_RESPONSE_REPLAY_TO_PREVIOUSLY_MISSING_PLANNING_CORRECTION; NOT_MODEL_SUCCESS',
        api_calls=0,saved_front_responses=provider.index,cached_public_scope='Exact saved S0 public observations; no fresh runtime/performance claim',
        actual_validation_feedback_delivered=True,existing_planning_correction_captured=True,request_version=provider.request.prompt_version,
        input_bound=sum(_token_counter(config['tokenizer'])(m['content']) for m in provider.request.messages),format_retry_index=provider.request.metadata['planning_format_retry_index'],
        support_audit=provider.request.metadata.get('read_only_support_audit'),
        source_changed=False,source_unchanged_receipt=str(receipt))
    (dest/'request.json').write_text(json.dumps(dict(messages=list(provider.request.messages),metadata=provider.request.metadata),ensure_ascii=False,indent=2)+'\n')
    (dest/'summary.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');print(json.dumps(record))


if __name__=='__main__':main()
