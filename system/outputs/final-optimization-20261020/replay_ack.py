import sys,json,tempfile
from pathlib import Path
ROOT=Path('/home/xhj/contractedge-system');sys.path.insert(0,str(ROOT/'src'))
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.benchmark import method_input_from_task,public_task
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
cid='R4-ACK-CLOUDWATCH-2985';method,projection=method_input_from_task(bench/'public',cid);_,directory,task,manifest=public_task(bench/'public',cid)
config=json.load(open(ROOT/'configs/deepseek-development-final-v1-budget80.json'));config.update(model={'model':config['model'],'schema_in_prompt':True},public_execution_capabilities=projection['public_execution_capabilities'])
class Capture:
    request=None
    def complete_json(self,request):self.request=request;raise RuntimeError('INTENTIONAL_PRE_HTTP_CAPTURE')
with tempfile.TemporaryDirectory(prefix='ce-final-ack-source-') as td:
    workspace=Path(td)/'source';snapshot=safe_extract(directory/'source.tar.gz',workspace)
    assert snapshot.snapshot_id==method['initial_snapshot_id']
    port=Capture();trace=run_front_half(method,workspace,port,config,token_counter=lambda s:len(s.encode()))
    if port.request is None:raise ValueError(str(trace.errors))
    payload=json.loads(port.request.messages[1]['content']);records=[{'id':r['id'],'path':r['locator']['path'],'start':r['locator'].get('start_line')} for r in payload['evidence_catalog'] if r['kind']=='SOURCE_SPAN']
    result={'scope':'CURRENT_FIRST_REQUEST_CAPTURE_NO_HTTP','case_id':cid,'api_calls':0,'source_snapshot':snapshot.snapshot_id,
       'source_records':records,'scope_hints':method['scope_hints'],'configuration_consumer_audit':trace.retrieval_rounds[0].get('probe_entrypoint_resolution') if trace.retrieval_rounds else None,
       'interpretation':'Retrieval evidence only; a source hint is neither causal proof nor repair success'}
    out=ROOT/'outputs/final-optimization-20261020/ack-retrieval-current.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
