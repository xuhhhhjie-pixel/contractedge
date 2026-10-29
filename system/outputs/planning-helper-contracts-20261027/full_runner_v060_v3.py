"""Run the exact full suite and publish its actual exit status atomically."""
import hashlib,json,os,re,subprocess,sys
from pathlib import Path
SYSTEM=Path('/home/xhj/contractedge-planning-branch-continuity-20261026/system')
OUT=SYSTEM/'outputs/planning-helper-contracts-20261027'
FREEZE=SYSTEM/'docs/development/planning-helper-contracts-v060-20261027-v2.snapshot.json'
LOG=OUT/'full-v060-v3.log'
RECEIPT=OUT/'FULL_REGRESSION_COMPLETION_v060_v3.json'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
sys.path.insert(0,str(SYSTEM/'scripts'))
from verify_development_snapshot import verify_snapshot
assert sha(__file__)==sha(OUT/'full_runner_v060_v3.py')
assert not LOG.exists() and not RECEIPT.exists()
frozen=json.loads(FREEZE.read_text())
assert verify_snapshot(SYSTEM,frozen,require_current_bindings=True)['status']=='PASS'
sys.path[:0]=[str(SYSTEM/'src'),str(OUT)]
from external_fixture_inventory import verify_external_fixtures
fixture_before=verify_external_fixtures(SYSTEM,OUT/'EXTERNAL_PUBLIC_FIXTURES.json')
settings=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='src:.:/home/xhj/contractedge-system/.topomen-deps',CONTRACTEDGE_TOPOMEN_SOURCE=str(SYSTEM/'vendor/topomen'),CONTRACTEDGE_RUN_ISOLATED_DEMOS='1',DOCKER_HOST='unix:///run/user/1008/docker.sock',DOCKER_API_VERSION='1.54',CONTRACTEDGE_RUNTIME_IMAGE='golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d')
command=['/home/xhj/contractedge-system/.venv-dev/bin/python','-m','pytest','-q','tests']
with LOG.open('x') as stream:code=subprocess.call(command,cwd=SYSTEM,env=settings,stdout=stream,stderr=subprocess.STDOUT)
verification=verify_snapshot(SYSTEM,frozen,require_current_bindings=True)
fixture_after=verify_external_fixtures(SYSTEM,OUT/'EXTERNAL_PUBLIC_FIXTURES.json')
assert fixture_before==fixture_after
match=re.fullmatch(r'(\d+) passed, (\d+) subtests passed in [^\n]+',LOG.read_text().strip().splitlines()[-1])
counts=tuple(map(int,match.groups())) if match else None
passed=code==0 and counts==(2172,91) and verification['status']=='PASS'
result=dict(status='PASS' if passed else 'FAIL',exit_code=code,tests=counts[0] if counts else None,subtests=counts[1] if counts else None,command=command,source_sha256=frozen['source_sha256'],freeze_sha256=sha(FREEZE),log_sha256=sha(LOG),runner_sha256=sha(__file__),verification=verification,actual_isolated_demos_enabled=True,external_public_fixtures=fixture_after,formal_ready=False)
pending=RECEIPT.with_suffix('.pending')
with pending.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
os.link(pending,RECEIPT);pending.unlink()
print(json.dumps({k:result[k] for k in ['status','exit_code','tests','subtests','source_sha256','formal_ready']}))
sys.exit(0 if passed else 1)
