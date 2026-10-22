> 2026-10-22最新审查：0.6.27真实ACK最终INVALID_PLAN / NO_CANDIDATE（6次调用），当前不具备正式实验准入。完整后续方案见 docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md。下文为历史记录。

> 最新进度：已冻结 0.6.27，修复诊断到计划的已选源码丢失及计划提示中的前置实现要求；662项测试+22项子测试通过。真实开发效果待验收，上一版ACK为NO_CANDIDATE。详见 [本轮记录](/home/xhj/contractedge-system/outputs/reliability-closure-20261020/DELIVERY_CHECKPOINT_v0627.md)。正式150与B面板仍关闭。

> 最新核查：0.6.26 的 ACK 开发尝试已结束，6 次调用，NEEDS_EVIDENCE / NO_CANDIDATE，未生成补丁。同版仅完成 1/46 个登记尝试，其余 45 未运行；正式150与B面板仍关闭。正确诊断已保留，但选中源码在诊断到计划的交接仍丢失。详见 [故障对齐记录](/home/xhj/contractedge-system/outputs/reliability-closure-20261020/ACK_FAILURE_ALIGNMENT_v0626.json)。不能宣称可靠性交付完成。

> 2026-10-22 最新截点：0.6.26已补通用字段契约短摘录及诊断→计划→原生执行的必要源码送达，659项测试、22项子测试通过；本版真实ACK登记验证进行中。上一版ACK独立SEMANTIC_FAIL，不能以公开构建成功替代行为修复。详见[当前验收说明](/home/xhj/contractedge-system/outputs/reliability-closure-20261020/DELIVERY_CHECKPOINT_v0626.md)。`formal_ready=false`，正式150未启动。

# 当前版本0.6.23：实际续修验收中

646项测试、22项子测试通过。最新真实失败续修已零API重建到原生第二次执行请求，并在原220000预算内保留7项历史失败事实与4段辅助源码。0.6.23原登记KPT续修0候选已独立PASS（4调用、5605字节；34PASS4FAIL→38PASS0FAIL），同版固定A执行中，第二次续修及其他门仍待验收。0.6.22新鲜KPT PASS、保存失败续修SEMANTIC_FAIL均单独保留；formal_ready=false。详见[当前交付截点](DELIVERY_CHECKPOINT_v0623.md)。下述版本内容保留作历史审计。

# 当前版本0.6.22：真实验证进行中

632项测试、22项子测试通过；补齐无异常行为失败的结构化反馈及已确认回退后的单操作推理。KPT新鲜G4已独立PASS（8调用、3574字节、38公开检查PASS）；已登记KPT保存失败续修进行中。0.6.21部分面板与KPT失败完整保留，未拼接旧成功；formal_ready=false。详见[本版交付截点](DELIVERY_CHECKPOINT_v0622.md)。下述各版审计按历史保留。

# 可靠性补齐：当前验收记录

当前冻结候选 **0.6.21**，源码摘要 `057ba0f8b51d832e70d820647936226a8bd5f0a9c9ab8e16fddde5629b28dcb2`。隔离副本与根工作区均 **613项测试、22项子测试通过**，根日志 `v0.6.21-root-verification.log`。**最终交付尚未通过，formal_ready=false，正式150尚未启动。** 当前KOPF单条已独立PASS（6调用、1240字节），同版A固定面板执行中；下述各旧版结果按版本保留。

0.6.17保存计划分量已结束：合法编辑落盘、编译通过，但生产启动panic，不是修复成功。0.6.18真实单条已结束INVALID_PLAN/独立NO_CANDIDATE。0.6.19正式输入路径的G4端到端验证已独立PASS：`runs/delivery-0.6.19-A-RW-ALERTMANAGER-4705-0-candidate`。同版G5固定A组已启动，B仍关闭；当前实时状态以 `CURRENT_PROGRESS.json`、分量状态和各次 `batch.json` 为准。

