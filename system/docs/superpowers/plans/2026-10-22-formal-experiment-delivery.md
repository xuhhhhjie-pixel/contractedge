# ContractEdge 正式实验准入与最终优化实施计划

> 2026-10-24执行入口：当前冻结0.6.36（847+22完整测试、814+17可移植子集）。下文0.6.27是制定计划时的基线，不是当前源码。当前进展、失败与剩余项以`CURRENT_STATUS.json`及`docs/reports/2026-10-24-feedback-context-admission.md`为准；任务与G0—G6退出条件不变，不因工程局部通过而降低。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户已授权执行普通修复；不得重复要求批准，不得擅自改变验收口径。

**Goal:** 在冻结 benchmark150 上，交付可迁移、可复现、通过既定开发效果验收的系统，再开放正式实验。

**Architecture:** 保留0—10阶段及关系诊断、Semantic ChangeSet、ExecutionFeedback三个核心。围绕最早证据丢失或无依据判断作最小修复；先离线回放和反例验证，再冻结真实开发验证。保持有界取证，不增加独立分析Agent、通用调用图或无限重试。

**Tech Stack:** Python 3.10+、JSON Schema、pytest、原生TopoMen DefaultAgent、Docker公开运行器、隔离独立评分。

**Spec:** `docs/reports/2026-10-20-final-optimization-design.md`、`docs/reports/2026-10-20-reliability-closure-design.md`、`docs/reports/2026-10-21-optimization-reset-and-delivery.md`。本文件是2026-10-22现状审查和后续执行入口；旧方案要求继续有效，历史“已完成”只按其明确范围解释。

## 当前结论

当前运行源码0.6.27，冻结摘要 `362780da39f585408b4b725a6cd72f08c6d66caaf8ef52b27d0086d466e80d22`。工程回归662项及22项子测试通过。**修复可靠性未验收，formal_ready=false，不能直接称为正式实验完成版。**

最新ACK真实尝试6次调用，最终INVALID_PLAN、独立NO_CANDIDATE、无补丁。日志中的最后错误是`PRESERVATION_IMPACT_INVALID`：无修改时仍保留不合法的CHANGES影响关联。此前两次计划同时输出NEEDS_EVIDENCE和执行动作，违反状态契约。补证后仍提出SDK操作绑定和当前标签读取缺口。不能仅改成合法JSON就宣称修好了运行时行为。

0.6.26已经证明诊断保留了“更新接口忽略Tags”的源码契约，但最终计划只得到12段已选源码中的3段。0.6.27真实旧轨迹零调用回放将其恢复为12/12，客户端类型定义得以保留。执行输入投影测得非目标源码29506字节，超过旧局部24000额度；新配置将该额度调为48000，完整执行请求加预留输出122408，小于220000总上下文。这是信息送达改善的证据，当前真实失败说明仍不足以保证正确计划与补丁。

## Global Constraints

- 正式数据入口：ContractEdge-benchmark150-v1-20261019，150条；development115、validation18、blind17。正式150及消融尚不执行。
- 公开manifest SHA256：469d2aea9ee8d7d9b00cddcbc7cdbb2816eb27b7b23f3cde92ec3032848f5492；划分SHA256：aa32b07dc8cd2f3c946cb54e42385bded2ccbdf3cdd31b2b358a999480146aea。
- Top12、一次补证、12次模型调用、2轮修复、初诊120000、整体上下文220000、输出16384；局部只读源码48000字节。不得以放宽PASS门取得表面提升。
- 本会话累计API上限150元，包含历史和不确定预留。合作方使用自己的密钥及账本，不继承服务器账本路径。
- 方法只读公开数据和当前允许源码；隐藏评分、GT、Fixed和他人成功补丁不能进入修复输入或调参规则。
- 每个方法版本冻结代码、配置、提示、适配器、探测与执行协议。失败/无候选/未运行均保留；不合并多版最佳结果，不重复失败登记槽位直到通过。
- 检查未知就保留UNKNOWN；公开构建通过不等于外部行为通过，有限时序采样不证明所有时刻成立。

## 逐阶段审查

