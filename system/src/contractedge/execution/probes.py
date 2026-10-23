"""Bounded, public-input-only execution of registered method behavior probes."""
import hashlib,json,os,sys,tempfile,time,shutil
import re
from pathlib import Path
from contractedge.execution.public import cleanup_owned_containers


def candidate_runtime_failures(report, output, source):
    """Recognize the registered wrapper's production startup panic boundary.

    This is an execution failure, not a semantic check result or a proven
    source cause. Unclassified errors, partial logs and foreign tool frames
    retain PROBE_ERROR. No candidate-emitted JSON can register this fact.
    """
    from contractedge.execution.probe_specs import registered_programs
    from contractedge.execution.topomen import safe_target
    from contractedge.source.snapshot import compute_snapshot
    source=Path(source);output=Path(output)
    if (report.get('status')!='PROBE_ERROR' or report.get('source_unchanged') is not True
            or report.get('snapshot_id')!=compute_snapshot(source).snapshot_id):
        return []
    try:
        module=re.search(r'^module\s+(\S+)\s*$',safe_target(source,'go.mod').read_text(),re.M)
    except (OSError,ValueError,UnicodeError):
        return []
    if module is None:return []
    registry=registered_programs();failures=[]
    for probe in report.get('probes',[]):
        pid=probe.get('probe_id')
        # These versions own the explicit server readiness + child-log wrapper.
        # Other registered probes have different execution boundaries.
        if pid not in {'alertmanager-reload-v1','alertmanager-reload-v2','alertmanager-reload-v3'}:continue
        if (probe.get('probe_sha256')!=hashlib.sha256(registry[pid][0].encode()).hexdigest()
                or probe.get('snapshot_id')!=report['snapshot_id']
                or probe.get('scenario')!='method-'+pid
                or type(probe.get('returncode')) is not int or probe['returncode']==0
                or probe.get('timed_out') is not False or probe.get('output_limited') is not False):continue
        path=output/pid/'stderr'
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:continue
            raw=path.read_bytes();message=raw.decode('utf-8')
        except (OSError,UnicodeError):continue
        if not message.startswith('PROBE_ERROR: server readiness: panic:'):continue
        locations=[]
        for match in re.finditer(r'^\s*'+re.escape(module[1])+r'/([^\s:]+\.go):(\d+)\b',message,re.M):
            relative,line=match[1],int(match[2])
            try:
                lines=safe_target(source,relative).read_text().splitlines()
            except (OSError,ValueError,UnicodeError):continue
            item={'path':relative,'line':line}
            if 1<=line<=len(lines) and item not in locations:locations.append(item)
        if not locations:continue
        failures.append(dict(operation='PRODUCTION_PROCESS_START',status='FAILED',
            snapshot_id=report['snapshot_id'],probe_id=pid,probe_sha256=probe['probe_sha256'],
            scenario=probe['scenario'],source_locations=locations[:8],
            stderr_sha256=hashlib.sha256(raw).hexdigest(),
            stderr_excerpt=message[:4000],stderr_truncated=len(message)>4000,
            interpretation='Registered public wrapper observed startup panic with current production frames; execution failed, root cause and semantic obligations remain unproven'))
    return failures


