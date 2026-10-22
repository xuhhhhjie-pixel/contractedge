> 2026-10-22最新审查：0.6.27真实ACK最终INVALID_PLAN / NO_CANDIDATE（6次调用），当前不具备正式实验准入。完整后续方案见 docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md。下文为历史记录。

> 最新进度：已冻结 0.6.27，修复诊断到计划的已选源码丢失及计划提示中的前置实现要求；662项测试+22项子测试通过。真实开发效果待验收，上一版ACK为NO_CANDIDATE。详见 [本轮记录](/home/xhj/contractedge-system/outputs/reliability-closure-20261020/DELIVERY_CHECKPOINT_v0627.md)。正式150与B面板仍关闭。

> 最新核查：0.6.26 的 ACK 开发尝试已结束，6 次调用，NEEDS_EVIDENCE / NO_CANDIDATE，未生成补丁。同版仅完成 1/46 个登记尝试，其余 45 未运行；正式150与B面板仍关闭。正确诊断已保留，但选中源码在诊断到计划的交接仍丢失。详见 [故障对齐记录](/home/xhj/contractedge-system/outputs/reliability-closure-20261020/ACK_FAILURE_ALIGNMENT_v0626.json)。不能宣称可靠性交付完成。

> 2026-10-22 最新截点：0.6.26已补通用字段契约短摘录及诊断→计划→原生执行的必要源码送达，659项测试、22项子测试通过；本版真实ACK登记验证进行中。上一版ACK独立SEMANTIC_FAIL，不能以公开构建成功替代行为修复。详见[当前验收说明](/home/xhj/contractedge-system/outputs/reliability-closure-20261020/DELIVERY_CHECKPOINT_v0626.md)。`formal_ready=false`，正式150未启动。

# 当前版本0.6.23：实际续修验收中

646项测试、22项子测试通过。最新真实失败续修已零API重建到原生第二次执行请求，并在原220000预算内保留7项历史失败事实与4段辅助源码。0.6.23原登记KPT续修0候选已独立PASS（4调用、5605字节；34PASS4FAIL→38PASS0FAIL），同版固定A执行中，第二次续修及其他门仍待验收。0.6.22新鲜KPT PASS、保存失败续修SEMANTIC_FAIL均单独保留；formal_ready=false。详见[当前交付截点](DELIVERY_CHECKPOINT_v0623.md)。下述版本内容保留作历史审计。

# 当前版本0.6.22：真实验证进行中

632项测试、22项子测试通过；补齐无异常行为失败的结构化反馈及已确认回退后的单操作推理。KPT新鲜G4已独立PASS（8调用、3574字节、38公开检查PASS）；已登记KPT保存失败续修进行中。0.6.21部分面板与KPT失败完整保留，未拼接旧成功；formal_ready=false。详见[本版交付截点](DELIVERY_CHECKPOINT_v0622.md)。下述各版审计按历史保留。

# 0.6.21能力、证据与交付边界

冻结：`frozen-method-v0.6.21.json`；隔离副本及主工作区613项测试、22项子测试通过。新增共用前提投影，Ready仍独立重算并拒绝删改/伪造。`ready-consumer-native-replay-v0621/summary.json`证明本次保存的两次真实响应到达原生执行请求（48322输入、0API、源码未变），不能替代补丁效果。0.6.20首次HTTP截断、第二次Ready边界失败均保留；0.6.21新鲜KOPF已独立PASS（6调用、1240字节），同版A固定面板执行中，重复/恢复/B待验收。**formal_ready=false**。

