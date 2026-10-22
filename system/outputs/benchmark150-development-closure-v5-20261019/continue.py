import datetime, json, os, subprocess
from pathlib import Path
root=Path('/home/xhj/contractedge-system')
base=Path(__file__).resolve().parent
bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
env=dict(os.environ,PYTHONPATH=str(root/'.runtime-deps')+':'+str(base/'implementation'))
args=['python3','-m','contractedge.cli','repair','--public-root',str(bench/'public'),'--tools-root',str(bench/'tools'),'--case-id','RW-CAND-130','--config',str(base/'config.json'),'--output',str(base/'run')]
code=subprocess.call(args,cwd=root,env=env)
result={'repair_process_exit_code':code,'finished_at':datetime.datetime.now().astimezone().isoformat(),'implementation_version':'0.2.2','scope':'Single development case, not150-case model experiment'}
trace=base/'run/repair.trace.json'
if trace.exists():
    repair=json.loads(trace.read_text());result['repair_status']=repair['status'];result['errors']=repair['errors']
    candidate=repair.get('candidate')
    if candidate and candidate.get('patch_bytes',0)>0:
        receipt={'case_id':'RW-CAND-130','candidate':candidate,'selected_before_hidden_score':True,'repair_status':repair['status']}
        (base/'selected-model-candidate.json').write_text(json.dumps(receipt,indent=2)+'\n')
        scoreout=root/'outputs/benchmark150-independent-model-score-v5-20261019'
        scorecode=subprocess.call(['python3',str(bench/'tools/score_case.py'),'--evaluation-root',str(bench/'evaluation'),'--case-id','RW-CAND-130','--patch',candidate['patch_path'],'--output',str(scoreout)],cwd=root)
        result['independent_score_exit_code']=scorecode
        if (scoreout/'score.json').exists():
            score=json.loads((scoreout/'score.json').read_text());result['independent_score_status']=score['status'];result['score_path']=str(scoreout/'score.json')
    else:
        result['independent_score_status']='NOT_RUN_EMPTY_OR_MISSING_PATCH'
(base/'completion.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
statuspath=root/'CURRENT_STATUS.json'
status=json.loads(statuspath.read_text())
if status.get('development_run',{}).get('directory')==str(base.relative_to(root)):
    status['development_run'].update(status=result.get('repair_status','PROCESS_FAILED'),completion=str((base/'completion.json').relative_to(root)),independent_score=result.get('independent_score_status'),method_performance_accepted=result.get('independent_score_status')=='PASS')
    status['as_of']=datetime.datetime.now().astimezone().isoformat()
    temporary=statuspath.with_suffix('.json.tmp');temporary.write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n');temporary.replace(statuspath)
