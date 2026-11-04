# 冻结15141真实验证的新增发现（进行中）

固定24槽仍在运行，源码、配置和槽不改写；本文件为观察与后续设计依据，不代表新修复或正式就绪。对应原问题2／6／7，不另建一套错误标签。

## KOPF：有原因仍等待未来成功观察

槽4 new_current独立PASS，槽6 old_current独立PASS；槽5 new_large在三次诊断后以EVIDENCE_GAP停下，未编辑。槽4/5初诊选中ID与SOURCE_SPAN正文完全相同，输入预留同为98950。公开观察中的OBS5/6与run identity有自然运行差异，不声称字节完全相同的模型输入，也不能把一次胜负归为扩容的确定性伤害。

失败回复已经给出SUPPORTED局部违反：缺失owner namespace以None传入adjust_namespace，被当成“尚未提供namespace”而再次猜owner并抛错；但gap又要求post-repair成功观察／尚未新增的区分接口。属于诊断对阶段边界的判断问题，不是已证实的截断。不得自动改成READY、删除未知端点、增加case例外或无限补查。

## Helm：两种不同的失败必须分开

槽7 new_large：三次诊断，无PUBLIC_DEPENDENCY_DECLARATION送达。首诊已经有包含registry.NewClient的完整NewClient正文。前两次gap指向registry.ClientOpt一类不存在于所选正文的精确名称；source/dependency.py::gap_dependency_support只接受查询与所选正文精确相交的限定符号，因此未进入依赖读取。第三次才提到实际registry.NewClient，此时两次取材已用完。槽8 old_current亦无API送达并失败。这说明旧四函数／三API材料探针只证明指定查询路径可用，尚未证明新模型各种合理查问都可用。

槽9 new_current：第二、三次请求实际包含原有三条完整只读API声明（PUBLICDEP-913e1faf1d41b9ab、PUBLICDEP-e5014cf70c19127b、PUBLICDEP-2130468171470b88），回复仍声称缺少plain-HTTP选项。不能把这一失败再记为API未发送。

代码接口不一致：diagnosis/prompt.py的SYSTEM_PROMPT列出的允许材料仅含requirement、observations、evidence_catalog、allowed_evidence_ids、selected candidates；新API实际上仅在adopted_source_context.records中，且被有意排除在因果evidence IDs之外。只读参考与可引用因果证据应区分，但不能让只读材料已发送却在消费者说明中没有明确用途。此次源码检查证明存在说明缺口，尚未证明它是模型失败的唯一原因。

## 完成固定面板后如何处理

1. 先完成冻结24槽与预选公开配对，保留代码／容量各自胜负、阶段停止与成本，不能按失败更换当前版本或样本。
2. 若需要后继修复，用独立继承版本收窄在“依赖取材—可用只读参考”连接，不动三核心分工与既有预算。
3. 检索候选方向：当查询明确指向已选代码实际使用的导入别名，但未知API名尚不存在于调用点时，考虑以真实调用点／固定导入版本作为有界读取入口；保留import binding、shadowing、版本、完整扫描与4条／24000字节限制。不能让纯词法名称制造必须送达的证据，也不能允许模型任意选择外部包。具体候选排序与工厂返回类型的关联要先做通用正反例，不能硬编码Helm或PlainHTTP。
4. 消费候选方向：在现有字段上澄清只读参考可帮助理解API／规划选项，而因果evidence_refs和编辑权限仍受原校验。先用同一份已准备材料对照核查接口说明的影响；不把新增提示语本身当作效果证明，也不增添第二目录／标签／账本。
5. KOPF未来观察问题按已有Diagnosis/ChangeSet职责继续分析，不能仅凭查询词出现就自动判断缺口已关闭。SUPPORTED局部原因不必然说明所有目标均可行动，保留真实未知与有界纠错。

这些是现有真实失败的可核查定位，尚未在冻结15141上修改。正式就绪仍为false。

## KPT4673：保留同容量退步