|步骤/核心部分|已实现及工程证据|仍需验证或限制|
|---|---|---|
|0—1 数据/Broken|冻结150公开入口、来源、权限、115/18/17划分；构建副作用隔离|入口接通不证明150条都已运行或需求绝无歧义；数据问题另列|
|2 义务身份/谱系|CONTINUE、REANCHOR、SPLIT、MERGE；原义务、检查及拆分残余保留；反例测试|显式谱系不自动证明语义等价或充分拆分|
|3 生产取证|预算内Top12、有界缺口角色/精确符号/本地工厂与辅助函数取证；S0/S1及五类开发请求回放|词法关联不等于完整因果图；未找到、未容纳保持缺口|
|4 关系诊断|操作事实和满足判断一致；失败后部分对象不能证明成功，正常异常和不相关对象反例|仍需当前版KOPF和跨案例真实正确判断；模型因果可能错误|
|5—6 ChangeSet|具体义务/保持/分支/前提绑定；必要源码共用投影连续送达；开放义务只保守下调状态|来源和绑定齐全不保证具体并发算法、就绪条件或资源处理正确|
|7 实际执行|原生TopoMen DefaultAgent、受控原子编辑；真实APPLIED证明支持局部续修；0.6.17将严格Schema接入兼容API消息|Schema并非兼容API原生强制输出；非法输出仍拒绝，模型有效输出率及行为正确性待真实验证|
|8 公开行为|对象/请求/Helm结构化适配；KPT v4 38检查；Alert v3完整JSON、2048公开分组和重载返回立即观察|有限时间采样不覆盖所有瞬间/配置；本地传输不证明部署收敛；超限/能力不足继续未知|
|9 Feedback/恢复|必要失败可证FAIL，充分覆盖才PASS；UNKNOWN缺口明确；唯一当前路由；辅助前提/实际编辑/回滚快照进续修|0.6.18已将登记启动panic与适配器错误区分；回滚后的历史错误正文连续送达，保留原快照，既有轮次可恢复；其他错误仍未知。双仓库真实恢复和同版重复待完成|
|10 独立评价|评分与方法隔离；冻结同版登记全部结果；效果门要求主面板严格提升及B不退化|完整配对面板未完成，不可宣布最终可靠性达标|

0.6.17零API实际回放证据：`execution-schema-native-replay-v0617/summary.json`、`kpt-restored-second-request-capture-v0617.json`。严格消费正反例见 `tests/test_execution_schema_delivery.py`，新增Schema计入最终上下文预算。

Alert v3已在同版Broken与保存0.6.15失败候选校准：期间/立即返回均观察到缺组，最后可见性和唯一性保持PASS；`temporal-v3-calibration/summary.json`。这是方法侧公开反例，不是独立成功。旧v1/v2程序与记录保留。

各历史实现细节和错误修正见 `CAPABILITY_MATRIX-through-v0616.md` 及 `docs/reports/2026-10-22-optimization-execution-checkpoint.md`。实时运行以 `CURRENT_PROGRESS.json` 和各记录文件为准。

0.6.18新增工程证据：`startup-failure-continuity-v0618/summary.json`，普通主循环/保存失败续修都对当前启动失败做有界恢复，不放宽候选晋级；`v0.6.18-root-verification.log`575+22。启动失败识别是已登记公开生产进程边界的有限适配，不是任意语言/任意错误的通用归因器。

0.6.19：已由独立审查复核兼容端默认Schema、保存失败续修声明输入envelope，以及独立计数的1补证/1规划纠错。真实四响应回放将原保持错误送入新请求，89634输入；未降低任何原验证标准。592+22工程测试通过，效果门仍未通过。

2026-10-22：G4完整单条已通过，证据`G4_ACCEPTANCE_v0619.json`。生产取证、诊断、计划、实际编辑与公开观察贯通一次真实独立PASS；此证据不替代同版重复、恢复、原成功回归及B迁移。A组14次登记执行中，B继续关闭。

0.6.20新增：从当前登记操作的精确异常类型/字面消息，在已选Python文件内定位顶层raise及两层本地词法调用，最多4文件/4MiB、6共享片段，选当前消费者相关链，不填充无关调用者；producer要求计划前提审查。方法/嵌套函数同名不能覆盖顶层精确AST范围。局部参数遮蔽仍可能形成词法提示，未解析分派/因果，保留为minor能力边界。范围引用做规范证据绑定，独立缺字段与Schema错误同时送达已有纠错。真实S0/S1回放和五案例请求检查见kopf-closure-replay-v0620/summary.json及cross-case-candidate-request-replay-v0620.json。

2026-10-22 当前补充：0.6.20 KOPF首次槽在首次HTTP响应读取时IncompleteRead（64278字节）结束，未得到完整诊断，0补丁，独立NO_CANDIDATE；不能将其当成修法失败或通过。失败和保守费用预留保留；仅继续原登记repeat=1候选尝试，不覆盖首次槽。完整首次+重复效果门仍未通过，B/正式150保持关闭。见G4_TRANSPORT_FAILURE_v0620.json。

2026-10-22最新：0.6.21新鲜KOPF单条已PUBLIC_REVIEW_COMPLETE/独立PASS，6调用、1语义轮、1240字节补丁；3操作完整成功、3公开后置PASS，源码/harness/补丁身份已核验。见G4_ACCEPTANCE_v0621.json。进入同版固定A，不重跑该首次槽；重复、双仓库恢复与B未验收，formal_ready=false。
