"""Compare frozen methods with identical public source, plan and budgets."""
import json
import os
import subprocess
import sys
from pathlib import Path

out=Path(__file__).resolve().parent
program=r'''
import json,re
from pathlib import Path
from contractedge.execution.topomen import build_execution_request
from contractedge.cli import _token_counter
root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
case=out/'runs/delivery-0.6.9-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
plan=json.loads((case/'rounds/0/plan.json').read_text())
cfg=json.loads((root/'configs/deepseek-reliability-closure-v4-budget80.json').read_text())
projection=json.loads((case/'projection.json').read_text());counter=_token_counter(cfg['tokenizer'])
request=build_execution_request(plan,next((out/'public-source-alertmanager').iterdir()),projection['editable_policy'],counter,cfg,
requirement=json.loads((case/'method_input.json').read_text())['requirement_text'])
body=json.loads(request.messages[1]['content'])
print(json.dumps(dict(api_calls=0,input_bound=sum(counter(m['content']) for m in request.messages),output_reserved=request.max_output_tokens,
helpers=[dict(path=r['locator']['path'],symbol=re.search(r'func\s+(?:\([^)]*\)\s*)?(\w+)',r['excerpt'])[1],
content_digest=r['content_digest'],snapshot_id=r['snapshot_id']) for r in body['read_only_source_support']])))
'''
results={}
for version in ['0.6.9','0.6.10']:
    freeze=json.loads((out/f'frozen-method-v{version}.json').read_text())
    env={**os.environ,'PYTHONPATH':freeze['candidate_root']+'/src:'+str(out.parents[1]/'.topomen-deps')}
    results[version]=json.loads(subprocess.check_output([sys.executable,'-c',program],env=env,text=True))
def keys(value):
    return {(r['path'],r['symbol']) for r in value['helpers']}
assert ('dispatch/dispatch.go','Run') not in keys(results['0.6.9'])
assert ('dispatch/dispatch.go','Run') in keys(results['0.6.10'])
assert ('dispatch/dispatch.go','run') in keys(results['0.6.10'])
target=out/'async-helper-frozen-comparison-v069-v0610.json'
assert not target.exists()
target.write_text(json.dumps(dict(scope='Same immutable public source/plan/config, separate frozen method processes; no model calls or repair outcome claim',results=results),ensure_ascii=False,indent=2)+'\n')
print({version:dict(input_bound=d['input_bound'],helpers=len(d['helpers'])) for version,d in results.items()})