|步骤|当前实现与已验证内容|仍需完成的验收|
|---|---|---|
|0 数据准备|dataset/benchmark.py、admission.py：150入口适配、来源/范围/预算和development准入|干净克隆下恢复数据入口与路径；每案公开能力与资源要求可重建|
|1 输入/Broken|execution/public.py、observations.py：原始输出、结构化对象、场景/快照/异常保留|保持部分对象与操作成功的区别；外部观察边界不可扩大|
|2 Requirement|requirement/normalize.py、lineage.py：子句锚点、稳定身份、CONTINUE/REANCHOR/SPLIT/MERGE|真实跨轮产生的谱系与剩余谓词审计；同义不能靠词面自动证明；子义务PASS不自动证明父义务|
|3 检索|source/units.py、retrieval/gap_delivery.py：有界扩展、重复降权、端点送达|精确路径提示错误、外部依赖、状态读取链、同名符号的通用处理；词法找到不代表因果支持|
|4 Diagnosis|diagnosis/validate.py、operation_consistency.py：源码/操作一致性及支持分级|ACK等必须解释输入复制与消费效果的区别；公开观察不足不能自动撤销有依据静态诊断|
|5 ChangeSet|changeset/prompt.py、validate.py：原子义务、分支、保持项、前提与检查覆盖|NEEDS_EVIDENCE状态与动作/影响关联保持一致；明确本地待实现helper与外部已有契约缺口|
|6 Ready|orchestration/repair.py：独立重算当前来源、支持和计划校验|补证到计划、执行、重新诊断都保持同一必要事实；容量不足在付费执行前发现|
|7 执行|execution/topomen_agent.py：调用真实TopoMen DefaultAgent生成受控源码编辑|此处是源码修复消费者，不是旧部署调度器全功能；实际helper使用前提由源码复核与行为探测确认|
|8 公开检查|小型适配器、KPT v4、Alertmanager v3公开探测|保持Broken/候选同版探测；跨仓机制有效性及外部API任务的有限可观察性|
|9 Feedback|feedback.py、candidate_guard.py：失败事实、路由、快照、回滚和候选晋级|真实失败修订能改善行为，不能只有反馈文件；原PASS保持项不可被新增PASS数量抵消|
|10 独立评价|冻结评分器独立进程、固定登记和完整分母|同一版本全部规定开发验收、恢复、迁移、成本与未运行统计；禁止历史混分|

三个核心的交付证据必须是：诊断改变了可支持的违反及补证内容；ChangeSet改变了允许编辑和所需检查；反馈改变了下一轮输入及候选晋级。三份JSON存在不构成效果证据。

## 历史结果如何使用

0.5.5固定7条有5个独立PASS、2条无候选，是历史基线。0.6.19 Alertmanager出现真实独立PASS；0.6.23固定A已观察RW130/KPT/KOPF/Provider候选PASS、ACK失败，并有一次KPT保存失败候选续修PASS。这些说明机制存在成功路径，**不等于当前0.6.27同版成绩**。

0.6.25 ACK产出补丁且编译通过、独立语义失败；0.6.26 ACK正确诊断后NEEDS_EVIDENCE；0.6.27工程送达修复后INVALID_PLAN。每版仅完成各自登记的部分尝试，剩余NOT_RUN不得忽略。泛化改善尚无充分证据。

## Review Focus

1. 当前源码变更/重命名但历史证据仍被引用：Task1验证新快照重读和拒绝旧正文。
2. 同名符号/错误路径/外部SDK未入库：Task2分别记录局部未命中、已有声明和真实缺口，不声称全仓不存在。
3. NEEDS_EVIDENCE带动作、空动作带CHANGES：Task3拒绝并一次反馈独立可检查错误，不由程序伪造语义计划。
4. 首轮部分成功、续修造成保持项回退：Task4执行同版检查、保留失败补丁并恢复当前候选。
5. 新机器缺少本地历史fixtures或绝对路径：Task5显式区分独立测试与历史回放，禁止静默把缺数据算PASS。

## Task 1：完成当前失败轨迹闭合

**Files:** `outputs/reliability-closure-20261020/ACK_FAILURE_ALIGNMENT_v0626.json`、最新0.6.27公开轨迹；`src/contractedge/orchestration/runner.py`、`tests/test_selected_source_continuity.py`。

**Interfaces:** `run_front_half(...)`、`required_planning_source_support(method_input,candidate_set,snapshot_id)`；输出当前来源与最终序列化请求的逐事实存在性记录。

- [x] 回放0.6.26已有5次响应，捕获第6次计划请求；证明12/12源事实送达，编辑权限不变。
- [x] 验证Ready与执行输入、丢失来源及局部额度不足的拒绝；工程全回归通过。
- [ ] 对0.6.27每次真实请求重新核对客户端类型、接口字段契约、当前状态来源、允许目标及补证结果。记录首次仍不可支持的推断，不用旧成功修法填空。
- [ ] 为发现的新边界写最小失败回放；旧快照、删除/重命名、同名符号反例必须拒绝错误复用。
- [ ] 只修改确证断点，运行 `PYTHONPATH=src:.topomen-deps python3 -m pytest -q tests/test_selected_source_continuity.py tests/test_cross_stage_evidence.py tests/test_execution_retry_snapshot.py`；保存红绿证据。

