"""Frozen public-only batch launch; credential loaded from a private external file."""
from pathlib import Path
import sys,json,os,hashlib,time
FREEZE=Path(__file__).resolve().parent
SYSTEM=FREEZE.parents[1]
sys.path[:0]=[str(FREEZE/'src'),str(SYSTEM/'.topomen-deps')]
from contractedge.cli import _build_provider
from contractedge.orchestration.batch import run_batch
from contractedge.orchestration.repair import repair_case
for relative,digest in json.loads((FREEZE/'source-manifest.json').read_text()).items():
    assert hashlib.sha256((FREEZE/relative).read_bytes()).hexdigest()==digest,relative
config=json.loads((FREEZE/'config.json').read_text())
if (SYSTEM/'outputs/deepseek-formal150-batch-v1-20261020/STOPPED_BY_USER.json').exists():
    raise SystemExit('Batch was stopped: user requires single-case acceptance first. No paid request sent.')
os.environ['CONTRACTEDGE_API_KEY']=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
provider,counter=_build_provider(config)
benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
output=SYSTEM/'outputs/deepseek-formal150-batch-v1-20261020'
output.mkdir(exist_ok=True)
def event(value):
    encoded=json.dumps(value,ensure_ascii=False)
    with (output/'events.jsonl').open('a') as stream:
        stream.write(encoded+'\n');stream.flush();os.fsync(stream.fileno())
    print(encoded,flush=True)
def execute(case_id,directory):
    event(dict(event='CASE_START',case_id=case_id))
    started=time.monotonic()
    report=repair_case(benchmark/'public',benchmark/'tools',case_id,directory,provider,counter,config)
    event(dict(event='CASE_DONE',case_id=case_id,status=report['status'],errors=report['errors'],
        patch_bytes=(report.get('candidate') or {}).get('patch_bytes'),
        elapsed_seconds=round(time.monotonic()-started,2),money=provider.money_budget.summary()))
    return report
result=run_batch(benchmark/'public',output,config,execute,tools_root=benchmark/'tools')
event(dict(event='BATCH_RETURN',case_count=result['case_count'],attempted=result['attempted'],
    status_counts=result['status_counts'],stop_reason=result.get('stop_reason'),money=provider.money_budget.summary()))
