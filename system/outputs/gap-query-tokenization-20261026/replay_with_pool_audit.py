import json,runpy,sys
from pathlib import Path
from contractedge.orchestration import runner
OUT=Path(__file__).parent
original=runner.extract_gap_extension_units

def capture(*args,**kwargs):
 units,audit=original(*args,**kwargs)
 (OUT/'CERT_EXTENSION_POOL.json').write_text(json.dumps(dict(audit=audit,units=[{k:u[k] for k in ['id','path','symbol','locator','scope_extension','truncated','evidence_gap_refs'] if k in u} for u in units]),indent=2)+'\n')
 return units,audit
runner.extract_gap_extension_units=capture
sys.argv=['replay_saved_front_request.py','--public-case','/home/xhj/contractedge-diagnostic-source048-v2-20261026/methods/RW-CERTMANAGER-9116/RW-CERTMANAGER-9116','--workspace','/home/xhj/contractedge-diagnostic-source048-v2-20261026/methods/RW-CERTMANAGER-9116/RW-CERTMANAGER-9116/workspace/source','--saved-responses','1','--output',str(OUT/'cert-pool-capture')]
runpy.run_path('scripts/replay_saved_front_request.py',run_name='__main__')
