"""Read-only evidence collection plus handoff documents; no model/evaluator calls."""
import hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from contractedge.orchestration.batch import _method_digest
OUT=Path(__file__).parent

def read(p):return json.loads(p.read_text())
def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')

def main():
    frozen=read(OUT/'optimized-method.json');assert frozen['method_code_digest']==_method_digest()
    assert all(hashlib.sha256((ROOT/'src'/p).read_bytes()).hexdigest()==h for p,h in frozen['files'].items())
    tests=read(OUT/'tests.json');assert tests['method_code_digest']==_method_digest()
    assert re.search(r'294 passed, 14 subtests passed', (OUT/'final-tests.log').read_text())
    replay=read(OUT/'actual-query-replay.json');assert replay['method_code_digest']==_method_digest()
    assert read(OUT/'capability-audit150.json')['method_code_digest']==_method_digest()
    public_audit=read(OUT/'public-single-audit-v0.4.7.json')
    assert public_audit['status']=='PASS' and public_audit['method_code_digest']==_method_digest()
    rows=[]
    for run,score in [('deepseek-acceptance-recovery-v1-20261020','deepseek-independent-acceptance-recovery-v1-score-20261020')]:
        batch=read(ROOT/'outputs'/run/'batch.json');assert batch['identity']['method_code_digest']==_method_digest()
        assert not any(v['status']=='RUNNING' for v in batch['records'])
        assert read(ROOT/'outputs'/run/'implementation-manifest.json')['unchanged']
        scored=read(ROOT/'outputs'/score/'cohort.json');assert scored['pending_count']==0
        assert len(batch['records'])==len(scored['records'])==150
        assert scored['batch_sha256']==hashlib.sha256((ROOT/'outputs'/run/'batch.json').read_bytes()).hexdigest()
        by_id={v['case_id']:v for v in scored['records']}
        selected=read(ROOT/'outputs'/run/'selection.json')['selected_cases']
        for row in batch['records']:
            if row['case_id'] not in selected:continue
            candidate=row.get('candidate') or {};s=by_id[row['case_id']]
            if candidate.get('patch_bytes'):
                assert hashlib.sha256(Path(candidate['patch_path']).read_bytes()).hexdigest()==candidate['patch_sha256']==s['patch_sha256']
            trace=read(ROOT/'outputs'/run/row['trace']) if row.get('trace') else {}
            fronts=[read(p) for p in sorted((ROOT/'outputs'/run/'cases'/row['case_id']/'rounds').glob('*/front.trace.json'))]
            rows.append({'case_id':row['case_id'],'run':run,'method_status':row['status'],'independent_status':s['status'],
                         'patch_bytes':candidate.get('patch_bytes',0),'model_calls':trace.get('budget',{}).get('model_calls'),
                         'patch_sha256':candidate.get('patch_sha256'),'errors':trace.get('errors',[]),
                         'front_statuses':[f['status'] for f in fronts],
                         'truncation_recovery_calls':sum(c.get('stage')=='PLANNING_TRUNCATION_FORMAT_RETRY' for f in fronts for c in f.get('model_calls',[]))})
    historical=[]
    for version,run,score in [('0.4.5','deepseek-acceptance-single-v2-20261020','deepseek-independent-acceptance-single-v2-score-20261020'),
                      ('0.4.5','deepseek-acceptance-failures-v1-20261020','deepseek-independent-acceptance-failures-v1-score-20261020'),
                      ('0.4.6','deepseek-acceptance-single-v3-20261020','deepseek-independent-acceptance-single-v3-score-20261020'),
                      ('0.4.6','deepseek-acceptance-failures-v2-20261020','deepseek-independent-acceptance-failures-v2-score-20261020')]:
        batch=read(ROOT/'outputs'/run/'batch.json');scored=read(ROOT/'outputs'/score/'cohort.json')
        assert scored['pending_count']==0
        for row in scored['records']:
            if row['method_status']!='NOT_RUN':historical.append({'version':version,'run':run,**row})
    ledger_path=Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json');ledger=read(ledger_path)
    committed=sum(c.get('charged_micro_cny',c['reserved_micro_cny']) for c in ledger['calls']);limit=ledger['settings']['limit_micro_cny'];assert committed<=limit
    money={'limit_cny':limit/1e6,'committed_upper_cny':committed/1e6,'remaining_cny':(limit-committed)/1e6,
           'pending_calls':sum(c['status']!='SETTLED' for c in ledger['calls']),'total_calls':len(ledger['calls']),
           'ledger_sha256':hashlib.sha256(ledger_path.read_bytes()).hexdigest()}
    result={'implementation_version':'0.4.7','method_code_digest':_method_digest(),'tests':tests,'capability_cases':150,
            'actual_query_replay_cases':3,'rows':rows,'historical_versions':historical,'money':money,'formal_ready':False,'formal150_started':False,
            'effectiveness':'Bounded development evidence only; no general or statistically clear repair-rate improvement claim',
            'independent_review':'4 Important findings fixed RED-to-GREEN; subsequent public-run scope/long-body refinements independently reviewed with15 tests+3subtests passing; no Important/Critical findings',
            'limitations':['Semantic entailment and causal correctness are not guaranteed by typed evidence',
                'Field/branch selection is still a semantic planning claim','No new arbitrary runtime probes; unavailable behavior remains UNKNOWN']}
    write(OUT/'final-validation.json',result)
    lines=['# 验收后系统优化交付（0.4.7）','',
      '按用户附件0–10逐步判断后实施，保留原架构、冻结150和独立评分隔离。完整源码、计划、失败及原始观察分别留存。',
      '', '核心改动：公开观察能力/场景/资源及必要字段绑定门禁贯穿复核与反馈；真实目录缺口和跨文件取证、已定位长声明完整正文；计划纠错与取证衔接、可编辑范围前移；原文条件/相关生产分支及实际edit映射。',
      '', '工程验证：294项测试、14项子测试通过；150条公开能力重新核对；3条实际第一诊断响应零HTTP回放保存完整第二请求。源码摘要：`'+_method_digest()+'`。',
      '', '|案例|方法结果|独立结果|补丁字节|调用数|','|---|---|---|---|---|']
    for r in rows:lines.append('|'+ '|'.join(str(r[k]) for k in ['case_id','method_status','independent_status','patch_bytes','model_calls'])+'|')
    lines += ['', '历史0.4.4首次RW130生成1474字节补丁并公开执行成功，但门禁错误拒绝附加总索引；记录保留，未计为公开闭环。0.4.5 RW130公开闭环且独立PASS；KPT在不可编辑thirdparty目标被拒绝后NEEDS_EVIDENCE，ACK长函数证据不足EVIDENCE_GAP，均无候选。随后0.4.6前移编辑权限并恢复已定位声明正文：RW130公开闭环并独立PASS，KPT完整诊断有生产路径校验错误而INVALID_OUTPUT，ACK仍EVIDENCE_GAP，后两条独立NO_CANDIDATE。0.4.7将已返回截断恢复与完整JSON纠错分别限一次，12次调用及费用上限不变。本表仅列0.4.7新运行，不混算历史成功或离线回放。',
      '', f"总账占用上界{money['committed_upper_cny']:.6f}/{money['limit_cny']:.0f}元，剩余{money['remaining_cny']:.6f}元，未结调用{money['pending_calls']}笔继续计入。",
      '', '清理：用户授权后，仅清空其回收站中此前已删除的2502688768字节压缩包，释放约2.50GB；未删除正式数据、源码、评分器或实验记录。清理回执disk-cleanup.json。',
      '', '结果解释：证据接收可信度和实际取证路径已有工程证据；当前样本不足以证明普遍修复率明显提升。即使当前公开义务全部PASS，也只是不确定的方法信号，独立评分另列。没有启动正式150和消融。',
      '', '本次重跑与旧版不是受控消融。是否触发截断恢复在final-validation.json逐案登记，未触发时不能将进展归因于新增恢复策略；模型随机性、候选内容及版本均需区分。',
      '', '明确保留的边界：自然语言义务与分支完整性不能通过字段存在自动证明；结构观察不能证明运行时效果；必要比较的语义相关性仍需审查。未扩建Agent、图数据库或任意测试生成。',
      '', '见COMPLETION_AUDIT.md逐项证据；WORK_LOG.md记录判断与RED→GREEN；independent-review.json记录4项审查发现。']
    (OUT/'IMPLEMENTATION_REPORT.md').write_text('\n'.join(lines)+'\n')
    status=read(ROOT/'CURRENT_STATUS.json');status['implementation_version']='0.4.7';status['acceptance_optimization'].update(status='IMPLEMENTED_AND_BOUNDED_DEVELOPMENT_VALIDATED',formal_ready=False,report=str(OUT.relative_to(ROOT)/'IMPLEMENTATION_REPORT.md'),actual_results=rows,money=money)
    if 'historical_real_api_experiment_pre_acceptance_optimization' not in status:status['historical_real_api_experiment_pre_acceptance_optimization']=status.get('real_api_experiment')
    status['real_api_experiment']={'scope':'Bounded development only; formal150/ablations deferred','method_version':'0.4.7','rows':rows,'money':money}
    status['acceptance_optimization']['method_code_digest']=_method_digest()
    status['acceptance_optimization']['version']='0.4.7'
    write(ROOT/'CURRENT_STATUS.json',status)
    print(json.dumps({'status':'FINAL_EVIDENCE_COLLECTED','rows':rows,'money':money},ensure_ascii=False))

if __name__=='__main__':main()
