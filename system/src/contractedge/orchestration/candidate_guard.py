"""Public-check candidate promotion and bounded per-case source checkpoints."""
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from contractedge.execution.topomen import safe_target
from contractedge.source.snapshot import compute_snapshot


def bound_public_check_table(report: dict) -> dict:
    """Current-snapshot checks keyed by their comparable execution identities."""
    values={}
    for r in report.get('observations',[]):
        check=r.get('behavior_check',{})
        if check and r.get('snapshot_id')==report.get('snapshot_id'):
            key=(check['id'],r.get('scenario'),check.get('probe_sha256'),
                 report.get('input_digest'),report.get('environment_digest'))
            if key in values and values[key]!=check['status']:
                values[key]='UNKNOWN'
            else:values[key]=check['status']
    return values


def compare_candidate_checks(incumbent: dict, trial: dict, preservation_ids: list[str]) -> dict:
    def table(report):
        values=bound_public_check_table(report)
        feedback=report.get('feedback') or {}
        for retained in feedback.get('retained_constraints',[]):
            if retained.get('latest_review_snapshot')!=report.get('snapshot_id'):continue
            key=('obligation:'+retained['retention_id'],json.dumps(retained.get('verification_scope'),sort_keys=True),
                 'EVIDENCE_BOUND_REVIEW',feedback.get('input_digest'),report.get('environment_digest'))
            values[key]=retained.get('latest_public_status','UNKNOWN')
        return values
    old,new=table(incumbent),table(trial); regressions=[];unknown=[]
    for key,state in old.items():
        if key[0] not in preservation_ids or state!='PASS':continue
        if not key[3] or not key[4]:
            unknown.append(dict(check_id=key[0],reason='Missing explicit input/environment identity'));continue
        if trial.get('status') not in {'EXECUTION_PASS','EXECUTION_FAILED'} or key not in new:
            unknown.append(dict(check_id=key[0],reason='Missing comparable complete trial evidence'));continue
        if new[key]=='FAIL':regressions.append(dict(check_id=key[0],scenario=key[1],probe_sha256=key[2],before='PASS',after='FAIL'))
        elif new[key]!='PASS':unknown.append(dict(check_id=key[0],reason='Inconclusive trial result'))
    return dict(schema_version='ce.candidate_decision.v1',
        decision='REJECT_REGRESSION' if regressions else 'UNCOMPARABLE' if unknown else 'PROMOTE',
        from_snapshot=incumbent.get('snapshot_id'),trial_snapshot=trial.get('snapshot_id'),
        regressions=regressions,uncomparable=unknown,
        interpretation=('Observed protected behavior regressed; reject this trial and retain the incumbent.' if regressions else
            'Protected behavior could not be compared sufficiently; do not promote this trial.' if unknown else
            'No known comparable preservation regression; PROMOTE is not semantic correctness'))


def checkpoint_candidate(source, paths, destination):
    root=Path(source).resolve(strict=True);files={};size=0
    for relative in sorted(set(paths)):
        path=safe_target(root,relative)
        if path.exists():
            if not path.is_file():raise ValueError('Checkpoint target must be regular')
            data=path.read_bytes();size+=len(data)
            if size>8*1024*1024:raise ValueError('Checkpoint exceeds concrete edit byte budget')
            files[relative]=dict(data=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest(),mode=path.stat().st_mode&0o777)
        else:files[relative]=None
    result=dict(schema_version='ce.candidate_checkpoint.v1',source_root=str(root),
                snapshot_id=compute_snapshot(root).snapshot_id,files=files)
    dest=Path(destination);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,indent=2)+'\n');return result


def restore_candidate(source, checkpoint):
    root=Path(source).resolve(strict=True)
    if checkpoint.get('schema_version')!='ce.candidate_checkpoint.v1' or str(root)!=checkpoint['source_root']:
        raise ValueError('Checkpoint source identity mismatch')
    decoded={}
    for relative,record in checkpoint['files'].items():
        path=safe_target(root,relative)
        if record is None:decoded[path]=None;continue
        try:data=base64.b64decode(record['data'],validate=True)
        except ValueError as exc:raise ValueError('Checkpoint data invalid') from exc
        if hashlib.sha256(data).hexdigest()!=record['sha256']:raise ValueError('Checkpoint digest mismatch')
        decoded[path]=(data,record['mode'])
    for path,record in decoded.items():
        if record is None:
            if path.exists():path.unlink()
        else:
            fd,name=tempfile.mkstemp(prefix='.ce-restore-',dir=path.parent)
            try:
                with os.fdopen(fd,'wb') as stream:stream.write(record[0]);stream.flush();os.fsync(stream.fileno())
                os.chmod(name,record[1]);os.replace(name,path)
            finally:
                if os.path.exists(name):os.unlink(name)
    snapshot=compute_snapshot(root).snapshot_id
    if snapshot!=checkpoint['snapshot_id']:raise ValueError('Restored snapshot mismatch; uncontrolled modifications or corruption')
    return dict(snapshot_id=snapshot,status='RESTORED')


def preservation_check_ids(plan, previous_feedback=None):
    from contractedge.requirement.lineage import disputed_obligation
    ledger=plan.get('obligation_lineage',{})
    retained=[p for p in (previous_feedback or {}).get('retained_constraints',[]) if not disputed_obligation(p,ledger)]
    obligations=[p for p in [*plan.get('preserve_constraints',[]),*retained] if not disputed_obligation(p,ledger)]
    return sorted({ref for item in obligations for ref in item.get('behavior_check_refs',[])} |
        {'obligation:'+item['retention_id'] for item in retained})
