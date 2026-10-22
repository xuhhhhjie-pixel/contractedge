"""Zero HTTP replay of actual 0.4.2 first diagnosis and its extension request."""
import json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.benchmark import method_input_from_task,public_task
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.repair import select_observations
from contractedge.orchestration.batch import _method_digest
BENCH=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')

class Replay:
    def __init__(self,first): self.first=first;self.requests=[]
    def complete_json(self,request):
        self.requests.append(request)
        if len(self.requests)==1:return ModelResponse(self.first['response_content'],self.first.get('response_metadata',{}))
        raise RuntimeError('ZERO_HTTP_CAPTURE_SECOND_REQUEST')

rows=[]
for cid in ['R3-KPT-4673']:
    historical=ROOT/'outputs/deepseek-acceptance-recovery-v1-20261020/cases'/cid
    trace=json.loads((historical/'rounds/0/front.trace.json').read_text())
    public=json.loads((historical/'broken-public/observation.json').read_text())
    method,projection=method_input_from_task(BENCH/'public',cid)
    _,directory,_,_=public_task(BENCH/'public',cid)
    config=json.loads((ROOT/'configs/deepseek-development-pilot-budget80.json').read_text())
    config['production_path_review_enabled']=True
    config.update(model={'model':config['model'],'schema_in_prompt':True},public_execution_capabilities=projection['public_execution_capabilities'])
    obs=select_observations(public['observations'],method['requirement_text'],len,config['max_public_evidence_tokens'])
    with tempfile.TemporaryDirectory(prefix='ce-actual-gap-') as td:
        snapshot=safe_extract(directory/'source.tar.gz',Path(td)/'source')
        assert snapshot.snapshot_id==method['initial_snapshot_id']
        provider=Replay(trace['model_calls'][0])
        result=run_front_half(method,Path(td)/'source',provider,config,len,public_observations=obs).to_wire()
        candidates=result.get('candidate_set') or {}
        row={'case_id':cid,'api_calls':0,'first_response':'exact recorded first diagnosis from '+str(historical.relative_to(ROOT))+'; no new diagnosis generated',
             'captured_requests':len(provider.requests),'retrieval_rounds':result.get('retrieval_rounds'),
             'candidate_paths':[u['path'] for u in candidates.get('candidates',[])],
             'candidate_gap_refs':[{'path':u['path'],'gaps':u.get('evidence_gap_refs',[]),'locator':u['locator'],'symbol':u.get('symbol'),'truncated':u.get('truncated'),'expanded_from_unit_id':u.get('expanded_from_unit_id')} for u in candidates.get('candidates',[])],
             'actual_first_response_gaps':json.loads(trace['model_calls'][0]['response_content']).get('evidence_gaps',[]),
             'final_request_prompt_version':provider.requests[-1].prompt_version if provider.requests else None,
             'final_request_messages':list(provider.requests[-1].messages) if provider.requests else [],
             'request_metadata':dict(provider.requests[-1].metadata) if provider.requests else None,
             'capture_status':result['status'],'capture_errors':result['errors']}
        rows.append(row);print(cid,row['candidate_paths'],flush=True)
Path(__file__).with_name('current-query-replay.json').write_text(json.dumps({'method_code_digest':_method_digest(),'api_calls':0,
    'private_materials_loaded':False,'records':rows,'scope':'Actual public diagnosis replay; capture stops before next HTTP; no repair-success claim'},ensure_ascii=False,indent=2)+'\n')