## 本次实际修正

0.6.16真实诊断和计划已表达“替代对象就绪后再发布”，但执行首次正文为空，唯一格式纠错返回非法 `type` 字段和双重转义源码；0编辑，独立 `NO_CANDIDATE`。

回放发现：执行器构造的严格Schema只放在ModelRequest属性，而兼容API只发送通用JSON模式；实际模型消息没有Schema。现按已有schema_in_prompt开关把同一Schema送入执行消息，并明确源码只做一次JSON转义。严格消费器、原子编辑、权限与快照约束不变；不自动删除非法字段或猜测反转义。

验证：

- HTTP边界反例先失败（缺output_schema），修正后通过；原生Schema模式不重复。
- 真实多行和制表符编辑可落盘，双重转义锚点和非法字段仍拒绝。
- 保存0.6.16两次真实前半链响应，经完整repair_case、Ready消费及实际TopoMen到执行请求：Schema在场，S0不变，导出0字节。见 `execution-schema-native-replay-v0617/summary.json`。
- KPT保存的真实失败续修：202771输入+16384输出≤220000，Merge源码与历史前提冲突保留。见 `kpt-restored-second-request-capture-v0617.json`。

这些是传递与消费边界证据，尚不证明模型输出或修复成功率提升。

## 本轮各版真实结果

|版本|方法结果|独立结果|直接定位|
|---|---|---|---|
|0.6.11|ROUND_LIMIT|BUILD_FAIL|生成了不存在的辅助API/类型|
|0.6.12|BUDGET_EXHAUSTED，0补丁|NO_CANDIDATE|实际必需源码被软配额挤掉，非费用不足|
|0.6.13|消费端拒绝，0补丁|NO_CANDIDATE|Ready端维护旧源码投影|
|0.6.14|NEEDS_EVIDENCE，0补丁|NO_CANDIDATE|精确符号被词频/大小写匹配抢占|
|0.6.15|4调用，有补丁；公开3PASS，语义UNKNOWN|SEMANTIC_FAIL|采样漏检短暂缺组，不能称为修复通过|
|0.6.16|4调用，INVALID_OUTPUT，0补丁|NO_CANDIDATE|执行Schema未传至兼容API消息|
|0.6.17|保存计划分量：1调用、合法编辑、编译通过、启动panic|未运行|分量非端到端、非严格单因素，不计修复通过|
|0.6.18|4调用，补证后首个计划INVALID_PLAN，0补丁|NO_CANDIDATE|原循环把补证占为一次纠错位置，未送出PRESERVE_CONSTRAINT_MISSING纠错|
|0.6.19|8调用，3163字节补丁，构建与4项公开行为检查PASS；语义UNKNOWN|PASS|完整真实链路通过单条验收；同版跨案例与恢复仍待验证|

全部失败、原始输出、版本冻结和独立聚合保留；不拼接最好结果。

## 原方案要求与剩余验收

显式义务谱系、操作异常一致性、有界路径补证和跨阶段送达、计划前提审查、辅助函数前提检查、候选保护回退、结构化观察及反馈路由已有实现和工程反例。具体能力边界见 `CAPABILITY_MATRIX.md`，历史详细记录保留在 `COMPLETION_AUDIT-through-v0616.md`。

但工程反例不能代替真实修复：原7条A组、10条固定B组各版本配对；KOPF/Alert重复；KPT/Alert双仓库保存失败续修，仍需同版完成。共46次登记运行，名单、预算、模型、Top12、12调用、2语义轮不改变。B仍关闭，旧版成功不补本版分母。

最终效果判定使用 `scripts/assess_reliability_delivery_v2.py`：全部原行为门、原成功保持、必要恢复和重复验收，同时主面板独立PASS严格多于基线，B不低于基线。持平、只有恢复提升、单条通过或测试数量增加，都不能授予正式交付。