旧版原容量通过，新版原容量以EVIDENCE_GAP停止，新版扩容通过。旧／新原容量均选中10条；新版本把runfn声明从2554字节补全到2973字节，身份随正文改变，排序由第5变为第3。其余选中记录相同，没有证据证明关键生产函数被挤掉。新版扩容额外增加两条文档，不能用这一成功覆盖新版同容量的退步，也不能据一次请求判断容量具有确定性因果效果。

## CloudWatch：已有函数定位没有回答缺失操作的查询

三组均在编辑前停下。新版原容量的gap同时给出已有函数newUpdateRequestPayload/sdkFind和搜索词TagResource/ListTagsForResource；locate_gap_roles采用symbol优先，collect_absence_search_evidence只对准确path/symbol生成库存检索事实。实际请求包含的完整搜索证明这些已有函数存在，没有生成标签操作缺失的完整库存搜索。

这说明“返回了材料”与“回答了缺口”有区别；仍不证明需要新增实现，更不能让一个文件没有某段文本自动证明全仓库缺少一个语义角色。继续使用已有有界查询与证据接口审查，不能靠自动READY、扩大所有权限或编造缺失事实绕过。

## Certmanager：同容量出现一例新增通过

旧版原容量以EVIDENCE_GAP停在编辑前，新版原容量经过Diagnosis、ChangeSet、TopoMen，独立评分PASS。实际补丁只在CNAME查询的SERVFAIL分支返回错误，保留原有其他分支。公开执行完成，但有一项内部调用连接没有直接公开观察，因此公共语义投影仍为UNKNOWN；这不改变独立评分PASS，也不能把两种状态合成一个PASS。

公开复核的首次回复因8192输出token限额被截断，其中7748用于reasoning；现有一次有界纠错取得完整合法回复。记录保留原截断，不把它说成输入证据又被删除。此案表明同容量候选能够推进并成功修复，尚不能把收益归到整组修改中的某一行代码。

## 本次进度快照

补充核对：Certmanager 扩容组也已结束，结果为编辑前 EVIDENCE_GAP。该组交付了33条源码记录，但没有 `lookupCNAMETarget`；新版原容量的成功组只有16条，却包含该函数的582字节正文。不能用材料总量代替关键材料到位。

在不调用模型、不改生产源码的回放中，已重建扩容组相同的初选ID和第二次取材查询。扫描审计一致：原40条候选池没有该函数；诊断用的200条上限得到106条候选，原40条是其前缀，函数在第48位。这确认了 `source/units.py::extract_gap_extension_units` 在最终相关性排序前，按跨文件交错顺序截取候选池，能够挤掉同一缺口明确点名的声明。后继修改应在原有额度内优先保留同路径范围下明确点名的声明，再分配普通匹配位置；仍须检查同名歧义、其他缺口、公平分配和最终送达。200条探针仅用于定位，不是正式扩容方案或模型收益。

Alertmanager 槽22（新版原容量）已结束：完成一次编辑、公开构建通过，但目标检查失败后进入重诊；第二轮 ChangeSet 最终因 `PRECONDITION_REVIEW_INCOMPLETE` 和 `BEHAVIOR_CHECK_SCENARIO_MISMATCH` 停止，独立状态为 METHOD_FAILURE。模型把实际 `HELPER-…` 前提写成 `SRC-CU-HELPER-…`，且条件绑定与声明场景不一致。这是已保存的失败，不能当作补丁已通过，也不能在其他两组尚未结束时宣称是新版独有退步。

22/24槽已完成：旧版原容量3/7、新版原容量3/8、新版扩容2/7通过；剩余两槽尚未完成，不能当作最终八例成绩。当前完成配对的代码对照为1例新增通过（Certmanager）、1例退步（KPT4673），总体暂无净增；容量对照为1例改善（KPT4673）、2例退步（KOPF、Certmanager）。Alertmanager旧版与扩容组，以及预选的额外四案例配对尚待完成。`formal_ready=false`。

工程检查已完成（3384项通过、4项跳过、90个子测试；原生9项；六组公开流程模拟），真实失败不会被工程通过覆盖。源码与配置保持冻结；主分支和已安装系统没有更新。外层会话中断后对原槽20进行了一次有据收尾，未重复模型请求，详见[调度中断与收尾](EXPERIMENT_SUPERVISION.md)。
