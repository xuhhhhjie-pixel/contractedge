"""Read-only acceptance assertions and handoff documents; no model/scorer calls."""
import hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from contractedge.orchestration.batch import _method_digest
from contractedge.execution.probe_specs import KPT_CONFIGPATH_GO,KPT_CHECKS
OUT=Path(__file__).parent
RUN=ROOT/'outputs/deepseek-production-path-probe-v5-20261020'
SCORE=ROOT/'outputs/deepseek-independent-production-path-probe-v5-score-20261020'
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def main():
 method=read(OUT/'candidate-method.json');assert method['method_code_digest']==_method_digest()
 assert all(hashlib.sha256((ROOT/'src'/p).read_bytes()).hexdigest()==s for p,s in method['files'].items())
 tests_log=(OUT/'frozen-tests.log').read_text();matched=re.search(r'(\d+) passed, (\d+) subtests passed',tests_log);assert matched and 'failed' not in tests_log
 tests={'passed':int(matched[1]),'subtests_passed':int(matched[2]),'status':'PASS','method_code_digest':_method_digest(),'log':'frozen-tests.log'};write(OUT/'tests.json',tests)
 probe_sha=hashlib.sha256(KPT_CONFIGPATH_GO.encode()).hexdigest()
 broken=read(OUT/'probe-broken-v2/report.json');historical=read(OUT/'historical-probe-comparison.json')
 assert broken['status']==historical['status']=='COMPLETE'
 assert broken['probes'][0]['probe_sha256']==historical['probe_sha256']==probe_sha
 assert {c['id'] for c in broken['probes'][0]['checks']}==set(KPT_CHECKS)
 assert {c['id'] for c in broken['probes'][0]['checks'] if c['status']=='FAIL'}=={'mutator.owner_excludes_config','validator.owner_excludes_config'}
 assert {c['id'] for c in historical['checks'] if c['status']=='FAIL'}=={'mutator.owner_excludes_config'}
 initial=read(OUT/'initial-entry-context.json');assert initial['method_code_digest']==_method_digest()
 assert {'runMutators','runValidators','runPipeline'}<={u.get('symbol') for u in initial['initial_candidates']}
 replay=read(OUT/'current-query-replay.json');assert replay['method_code_digest']==_method_digest()
 assert {'runMutators','runValidators'}<={u.get('symbol') for u in replay['records'][0]['candidate_gap_refs']}
 batch=read(RUN/'batch.json');scored=read(SCORE/'cohort.json');assert len(batch['records'])==len(scored['records'])==150
 assert not any(r['status']=='RUNNING' for r in batch['records']);assert scored['pending_count']==0
 assert scored['batch_sha256']==hashlib.sha256((RUN/'batch.json').read_bytes()).hexdigest()
 assert batch['identity']['method_code_digest']==_method_digest() and read(RUN/'implementation-manifest.json')['unchanged']
 selected=read(RUN/'selection.json')['selected_cases'];rows=[];scored_by={r['case_id']:r for r in scored['records']}
 for row in batch['records']:
  if row['case_id'] not in selected:continue
  cid=row['case_id'];path=RUN/'cases'/cid;trace=read(path/'repair.trace.json');candidate=row.get('candidate') or {};score=scored_by[cid]
  if candidate.get('patch_bytes'):
   assert hashlib.sha256(Path(candidate['patch_path']).read_bytes()).hexdigest()==candidate['patch_sha256']==score['patch_sha256']
  fronts=[read(p) for p in sorted((path/'rounds').glob('*/front.trace.json'))]
  observations=[read(p) for p in sorted((path/'rounds').glob('*/public/observation.json'))]
  probes=[read(p) for p in sorted(path.glob('**/method-probes/report.json'))]
  for probe in probes:
   assert all(p['probe_sha256']==probe_sha for p in probe['probes'])
  calls=read(path/'model_calls.json');calls=calls.get('calls',[]) if isinstance(calls,dict) else calls
  execution_calls=[c for c in calls if c['prompt_version']=='topomen-source-execution.v1']
  for call in execution_calls:
   assert call['request_metadata'].get('execution_thinking_policy')=='EXPLICIT_STAGE_CONFIGURATION'
  rows.append({'execution_calls':[{'finish_reason':c.get('response_metadata',{}).get('finish_reason'), 'usage':c.get('response_metadata',{}).get('usage'), 'read_only_support_audit':c['request_metadata'].get('read_only_support_audit',[])} for c in execution_calls], 'case_id':cid,'method_status':row['status'],'independent_status':score['status'],'patch_bytes':candidate.get('patch_bytes',0),
   'patch_sha256':candidate.get('patch_sha256'),'model_calls':trace['budget']['model_calls'],'errors':trace['errors'],
   'front_statuses':[f['status'] for f in fronts],
   'branch_reviews':[f.get('changeset',{}).get('production_path_review',[]) for f in fronts if f.get('changeset')],
   'probe_runs':[{'snapshot_id':p['snapshot_id'],'status':p['status'],'checks':[{k:c[k] for k in ['id','status']} for r in p['probes'] for c in r['checks']]} for p in probes],
   'public_feedback':[{k:read(p).get(k) for k in ['build_state','semantic_outcome','route']} for p in sorted((path/'rounds').glob('*/feedback.json'))]})
 historical_rows=[]
 for version,run,score in [('0.4.8','deepseek-production-path-probe-v1-20261020','deepseek-independent-production-path-probe-v1-score-20261020'),
                            ('0.4.9','deepseek-production-path-probe-v2-20261020','deepseek-independent-production-path-probe-v2-score-20261020'),
                            ('0.4.10','deepseek-production-path-probe-v3-20261020','deepseek-independent-production-path-probe-v3-score-20261020'),
                            ('0.4.11','deepseek-production-path-probe-v4-20261020','deepseek-independent-production-path-probe-v4-score-20261020')]:
  rb=read(ROOT/'outputs'/run/'batch.json');rs=read(ROOT/'outputs'/score/'cohort.json')
  assert rs['pending_count']==0 and len(rs['records'])==150
  assert rs['batch_sha256']==hashlib.sha256((ROOT/'outputs'/run/'batch.json').read_bytes()).hexdigest()
  assert read(ROOT/'outputs'/run/'implementation-manifest.json')['unchanged']
  assert rb['identity']['method_code_digest']==read(OUT/('candidate-method-v'+version+'.json'))['method_code_digest']
  sb={r['case_id']:r for r in rs['records']}
  for r in rb['records']:
   if r['status']=='NOT_RUN':continue
   c=r.get('candidate') or {};sc=sb[r['case_id']]
   if c.get('patch_bytes'):
    assert hashlib.sha256(Path(c['patch_path']).read_bytes()).hexdigest()==c['patch_sha256']==sc['patch_sha256']
   historical_rows.append({'version':version,'case_id':r['case_id'],'method_status':r['status'],'independent_status':sc['status'],'patch_bytes':c.get('patch_bytes',0)})
 ledger_path=Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json');ledger=read(ledger_path)
 used=sum(c.get('charged_micro_cny',c['reserved_micro_cny']) for c in ledger['calls']);limit=ledger['settings']['limit_micro_cny'];assert used<=limit
 money={'limit_cny':limit/1e6,'committed_upper_cny':used/1e6,'remaining_cny':(limit-used)/1e6,'pending_calls':sum(c['status']!='SETTLED' for c in ledger['calls'])}
 helper=read(OUT/'execution-helper-replay.json');assert helper['api_calls']==0 and helper['thinking']=='disabled'
 assert any('func MergeWithInput' in r['excerpt'] and 'kpt-resource-id' in r['excerpt'] for r in json.loads(helper['messages'][1]['content'])['read_only_source_support'])
 routing=read(OUT/'routing-public-replay.json');assert routing['api_calls']==0 and routing['original_feedback']['route']=='CHANGESET' and routing['new_feedback']['route']=='EXECUTE'
 assert routing['original_feedback']['pending_obligations']==routing['new_feedback']['pending_obligations']
 result={'version':'0.4.12','method_code_digest':_method_digest(),'tests':tests,'probe_sha256':probe_sha,'public_probe_checks':len(KPT_CHECKS),
  'broken_probe_failed_checks':2,'historical_v0_4_7_probe_failed_checks':1,'rows':rows,'historical_development_rows':historical_rows,'money':money,'formal_ready':False,'formal150_started':False,'independent_review':{'important':3,'critical':0,'fixes_verified':True,'report':'INDEPENDENT_REVIEW.md'},'execution_helper_replay_api_calls':0,'bound_behavior_routing_replay_api_calls':0,
  'limits':['Inventory is bounded lexical evidence, not complete repository causality','Plan dispositions remain semantic claims beyond deterministic reference checks',
   'One currently supported public probe family; local stand-ins do not prove external container/WASM behavior', 'Candidate and public probe share a process; frozen source and readonly mounts are not tamper-proof result evidence', 'Execution-first routing is a bounded operational preference, not proof of causal failure attribution','Development samples are not general or statistically clear repair-rate evidence']}
 result['scope_complete']=all(r['independent_status']=='PASS' and r['method_status']=='PUBLIC_REVIEW_COMPLETE' for r in rows)
 result['live_failure_route_rule_triggered']=False
 write(OUT/'final-validation.json',result)
 lines=['# 生产路径、跨分支计划与公开行为验证交付（0.4.12）','',
  '先从公开轨迹定位机制，再做失败复现、实现和验证。未读取隐藏断言或Fixed，未派发正式150/消融。',
  '', '## 问题证据与改动','',
  'KPT的validator直接引用NewRunner，mutator通过fnChain间接引用；原一跳过滤漏掉同文件mutator，公开计划因此明确只修validator。新增已连接文件内同查询定位，保留词法/只读边界、范围、Top-N与一次扩展限制。真实旧第一响应零HTTP回放后，最终12片段同时取得runMutators/runValidators。',
  '', '0.4.8/0.4.9真实诊断又暴露初始上下文缺少静态生产依据。0.4.10通过注册探测实际使用的公开Renderer/FunctionRuntime API定位入口声明（限定包、128文件/4MiB），不是提供预设修复位置；入口文件的需求声明/相邻调用最多8项进入原80候选池，不强占最终Top-N。零HTTP首次请求捕获已包含Execute、runPipeline和两个生产分支。',
  '', '同文件、命中公开标识符且共享多个调用名的候选形成有界生产路径清单，明确不是因果图。诊断看到清单，计划逐项说明MODIFY/PRESERVE/OUT_OF_SCOPE/UNRESOLVED；校验防漏项、假修改引用和不存在的开放义务。实际修改仍由原TopoMen执行，具体语义是否正确由行为和独立评价检验。',
  '', '新方法侧公开探测在独立只读运行副本中调用公开Renderer API，使用本地函数替身；同一冻结程序运行于Broken、历史固定补丁及新候选。探测不写入候选补丁、不改变冻结launcher/评分器。9项检查覆盖两类所属函数的配置绑定/输入隔离、mutator后续可见性、最终资源和无configPath对照。',
  '', 'Broken的两个隔离检查FAIL；同程序对历史0.4.7补丁只剩mutator隔离FAIL，其余通过，直接验证探测能解释旧补丁覆盖局限。失败是公开实测，不由隐藏评分推导。',
  '', '公开运行器赋予行为证据类型和固定检查ID；计划预先绑定behavior_check_refs。失败结果不能冒充PASS，其他对照PASS不能替代缺失检查，绑定随待办/保持项跨轮保留；探测编译、超时、格式或环境错误为PROBE_ERROR，不是行为FAIL。原有快照、精确引用和独立评分隔离保持。',
  '',f"工程回归：{tests['passed']}项测试、{tests['subtests_passed']}项子测试通过。程序SHA256：`{probe_sha}`。",'',
  '0.4.11修复独立审查的3项Important：同身份保持/待办/新计划绑定累计合并，冲突边界不判PASS；Broken准备时冻结探测集合，候选改名不能跳过；分支修改用所选单元真实SOURCE_SPAN逐项核对，拒绝包络虚报。先3项失败复现，再回归通过。', '', '0.4.10真实补丁错误使用MergeWithInput并缺失资源ID前提，导致重复资源；反馈确实回EXECUTE但输出全思考截断。0.4.11在原24K只读预算内读取目标直接Go调用的本模块辅助声明和邻接API注释（128文件/4MiB、最多8片段）；真实计划零HTTP回放已包含资源ID前提。仅具体编辑阶段显式关闭thinking，不增加全局调用或语义轮数。', '', '方法冻结程序和候选在同一Go进程，stdout并非防伪证据；只读挂载只保护探测文件，不防恶意候选提前退出并伪造输出。这些是方法侧开发反馈，独立评分仍不可替代。', '', '0.4.11公开候选首轮7/9通过，但低置信度PLAN归因触发重新诊断并校验失败。0.4.12对首轮已改变候选、注册检查确认已接受postcondition/preserve失败的情形，先用原有下一轮执行兑现原意图；保留提交归因与LOW，不增加轮次或权限，也不证明因果归属。真实公开反馈零HTTP回放改变路由而完整保留5项未完成义务；负向回归确保不用于无变更、未知/无绑定、开放项和后续轮次。新增审查层级定义，根trace不再遗漏重新诊断的校验错误。', '', '## 固定版本真实开发结果','', '|案例|方法结果|补丁字节|模型调用|独立结果|','|---|---|---|---|---|']
 for r in rows:lines.append('|'+ '|'.join(str(r[k]) for k in ['case_id','method_status','patch_bytes','model_calls','independent_status'])+'|')
 lines += ['', '历史开发运行单列，不与本版合并计算通过率：', '', '|版本|案例|方法结果|独立结果|', '|---|---|---|---|']
 for r in historical_rows:lines.append('|'+ '|'.join(str(r[k]) for k in ['version','case_id','method_status','independent_status'])+'|')
 lines+=['','逐轮分支处理、探测结果和反馈路由完整保存在final-validation.json及原始批次，不能把计划齐全或构建通过当行为修复成功。本版仅派发2条开发案例，148条NOT_RUN用于保持成员账本，不是正式150结果。',
  '',f"费用累计占用上界{money['committed_upper_cny']:.6f}/{money['limit_cny']:.0f}元，剩余{money['remaining_cny']:.6f}元，历史/不确定预留未重置。",'',
  '本轮两个案例均首轮通过；执行优先路由失败分支由真实公开反馈零HTTP回放及工程回归验证，本次未在真实第二轮触发，不计额外自主修复成功。', '', '## 边界','', '当前仅首个公开探测家族真实贯通，不能声称150条都有同等行为覆盖。词法清单不穷举全部生产分支；模型对相关性和保持语义仍可能误判。局部替身不覆盖远程服务、容器函数或WASM的内部行为。新开发结果须独立解释，小样本和模型波动不能作为普遍显著提升的证明。']
 (OUT/'IMPLEMENTATION_REPORT.md').write_text('\n'.join(lines)+'\n')
 from datetime import datetime
 status=read(ROOT/'CURRENT_STATUS.json');status['as_of']=datetime.now().astimezone().isoformat();status['production_path_behavior_optimization'].update(status='COMPLETED_FOR_BOUNDED_DEVELOPMENT_SCOPE' if result['scope_complete'] else 'ENGINEERING_VERIFIED_DEVELOPMENT_RESULTS_RECORDED',scope_complete=result['scope_complete'],formal_ready=False,actual_results=rows,money=money,report=str(OUT.relative_to(ROOT)/'IMPLEMENTATION_REPORT.md'))
 status['protocol_tests']={**tests,'version':'0.4.12','log':str(OUT.relative_to(ROOT)/'frozen-tests.log')}
 status['real_api_experiment']={'scope':'Bounded development; formal150/ablations deferred','method_version':'0.4.12','rows':rows,'money':money};write(ROOT/'CURRENT_STATUS.json',status)
 print(json.dumps({'rows':rows,'money':money,'tests':tests},ensure_ascii=False))
if __name__=='__main__':main()
