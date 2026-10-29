import argparse,sys,json,hashlib,time
from pathlib import Path
from dataclasses import asdict
p=argparse.ArgumentParser();p.add_argument('--variant',required=True);p.add_argument('--case',required=True);p.add_argument('--seed');p.add_argument('--output',required=True);p.add_argument('--method',required=True);p.add_argument('--config',required=True);p.add_argument('--benchmark',required=True);a=p.parse_args()
system=Path(__file__).resolve().parents[2]
method=Path(a.method).resolve(strict=True)
sys.path[:0]=[str(method/'src'),str(system/'scripts')]
from contractedge.cli import _token_counter
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.source.snapshot import compute_snapshot
from run_reliability_worker_v10 import recover,invoke_repair
from contractedge.orchestration import repair
from fixed_panel import source_files
before=source_files(method);out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
config=json.loads(Path(a.config).read_text());config.update(money_budget=None,runtime_preflight=None,keep_workspaces=False)
benchmark=Path(a.benchmark).resolve(strict=True)
class Captured(BaseException):pass
class NoModel:
    money_budget=None
    def request_overhead(self,request):return ''
    def request_metadata(self,request):return {'scope':'ZERO_HTTP_INTERFACE_CAPTURE'}
    def complete_json(self,request):
        out.mkdir(exist_ok=True)
        (out/'captured-request.json').write_text(json.dumps(asdict(request),ensure_ascii=False)+'\n')
        raise Captured()
class InterfacePublic:
    def __init__(self):
        self.real=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',lock_root=config['lock_root'],timeout_seconds=60,method_probe_profiles=config['method_probe_profiles'])
    def prepare(self,*args):return self.real.prepare(*args)
    def export(self,*args):return self.real.export(*args)
    def observe(self,workspace,output):
        import hashlib
        root=Path(json.loads((Path(workspace)/'workspace.json').read_text())['source_root']);s=compute_snapshot(root).snapshot_id
        text='Interface fixture only: no behavior observation or semantic success is asserted.'
        return {'status':'EXECUTION_PASS','snapshot_id':s,'observations':[{'id':'OBS-COMPAT','snapshot_id':s,'kind':'PUBLIC_OBSERVATION','evidence_kind':'EXECUTION_ONLY','scenario':'compatibility-interface-fixture','locator':{'path':'compatibility-fixture.json'},'excerpt':text,'content_digest':hashlib.sha256(text.encode()).hexdigest(),'origin':'Synthetic interface fixture, not runtime behavior'}],'fixture_only':True}
try:
    runner=InterfacePublic()
    if a.seed:result=recover(a.case,out,NoModel(),_token_counter(config['tokenizer']),config,benchmark,Path(a.seed),public_runner=runner,runtime_started=time.monotonic())
    else:result=invoke_repair(repair,benchmark,a.case,out,NoModel(),_token_counter(config['tokenizer']),config,runner,{'status':'PASS','runtime_verified':True},time.monotonic())
except Captured:
    request=json.loads((out/'captured-request.json').read_text())
    row={'status':'FIRST_REQUEST_CAPTURED_ZERO_HTTP','variant':a.variant,'case_id':a.case,'source_unchanged':source_files(method)==before,'prompt_version':request['prompt_version'],'seeded':bool(a.seed),'scope':'Real frozen front API with synthetic observation fixture; not behavior or model effectiveness','request_sha256':hashlib.sha256((out/'captured-request.json').read_bytes()).hexdigest()}
    assert row['source_unchanged']
    (out/'compatibility.json').write_text(json.dumps(row,indent=2)+'\n');print(json.dumps(row))
else:
    out.mkdir(exist_ok=True);(out/'unexpected-result.json').write_text(json.dumps(result,default=str));raise SystemExit('Front did not reach a model request; inspect preserved result')