## Task 2：有界获取真正必要的外部/跨模块契约

**Files:** `src/contractedge/source/units.py`、`retrieval/gap_delivery.py`、`execution/helper_context.py`；`tests/test_exact_gap_queries.py`、`test_gap_source_recovery.py`。

**Interfaces:** 沿用当前Snapshot、locator_hint、SOURCE_SPAN和缺口审计；只读证据不增加editable_policy。

- [ ] 从Task1区分：库类型在当前源码但未送达；类型由已锁依赖提供；当前状态读取链不存在；模型把新增helper误当成必须现存。每类有单独公开证据。
- [ ] 若必要SDK契约只在锁定依赖中，先制定最小白名单读适配（依赖包/版本/摘要/文件/行/快照来源），仅读取已公开锁定资源；缺依赖返回明确缺口，禁止网络无界搜索或读隐藏夹具。
- [ ] 用非ACK的小仓库复现：同名类型跨包、错误文件提示、方法名只有注释、依赖版本不一致、源体被裁剪。只给可核验来源，不把定位升级为调用/因果证明。
- [ ] 复用现有一次补证、Top12及预算；分别验收“找到→选中→送达→是否足以支持计划”。没有证据证明需要新适配时不新增模块。

## Task 3：计划语义与纠错一次闭合

**Files:** `changeset/prompt.py`、`changeset/validate.py`、`orchestration/runner.py`；`tests/test_planning_gap_correction.py`、`test_preservation_impact.py`、`test_planning_correction_accounting.py`。

**Interfaces:** 现有SemanticChangeSet及ValidationResult；不改执行权限与公开判定规则。

- [ ] 用0.6.27保存输出写失败回放：NEEDS_EVIDENCE+changes、无changes但CHANGES关联、计划违反未覆盖。记录首次输出中已存在的独立错误。
- [ ] 结构可安全检查时一次汇总全部独立错误；结构错误阻断依赖语义检查，不能吞错。纠错请求保留当前证据和真实剩余额度。
- [ ] 计划需要新增本地helper时写明所属文件、前置条件、调用与清理、保持项、验证方法；必要外部契约缺失保留NEEDS_EVIDENCE。禁止“去掉校验以生成补丁”。
- [ ] 正反例覆盖空动作GLOBAL、真实影响CHANGES、错误ID、未覆盖原子义务；运行上述三组测试和契约测试。

## Task 4：真实修订及跨案例效果验收

**Files:** `orchestration/feedback.py`、`candidate_guard.py`、`execution/preconditions.py`及已有公开探测；`tests/test_failure_tasks.py`、`test_candidate_regression_guard.py`、`test_helper_preconditions.py`。

**Interfaces:** 当前候选/试验候选、同快照公开观察、失败谓词→相关修改→执行反馈；保留历史失败身份。

- [ ] 对每个UNKNOWN写明谓词、场景、缺少的来源/观察能力；构建日志不能证明云收敛。
- [ ] 回放首次执行错误及连锁失败，只把有根据的失败/前提送入真实修订。通过的保持项如变FAIL则拒绝晋级并保留补丁；目标行为与保持行为预先区别。
- [ ] 回放和工程门通过后冻结新版本；先一条原始Broken，独立PASS且无公开明确FAIL才进入固定面板，不从成功候选继续冒充首次成功。
- [ ] A固定7条：RW-CAND-130、R3-KPT-4673、R3-KOPF-726、R4-PROVIDER-HELM-408、R4-ACK-CLOUDWATCH-2985、RW-ALERTMANAGER-4705、RW-CERTMANAGER-9116。
- [ ] 按原登记完成A+B共34个配对槽位、KOPF/Alert额外重复4个槽位、KPT/Alert保存失败续修两次共8个槽位，合计46。先A/重复/续修，核查后才打开预选B10；不得根据结果替换困难案例。
- [ ] 原5成功回归保持；KOPF/Alert两次候选各独立PASS；KPT/Alert两次续修各PASS；无baseline PASS→candidate FAIL；候选主面板PASS数高于基线且B不回退。逐项沿用原登记，不降低退出条件。
- [ ] 报告独立修复率、配对胜负、公开假PASS、独立PASS/公开UNKNOWN、无候选、失败恢复率、费用/耗时/调用与未运行。小样本收益不宣传为150总体保证。

## Task 5：可迁移发布与正式开闸

