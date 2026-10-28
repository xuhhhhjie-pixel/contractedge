"""Existing READY import, with the exact four-call ACK prefix carried forward."""
import argparse,json,os,sys
from pathlib import Path

def main():
    ap=argparse.ArgumentParser()
    for k in ['method-root','benchmark-root','config','output','case','preflight','control']:ap.add_argument('--'+k,required=True)
    a=ap.parse_args();sys.path.insert(0,str(Path(a.method_root)/'src'))
    from isolated_protocol import RpcClient
    from run_isolated_method import ProxyProvider,ProxyPublicRunner
    from run_reliability_worker_v10 import source_files,write
    from contractedge.cli import _token_counter
    from contractedge.orchestration.repair import _repair_case_impl
    read=lambda p:json.loads(Path(p).read_text())
    cfg=read(a.config);ready=read('/run/ce-ack-ready.json');pre=read(a.preflight);ctrl=read(a.control)
    assert cfg.get('money_budget') is None and cfg.get('runtime_preflight') is None
    assert pre['status']=='PASS' and pre['runtime_verified'] is True
    assert len(ready['model_calls'])==4 and ready['model_calls'][2].get('error')=='HTTPError: HTTP Error 402: Payment Required'
    assert ready['status']=='READY' and ready['case_id']==a.case
    token=os.environ['CONTRACTEDGE_RPC_TOKEN']
    provider=ProxyProvider(RpcClient('/run/ce-method.sock',token,timeout=cfg['timeout_seconds']+30),timeout_seconds=cfg['timeout_seconds'])
    runner=ProxyPublicRunner(RpcClient('/run/ce-method.sock',token,timeout=cfg['public_timeout_seconds']+60),timeout_seconds=cfg['public_timeout_seconds'])
    before=source_files(Path(a.method_root))
    try:
        b=Path(a.benchmark_root)
        result=_repair_case_impl(b/'public',b/'tools',a.case,Path(a.output),provider,_token_counter(cfg['tokenizer']),cfg,
            initial_trace=ready,public_runner=runner,_runtime_preflight=pre,_runtime_started=ctrl['started_monotonic'])
    finally:
        after=source_files(Path(a.method_root));out=Path(a.output)
        if out.exists():write(out/'isolated-method-integrity.json',dict(before=before,after=after,unchanged=before==after,preflight_delegated=True,real_docker_socket_mounted=False,api_key_mounted=False,protocol='ce.controlled_ack402_ready_continuation.v1'))
        assert before==after

if __name__=='__main__':main()