def probe_command(probe_id):
    if probe_id=='cloudwatch-tags-v1':
        return ['sh','-eu','-c',
            'export GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOMAXPROCS=2 GOMODCACHE=/work/.contractedge-deps/gomod GOCACHE=/tmp/ce-gocache CGO_ENABLED=0; '
            'cp /work/go.mod /tmp/method-probe.go.mod; cp /work/.contractedge-deps/setup/go.sum /tmp/method-probe.go.sum; '
            'if go test -count=1 -p=1 -mod=readonly -modfile=/tmp/method-probe.go.mod -buildvcs=false '
            '-overlay=/method-probe/overlay.json -run "^TestContractEdgePublicTagUpdate$" -v ./pkg/resource/metric_alarm >/tmp/ce-probe.stdout 2>/tmp/ce-probe.stderr; '
            'then sed -n "/^{/p" /tmp/ce-probe.stdout; else cat /tmp/ce-probe.stdout /tmp/ce-probe.stderr >&2; exit 1; fi']
    if probe_id in {'alertmanager-reload-v1','alertmanager-reload-v2','alertmanager-reload-v3'}:
        return ['sh','-eu','-c','export GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOMAXPROCS=2 GOMODCACHE=/work/.contractedge-deps/gomod GOCACHE=/tmp/ce-gocache CGO_ENABLED=0 GOFLAGS="-mod=readonly -buildvcs=false"; exec go run -p=1 /method-probe/main.go']
    return ['sh','-eu','-c',
        'export GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off GOMAXPROCS=2 GOMODCACHE=/work/.contractedge-deps/gomod CGO_ENABLED=0; '
        'cp /work/go.mod /tmp/method-probe.go.mod; cp /work/.contractedge-deps/setup/go.sum /tmp/method-probe.go.sum; '
        'exec go run -p 2 -mod=readonly -modfile=/tmp/method-probe.go.mod -buildvcs=false /method-probe/main.go']


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
                if spec['id']=='cloudwatch-tags-v1':
                    virtual='pkg/resource/metric_alarm/zz_contractedge_public_probe_test.go'
                    if (source/virtual).exists():raise ValueError('Method-owned probe overlay collides with source')
                    (program/'overlay.json').write_text(json.dumps({'Replace':{
                        '/work/'+virtual:'/method-probe/main.go'}}))
                remaining=timeout_seconds-(time.monotonic()-start)
                if remaining<=0:raise TimeoutError('Method probe deadline exceeded')
                command=probe_command(spec['id'])
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
            if probe['probe_id'] in {'kpt-configpath-v2','kpt-configpath-v3','kpt-configpath-v4'}:
                state=check.get('observed',{});name=check['id'];kind=name.split('.')[0];predicate=name.split('.')[1]
                functions=state.get('functions',{});rendered=state.get('rendered',{})
                if predicate=='resource_inventory':observed=state.get('inventory',{})
                elif predicate=='identity_annotations_cleaned':observed={'inventory':state.get('inventory',{}),
                    'annotations':{key:obj.get('metadata',{}).get('annotations',{}) for key,obj in rendered.items()}}
                elif predicate=='owner_binding': observed={'function_config':functions.get('owner',{}).get('function_config')}
                elif predicate in {'owner_excludes_config','ordinary_inputs'}:observed={'ordinary_inputs':functions.get('owner' if kind!='control' else 'control',{}).get('ordinary_inputs')}
                elif predicate=='later_sees_config':observed={'ordinary_inputs':functions.get('later',{}).get('ordinary_inputs')}
                else:
                    resource='settings' if predicate.startswith('config_') else 'workload'
                    observed={'resource':resource,'exists':resource in rendered,'data':rendered.get(resource,{}).get('data')}
                    if predicate=='final_resources':observed={'rendered':rendered}
                value['observed']={'render_error':state.get('render_error'),**observed}
                value['raw_observation_pointer']='/checks/'+str(index)+'/observed'
                value['raw_source']='stdout'
            if probe['probe_id']=='kpt-configpath-v4':
                # Snapshot/program identity and capability boundary are already
                # bound in the enclosing EvidenceRecord and frozen capability
                # descriptor. Keep full raw stdout, avoiding 38 repetitions in
                # the finite diagnostic context.
                for key in ('snapshot_id','probe_sha256','boundary'):value.pop(key,None)
                text=json.dumps(value,ensure_ascii=False,separators=(',',':'))
            else:
                text=json.dumps(value,ensure_ascii=False,indent=2)
            path.write_text(text+'\n')
            records[path]={'evidence_kind':'PUBLIC_BEHAVIOR','scenario':probe['scenario'],
                'behavior_check':{'id':probe['probe_id']+':'+check['id'],'status':check['status'],
                                  'probe_sha256':probe['probe_sha256']},
                'origin':'Method-owned public requirement-derived runtime probe; candidate executes in the probe process, not tamper-proof or independent grading'}
    return records
