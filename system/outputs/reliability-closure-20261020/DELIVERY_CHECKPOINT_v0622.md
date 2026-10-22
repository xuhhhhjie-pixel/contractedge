# 0.6.22：行为失败反馈与真实修订优化

当前632项测试、22项子测试通过；源码与全部46个预登记槽已冻结。KPT新鲜单条已独立PASS（8调用、3574字节补丁、38/38公开检查PASS），原登记KPT保存失败续修进行中，`formal_ready=false`。正式150与消融未启动。

## 本次为什么继续优化

0.6.21固定A面板已完成10个方法槽，其中9个完整独立评分：RW130、Provider的基线及候选PASS；KOPF候选PASS、基线SEMANTIC_FAIL；KPT基线SEMANTIC_FAIL、候选NO_CANDIDATE；ACK基线PASS。ACK候选已有native聚合PASS，但独立cohort未收尾，不计作完整独立PASS。Alert处于OBSERVING_BROKEN时进程中断，原batch仍RUNNING；未得出模型结果。其余登记成员未运行。所有原始记录和中断状态保留。

KPT发生的是实际行为回退：生成的保留函数从已排除配置的selectionInput里试图找回配置，无法找到已被过滤掉的资源；四项保持检查失败。候选保护两次正确拒绝并恢复Broken，因此导出0字节，没有把坏补丁交给独立评价。第二轮却给出与第一轮逐字相同的补丁。这说明保护机制有效，而反馈指导修法仍不足。

沿实际请求检查：历史失败的文字rationale在场；但紧凑失败任务只保留异常/运行报错及辅助前提冲突。此次render_error为`<nil>`，所以没有生成失败任务，原公开对象中的具体ordinary_inputs、exists与资源清单未进入历史执行请求。原执行策略因只有一个操作且无REQUIRED前提，修订仍关闭推理。

这些是可定位的输入与策略缺口，不等于已经证明其为模型重复输出的唯一原因。不能承诺补齐后每次都能修好。

## 实际改动

|既有部分|改动|边界|
|---|---|---|
|ExecutionFeedback摘要|提取完整、当前快照、摘要相符、JSON身份与FAIL状态一致的公开失败谓词；携带实际observed、公开预期statement、场景、探测摘要、原观察ID|最多8项/8192字节，超限计数明确；不截断JSON，不把有限检查升级为完整义务FAIL/PASS|
|回滚后的历史反馈|沿已有failure_tasks通道保留上述事实；失败试验保持原snapshot，恢复后的当前源码仍是权威|不以旧失败伪装当前源码行为，不清除旧约束|
|既有执行推理策略|在complex_plan策略下，同一当前快照上的已核实回退及相符失败事实可启用推理，即使只有一个修改操作|共同worker的candidate_decision与普通主循环decision均支持；矛盾决定、错快照、不适用策略不触发；原截断回退策略优先|

没有新增Agent、调用图、模型调用或语义轮数；未修改benchmark、探测、独立评分器、公共worker或配置。源码只变动版本号、feedback.py和topomen.py。

## 验证

- 普通行为FAIL且无异常的通用反例先RED，修复后GREEN。
- 错快照、PASS、非行为证据、坏摘要、截断、部分读取、坏JSON对象、检查ID/状态不符均不能成为具体失败事实。
- 8项/字节预算和遗漏数有正反例；保留完整结构而非拼出残缺JSON。
- 单操作回退推理、策略关闭、错快照、共同worker字段、冲突决定均测试；原空推理/截断回退仍有效。
- 保存0.6.21 KPT四个真实响应，经过真正repair_case、原生TopoMen落盘、缓存公开观察快照核验、回退保护、源码恢复，捕获第二个原生执行请求：四项实际失败事实在场，153731输入+16384输出≤220000，源码回到原S0，导出0字节，0API。共同续修worker的另一字段接口也经过原生DefaultAgent捕获。
- 隔离副本632tests+22subtests通过，59.27秒；主工作区632+22通过，56.48秒。

记录：`KPT_FAILURE_ALIGNMENT_v0621.json`、`behavior-revision-v0622-final-green.log`、`kpt-behavior-revision-replay-v0622-final/summary.json`、`v0.6.22-{root,final-candidate}-verification.log`。

源码摘要：`aaea5af48569e078b81834398e2c7f6a0615ce0159c69a9b9e869d093670b36c`。冻结/登记：`frozen-method-v0.6.22.json`、`experiment-registration-v0.6.22.json`。元数据完成在任何本版API调用之前：`FREEZE_PRECALL_VERIFICATION_v0622.json`。

## 下一项实际验收

先从正式公开输入的原始Broken完成本版KPT单条真实诊断、计划、编辑、公开反馈与独立评价。无论首轮成功还是需要修订，均按实际轨迹标明，不能把首轮成功称为恢复机制验证。随后才可进入同一冻结版固定A、登记重复/双仓库续修及B；旧版本PASS不能补本版未运行项。遇到实际失败先定位对应边界，不追加盲目重试。

当前完整性与效果验收仍分开：工程反例已通过，但本版单条、恢复和跨案例效果仍等待真实结果。新增小范围差异为作者自审与实际反例测试，尚无新鲜外部代码审查；既有Python局部遮蔽的词法提示边界仍保留。

本版真实结果更新：G4_ACCEPTANCE_v0622.json记录新鲜KPT独立PASS。完整语义UNKNOWN因必要检查覆盖有限而保留；本条首轮成功，不能称为恢复验证。按RECOVERY_ORDERING_UPDATE_v0622.json，先执行已登记KPT续修0候选，再扩固定A，未改变46个成员或尝试次数。新增逐项派发控制仅作用于实验启动：候选和独立结果失败时不再继续扩派，未收尾输出不得重跑覆盖；其5项零调用控制检查见GUARDED_DISPATCH_VERIFICATION.json。
