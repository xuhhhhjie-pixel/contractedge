"""Independent evaluator process for final public repair candidates.

This command may see the evaluator registry; its results must never enter a
repair model's prompts or same-case candidate selection/retry decisions.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

from contractedge.dataset.benchmark import file_sha
from contractedge.contracts.status import project_statuses, scoring_exclusion
from contractedge.execution.public import run_tool
from contractedge.orchestration.repair import write_json


def _frozen_trace(batch, method):
    """Read the same frozen trace that owns the selected candidate and status."""
    trace=(batch/method['trace']).resolve(strict=True)
    trace.relative_to(batch)
    trace_bytes=trace.read_bytes()
    if sha256(trace_bytes).hexdigest()!=method['trace_sha256']:
        raise ValueError('Final candidate or repair trace changed before independent scoring')
    value=json.loads(trace_bytes)
    if (not isinstance(value,dict) or value.get('case_id')!=method['case_id']
            or value.get('status')!=method['status'] or value.get('candidate')!=method.get('candidate')):
        raise ValueError('Frozen repair trace case/status/candidate identity mismatch')
    return value


def _frozen_patch(batch, candidate):
    patch=Path(candidate['patch_path']).resolve(strict=True)
    patch.relative_to(batch)
    patch_bytes=patch.read_bytes()
    if (sha256(patch_bytes).hexdigest()!=candidate['patch_sha256']
            or len(patch_bytes)!=candidate['patch_bytes']):
        raise ValueError('Final candidate or repair trace changed before independent scoring')
    return patch


def evaluate_repair_batch(evaluation_root, batch_root, tools_root, output, *, timeout_seconds=7200):
    evaluation=Path(evaluation_root).resolve(strict=True)
    batch=Path(batch_root).resolve(strict=True)
    state=json.loads((batch/'batch.json').read_text())
    registry=[json.loads(line) for line in (evaluation/'manifest.jsonl').read_text().splitlines() if line.strip()]
    ids=[r['case_id'] for r in registry]
    records={r['case_id']:r for r in state['records']}
    if any(r['status']=='RUNNING' for r in records.values()):
        raise ValueError('Cannot score an active batch before final candidate selection')
    if len(ids)!=len(set(ids)) or len(records)!=len(state['records']) or set(ids)!=set(records) or len(ids)!=state['case_count']:
        raise ValueError('Independent scoring membership differs from the public batch')
    out=Path(output).resolve()
    if out.is_relative_to(batch) or out.is_relative_to(evaluation):
        raise ValueError('Independent scores must remain outside method and evaluator inputs')
    out.mkdir(parents=True,exist_ok=False)
    report={'schema_version':'ce.independent_repair_cohort.v1','case_count':len(ids),
            'batch_sha256':file_sha(batch/'batch.json'),'evaluation_manifest_sha256':file_sha(evaluation/'manifest.jsonl'),
            'candidate_selection':'Frozen batch candidates before private scoring; no score feedback to method', 'records':[]}
    def save():
        report['status_counts']=dict(Counter(r['status'] for r in report['records']))
        report['pass_count']=report['status_counts'].get('PASS',0)
        report['pass_rate_fixed_denominator']=report['pass_count']/len(ids) if ids else 0
        report['pending_count']=len(ids)-len(report['records'])
        write_json(out/'cohort.json',report)
    save()
    for case_id in ids:
        method=records[case_id]
        candidate=method.get('candidate')
        if 'trace' in method:
            trace=_frozen_trace(batch,method)
        elif candidate:
            raise ValueError('Candidate has no frozen repair trace identity')
        else:
            trace=method
        projection=project_statuses(trace)
        result={'case_id':case_id,**projection}
        patch=_frozen_patch(batch,candidate) if candidate else None
        if method['status']=='NOT_RUN':
            result['status']='NOT_RUN'
        elif not candidate or not candidate.get('patch_bytes'):
            result['status']='NO_CANDIDATE'
        else:
            result['patch_sha256']=candidate['patch_sha256']
            exclusion=scoring_exclusion(trace,projection,independent_repair=True)
            if exclusion:
                result['failure_kind'],result['status']=exclusion
            else:
                case_out=out/'cases'/case_id
                command=[sys.executable,str(Path(tools_root).resolve(strict=True)/'score_case.py'),
                    '--evaluation-root',str(evaluation),'--case-id',case_id,'--patch',str(patch),'--output',str(case_out/'native')]
                execution=run_tool(command,case_out/'launcher',timeout_seconds)
                score=case_out/'native/score.json'
                result['execution']=execution
                if score.is_file():
                    value=json.loads(score.read_text())
                    if value.get('case_id')!=case_id or value.get('patch_sha256')!=candidate['patch_sha256']:
                        raise ValueError('Independent score case/patch identity mismatch')
                    result.update(status=value.get('status','INVALID_SCORE'),score=str(score.relative_to(out)),score_sha256=file_sha(score))
                    if execution['timed_out'] or (result['status']=='PASS' and execution['returncode']!=0):
                        result['native_score_status']=result['status']
                        result['status']='SCORER_TIMEOUT' if execution['timed_out'] else 'SCORER_ERROR'
                else:
                    result['status']='SCORER_TIMEOUT' if execution['timed_out'] else 'SCORER_ERROR'
        result['independent_score_status']=result['status']
        report['records'].append(result);save()
    return report
