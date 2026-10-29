"""Run a published native execution contract using public assets only.

No adapter, repair, expected output or semantic oracle is loaded. Execution
success means the native producer completed and returned raw observations.
"""
from dataclasses import asdict
from pathlib import Path, PurePosixPath
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

SUPPORTED={'RW-ISTIO-2944','RW-ALLOY-6818','R3-GARDEN-6713',
           'R3-PULUMI-KUBERNETES-4426','R4-FLINK-OPERATOR-1168'}

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def put(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def inside(root,name,exists=True):
    rel=Path(name)
    if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe relative public path')
    target=(root/rel).resolve(strict=exists)
    if not target.is_relative_to(root.resolve()):raise ValueError('Public path escapes root')
    return target

def union_node_seed(archive,source):
    """Only node_modules entries; allow links into the confined source union."""
    with tarfile.open(archive,'r:*') as tf:
        for m in tf.getmembers():
            path=PurePosixPath(m.name)
            if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe dependency union member')
            parts=[x for x in path.parts if x not in ('','.')] 
            if not parts:continue
            if 'node_modules' not in parts and not m.isdir():raise ValueError('Nondependency union payload')
            dst=inside(source,str(path),exists=False)
            if m.isdir():
                if dst.is_symlink():raise ValueError('Dependency directory is symlink')
                dst.mkdir(parents=True,exist_ok=True)
                continue
            dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists() or dst.is_symlink():raise ValueError('Dependency overwrites candidate source')
            if m.issym():
                if Path(m.linkname).is_absolute() or not (dst.parent/m.linkname).resolve().is_relative_to(source.resolve()):
                    raise ValueError('Dependency symlink escapes source union')
                dst.symlink_to(m.linkname)
            elif m.isfile():
                with tf.extractfile(m) as data,dst.open('xb') as output:shutil.copyfileobj(data,output)
                dst.chmod(0o755 if m.mode&0o111 else 0o644)
            else:raise ValueError('Unsupported dependency union entry')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--public-root',type=Path,required=True)
    p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--tools-root',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--lock-root',type=Path)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    result={'status':'EXECUTION_FAILED','private_materials_loaded':False,
            'hidden_semantic_evaluation':'NOT_RUN','runtime_executed':False}
    try:
        public=a.public_root.resolve(strict=True);workspace=a.workspace.resolve(strict=True)
        record=json.loads((workspace/'workspace.json').read_text());cid=record['case_id']
        if cid not in SUPPORTED:raise ValueError('No supported native execution contract for this case')
        rows=[json.loads(x) for x in (public/'manifest.jsonl').read_text().splitlines() if x.strip()]
        selected=[r for r in rows if r['case_id']==cid]
        if len(selected)!=1:raise ValueError('Case must occur exactly once in public manifest')
        row=selected[0];task=inside(public,row['task_path'])
        if sha(task/'manifest.json')!=row['task_manifest_sha256']:raise ValueError('Task manifest hash mismatch')
        m=json.loads((task/'manifest.json').read_text())
        if m['source_sha256']!=record['source_sha256'] or m['source_sha256']!=row['source_sha256']:raise ValueError('Broken source identity mismatch')
        for name in m['files']:
            if sha(inside(task,name))!=m['file_sha256'][name]:raise ValueError('Public asset hash mismatch: '+name)
        contract=json.loads((task/'execution_contract.json').read_text())
        original=Path(record['source_root']).resolve(strict=True)
        if not original.is_relative_to(workspace):raise ValueError('Candidate source escapes workspace')
        result.update(case_id=cid,source_sha256=m['source_sha256'],task_manifest_sha256=row['task_manifest_sha256'],
                      native_input_boundary=contract['native_input_boundary'],observation_boundary=contract['observation_boundary'],
                      runtime_image=contract['runtime_image'])
        put(out/'plan.json',{'case_id':cid,'task_path':row['task_path'],'contract':contract,'execute':a.execute})
        if not a.execute:
            result['status']='PUBLIC_PLAN_VALIDATED';put(out/'public_execution.json',result);return 0
        if os.getuid()==0:raise ValueError('Non-root host owner required')
        security=subprocess.check_output(['docker','info','--format={{json .SecurityOptions}}'],text=True,timeout=30)
        if not any('rootless' in v for v in json.loads(security)):raise ValueError('Rootless Docker required')
        sys.path.insert(0,str(a.tools_root.resolve(strict=True)))
        from scorer.executor import safe_extract_archive,source_tree_sha256,run_container_bounded
        if a.lock_root:
            lock=inside(a.lock_root.resolve(strict=True),'runtime-calibration-slot.lock',exists=False).open('a')
            fcntl.flock(lock,fcntl.LOCK_EX)
        if shutil.disk_usage(out).free<24*1024**3:raise RuntimeError('24 GiB free disk required before native build')
        with tempfile.TemporaryDirectory(prefix='contractedge-public-native-') as temporary:
            tmp=Path(temporary);source=tmp/'candidate'
            shutil.copytree(original,source,symlinks=True,ignore=shutil.ignore_patterns('.git'))
            candidate_before=source_tree_sha256(source)
            mounts=[];writable=[]
            for item in contract['copy_into_disposable_source']:
                src=inside(task,item['from']);dst=inside(source,item['to'],exists=False)
                if dst.exists() and not item.get('replace'):raise ValueError('Reserved scaffold path already occupied')
                dst.parent.mkdir(parents=True,exist_ok=True)
                if src.is_dir():shutil.copytree(src,dst)
                else:shutil.copy2(src,dst)
            for i,dep in enumerate(contract['dependency_archives']):
                archive=inside(task,dep['archive'])
                if dep.get('operation'):
                    union_node_seed(archive,source);continue
                dest=tmp/('dependency-'+str(i))
                safe_extract_archive(archive,dest,m['file_sha256'][dep['archive']])
                mounted=inside(dest,dep.get('strip_prefix',''))
                mounts.append((mounted,dep['target'],bool(dep.get('readonly',True))))
                if not dep.get('readonly',True):writable.append((mounted,dep['target']))
            for item in contract['readonly_mounts']:
                host=source if item['source'] in {'candidate_tree','candidate_tree_with_dependency_union'} else inside(task,item['source'])
                mounts.append((host,item['target'],True))
            script=contract['candidate_script_readonly_mount'];mounts.append((inside(task,script['source']),script['target'],True))
            for i,item in enumerate(contract['fresh_writable_mounts']):
                host=tmp/('writable-'+str(i));host.mkdir()
                mounts.append((host,item['target'],False));writable.append((host,item['target']))
            # Create missing nested bind placeholders only in the disposable copy.
            for host,target,readonly in mounts:
                roots=[(h,t) for h,t,ro in mounts if h==source and target.startswith(t.rstrip('/')+'/')]
                for root,base in roots:
                    placeholder=inside(root,target[len(base):].lstrip('/'),exists=False)
                    placeholder.parent.mkdir(parents=True,exist_ok=True)
                    if host.is_dir():placeholder.mkdir(exist_ok=True)
                    elif not placeholder.exists():placeholder.touch()
            staged_identity=source_tree_sha256(source)
            resources=contract['resources'];token=uuid.uuid4().hex;cidfile=out/'owned.cid'
            argv=['docker','run','--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL',
                  '--security-opt=no-new-privileges:true','--cpus='+str(resources['cpus']),
                  '--memory='+str(resources['memory_bytes']),'--pids-limit='+str(resources['pids']),
                  '--cidfile='+str(cidfile),'--label=contractedge.case='+cid,'--label=contractedge.run-token='+token,
                  '--log-driver=json-file','--log-opt=max-size=1m','--log-opt=max-file=1',
                  '--tmpfs=/tmp:rw,exec,nosuid,nodev,size='+str(contract['tmpfs_bytes'])]
            for host,target,readonly in mounts:
                if ',' in str(host):raise ValueError('Unsupported comma in bind path')
                argv.append(f'--mount=type=bind,src={host},dst={target}'+(',readonly' if readonly else ''))
            for k,v in contract['environment'].items():argv.append('--env='+k+'='+str(v))
            argv += [contract['runtime_image'],*contract['candidate_command']]
            put(out/'container-command.json',argv)
            try:
                run=run_container_bounded(argv,contract['timeout_seconds'],out,
                    max_stdout_bytes=contract['output_limits']['stdout_bytes'],max_stderr_bytes=contract['output_limits']['stderr_bytes'])
            finally:
                cleanup={'token':token,'case_id':cid}
                if cidfile.exists():
                    owned=cidfile.read_text().strip()
                    if len(owned)!=64 or any(c not in '0123456789abcdef' for c in owned):raise ValueError('Malformed owned CID')
                    inspected=subprocess.run(['docker','inspect',owned],capture_output=True,text=True,timeout=30)
                    cleanup.update(container_id=owned,inspect_exit_code=inspected.returncode)
                    if inspected.returncode==0:
                        obj=json.loads(inspected.stdout)[0];labels=obj['Config']['Labels']
                        if obj['Id']!=owned or labels.get('contractedge.case')!=cid or labels.get('contractedge.run-token')!=token:raise ValueError('Owned CID labels mismatch')
                        subprocess.run(['docker','rm','-f',owned],check=True,capture_output=True,timeout=30)
                    probe=subprocess.run(['docker','inspect',owned],capture_output=True,timeout=30)
                    cleanup['owned_container_absent']=probe.returncode!=0
                    if probe.returncode==0:raise RuntimeError('Owned container not cleaned')
                put(out/'cleanup.json',cleanup)
            evidence=asdict(run)
            for stream in ['stdout','stderr']:
                path=out/(stream+'.log');path.write_text(evidence.pop(stream));evidence[stream+'_sha256']=sha(path)
            put(out/'container-result.json',evidence)
            if source_tree_sha256(source)!=staged_identity:raise ValueError('Readonly staged source changed')
            result.update(runtime_executed=True,candidate_tree_sha256=candidate_before,staged_tree_sha256=staged_identity)
            if run.returncode or run.timed_out or run.output_limited:
                result.update(status='EXECUTION_FAILED',exit_code=run.returncode)
            else:
                protocol=contract['raw_output']
                if protocol['kind']=='stdout_marker':
                    values=[json.loads(line.partition(protocol['marker'])[2]) for line in run.stdout.splitlines() if protocol['marker'] in line]
                    if len(values)!=1:raise ValueError('Expected exactly one native raw observation')
                    raw=values[0]
                    if not all(k in raw for k in protocol['fields']):raise ValueError('Missing raw observation fields')
                elif protocol['kind']=='entire_stdout_json':raw=json.loads(run.stdout)
                elif protocol['kind']=='file':
                    matches=[(h,t) for h,t in writable if protocol['path'].startswith(t.rstrip('/')+'/')]
                    if not matches:raise ValueError('Raw file is outside declared writable mounts')
                    host,target=max(matches,key=lambda x:len(x[1]));path=inside(host,protocol['path'][len(target):].lstrip('/'))
                    if path.stat().st_size>protocol['maximum_bytes']:raise ValueError('Raw file exceeds limit')
                    raw=json.loads(path.read_text())
                else:raise ValueError('Unsupported raw observation protocol')
                put(out/'raw-observation.json',raw);result.update(status='EXECUTION_PASS',raw_sha256=sha(out/'raw-observation.json'))
            put(out/'public_execution.json',result)
        print(json.dumps({'case_id':cid,'status':result['status'],'hidden_semantic_evaluation':'NOT_RUN'}))
        return 0 if result['status']=='EXECUTION_PASS' else 1
    except Exception as exc:
        result.update(status='EXECUTION_FAILED',failure_type=type(exc).__name__,failure=str(exc))
        put(out/'public_execution.json',result);raise

if __name__=='__main__':raise SystemExit(main())