**Files:** `README.md`、`pyproject.toml`、配置模板、运行脚本、交付清单；`scripts/assess_reliability_delivery_v2.py`及固定结果。

- [ ] 从GitHub干净克隆验证安装、CLI、TopoMen来源摘要、独立测试和所需历史回放资源；本地绝对路径必须参数化，API密钥只取环境变量。
- [ ] 构建数据适配指引：已有分类Release包与内部统一public/evaluation/tools布局不能默认视为相同；完成身份核验后再用统一系统入口。
- [ ] 需要原始公开回放数据的测试明确登记为integration/replay。资源缺失必须报告NOT_RUN或SKIP和原因；不得称其为完整662项验收。
- [ ] 发布源码、测试、配置模板、文档、版本/依赖/数据与探测摘要、完整结果索引、已知限制；大型输入/镜像/数据归档通过现有Release下载。密钥、虚拟环境、缓存和临时受控工作区不进入Git。
- [ ] 只有原方案实现映射、工程回归、G4真实恢复、G5固定效果、数据隔离和干净复现全部通过，才设formal_ready=true并单独批准/启动正式方案。任何必要项未通过继续列为未完成，不能发布为可靠性已达标。

## 执行顺序与停止条件

Task1明确最早断点后，按证据选择Task2/3最小改动；Task4验证作用，再Task5交付。不同时重写所有模块，不因每次模型失败就叠加新提示或新规则。新版本未达到同版准入门不晋级；工程改进可保留为候选，但“每次优化更好”只能由预先固定的配对指标决定，不能保证随机模型每一次调用都优于上一轮。

资源不足、费用达到上限、独立评分基础设施失败、源码/配置摘要改变或回放证据过期时停止新增调用并记录具体原因。此类停止不冒充模型失败或通过。用户当前要求交付文档与GitHub开发快照，本轮发布不等于正式实验发布。

## 2026-10-22二次审查：正式准入必须闭合的执行条件

**本节为Task4/5的强制补充，不能用勾选计划或汇总PASS代替。** 计划能够规定可执行检查与退出条件，但不能事先证明实现无缺陷、模型始终修复成功或外部服务永不故障。通过全部门表示该冻结版本具备启动受控正式实验的证据；并非150条全部成功的保证。

### A. 效果汇总不是最终准入程序

直接检查`scripts/assess_reliability_delivery.py`及`assess_reliability_delivery_v2.py`可见：它们核验登记槽位、重复、回归和通过计数，但不独立验证每条记录的源码/配置/数据/评分摘要，也不审计隔离或工程复现。当前`delivery_effect_gates_passed=true`只能作为以下G5的一项输入，**不得单独设置formal_ready=true**。

Task5须新增最终准入聚合入口与反例测试，复用当前报告而非增加模型Agent。拟交付`scripts/assess_formal_readiness.py`，输入版本冻结单、来源核验报告、工程报告、固定面板报告、运行恢复报告；输出`formal-readiness.json`。缺失、未运行、摘要不一致或必要检查失败，一律拒绝准入。该入口尚未实现，不得据文档名称声称已具备。

|门|必须提供的产物|通过要求|归属|
|---|---|---|---|
|G0 数据与分区|dataset-admission.json|150个唯一成员、公开与评价入口对应、源归档/评分器/分区身份一致；分类Release到统一布局有逐案映射；缺失、重复、校验和错误均拒绝|Task5|
|G1 原方案实现|implementation-coverage.json|原方案每项要求映射到实现、正反例与记录；谱系/异常一致性/保持项/未知原因等均不能只写“支持”|Task1—4|
|G2 工程与跨阶段|engineering-verification.json|必要源码在最终初诊、补证、计划、执行、重诊请求中有正确当前身份；完整回归与明确支持的环境均通过；必要测试不得因fixture缺失而豁免|Task1—3、5|
|G3 隔离与来源|provenance-isolation.json|模型进程无法读取evaluation、GT、Fixed、独立评分输出或其他案例补丁；不仅是提示词禁止；每次结果绑定冻结方法、配置、模型参数、探测、原始Broken、候选patch和独立评分产物摘要|Task5|
|G4 已知故障与恢复|registered-recovery.json|满足既定KOPF/Alert重复、KPT/Alert续修以及原成功回归要求；续修与原始Broken首次修复分别计数|Task4|
|G5 同版开发效果|fixed-panel-assessment.json|46个原登记槽位全部有终态、无重复/替换/混版，原v2效果条件通过；未参与调参的B按冻结方法完成；不把少量配对改善称作统计显著|Task4|
|G6 可运行与可恢复|operational-readiness.json|干净克隆到公开执行、导出patch、独立评分的真实开发案例可复现；有限运行恢复演练通过；资源准入和版本漂移检测生效|Task5|

