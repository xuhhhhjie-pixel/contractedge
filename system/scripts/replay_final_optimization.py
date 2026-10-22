"""Zero-model replay of preserved PUBLIC outputs through current adapters/gates.

Usage: python scripts/replay_final_optimization.py NEW_OUTPUT
Synthetic source snapshots exercise observation plumbing only; not a new repair
experiment and not evidence of current live candidate behavior.
"""
import hashlib,json,shutil,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.support import support_error


def main():
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
    benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    inputs=[('R3-KOPF-726','deepseek-development-pilot-v7-20261020','PUBLIC_STRUCTURE'),
            ('R4-PROVIDER-HELM-408','deepseek-optimized-new-development-v1-20261020','PUBLIC_BEHAVIOR')]
    rows=[]
    for case,run,kind in inputs:
        raw=ROOT/'outputs'/run/'cases'/case/'rounds/0/public/raw'
        with tempfile.TemporaryDirectory(prefix='ce-public-replay-') as td:
            p=Path(td);w=p/'workspace';source=w/'source';source.mkdir(parents=True);(source/'synthetic.txt').write_text('Adapter replay only\n')
            declared=w/'inputs';declared.mkdir();(w/'workspace.json').write_text(json.dumps({'case_id':case,'source_root':str(source),'inputs_root':str(declared)}))
            runner=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',lock_root=p/'locks',query='namespace owner reference plainHTTP local transport')
            def replay(argv,logs,timeout):
                logs.mkdir(parents=True);(logs/'tool.stdout').write_text('');(logs/'tool.stderr').write_text('')
                shutil.copytree(raw,Path(argv[argv.index('--output')+1]));return {'returncode':0,'timed_out':False}
            with patch('contractedge.execution.public.run_tool',replay),patch('contractedge.execution.public.cleanup_owned_containers',return_value={'status':'OWNED_CONTAINERS_CLOSED','removed':[]}):
                report=runner.observe(w,out/case)
            typed=[r for r in report['observations'] if r.get('adapter_id')]
            assert typed and all(r['evidence_kind']==kind and r['structured_complete'] for r in typed)
            assert all(json.loads(r['excerpt']) is not None for r in typed)
            assert all(support_error({'verification_kind':kind,'verification_scope':{'scenario':r['scenario'],'resource':r['resource_identity']}},
                {'status':'PASS','evidence':[{'observation_id':r['id'],'quote':r['excerpt']}]},[r]) is None for r in typed)
            rows.append({'case_id':case,'adapter_audit':report['observation_adapter_audit'],
                'typed_complete_objects':len(typed),'gate_capability_accepted':True,'requirement_success':'NOT_EVALUATED',
                'source_snapshot':'SYNTHETIC_ADAPTER_REPLAY','model_calls':0,'source_public_run':run})
    (out/'summary.json').write_text(json.dumps({'scope':'PUBLIC_OUTPUT_ADAPTER_REPLAY_ONLY','records':rows,'api_calls':0},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(rows,ensure_ascii=False))


if __name__=='__main__':main()
