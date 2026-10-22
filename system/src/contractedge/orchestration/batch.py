"""Resumable public-only repair cohort. Never reads scores or evaluator data."""
from collections import Counter
import fcntl
import json
import platform
import shutil
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

from contractedge.contracts.validation import canonical_digest
from contractedge.dataset.benchmark import file_sha, read_members
from contractedge.dataset.admission import development_case_ids
from contractedge.orchestration.repair import write_json


def _method_digest():
    root = Path(__file__).resolve().parents[1]
    return canonical_digest({p.relative_to(root).as_posix():file_sha(p)
        for p in sorted(root.rglob('*')) if p.suffix in {'.py','.json'} and p.is_file()})


def run_batch(public_root, output, config, execute_case, *, case_ids=None, max_new_cases=None, tools_root=None):
    public = Path(public_root).resolve(strict=True)
    out = Path(output).resolve()
    if out.is_relative_to(public) or public.is_relative_to(out):
        raise ValueError('Batch output must be outside frozen public data')
    members = read_members(public)
    all_ids = [r['case_id'] for r in members]
    manifest_digest = file_sha(public/'manifest.jsonl')
    if (config.get('expected_case_count', len(all_ids)) != len(all_ids)
            or config.get('expected_public_manifest_sha256', manifest_digest) != manifest_digest):
        raise ValueError('Configured frozen benchmark membership does not match public data')
    development = development_case_ids(public, config)
    selected = ([cid for cid in all_ids if development is None or cid in development]
                if case_ids is None else list(case_ids))
    if len(selected) != len(set(selected)) or not set(selected) <= set(all_ids):
        raise ValueError('Duplicate or unknown batch case IDs')
    if development is not None and not set(selected) <= development:
        raise ValueError('development_only rejects non-development batch selections before model execution')
    if max_new_cases is not None and max_new_cases < 0:
        raise ValueError('max_new_cases must be nonnegative')
    runtime = {'python':platform.python_version()}
    for package in ['PyYAML','jsonschema','pydantic','rich','Jinja2','python-dotenv','platformdirs']:
        try:
            runtime[package]=version(package)
        except PackageNotFoundError:
            runtime[package]='NOT_INSTALLED'
    tools_identity=None
    if tools_root is not None:
        tools=Path(tools_root).resolve(strict=True)
        tools_identity={'root':str(tools),'sha256':canonical_digest({p.relative_to(tools).as_posix():file_sha(p)
            for p in sorted(tools.rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}})}
    native_identity=None
    if config.get('execution_backend')=='topomen_agent':
        from contractedge.execution.topomen_agent import source_identity
        native_identity=source_identity(config['topomen_source_root'])
        if native_identity['source_sha256'] != config['topomen_source_sha256']:
            raise ValueError('Native TopoMen source identity changed')
    identity = dict(public_manifest_sha256=manifest_digest,
                    runtime=runtime,public_tools=tools_identity,native_topomen=native_identity,
                    config_digest=canonical_digest(config), method_code_digest=_method_digest())
    out.mkdir(parents=True, exist_ok=True)
    with (out/'batch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('This batch already has an active owner') from exc
        state_path=out/'batch.json'
        if state_path.exists():
            state=json.loads(state_path.read_text())
            if state['identity'] != identity:
                raise ValueError('Batch identity changed: use a new output; prior paid attempts remain preserved')
            for record in state['records']:
                if record.get('trace'):
                    trace = out/record['trace']
                    if not trace.is_file() or file_sha(trace) != record['trace_sha256']:
                        raise ValueError('Stored batch trace has changed or is missing')
        else:
            state={'schema_version':'ce.public_repair_batch.v1','identity':identity,
                'case_count':len(all_ids),'records':[dict(case_id=r['case_id'],primary_class=r['primary_class'],status='NOT_RUN') for r in members]}
        def save():
            state['status_counts']=dict(Counter(r['status'] for r in state['records']))
            state['attempted']=sum(r['status']!='NOT_RUN' for r in state['records'])
            state['unattempted']=state['case_count']-state['attempted']
            write_json(state_path,state)
        def retire_workspace(record, directory):
            candidate=record.get('candidate')
            workspace=directory/'workspace'
            if config.get('keep_workspaces', True) or not candidate or not workspace.exists():
                return
            patch=Path(candidate['patch_path']).resolve(strict=True)
            patch.relative_to(directory.resolve())
            if file_sha(patch)!=candidate['patch_sha256'] or workspace.is_symlink():
                raise ValueError('Cannot retire workspace without its exact retained candidate patch')
            write_json(directory/'workspace-retention.json',dict(
                case_id=record['case_id'],public_root=str(public),public_manifest_sha256=manifest_digest,
                candidate_patch_sha256=candidate['patch_sha256'],candidate_patch=str(patch),
                retained='All model traces, observations, feedback and final patch',
                rebuild='Run frozen prepare_case.py --git for this case, then git apply the retained patch; inputs come from the pinned public task'))
            shutil.rmtree(workspace)
        save()
        new=0
        for record in state['records']:
            if record['case_id'] not in selected or record['status'] not in {'NOT_RUN','RUNNING'}:
                continue
            directory=out/'cases'/record['case_id']
            trace=directory/'repair.trace.json'
            if record['status']=='RUNNING' or directory.exists():
                if trace.is_file():
                    result=json.loads(trace.read_text())
                    if result['case_id'] != record['case_id']:
                        raise ValueError('Recovered case identity mismatch')
                    record.update(status=result['status'],trace=str(trace.relative_to(out)),trace_sha256=file_sha(trace))
                    if result.get('candidate'):
                        record['candidate']=dict(result['candidate'])
                else:
                    record['status']='INTERRUPTED'
                if record['status']!='INTERRUPTED':
                    retire_workspace(record,directory)
                save()
                continue
            if max_new_cases is not None and new>=max_new_cases:
                break
            if shutil.disk_usage(out).free < int(config.get('min_free_work_disk_bytes', 0)):
                state['stop_reason']='INSUFFICIENT_WORK_DISK'
                save()
                break
            record['status']='RUNNING';save();new+=1
            try:
                result=execute_case(record['case_id'],directory)
                if result['case_id'] != record['case_id'] or not trace.is_file():
                    raise ValueError('Case runner did not persist a matching repair trace')
                record.update(status=result['status'],trace=str(trace.relative_to(out)),trace_sha256=file_sha(trace))
                candidate=result.get('candidate')
                if candidate:
                    record['candidate']=dict(candidate)
            except Exception as exc:
                # Never replay a possibly billed interrupted case automatically.
                record.update(status='INTERRUPTED',error=type(exc).__name__+': '+str(exc))
            save()
            if record['status']!='INTERRUPTED':
                retire_workspace(record,directory)
            budget=config.get('money_budget')
            if budget:
                from contractedge.model.money import MoneyBudget
                summary=MoneyBudget(**budget).summary()
                state['money_budget']=summary;save()
                # Leave enough conservative reservation for a minimal request.
                if summary['reservation_rejected'] or summary['limit_micro_cny']-summary['committed_micro_cny']<1000:
                    state['stop_reason']='EXPERIMENT_MONEY_CAP'
                    save()
                    break
        return state