所有门的报告必须记录检查器版本、执行时间、输入摘要、具体结果和证据路径。最终报告引用这些报告的摘要，绑定**一个明确冻结版本**。仅有布尔值或手写“通过”不构成验收证据。新增最终聚合入口至少测试：缺报告、伪造摘要、混用历史PASS、重复槽位、未运行必要测试、公开明确FAIL但独立PASS、UNKNOWN被升级为PASS、报告来自其他版本等拒绝路径。

### B. 150条正式启动前的零模型预检

这不是对正式数据调参，也不提前运行隐藏行为评分。方法冻结后，由实验管理侧完成所有150条的静态身份/运行契约核查：输入可解析、归档可用、限定编辑范围、公开launcher与独立评分入口、镜像/依赖可取得、磁盘/内存/超时要求可满足。针对受限环境不能执行的成员须在启动前处理，不能到汇总时悄悄删掉。

模型侧只获得公开白名单。独立评价内容由管理/评分进程验证，不能流入方法上下文。预检结果绑定准备发布的同一数据版本和执行环境；任何包或镜像更新需重新核验。预检不产生正式修复分数。

### C. 恢复与停止规则须经演练，不只写在说明中

Task5在开发案例或确定性假服务上演练以下失败；不通过大量付费请求测试网络错误：

- 模型请求前进程退出：恢复后可以准确判断是否已派发，不能丢失登记槽位。
- 请求已发送但响应未知：记录不确定调用和费用预留；不能当作零费用并自动重复发送。只有明确可证明安全的恢复或预登记新尝试才能继续。
- 补丁应用/检查中进程退出：用持久化状态和快照核验选定候选，拒绝把部分写入当成完整补丁。
- 评分未完成或基础设施失败：保留原候选和评分身份；评分恢复不得再次调用模型修改候选，重复评分不得计为新的方法成功。
- 磁盘/内存不足、超时、输出超限、模型服务/参数漂移：记录明确终态或等待维护状态；停止派发受影响任务，预算和队列不重置。
- 并发/连续案例：工作目录、锁、费用账本和源快照不串案；若只验证串行，正式初版只支持串行，不默认开放并发。

正式运行的`METHOD_FAIL`、`NO_CANDIDATE`和保留UNKNOWN可以是合法实验结果，不代表基础设施失控。它们应按预登记口径记录后继续其他成员；身份/隔离/源码污染、预算、数据或执行基础设施错误触发停止。**开发阶段“关键候选失败就停止优化面板”的派发规则不能直接照搬成“正式150遇到任何修复失败就结束”的规则。** 两种执行模式需要显式区分并测试。

### D. 版本、曝光和统计口径

- 150是冻结全集，不等于150条未见测试数据。115个development含已调参案例，必须与validation18、blind17及实际曝光成员分别报告；不能把总150结果包装成纯泛化成绩。
- A反复开发结果用于已知故障回归。B首次复测后若被用于定位问题/调参，其曝光账本立即更新；原B登记保留，不能宣称修改后的版本仍在“未参与调参的B”上验证。新的迁移验证需要预先登记其他未曝光development成员，不得进入validation/blind持续调参。
- 允许工程源码因新根因而产生新版本，但禁止只因同版随机失败不断创建名称不同的版本重跑。新版本必须有失败回放、实际差异、机制验证和全套必要回归，完整保留开发尝试史。
- 记录实际模型标识、供应商、参数、限额、调用原文摘要与时间；temperature=0也不意味着服务可逐字重现。必要重复按登记执行，报告逐次结果而非只选最好一次。
- 正式方案开始前冻结主要指标、统计分母、预算、终止/恢复规则、比较方法同等资源口径和曝光分层。若发现影响结论的方法缺陷，暂停、发布新版本并另立实验批次，禁止中途换方法后继续合算。

### E. 当前完成状态与可作出的承诺

上述G0—G6尚未全部实现及验收；尤其最终准入聚合、完整可移植运行和恢复演练仍为Task5待办。现有662+22服务器测试和629+17干净环境子集为工程证据，不能代替这些门。

最终可以作出的交付承诺是：**已知必要能力有证据、未满足条件不能开闸、正式运行失败可被识别和保留、异常可按冻结规则停止/恢复、结果可追溯并如实统计。** 不能承诺所有模型尝试均成功、150条全部通过或正式运行零故障。若实现者仅完成源码任务却未提供上述真实验收证据，仍然不符合本计划的正式准入定义。