冻结与登记：`frozen-method-v0.6.19.json`、`experiment-registration-v0.6.19.json`。累计API上限150元；实时占用来自共享账本，不从历史嵌入报告读取。

执行分量输入核对：脚本漏传原请求的declared_artifacts；与旧请求实际差异是edit_contract、output_schema及declared_artifacts。保存了 input-difference-audit.json，不作严格单因素因果结论，不计首次端到端/面板结果。正式worker原输入路径不变。

## 0.6.18：启动失败与历史错误传递

原公开launcher只做构建，编译PASS而方法探测启动panic时，整体仍报EXECUTION_PASS。现仅对已登记程序、精确程序摘要、当前快照、完整非超时日志、Go模块及当前源码行全部匹配的启动panic，记录PRODUCTION_PROCESS_START失败并返回EXECUTION_FAILED。行为检查仍未运行，不伪造谓词FAIL或整体语义结论；其他探测错误保持未知。

原生请求零API回放证明：旧STOP变为EXECUTE，语义仍UNKNOWN；失败候选和恢复后的原候选请求都包含真实堆栈、对应源码及4份声明输入，失败快照不冒充当前快照。输入分别83069/68177，均在原预算内。见 `startup-failure-continuity-v0618/summary.json`。

回滚时保持条件仍为UNCOMPARABLE，不晋级、不改为FAIL。仅已核实的本次候选启动失败可以使用剩余既有修订轮；未分类错误、过期证据、轮次耗尽继续停止。普通repair_case和v7保存失败续修使用同一事实/诊断边界，历史基线不支持的可选API不传入。共同续修协议v5，额度仍为两轮/12调用/一次格式纠错。

独立只读审查已完成并复核发现的契约问题；审查记录 `CODE_REVIEW_v0618_v0619.md`。该工程修正不替代固定面板与真实恢复效果验收。

## 0.6.19：审查发现与真实规划停止边界

兼容API省略schema_in_prompt也默认送达严格Schema；原生模式仍用原生Schema通道。保存失败续修v8将同一可信declared_artifacts和能力送到评审、起始反馈、后续反馈和保持选择，缺输入仍UNKNOWN，模型字段不能代替可信输入。四项声明比较正反例也在原0.5.5基线上通过。

0.6.18合法补证后，首次新计划的保持摘要错误被正确发现，但原range(2)循环没有留下一次格式纠错。现补证尝试最多一次，规划解析和契约错误共用配置的一次纠错；补证不充值、不消费该纠错。空补证/选择失败也不能重复搜索；同一错误连续出现、额度0、总调用耗尽均停止。12项正反例和独立审查通过。

实际四次响应回放到达此前缺失的第五次规划纠错请求：89634输入、retry index1，错误确实进入请求，S0不变、导出0字节；见 `planning-correction-saved-replay-v0619/summary.json`。这不是模型已经纠正或真实补丁通过的证据。当前0.6.19G4为新鲜运行，各版结果不拼接。

## 2026-10-22 G4验收与同版A组启动

`G4_ACCEPTANCE_v0619.json`核验冻结源码、配置、共同harness、实际补丁、trace与独立评分摘要。新鲜完整单条在20次有效重载、2048分组下，267次期间采样和20次返回立即采样均未缺组，最终可见性和唯一性通过；独立评分PASS。方法仍保留P1覆盖不足和开放义务UNKNOWN，不升级为完整语义证明。

固定A组按原登记7案例×基线/候选共14次，已完成的Alert候选直接计入，不再新增同名调用。评分进程独立于方法；源码和harness保持冻结，未开B、未开正式150。一次单条成功不归因到单项修改；原0.6.18的规划纠错机制收益仍以实际保存轨迹回放证明，不能用新运行作单因素推断。

实际请求核对：`G4_STAGE_EVIDENCE_v0619.json`保存初诊、首计划、补证重诊、最终计划和两次执行请求的源码位置、正文摘要及公开S0匹配；各阶段所核片段均匹配。实际计划明确等待初始订阅消费完成再停旧对象、发布新对象；此运行没有第二轮语义编辑，不能用它代替真实失败恢复验收。

