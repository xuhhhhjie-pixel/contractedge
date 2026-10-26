# 初始范围正文评分修复审计

2026-10-26，当前候选0.6.50；formal_ready=false。原0.6.49的Cert真实尝试3次调用后EVIDENCE_GAP、0字节补丁、独立SEMANTIC_FAIL；KPT未运行。结果完整保留，不重复登记原槽位。

## 最早断点与最小修复

concept检索先按路径词计分，但路径命中至少两词的文件被排除于正文扫描，相关实现的分数被路径命中数封顶。生成代码经正文扫描获得更多词命中，反而挤出生产实现。改为在原共享文件/字节限额内也扫描路径命中文件，保持低优先级目录策略，移除分数封顶；不增加模块、调用数、Top12或上下文预算。

同一公开Broken快照f2e4cbf0ec9e5bf26dbdbf80abf87b34576609de70958af5a6ab3c8a2572d4ba回放：issuing_controller.go原先不在初始64范围，新版入选；正文联合词分数15，旧路径计分3。扫描1262文件、15042378字节，均低于原2000文件、64MiB。最终实际初诊请求包含生产文件230—309行及准确内容摘要。

这一窗口不含完整PublicKeyMatchesCSR/终止处理分支。它证明初诊前的可用信息改善，尚不能证明完整机制被理解或补丁可靠。下一步需由真实诊断依据已见路径发起必要补证并完成修复，不得用送达记录替代效果。

## 已完成验证

先复现再修复，原RED记录保留。72项针对性回归通过，覆盖Go/Python/TypeScript评分、文件与字节限额、显式路径、重新诊断、计划与执行送达及源码变更后拒绝旧依赖。它们是通用工程反例，不能视作跨仓库修复率。

原始回放记录：CERT_INITIAL_SCOPE_CHECKPOINT.json、cert-initial-capture及scope-cross-stage-green.log。新增模型/评分调用均为0。

## 当前验证边界

v1冻结准备因状态文档摘要先后顺序被校验器拒绝，完整保留该失败文件；没有执行v1实验。v2冻结校验通过，当前完整回归执行中；实际八格、公开沙箱资格和新版本真实效果待验收。不得继承0.6.49的1933+91或历史五条PASS。

资格通过后才允许新登记最小验证：Cert一次；仅独立PASS且公开非FAIL后做预选KPT控制。失败完整保留，再按最早断点分析。原46槽后续、重复、恢复及B仍关闭，正式150与消融未启动。

## 完整工程验证结果

v2完整回归1939项及91项子测试通过（484.70秒），实际隔离演示已开启，回归后冻结校验PASS。证据FULL_ENGINEERING_v050_v2.json与full-v050-v2.log。当前实际八格与公开沙箱资格执行中；尚未产生本版真实修复结果。

## 隔离资格通过，目标真实验证开始

实际八格全部PASS（14会话、28候选/观察容器），公开沙箱PASS，准备与清理、源码完整性及资格后的冻结校验均PASS。资格新增模型调用0。记录ACTUAL8_v050_v2.json及RUNTIME_QUALIFICATION_v050_v2.json。

已新登记唯一Cert目标和条件KPT控制，见V050_DIAGNOSTIC_REGISTRATION.json。Cert当前执行中，尚无本版效果结论；KPT仅在目标独立PASS/公开非FAIL后开放。源码、配置和工具在队列期间不再变更。

## Cert目标独立通过

唯一预登记Cert尝试3次真实调用、2226字节补丁，method PUBLIC_EXECUTION_COMPLETE、独立PASS、公开行为UNKNOWN。此前0.6.49该目标EVIDENCE_GAP/空候选/独立SEMANTIC_FAIL，本次能进入诊断—计划—真实编辑并独立通过。真实首轮请求送达与离线回放一致，诊断引用的3份源码均完整跨越计划与执行，见CERT_ACTUAL_INITIAL_DELIVERY.json、CERT_ACTUAL_STAGE_ALIGNMENT.json、CERT_TARGET_RESULT.json。

这是单次诊断性改善证据，不是固定面板或稳定性成绩。公开仅支持构建，P1与未验证行为继续UNKNOWN。按原预登记条件已开放KPT控制，当前运行中；正式准入仍false。
