"""Public requirement-only audit of the fixed0.4.7 RW130 candidate. Zero HTTP."""
import hashlib,json,sys,yaml
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from contractedge.orchestration.batch import _method_digest
out=Path(__file__).parent
case=ROOT/'outputs/deepseek-acceptance-recovery-v1-20261020/cases/RW-CAND-130'
method=json.loads((case/'method_input.json').read_text())
values=next(yaml.safe_load(a['content']) for a in method['declared_artifacts'] if a['path'].endswith('trigger-values.yaml'))
raw=case/'rounds/0/public/raw'
target_names={'ct-vela-core-cluster-gateway-tls-secret-create','ct-vela-core-cluster-gateway-tls-secret-patch'}
def jobs(scene):
 return [d for d in yaml.safe_load_all((raw/(scene+'.stdout')).read_text())
         if isinstance(d,dict) and d.get('kind')=='Job' and d.get('metadata',{}).get('name') in target_names]
trigger=jobs('trigger');control=jobs('control');assert len(trigger)==2 and not control
rows=[]
for job in trigger:
 for field in ('nodeSelector','tolerations','affinity'):
  same=job['spec']['template']['spec'][field]==values[field];assert same
  rows.append({'job':job['metadata']['name'],'field':field,'matches_public_input':same})
patch=case/'candidate.patch'
result={'status':'PASS','scope':'Declared public requirement audit of fixed0.4.7 RW130 candidate; not a general verifier or hidden answer',
 'method_code_digest':_method_digest(),'case_id':'RW-CAND-130','trigger_target_jobs':2,'control_target_jobs':0,
 'six_field_comparisons':rows,'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),
 'raw_sha256':{n:hashlib.sha256((raw/n).read_bytes()).hexdigest() for n in ['trigger.stdout','control.stdout']},
 'api_calls':0,'private_materials_loaded':False}
(out/'public-single-audit-v0.4.7.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print('RW130: six public field equalities and disabled target-job absence PASS')
