import sys,json,tarfile,subprocess,shutil,tempfile,os
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'src')]
from contractedge.execution.public import BenchmarkPublicRunner
BENCH=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
OUT=Path.cwd()/'outputs/reliability-closure-20261020/kopf-invocation-debug-v2';OUT.mkdir(exist_ok=True)
r=BenchmarkPublicRunner(BENCH/'public',BENCH/'tools',lock_root='/tmp/contractedge-system-native-locks',timeout_seconds=900,query='adopt namespace')
workspace=OUT/'workspace';record=r.prepare('R3-KOPF-726',workspace,OUT/'prepare');source=Path(record['source_root']);inputs=Path(record['inputs_root'])
patch=Path.cwd()/'outputs/reliability-closure-20261020/runs/candidate-rc2-A-core/cases/R3-KOPF-726/candidate.patch'
subprocess.run(['git','apply',str(patch)],cwd=source,check=True,capture_output=True)
sys.path.insert(0,str(BENCH/'tools'))
from scorer.executor import safe_extract_archive,run_container_bounded
from scorer.isolation import build_candidate_docker_argv
from scorer.model import Adapter
env=json.loads((inputs/'environment.json').read_text());image,digest=env['runtime_image'].rsplit('@',1)
(source/'.cache').mkdir(exist_ok=True)
for m in env['public_mounts']:(source/m['target'].removeprefix('/work/')).mkdir(parents=True,exist_ok=True)
adapter=Adapter(case_id='R3-KOPF-726',patchable_paths=(),forbidden_paths=(),image=image,image_digest=digest,resources=env['resources'],timeout_seconds=900)
try:
 mounts=[]
 for i,m in enumerate(env['public_mounts']):
  extracted=safe_extract_archive(inputs/m['archive'],OUT/str(i),m['sha256']);mounts.append((extracted,m['target']))
 bridge=next(p for p,t in mounts if t.endswith('/bridge'));text=(bridge/'observe.py').read_text()
 text='import traceback,inspect\n'+text.replace('except LookupError as exc:', 'except LookupError as exc:\n        print(traceback.format_exc())')
 text=text.replace('import kopf\n','import kopf\nimport kopf.toolkits.hierarchies as h\nprint(kopf.__file__)\nprint(inspect.getsource(h.adjust_namespace))\n')
 probe=OUT/'probe';probe.mkdir();(probe/'observe.py').write_text(text)
 command=['sh','-eu','-c','python -m venv /tmp/venv; /tmp/venv/bin/python -m pip install --no-index --find-links=/work/.contractedge-deps/wheels --no-cache-dir --disable-pip-version-check --no-compile -r /work/.contractedge-deps/setup/requirements.in >/tmp/install.log 2>&1; export PYTHONPATH=/work PYTHONDONTWRITEBYTECODE=1; exec /tmp/venv/bin/python /method-debug/observe.py']
 argv=build_candidate_docker_argv(adapter,source,command,uid=os.getuid(),gid=os.getgid(),readonly_mounts=[*mounts,(probe,'/method-debug')],readonly_source=True)
 result=run_container_bounded(argv,900,Path.cwd(),max_stdout_bytes=100000)
 (OUT/'stdout').write_text(result.stdout);(OUT/'stderr').write_text(result.stderr);print(result.returncode)
finally:
 for p in [workspace,*[p for p,t in locals().get('mounts',[])]]:
  if p.exists():shutil.rmtree(p)
