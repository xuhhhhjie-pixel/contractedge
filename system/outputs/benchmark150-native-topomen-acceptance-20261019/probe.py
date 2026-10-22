"""Native TopoMen Agent public edit replay acceptance; not autonomous model performance."""
import json
from pathlib import Path
import yaml
from contractedge.dataset.benchmark import method_input_from_task, public_task, execution_contract, editable_policy
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.model.provider import FixtureProvider
from contractedge.model.budget import BudgetedProvider
from contractedge.tokenizers import qwen35_token_count
from contractedge.orchestration.repair import select_observations
from contractedge.orchestration.feedback import make_feedback
from contractedge.orchestration.repair import write_json
from contractedge.source.snapshot import compute_snapshot

out=Path(__file__).resolve().parent
bench=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
case='RW-CAND-130'
value,_=method_input_from_task(bench/'public',case)
_,taskroot,task,manifest=public_task(bench/'public',case)
policy=editable_policy(task,execution_contract(taskroot,task,manifest))
runner=BenchmarkPublicRunner(bench/'public',bench/'tools',lock_root='/tmp/contractedge-system-native-locks',timeout_seconds=300,query=value['requirement_text'])
workspace=out/'workspace'
record=runner.prepare(case,workspace,out/'prepare')
source=Path(record['source_root'])
before=runner.observe(workspace,out/'broken-public')
path='templates/cluster-gateway/job-patch.yaml'
snapshot=compute_snapshot(source)
operations=[];edits=[];postconditions=[]
for idx,job in enumerate(['create','patch'],1):
    cid='C'+str(idx)
    operations.append({'change_id':cid,'target':{'kind':'EXISTING','path':path,'snapshot_id':snapshot.snapshot_id,'content_digest':snapshot.file_digests[path]},'operation':'MODIFY','semantic_action':'Bind root scheduling values to '+job+' Job pod spec','intent':value['requirement_text'],'violation_refs':[],'postcondition_refs':['P'+str(idx)],'depends_on':[]})
    old='      containers:\n      - name: '+job+'\n'
    addition='      {{- with .Values.nodeSelector }}\n      nodeSelector:\n      {{- toYaml . | nindent 8 }}\n      {{- end }}\n      {{- with .Values.tolerations }}\n      tolerations:\n      {{- toYaml . | nindent 8 }}\n      {{- end }}\n      {{- with .Values.affinity }}\n      affinity:\n      {{- toYaml . | nindent 8 }}\n      {{- end }}\n'
    edits.append({'change_id':cid,'path':path,'operation':'MODIFY','old_text':old,'new_text':addition+old})
    postconditions.append({'id':'P'+str(idx),'statement':'The '+job+' Job pod spec receives root nodeSelector, tolerations and affinity.','requirement_refs':['O1']})
postconditions.append({'id':'P3','statement':'When multicluster is disabled neither target Job is emitted.','requirement_refs':['O1']})
plan={'schema_version':'contractedge.topomen_plan.v1','status':'READY','run_id':'developer-public-backhalf-20261019','snapshot_id':snapshot.snapshot_id,'input_digest':value['input_digest'],'operations':operations,'postconditions':postconditions,'preserve_constraints':[],'open_obligations':[],'origin':'DEVELOPER_SPECIFIED_PUBLIC_SOURCE_ACCEPTANCE; NO_MODEL_DIAGNOSIS_OR_PLAN_CLAIM'}
write_json(out/'public-method-input.json',value)
write_json(out/'developer-plan.json',plan)
write_json(out/'developer-edits.json',{'edits':edits})
config=json.loads(Path('configs/development-local-model.json').read_text())
config.update(model='public-edit-replay',provider='fixture-public-edit-replay')
budget=BudgetedProvider(FixtureProvider({'edits':edits}),qwen35_token_count,max_tokens=50000,max_seconds=120,max_calls=1,on_update=lambda calls:write_json(out/'model_calls.json',calls))
executor=TopoMenAgentExecutor(budget,qwen35_token_count,config,on_trace=lambda trace:write_json(out/'topomen-native-trace.json',trace))
result=executor.execute(plan,source,policy,requirement=value['requirement_text'],declared_artifacts=value['declared_artifacts'],observations=select_observations(before['observations'],value['requirement_text'],qwen35_token_count))
write_json(out/'budget.json',budget.summary())
write_json(out/'native-integration-config.json',config)
write_json(out/'consumer-execution.json',result)
patch=runner.export(workspace,out/'candidate.patch',out/'export')
after=runner.observe(workspace,out/'candidate-public')
write_json(out/'execution-feedback.json',make_feedback(case_id=case,run_id=plan['run_id'],input_digest=value['input_digest'],round_id=0,from_snapshot=result['from_snapshot'],to_snapshot=result['to_snapshot'],patch_digest=patch['patch_sha256'],execution_status=after['status'],observations=after['observations'],plan=plan))
trigger=next(a for a in value['declared_artifacts'] if 'nodeSelector:' in a['content'])
settings=yaml.safe_load(trigger['content'])
def inspect(directory):
    docs=list(yaml.safe_load_all((directory/'raw/trigger.stdout').read_text()))
    targets=[d for d in docs if isinstance(d,dict) and d.get('kind')=='Job' and '-cluster-gateway-tls-secret-' in d.get('metadata',{}).get('name','')]
    control=list(yaml.safe_load_all((directory/'raw/control.stdout').read_text()))
    checks=[]
    for d in targets:
        pod=d['spec']['template']['spec']
        for key in ['nodeSelector','tolerations','affinity']:
            checks.append({'job':d['metadata']['name'],'key':key,'pass':pod.get(key)==settings[key]})
    return {'target_jobs':len(targets),'scheduling_checks':checks,'control_targets':sum(isinstance(d,dict) and d.get('kind')=='Job' and '-cluster-gateway-tls-secret-' in d.get('metadata',{}).get('name','') for d in control)}
old_checks=inspect(out/'broken-public');new_checks=inspect(out/'candidate-public')
passed=new_checks['target_jobs']==2 and len(new_checks['scheduling_checks'])==6 and all(c['pass'] for c in new_checks['scheduling_checks']) and new_checks['control_targets']==0
write_json(out/'public-developer-verification.json',{'before':old_checks,'after':new_checks,'pass':passed,'method':'DETERMINISTIC_COMPARISON_TO_PUBLISHED_TRIGGER_VALUES_AND_PUBLIC_REQUIREMENT','not_model_review':True})
write_json(out/'selected-candidate.json',{'case_id':case,'patch':patch,'candidate_selected_before_hidden_score':True,'source':'REAL_TOPOMEN_AGENT_PUBLIC_EDIT_REPLAY', 'actual_agent_class':result['backend']['agent_class'], 'native_source_sha256':result['backend']['source_sha256'],'model_success_claim':False,'public_checks_pass':passed,'from_snapshot':result['from_snapshot'],'to_snapshot':result['to_snapshot']})
assert passed
print(json.dumps({'status':'REAL_TOPOMEN_AGENT_INTEGRATION_PASS','patch_bytes':patch['patch_bytes'],'model_success_claim':False,'public_scheduling_checks':6,'control_targets':new_checks['control_targets']}))
