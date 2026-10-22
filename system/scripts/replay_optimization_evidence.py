"""Replay public retrieval/request fitting against baseline and current source.

No provider, credential, public runtime or evaluator is contacted. The capture
port terminates at the first serialized model request; it is not an experiment.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.benchmark import method_input_from_task, public_task
from contractedge.orchestration.repair import select_observations

BENCH = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
OUT = ROOT / 'outputs/system-optimization-20261020'
CASES = ['RW-CAND-067', 'R3-KOPF-726', 'R3-KPT-4673', 'RW-ALERTMANAGER-4705', 'R4-ACK-CLOUDWATCH-2985']
WORKER = r'''
import json,sys,inspect
from dataclasses import asdict
from pathlib import Path
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.batch import _method_digest
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units,extract_gap_extension_units
from contractedge.retrieval.candidates import extend_once
class Capture:
    request=None
    def complete_json(self,request):
        self.request=request
        raise RuntimeError('ZERO_API_CAPTURE_COMPLETE')
d=json.load(sys.stdin); capture=Capture()
t=run_front_half(d['method'],Path(d['workspace']),capture,d['config'],len,
    public_observations=d['observations']).to_wire()
assert capture.request is not None,(t['status'],t['errors'])
scope=t['scope_resolution'];snapshot=compute_snapshot(d['source'])
kw={'query':d['method']['requirement_text']} if 'query' in inspect.signature(extract_units).parameters else {}
units=extract_units(snapshot,{'seed_paths':scope['seed_paths']},max_units=80,max_lines=80,max_extension_files=24,**kw)
gap_units,audit=extract_gap_extension_units(snapshot,scope['seed_paths'],units,d['gaps'])
from contractedge.retrieval.candidates import CandidateSet
initial=CandidateSet(**t['candidate_set'])
byid={u['id']:u for u in units}
ext=None
if d['gaps']:
    ext=extend_once(initial,d['gaps'],[byid[i] for i in initial.dropped_ids if i in byid]+gap_units,
        max_context_tokens=d['config']['max_context_tokens'],token_counter=len)
summary=lambda c:[{k:u.get(k) for k in ['id','path','locator','scope_extension']} for u in c.candidates]
print(json.dumps({'method_code_digest':_method_digest(),'initial':summary(initial),'after_recorded_gap':summary(ext) if ext else [],
    'gap_audit':audit,'request_metadata':dict(capture.request.metadata),
    'source_pool_paths':list(dict.fromkeys(u['path'] for u in units)),
    'request_tokens':sum(len(m['content']) for m in capture.request.messages),
    'context_audit':getattr(initial,'context_audit',()),'api_calls':0}))
'''


def main():
    profile = json.loads((ROOT / 'configs/deepseek-development-pilot-budget80.json').read_text())
    records = []
    for cid in CASES:
        method, projection = method_input_from_task(BENCH / 'public', cid)
        _, directory, _, _ = public_task(BENCH / 'public', cid)
        old = ROOT / 'outputs/deepseek-development-pilot-v7-20261020/cases' / cid
        trace = json.loads((old / 'rounds/0/front.trace.json').read_text())
        observation = json.loads((old / 'broken-public/observation.json').read_text())
        observations = select_observations(observation['observations'], method['requirement_text'], len,
                                           profile['max_public_evidence_tokens'])
        config = {**profile, 'public_execution_capabilities': projection['public_execution_capabilities'],
                  'model': {'model': profile['model'], 'schema_in_prompt': True}}
        with tempfile.TemporaryDirectory(prefix='ce-zero-api-replay-') as temporary:
            workspace = Path(temporary) / 'source'
            snapshot = safe_extract(directory / 'source.tar.gz', workspace)
            assert snapshot.snapshot_id == method['initial_snapshot_id']
            args = {'method': method, 'workspace': str(workspace), 'source': snapshot.source_root,
                    'config': config, 'observations': list(observations),
                    'gaps': (trace.get('diagnosis') or {}).get('evidence_gaps', [])}
            record = {'case_id': cid, 'source_snapshot': snapshot.snapshot_id, 'scope': 'Public retrieval/request replay only'}
            for label, source in [('baseline', OUT / 'baseline/src'), ('optimized', ROOT / 'src')]:
                env = {**os.environ, 'PYTHONPATH': str(source)}
                result = subprocess.run([sys.executable, '-c', WORKER], input=json.dumps(args),
                                        env=env, capture_output=True, text=True, check=True)
                record[label] = json.loads(result.stdout)
            records.append(record)
            (OUT / 'replay').mkdir(exist_ok=True)
            (OUT / 'replay' / (cid + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({'case': cid,
                **{k: {'initial_paths': list(dict.fromkeys(c['path'] for c in record[k]['initial'])),
                       'gap_paths': list(dict.fromkeys(c['path'] for c in record[k]['after_recorded_gap']))}
                   for k in ['baseline', 'optimized']}}, ensure_ascii=False), flush=True)
    (OUT / 'replay-summary.json').write_text(json.dumps({'status': 'COMPLETED', 'cases': len(records),
        'api_calls': 0, 'private_materials_loaded': False,
        'interpretation': 'Candidate diversity and public evidence access, not repair success',
        'records': records}, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
