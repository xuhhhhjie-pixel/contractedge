"""Bounded, public-input-only execution of registered method behavior probes."""
import hashlib,json,os,sys,tempfile,time,shutil
from pathlib import Path
from contractedge.execution.public import cleanup_owned_containers


def run_method_probes(workspace, output, tools_root, specs, timeout_seconds=1200):
    from contractedge.source.snapshot import compute_snapshot
    workspace=Path(workspace).resolve(strict=True);out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False)
    record=json.loads((workspace/'workspace.json').read_text());source=Path(record['source_root']).resolve(strict=True)
    inputs=Path(record['inputs_root']).resolve(strict=True);source.relative_to(workspace);inputs.relative_to(workspace)
    before=compute_snapshot(source).snapshot_id
    # Same public runtime primitives used by the frozen public launcher. Adapter
    # is constructed solely from public environment.json, never a scoring adapter.
    sys.path.insert(0,str(Path(tools_root).resolve(strict=True)))
    from scorer.executor import safe_extract_archive,run_container_bounded
    from scorer.isolation import build_candidate_docker_argv,build_writable_mount_cleanup_docker_argv
    from scorer.model import Adapter
    config=json.loads((inputs/'environment.json').read_text());image,digest=config['runtime_image'].rsplit('@',1)
    adapter=Adapter(case_id=record['case_id'],patchable_paths=(),forbidden_paths=(),image=image,image_digest=digest,
        resources=config['resources'],timeout_seconds=min(float(config['timeout_seconds']),timeout_seconds))
    reports=[];start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ce-method-probe-',dir=out.parent) as td:
        tmp=Path(td);mounts=[];writable=[]
        runtime_source=tmp/'source'
        shutil.copytree(source,runtime_source,symlinks=True,ignore=shutil.ignore_patterns('.git','.cache','.contractedge-deps'))
        (runtime_source/'.cache').mkdir()
        try:
            for i,m in enumerate(config.get('public_mounts',[])):
                archive=(inputs/m['archive']).resolve(strict=True);archive.relative_to(inputs)
                if not m['target'].startswith('/work/.contractedge-deps/') or '..' in Path(m['target']).parts:
                    raise ValueError('Method probe supports only public dependency mounts below /work/.contractedge-deps')
                (runtime_source/m['target'].removeprefix('/work/')).mkdir(parents=True,exist_ok=True)
                extracted=safe_extract_archive(archive,tmp/str(i),m['sha256'])
                if m.get('writable'):
                    for item in [extracted,*extracted.rglob('*')]:
                        if item.is_dir():item.chmod(0o777)
                        elif item.is_file() and not item.is_symlink():item.chmod(0o777 if item.stat().st_mode&0o111 else 0o666)
                    writable.append((extracted,m['target']))
                else:mounts.append((extracted,m['target']))
            for spec in specs:
                from contractedge.execution.probe_specs import registered_programs
                registry=registered_programs()
                if spec['id'] not in registry or spec['program']!=registry[spec['id']][0]:raise ValueError('Unregistered executable probe')
                program=out/spec['id'];program.mkdir();(program/'main.go').write_text(spec['program'])
                assert hashlib.sha256((program/'main.go').read_bytes()).hexdigest()==spec['sha256']
                remaining=timeout_seconds-(time.monotonic()-start)
                if remaining<=0:raise TimeoutError('Method probe deadline exceeded')
                command=['sh','-eu','-c',
                    'export GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOMAXPROCS=2 GOMODCACHE=/work/.contractedge-deps/gomod CGO_ENABLED=0; '
                    'cp /work/go.mod /tmp/method-probe.go.mod; cp /work/.contractedge-deps/setup/go.sum /tmp/method-probe.go.sum; '
                    'exec go run -p 2 -mod=readonly -modfile=/tmp/method-probe.go.mod -buildvcs=false /method-probe/main.go']
                if spec['id']=='alertmanager-reload-v1':
                    command=['sh','-eu','-c','export GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOMAXPROCS=2 GOMODCACHE=/work/.contractedge-deps/gomod GOCACHE=/tmp/ce-gocache CGO_ENABLED=0 GOFLAGS="-mod=readonly -buildvcs=false"; exec go run -p=1 /method-probe/main.go']
                argv=build_candidate_docker_argv(adapter,runtime_source,command,uid=os.getuid(),gid=os.getgid(),
                    readonly_mounts=[*mounts,(program,'/method-probe')],writable_mounts=writable,readonly_source=True)
                run=run_container_bounded(argv,min(adapter.timeout_seconds,remaining),Path.cwd(),max_stdout_bytes=1024*1024)
                (program/'stdout').write_text(run.stdout);(program/'stderr').write_text(run.stderr)
                report={'probe_id':spec['id'],'probe_sha256':spec['sha256'],'snapshot_id':before,
                    'argv':argv,'returncode':run.returncode,'timed_out':run.timed_out,'output_limited':run.output_limited,
                    'boundary':spec['boundary'],'scenario':spec['scenario'],'status':'PROBE_ERROR','checks':[]}
                if run.returncode==0 and not run.timed_out and not run.output_limited:
                    try:
                        value=json.loads(run.stdout)
                        checks=value['checks'];ids=[c['id'] for c in checks]
                        if (value['probe_id']!=spec['id'] or set(ids)!=set(spec['checks']) or len(ids)!=len(set(ids))
                            or any(c.get('status') not in {'PASS','FAIL'} or 'observed' not in c for c in checks)):
                            raise ValueError('Probe output check set invalid')
                        report.update(status='COMPLETE',checks=[{**c,'statement':spec['checks'][c['id']]} for c in checks])
                    except (ValueError,KeyError,TypeError) as exc:report['error']=str(exc)
                reports.append(report)
            for mount,target in writable:
                cleanup=build_writable_mount_cleanup_docker_argv(adapter,mount,target,uid=os.getuid(),gid=os.getgid())
                r=run_container_bounded(cleanup,120,Path.cwd(),max_stdout_bytes=4096)
                if r.returncode:raise RuntimeError('Public writable dependency cleanup failed')
        finally:
            cleanup=cleanup_owned_containers([tmp,out])
    after=compute_snapshot(source).snapshot_id
    if after!=before:raise ValueError('Method probe changed candidate source')
    result={'schema_version':'ce.method_behavior_probes.v1','case_id':record['case_id'],'snapshot_id':before,
        'status':'COMPLETE' if cleanup['status']=='OWNED_CONTAINERS_CLOSED' and all(r['status']=='COMPLETE' for r in reports) else 'PROBE_ERROR',
        'probes':reports,'source_unchanged':before==after,'cleanup':cleanup,'private_materials_loaded':False,
        'hidden_semantic_evaluation':'NOT_RUN','elapsed_seconds':time.monotonic()-start}
    (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');return result


def behavior_records(report, output):
    """Materialize only complete registered probe checks as typed evidence."""
    records={};out=Path(output)
    if report.get('status')!='COMPLETE':return records
    from contractedge.execution.probe_specs import registered_programs
    registry=registered_programs()
    for probe in report['probes']:
        if (probe.get('probe_id') not in registry or probe.get('status')!='COMPLETE'
                or probe.get('probe_sha256')!=hashlib.sha256(registry[probe['probe_id']][0].encode()).hexdigest()
                or len(probe.get('checks',[]))!=len(registry[probe['probe_id']][1])
                or {c.get('id') for c in probe.get('checks',[])}!=set(registry[probe['probe_id']][1])
                or any(c.get('status') not in {'PASS','FAIL'} for c in probe.get('checks',[]))):
            continue
        for index,check in enumerate(probe['checks']):
            path=out/probe['probe_id']/f'check-{index:03d}.json'
            value={'probe_id':probe['probe_id'],'probe_sha256':probe['probe_sha256'],
                   'snapshot_id':report['snapshot_id'],'boundary':probe['boundary'],**check}
            if probe['probe_id']=='kpt-configpath-v2':
                state=check.get('observed',{});name=check['id'];kind=name.split('.')[0];predicate=name.split('.')[1]
                functions=state.get('functions',{});rendered=state.get('rendered',{})
                if predicate=='owner_binding': observed={'function_config':functions.get('owner',{}).get('function_config')}
                elif predicate in {'owner_excludes_config','ordinary_inputs'}:observed={'ordinary_inputs':functions.get('owner' if kind!='control' else 'control',{}).get('ordinary_inputs')}
                elif predicate=='later_sees_config':observed={'ordinary_inputs':functions.get('later',{}).get('ordinary_inputs')}
                else:
                    resource='settings' if predicate.startswith('config_') else 'workload'
                    observed={'resource':resource,'exists':resource in rendered,'data':rendered.get(resource,{}).get('data')}
                    if predicate=='final_resources':observed={'rendered':rendered}
                value['observed']={'render_error':state.get('render_error'),**observed}
                value['raw_observation_pointer']='/checks/'+str(index)+'/observed'
                value['raw_source']='stdout'
            path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
            records[path]={'evidence_kind':'PUBLIC_BEHAVIOR','scenario':probe['scenario'],
                'behavior_check':{'id':probe['probe_id']+':'+check['id'],'status':check['status'],
                                  'probe_sha256':probe['probe_sha256']},
                'origin':'Method-owned public requirement-derived runtime probe; candidate executes in the probe process, not tamper-proof or independent grading'}
    return records