## 原文工作包逐项对照（冻结0.6.19）

以下实现证据对应本版未改动的592+22测试记录；真实效果一栏只引用本版结果，不用旧版成功补空项。

|原方案部分|本版实现与反例位置|真实效果验收状态|
|---|---|---|
|A：完整公开观察和类型边界|`execution/observations.py`、`public.py`、`support.py`；`test_structured_observation_adapters.py`、`test_observation_capability_gate.py`覆盖完整对象、生产者、场景、截断和不足能力|Alertmanager完整观察已实际进入检查与评审；RW130/KOPF/Provider同版面板待完成|
|B：来源、稳定身份与显式谱系|`requirement/normalize.py`、`lineage.py`；`test_explicit_obligation_lineage.py`覆盖REANCHOR、SPLIT、MERGE、循环、残余、旧检查和语义争议保留；不自动证明语义等价|G4计划保留稳定义务键和未验证残余；真正跨轮恢复由登记恢复组验证|
|C：生产路径、保持影响与必要前提|`retrieval/gap_delivery.py`、`execution/helper_context.py`、`changeset/validate.py`；`test_exact_gap_queries.py`、`test_plan_precondition_coverage.py`、`test_gap_delivery.py`含旧快照、误匹配与容量反例|Alertmanager实际各阶段源码核对通过，计划明确初始数据就绪条件，真实补丁独立PASS；ACK/KPT等跨案例待完成|
|D：必要/充分检查、可行动反馈与候选保护|`execution/comparison.py`、`orchestration/feedback.py`、`candidate_guard.py`、`repair.py`；`test_check_coverage.py`、`test_candidate_regression_guard.py`、`test_feedback_context.py`覆盖错误路由、回滚、声明输入与快照连续性|G4所有有限检查PASS但完整义务仍UNKNOWN，未伪造充分证明；真实失败恢复和保持保护效果待本版恢复组验证|
|E：有限行为探测及固定面板|冻结KPT v4与Alert v3；校准记录`temporal-v3-calibration/summary.json`；`test_probe_source_entrypoints.py`、`test_path_probe_review_regressions.py`|Alert v3在Broken及保存坏补丁均揭示缺组，本版真实补丁全部4检查PASS且独立PASS；A/重复/双仓库恢复/B仍按登记验收|

附加的执行Schema、可信声明输入和独立规划纠错计数均属于原传递契约补齐，没有新增分析Agent或增加语义修订额度。工程反例通过只说明对应约束生效，不代替该列尚未完成的真实效果门。当前不授予正式实验准入。

## 2026-10-22 A组发现与派发暂停

0.6.19已完成：RW130两侧PASS；KPT候选PASS、基线NO_CANDIDATE；KOPF两侧SEMANTIC_FAIL；Alert候选PASS（G4同次）。Provider基线允许收尾，其他未运行项仍保留。派发父进程暂停，收据dispatch-hold-v0619-kopf.json；B及正式150未开。

KOPF首轮公开失败被如实表达，下一轮操作事实和当前S1正文连续送达；但该轮首响应同时有多余字段和UNRELATED缺范围引用，仅前者进入唯一纠错，后者在纠错后才触发。更早的首轮诊断缺少实际异常生产者_guess_owner和adopt调用者，计划只改了错误发生之后的分支。新的隔离修正通过精确异常字面量及当前相关调用链补证，并将独立操作字段问题纳入既有预检查；不放松异常或引用标准，不新增重试。

保存输入回放kopf-closure-replay-v0620/summary.json已经证明初诊86493、重诊109476输入均在120000以内，三个必要源码片段及异常前提送达；首计划传输52741、执行传输31542。后两项使用保存计划的传输夹具，不冒充新模型计划或Ready验收；所有源码未更改，0新增API。staged0.6.20尚未冻结或效果验收，当前不能交付。

