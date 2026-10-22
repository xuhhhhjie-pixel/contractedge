"""Capture the real serialized executor request without provider access."""
import json
import re
from pathlib import Path

from contractedge.cli import _token_counter
from contractedge.execution.topomen import build_execution_request

out = Path(__file__).resolve().parent
case = out/'runs/delivery-0.6.9-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
plan = json.loads((case/'rounds/0/plan.json').read_text())
source = next((out/'public-source-alertmanager').iterdir())
config = json.loads(Path('configs/deepseek-reliability-closure-v4-budget80.json').read_text())
projection = json.loads((case/'projection.json').read_text())
counter = _token_counter(config['tokenizer'])
request = build_execution_request(plan, source, projection['editable_policy'], counter, config,
    requirement=json.loads((case/'method_input.json').read_text())['requirement_text'])
body = json.loads(request.messages[1]['content'])
records = body['read_only_source_support']
helpers = [dict(path=r['locator']['path'],
    symbol=re.search(r'func\s+(?:\([^)]*\)\s*)?(\w+)', r['excerpt'])[1],
    bytes=len(r['excerpt'].encode()), snapshot_id=r['snapshot_id'], content_digest=r['content_digest'])
    for r in records]
receipt = dict(api_calls=0, input_bound=sum(counter(m['content']) for m in request.messages),
    output_reserved=request.max_output_tokens, limit=config['max_context_tokens'], helpers=helpers,
    total_helper_bytes=sum(r['bytes'] for r in helpers),
    scope='Readonly final serialized request against unchanged public Broken source; not model repair success')
for symbol in ['Run', 'run', 'NewDispatcher', 'Groups', 'Stop']:
    assert any(r['path']=='dispatch/dispatch.go' and r['symbol']==symbol for r in helpers)
assert len(helpers)<=8 and receipt['total_helper_bytes']<=24000
assert receipt['input_bound']+receipt['output_reserved']<=receipt['limit']
target=out/'async-helper-final-request-v0610.json'
assert not target.exists(), 'Keep prior captures immutable'
target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(receipt, ensure_ascii=False))
