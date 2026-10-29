"""Predeclared single clean-clone CLI development run; no scientific panel score."""
import argparse,subprocess,os,json,hashlib,sys
from pathlib import Path
r=Path('/home/xhj/contractedge-system');a=Path(__file__).resolve().parent;b=Path('/tmp/contractedge-v0638-development');clone=b/'clone/system';bench=r.parent/'contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019'
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
write=lambda p,x:Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');args=p.parse_args()
freeze=read(r/'outputs/reliability-closure-20261020/frozen-method-v0.6.38.json');actual={x.relative_to(clone/'src').as_posix():sha(x) for x in (clone/'src').rglob('*') if x.is_file() and x.suffix in {'.py','.json'}};assert actual==freeze['files']
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=clone,text=True).strip();assert commit.startswith('8625d07')
basecfg=read(freeze['configuration_path']);cfg={**basecfg,'run_id':'portable-v0638-RW130-v2-20261024','topomen_source_root':str(clone/'vendor/topomen'),'lock_root':str(b/'locks'),'money_budget':{**basecfg['money_budget'],'path':str(b/'ledger/money-ledger.json')}}
for name in ['data/public','data/tools','data/partition','ledger','runs','test-tmp','locks']:(b/name).mkdir(parents=True,exist_ok=True)
cp=b/'portable-config-v2.json';output=b/'runs/portable-RW-CAND-130-v2';rp=a/'registration.json'
if not args.execute:
 assert not rp.exists()
 if cp.exists():assert read(cp)==cfg
 else:write(cp,cfg)
else:assert read(cp)==cfg
moneyparent=Path(basecfg['money_budget']['path']).parent;assert (moneyparent/'money-ledger.json').is_file()
cmd=['bwrap','--ro-bind','/','/','--dev-bind','/dev','/dev','--proc','/proc','--tmpfs','/tmp','--bind',str(b),str(b),'--ro-bind',str(bench/'public'),str(b/'data/public'),'--ro-bind',str(bench/'tools'),str(b/'data/tools'),'--ro-bind',str(bench/'partition'),str(b/'data/partition'),'--ro-bind',str(bench/'RELEASE_ENTRY.json'),str(b/'data/RELEASE_ENTRY.json'),'--bind',str(moneyparent),str(b/'ledger')]
for path in ['/home/xhj/contractedge','/home/xhj/contractedge-system','/home/xhj/contractedge-integrations','/home/xhj/contractedge-github-delivery-20261022','/home/xhj/contractedge-reports','/home/xhj/.config',str(b/'clone/.git'),str(clone/'docs'),str(clone/'tests'),str(clone/'outputs')]:cmd+=['--tmpfs',path]
cmd+=['--chdir',str(clone)]
env={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','CONTRACTEDGE_TOPOMEN_SOURCE'}};env.update(TMPDIR=str(b/'test-tmp'),PYTHONDONTWRITEBYTECODE='1')
registration=dict(role='SINGLE_CLEAN_CLONE_OPERATIONAL_VALIDATION',method_version='0.6.38',source_digest=freeze['method_digest'],clone_commit=commit,case_id='RW-CAND-130',partition='development',fresh_venv=True,config_sha256=sha(cp),base_config_sha256=sha(freeze['configuration_path']),script_sha256=sha(__file__),changed_config_keys=['run_id','topomen_source_root','lock_root','money_budget.path'],max_model_calls=cfg['max_model_calls'],max_edit_rounds=cfg['max_rounds'],shared_money_limit_cny=150,counts_in_46_slot_panel=False,counts_as_generalization_result=False,retry_if_failed=False,pre_dispatch_recovery='Partition+Release metadata corrections after zero-request startup rejection. Original INTERRUPTED batch retained, new operationalv2; no model response or candidate retried',source_roots_masked=True,G3_accepted=False,scope='Existing pinned public150 release mounted at new paths; clean installed CLI and vendoredTopoMen. Source/docs/tests/history/originalevaluation masked; Docker socket remains available, so not strong G3 isolation. Parent independently scores only after method subprocess exits.')
if not args.execute:
 write(rp,registration)
 probe="""import contractedge,json,os;from pathlib import Path;from contractedge.execution.topomen_agent import source_identity
b=Path('/tmp/contractedge-v0638-development');cfg=json.loads((b/'portable-config-v2.json').read_text());assert str(b/'clone/system/src') in contractedge.__file__
assert source_identity(cfg['topomen_source_root'])['source_sha256']==cfg['topomen_source_sha256']
l=json.loads(Path(cfg['money_budget']['path']).read_text());assert l['settings']['limit_micro_cny']==150000000 and len(l['calls'])>=981
assert not list(Path('/home/xhj/contractedge-system').iterdir())
assert not list(Path('/home/xhj/contractedge-reports').iterdir())
assert not list((b/'clone/system/outputs').iterdir())
assert (b/'data/public/manifest.jsonl').is_file()
from contractedge.dataset.admission import check_case_admission
check_case_admission(b/'data/public','RW-CAND-130',cfg)
from contractedge.dataset.benchmark import release_identity,read_members,method_input_from_task
from contractedge.contracts.validation import parse_method_input
identity=release_identity(b/'data');members=read_members(b/'data/public');assert len(members)==150
for row in members:
 value,_=method_input_from_task(b/'data/public',row['case_id'],release_id=identity['release_id']);parse_method_input(value)
(b/'input-projection-dry.json').write_text(json.dumps({'verified':150,'private_materials_loaded':False,'source_trees_extracted':0,'release_identity':identity}))
print(json.dumps({'installed_version':contractedge.__version__,'module':contractedge.__file__,'native_source_verified':True,'ledger_existing_records':len(l['calls']),'original_roots_masked':True}))
"""
 with (a/'environment-dry.log').open('x') as stream:res=subprocess.run(cmd+[str(b/'venv/bin/python'),'-c',probe],env=env,stdout=stream,stderr=subprocess.STDOUT)
 assert res.returncode==0,'Dry environment check failed; no model calls'
 write(a/'environment-dry.json',dict(status='PASS',new_model_calls=0,registration_sha256=sha(rp),config_sha256=sha(cp)))
 print('Dry operational environment qualified; no API calls');sys.exit(0)
assert read(rp)==registration and read(a/'environment-dry.json')['status']=='PASS';assert not output.exists()
env[cfg['api_key_env']]=Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
with (a/'method-cli.log').open('x') as stream:
 res=subprocess.run(cmd+[str(b/'venv/bin/contractedge'),'repair-batch','--public-root',str(b/'data/public'),'--tools-root',str(b/'data/tools'),'--config',str(cp),'--output',str(output),'--case-id','RW-CAND-130','--max-new-cases','1'],env=env,stdout=stream,stderr=subprocess.STDOUT)
# Retain any failure; never retry the method.
assert actual=={x.relative_to(clone/'src').as_posix():sha(x) for x in (clone/'src').rglob('*') if x.is_file() and x.suffix in {'.py','.json'}}
batch=read(output/'batch.json') if (output/'batch.json').exists() else None
record=next((x for x in (batch or {}).get('records',[]) if x['case_id']=='RW-CAND-130'),None)
write(a/'method-result.json',dict(role=registration['role'],returncode=res.returncode,method_status=(record or {}).get('status'),batch_sha256=sha(output/'batch.json') if batch else None,candidate=(record or {}).get('candidate'),source_unchanged=True,output=str(output),counts_in_46_slot_panel=False,formal_ready=False))
print(json.dumps(dict(returncode=res.returncode,method_status=(record or {}).get('status'),scoring_started=False)))