## 0.6.20当前冻结与真实验证

冻结收据`frozen-method-v0.6.20.json`，登记`experiment-registration-v0.6.20.json`。源码只变动5个文件；共同v8worker、配置v6、Top12、一次补证、12调用和两轮语义修订保持原设置。KOPF作为本次G4同一已知失败机制，从原始Broken新鲜运行，仍对应登记A组首次候选槽；不是保存失败续修。独立评分单独运行，未打开同版G5、B或正式150。

独立审查的重要同名声明错绑已以反例修正；`CODE_REVIEW_v0620.md`保留局部名称遮蔽导致词法提示误选的minor限制，不能宣称静态因果或完整名称解析。源取证3个必要片段均按当前快照送达；实际模型是否选对修法仍需当前真实结果。

**登记时序偏差：**元数据脚本误把继承的development_changes字符串当作列表，失败后shell仍启动worker。物理源码副本和608+22检查此前已完成；1个请求开始后暂停worker，在未观察首响应时补齐登记。源文件创建时间、实际批次方法摘要和配置/共同harness均核对不变，随后同一worker恢复，没有重发请求、改源码或按结果换案例。详见`freeze-setup-hold-v0620.json`。本次作为有此明确行政时序偏差的冻结开发验证保留，不能声称元数据在首调用前已写完。

## 0.6.20首次传输失败与登记重复

首次KOPF运行已结束INFRASTRUCTURE_ERROR，首响应读取IncompleteRead（64278字节），模型未交付可解析完整诊断、无补丁，独立NO_CANDIDATE。实际请求86493输入，必要源码三片段已送达；传输失败的上游原因尚无服务端证据，不能归为已证实的模型或诊断缺陷。保留不确定费用预留和元数据时序偏差。冻结源码、配置及共同harness重新核验一致，只继续已有repeat=1候选槽作为机制验证；无额外补跑槽，不替换首次结果。即使重复成功，原要求首次+重复均通过仍不成立；不得据此宣布全版验收通过。收据G4_TRANSPORT_FAILURE_v0620.json，当前重复真实结果等待中。

## 0.6.21前提投影消费边界修正

0.6.20登记重复获得了有源码依据的诊断与计划，仍因Ready消费端按旧生命周期说明重建异常前提而止步，2调用、0补丁、独立NO_CANDIDATE。具体记录G4_READY_CONSUMER_FAILURE_v0620.json；这不是外部网络失败，也不是已产生补丁后的语义失败。

本版在既有runner中共用required_source_prerequisites，规划生产与Ready消费都调用它；Ready仍重读当前源码并独立核对，删除、改写说明或伪造来源继续拒绝。没有跳过校验，也没有增加模块或模型额度。真实保存Ready反例RED→GREEN，增加通用混合角色/无前提对照；全套613+22通过。两次保存的真实响应经过完整repair_case、Ready和原生DefaultAgent捕获实际执行请求，3函数正文及严格Schema在场，48322输入，0API，0源码改动、0补丁；见ready-consumer-native-replay-v0621/summary.json。此前只做传输夹具遗漏消费校验，已作为工作方式缺口纠正。

本版元数据与全部46登记在任何新worker之前写完并核验，FREEZE_PRECALL_VERIFICATION_v0621.json留证；G4新鲜KOPF运行中，其他门仍按原要求。小范围代码差异由作者复核和红绿测试验收，未另做新鲜外部审查；0.6.20审查的局部名称遮蔽minor边界继续保留。

2026-10-22最新：0.6.21新鲜KOPF单条已PUBLIC_REVIEW_COMPLETE/独立PASS，6调用、1语义轮、1240字节补丁；3操作完整成功、3公开后置PASS，源码/harness/补丁身份已核验。见G4_ACCEPTANCE_v0621.json。进入同版固定A，不重跑该首次槽；重复、双仓库恢复与B未验收，formal_ready=false。
