"""Existing READY/repair API with a bounded semantic ledger projection."""
import argparse,json,os,sys
from pathlib import Path

def main():
 p=argparse.ArgumentParser()
 for k in ['method-root','benchmark-root','config','output','case','preflight','control']:p.add_argument('--'+k,required=True)
 a=p.parse_args();sys.path.insert(0,str(Path(a.method_root)/'src'))
 from isolated_protocol import RpcClient
 from run_isolated_method import ProxyProvider,ProxyPublicRunner
 from run_reliability_worker_v10 import source_files,write
 from contractedge.cli import _token_counter
 from contractedge.orchestration.repair import _repair_case_impl
 read=lambda x:json.loads(Path(x).read_text())
 cfg=read(a.config);ready=read('/run/ce-semantic-ready.json');mapping=read('/run/ce-projection.json');pre=read(a.preflight);control=read(a.control)
 assert len(ready['model_calls'])==3 and all(isinstance(c.get('response_content'),str) and not c.get('error') for c in ready['model_calls'])
 assert cfg['max_model_calls']==mapping['inner_max_calls'] and cfg['max_total_tokens']==mapping['inner_max_tokens']
 assert cfg.get('money_budget') is None and cfg.get('runtime_preflight') is None
 token=os.environ['CONTRACTEDGE_RPC_TOKEN'];provider=ProxyProvider(RpcClient('/run/ce-method.sock',token,timeout=cfg['timeout_seconds']+30),timeout_seconds=cfg['timeout_seconds']);runner=ProxyPublicRunner(RpcClient('/run/ce-method.sock',token,timeout=cfg['public_timeout_seconds']+60),timeout_seconds=cfg['public_timeout_seconds'])
 before=source_files(Path(a.method_root))
 try:
  b=Path(a.benchmark_root)
  result=_repair_case_impl(b/'public',b/'tools',a.case,Path(a.output),provider,_token_counter(cfg['tokenizer']),cfg,initial_trace=ready,public_runner=runner,_runtime_preflight=pre,_runtime_started=control['started_monotonic']-mapping['extra_inherited_active_seconds'])
 finally:
  after=source_files(Path(a.method_root));out=Path(a.output)
  if out.exists():write(out/'isolated-method-integrity.json',dict(before=before,after=after,unchanged=before==after,preflight_delegated=True,real_docker_socket_mounted=False,api_key_mounted=False,scope='Exact received response and runtime-fact recovery replay; no new model HTTP'))
  assert before==after

if __name__=='__main__':main()
