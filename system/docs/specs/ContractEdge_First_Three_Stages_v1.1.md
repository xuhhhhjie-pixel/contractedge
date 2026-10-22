# ContractEdge 前三阶段实施指导：Requirement → Diagnosis → Semantic ChangeSet

> 2026-10-19：本文保留2026-10-04的设计/数据快照作为历史依据。当前150正式数据基准、完整执行反馈框架及验收口径见 `ContractEdge_Current_Framework_v1.2.md`；不要将下文117/57/30/30等历史数字作为新实验分母。

版本：v1.1，2026-10-04 深度审查修订。沿用原文件路径，以本文替代审查前 v1。本文是实现规格，未声称系统已经实现或通过修复实验。

## 1. 要实现的研究目标

ContractEdge 要把 **Requirement 所约束、但散落在源代码和部署配置中的协调语义显式化**：哪些角色参与意图实现、它们应满足什么关系、Broken 中哪条关系有失效证据、需要哪些语义变化才能恢复它。Requirement 本身是题目；把它改写成字段只是输入整理。核心方法产物是有证据的 Unit–Expected Relation–Violated Relation–ChangeSet。

三个阶段分别回答：

1. Requirement：用户要求什么行为，哪些触发条件与约束已经明确，哪些仍未知？
2. Diagnosis：谁负责实现这些行为、他们应怎样协调、哪些关系出现了有证据支持的失效？
3. Semantic ChangeSet：本轮修复哪些已支持的断点、改变什么语义、保留什么行为、执行后检查什么？

成功的显式化应形成可审计链：`需求义务 O → 应有关系 E → 失效关系 V → 语义变更 C → 检查义务 P`。已经成立的需求应进入保留约束；证据不足的需求应进入开放义务，不能强行填入一个 violation。关系和 ChangeSet 必须用于后续修复决策，不能只在补丁生成后附上一段解释。

本阶段交付到 Semantic ChangeSet。后续 TopoMen 具体化并应用修改，Verify 收集结果，再按失败性质返回 Diagnosis、ChangeSet 或 Execute。前半段必须为这一闭环保留快照、证据和未完成义务，但本说明书不把端到端效果当作已经证实的事实。

## 2. 与既定流程的对应及本次必要细化

`ContractEdge827_v3.pptx` 第 3、5–9 页确定的主线保持不变：范围内检索→语法单元提取→一跳邻居→Top-N→LLM 三问诊断→语义 ChangeSet→轻量校验→执行反馈。Diagnosis 默认 Top-N=12；开发 pilot 比较 8/12/16；因 evidence_gap 只追加一次候选扩展。这些是既有设计初值，不是已验证的最优参数。

本次增加的是让既定流程能处理当前数据的接口细节：公开字段归一、源码根目录识别、语义 scope 解析、缺失工件的新增目标、部分诊断、分层校验和快照版本。RequirementSpec 是保守的输入表示；v1.1 不新增一个必需的“自主生成需求 Agent”。

当前主接入范围为 R1/R2/R5 的 57/30/30 条 release 成员，发布成员身份不等于本次已经逐案确认可用于所有指标。R3/R4 单独导出的两个案例已同时出现在 R2 清单中，应先裁定唯一主类。早期 PPT 的 160 条、多语言均衡和约 200 条扩展目标不能用作当前样本口径。

R1–R5 是场景/执行边界标签，Propagation、Transformation、Binding、Synchronization、Reconciliation 是关系机制，两者不能互相替代。前半段共享语义接口，按工件语法选择解析器，不根据 case 类别直接生成关系答案。

## 3. 数据适配：必须实现的真实差异

以下是 2026-10-04 对当前 manifest 所列成员的字段核查结果，版本变化后应重新生成适配报告。

### 3.1 公开输入并非统一格式

- R1：56 条用 `requirement.text`，`RW-CAND-136` 的 requirement 是字符串。该例还缺少 `broken` 和 `evidence` 字段。其 scope 是 `path:selector` 形式，不能整体当作文件名。
- R2：30 条均用 `requirement.statement`；30 条 method_view 都没有 `broken` 和 `evidence` 字段，观察说明使用 `behavioral_observable`。部分 scope 是符号名、路径加说明或自然语言角色，不是文件路径。
- R2：`RW-IMAGE-REFLECTOR-824`、`RW-KYVERNO-17395`、`RW-SOURCE-WATCHER-371`、`RW-SOURCE-WATCHER-373` 的公开输入还嵌有修复 PR 或 commit provenance。不能把 requirement 整个对象原样送入模型。
- R5：没有与 R1/R2 对等的 release-level method_view。30 条 case 有 requirement/history，但 `case.yaml` 同时含机制解释、关系和修复信息；应在可信数据准备端导出。GT 的 relations 有 29 条嵌于 coordination、1 条位于顶层，这只影响 evaluator 适配，不能将其复制成方法输入。
- 归档根目录不统一：已查的 R1 `RW-CAND-067` 为根层源码；R2 `RW-ARGO-CD-23823` 多一层 `source/`；R5 `RW-OTEL-OPERATOR-2171` 多一层仓库与 SHA 目录。

### 3.2 两个互相隔离的过程

**可信准备端**可读完整数据包，用 manifest、source_refs 和必要的 history 字段绑定 case 与 Broken 源码，生成公共任务目录和私有评价目录。它从 requirement 中只选择文本，不递归复制 provenance、机制、repair hints 或 GT。R1/R2 优先使用既有 method_view 的题目文本；R5 需要人工复核文本是否已经泄露修复方案。描述所需行为是允许的，直接指出应修改的实现策略需要单独审查。

**方法端**只挂载公共任务目录，既不能读完整 case 包，也不能通过路径跳转或软链接回到 Fixed、GT、repair patch 或隐藏 oracle。不给方法网络搜索修复历史的入口。公开 Broken 仓库原本存在的生产代码和原生测试按统一策略提供；之后添加的 benchmark overlay 属于 evaluator。归档清单发现来源不明的 overlay 或注入文件时，先复核再导出。

准备端按以下映射导出：

1. Requirement 文本：字符串原值，或 `requirement.text`，或 `requirement.statement`；同时出现且不一致时报告冲突，不能任取其一。
2. 期望观察：`observable.statement`、字符串 observable 或 `behavioral_observable`。这是行为描述，不自动视为 Broken 的实测值。
3. Broken 身份：公开记录有 `broken.revision` 时与包内 `history.broken_sha` 一致性核对；缺失时由准备端读取该允许公开的单值补齐。其他 history 字段不进入方法目录。
4. Broken 归档：通过 source_refs 的 package 定位，生成明确的 `archive_path/archive_sha256/source_root`；不能依赖当前工作目录猜相对路径。R5 的异构路径应使用经复核的 sidecar 映射，不能以通配符随机选一个 tar。
5. scope：保存原文，另产生路径、符号、字段、概念四类 hint，不能把它当现成文件清单或答案。
6. Broken evidence：公开 excerpt、允许的实测字段和运行上下文单独投影。禁止直接复制带 `observed_relations/violated_relations`、GT ID、因果答案的完整 result.json。允许公开的期望行为应归于需求/观察契约，不以隐藏测试内部结构泄露。

缺失 raw evidence 并不等于 case 不可用；应记录 `NO_RUNTIME_EVIDENCE`，允许后续使用源码推理。输入出现标为 SEMANTIC_FAIL 的记录时，即使 wrapper exit code 为 0，也不能把它改写成 PASS；未记录的 actual 值不能仅凭测试名称猜出。

### 3.3 发布身份与抽取边界

只从选定 release manifest 枚举成员。R1 method_view/gt 各有 59 个文件，正式成员只有 57 个；额外文件不能被目录扫描带入。R3/R4 导出中的 `RW-HELM-CONTROLLER-1412` 与 `RW-CROSSPLANE-PROVIDER-AWS-2256` 不新增计数。修复家族/PR/SHA 去重由可信准备端完成，方法端只检查公开任务身份和输入哈希。

解压先检查成员路径、软链接目标和体积限制，保留必要且指向源码根目录内部的链接。若只有一个公共目录前缀，可登记为 source_root；多项目布局必须显式配置。后续 locator 一律相对规范源码根目录。冻结解压树文件清单与内容哈希，保证源代码证据可重定位。

## 4. 统一数据契约

以下是必需字段定义。代码实现应据此建立模型/schema；不允许用不受约束的自然语言包替代。可选信息使用显式 null/空数组，核心身份缺失应拒绝加载。

### 4.1 公共任务 MethodInput

- `schema_version=ce.method_input.v1_1`、`case_id`、`release_id`。
- `original_broken_revision`、`archive_path`、`archive_sha256`、`source_root`、`initial_snapshot_id`。
- `requirement_text`：保留原文；`public_observable_text`：可为空。
- `scope_hints[]`：每项含 `kind=path|symbol|field|concept`、`value`、`raw_text`、`origin_ref`。
- `public_evidence[]`：仅指向已复制到公共目录的 EvidenceRecord。
- `input_digest`：规范 JSON 序列化的公开输入（排除本字段）加归档、公开证据内容哈希；不能只哈希一个路径字符串。

release 分类、历史修复和 GT 存在评价端。若某实验决定公开类别标签，必须对所有方法一致公开并记录协议；默认不把类别当作 Diagnosis 答案提示。

### 4.2 运行快照与证据

`RunContext` 含 `run_id、case_id、round_id、snapshot_id、parent_snapshot_id、input_digest、model_config、prompt_version、retrieval_config、budget`。`snapshot_id` 由规范路径排序后的源码文件内容清单产生，与 input_digest 独立计算，避免循环哈希；公开 origin_ref 只能指向公共输入，完整转换来源另留在可信准备端。第 0 轮 snapshot 对应原始 Broken；后续 Re-Diagnosis 必须分析当前修复后的 snapshot，不能继续用原始 Broken 的行号和结果冒充当前状态。

每项 `EvidenceRecord` 含 `id、snapshot_id、kind、locator、content_digest、excerpt、origin`：

- `kind` 可为 REQUIREMENT_TEXT、SOURCE_SPAN、SOURCE_INVENTORY、STATIC_SEARCH、PUBLIC_OBSERVATION。
- `locator` 按 kind 使用字符区间、相对源码路径及行区间、目录清单、查询与搜索范围，或公共观察文件中的字段位置。
- “没有某实现/引用”的结论必须记录搜索范围与查询，或完整目录清单。一次 Top-N 未召回不能证明全仓库不存在。
- 观察事实、静态源码事实、因果推断分别标注。真实输出能证实症状，不自动证明模型选择的原因。
- 从 requirement 引用的原文可以被复核；模型新写的解释不能引用自己作为外部证据。

### 4.3 三种对象必须区分

`CandidateUnit` 是检索得到的源码片段/配置子树；`SemanticUnit` 是实现需求的角色；`ChangeTarget` 是执行层可能修改或新增的目标。三者有关联，但不能共用一套“已有文件 ID”的限制。

一个文件可承担多个语义角色，一个语义角色也可对应多个 locator。资源、事件、字段、运行组件都可成为 SemanticUnit；它们不一定是源文件。缺失的必需角色使用 `existence=EXPECTED_MISSING`，由需求和缺失证据支撑，不伪造已有源码位置。

SemanticUnit 至少含 `id、role、kind、existence、locator_refs[]、requirement_refs[]`。`existence` 为 PRESENT、EXPECTED_MISSING 或 UNKNOWN。EXPECTED_MISSING 可支持新增工件；UNKNOWN 不能直接被当作确认缺失。

## 5. Requirement：忠实整理意图

输出 `RequirementSpec`，字段为 `raw_text、intent_summary、outcomes[]、constraints[]、trigger、unknowns[]、status`。每个 outcome 使用 O1/O2… ID，包含 `statement、condition、source_refs、support=EXPLICIT|DERIVED`。DERIVED 需要写推导依据，并保留与原文的区别；原文中的否定、条件、例外与时序不能丢失。

首版采用保守整理：字段规范化和标识符抽词确定性完成。初始 RequirementSpec 将原句作为粗粒度 O，保留原文区间；无法安全拆分时可以用一个 O 保留整个需求。首次检索使用原文、公开 observable 和已有标识符，不依赖尚未生成的精细 O。Diagnosis 首次调用可一并提出原子义务拆分，并在同一输出中建立 E/V 引用；每个细分 O 必须链接原始 O 和原文区间，保留 requirement_spec 版本与 ID 映射。这样消除检索依赖诊断结果的循环，同时不强制多加一次 LLM 调用。将来若单独加入需求规范化 LLM，应作为版本化配置并计入成本、消融和基线输入处理。

检查每个原文条件和期望是否映射到 outcome 或 constraint，确认没有加入原文与公开材料均不支持的行为要求。未知的运行时事实保持未知。明确表示自动检查只能验证引用和字段覆盖，不能证明改写与原文完全等价；开发集需人工抽查。

`status=READY` 表示意图足以启动诊断，不要求给定 bug 行或 runtime 证据。缺少精确路径但提供了符号或概念 scope 时继续做范围解析。只有原文缺失、内部冲突无法澄清或连预期行为都无法确定时返回 `UNDER_SPECIFIED`；损坏/错配/泄漏输入返回 `INVALID_INPUT`。

本研究的“显式化”在此只完成 O 层。真正需要恢复的协调结构 E/V 仍由 Diagnosis 从公开 Broken 世界推断，不能通过改写 requirement 偷渡 GT。

## 6. Diagnosis 的检索流程

### 6.1 明确 scope 的含义

PPT 的 artifact_scope 表示围绕题目限定搜索，不能在当前数据中机械解释成文件系统白名单：R2 本来就包含概念范围。首版将其作为 seed scope，公共 Broken 源码根目录是可访问的最外层边界。

先解析明确路径/字段/symbol；概念 hint 只用于范围定位，记录使用的检索词与命中路径。可以对公共仓库做有界符号/路径索引来解析 hint，但不把全仓库内容一次性送给模型。解析成功后冻结 `resolved_seed_scope`；无法定位的 hint 保留为未解析项。

默认一跳只补直接调用/引用/字段依赖或部署格式规定的相邻单元，例如 Helm `include`、模板 Helper 和当前 Chart 的 templates 目录。若越过 seed 范围，记录 `scope_extension` 的锚点、关系、目标和证据。任意扫描新子系统不算“一跳”。这是一项针对真实数据的明确实现细化，实验要报告 seed 范围与扩展量。若要严格复现“仅原始文件集合”，启用 `scope_mode=seed_only` 单独比较，不混合两种协议。

### 6.2 从线索到候选

1. 从原始 Requirement、公开 observable 和当前已经存在的粗粒度 O 提取标识符、路径、对象种类与行为词；第二次检索可加入首次 Diagnosis 已提出且通过来源检查的精细 O。默认做大小写、snake/camel/kebab 命名变体；模型扩词仅用于一次 evidence_gap 扩展。
2. 在 resolved_seed_scope 检索并记录命中。语法适配器提取 Go 函数/类型、TypeScript/JavaScript 单元、YAML/JSON 子树、Helm 模板块/Helper；Shell/CLI 配置和未支持语法可回退有界文本块。未渲染 Helm 文件不能假定是合法 YAML。
3. 构造一跳引用边，分别记录 syntactic 与 inferred 关系；模糊动态调用不能标成解析器已经证明的静态边。
4. 候选去重按 `snapshot_id + path + locator`，避免同一函数因多次命中占用 Top-N。确定性初始排序使用：精确标识符命中数降序、公开观察词命中数降序、不同需求词命中数降序、跳数升序、路径/位置字典序。它是开发起点，不宣称最优。
5. 取默认 N=12 个候选，并为它们附带引用边和证据位置。N 控制候选个数，还必须在运行配置中显式设置 `max_context_tokens`；其值随所选模型与实验预算冻结，不隐式采用服务默认值。若大函数被裁剪，保留函数签名、命中上下文与裁剪标记；模型不能用被裁剪片段证明整函数不存在某行为。

CandidateSet 保存候选总数、选中和落选 ID、排序特征、scope 解析结果、一跳边、token 预算与裁剪记录。模型只能引用本轮已读取的证据；想引用未提供单元，应先请求扩展。

### 6.3 一次证据扩展

模型给出 `evidence_gaps[]`：缺什么角色/关系、现有证据为何不足、建议搜索词或直接邻居。只有可定位的 gap 才触发扩展；“再多找一些文件”不算有效请求。系统最多追加一次检索与 Diagnosis 调用，合并候选去重后按同一规则重排。扩展失败是检索局限，不能推断需求不成立或必需组件不存在。

## 7. Diagnosis 的语义输出与校验

### 7.1 DiagnosisPacket

必需字段：公共 envelope（schema_version、run_id、snapshot_id、input_digest）、`requirement_spec、units[]、expected_relations[]、violations[]、outcome_coverage[]、evidence_refs[]、evidence_gaps[]、status`。

每个 ExpectedRelation 含 `id、source_unit_ids[]、target_unit_ids[]、mechanism、condition、expected_contract、requirement_refs[]、support_refs[]`。允许多端点，避免把共享约束强拆成互不相干的边；描述中要给出具体约束，例如“所选 ServiceAccount 名称与 Pod 引用一致”，不能只有“两个文件相关”。机制允许 UNKNOWN，由说明承载语义；不强迫模型为了填五类标签猜测。

每个 Violation 含 `id、relation_id、broken_behavior、causal_explanation、evidence_refs[]、support_basis=OBSERVED_AND_STATIC|STATIC_INFERENCE|OBSERVED_ONLY、causal_status=SUPPORTED|TENTATIVE`。OBSERVED_ONLY 可以证明关系输出失效，但不能自动定位生产代码中的原因；若没有支撑修改目标的证据，ChangeSet 应返回 gap。STATIC_INFERENCE 可以用于规划，但必须明确未运行验证，不能写成实测结果。

`outcome_coverage` 对每个 O 分别记录 `repair_supported、preserve_required、unresolved`，并链接对应的 E/V/证据。preserve_required 表示需求要求保留该行为，另用 `current_satisfaction=OBSERVED|INFERRED|UNKNOWN` 说明当前是否已证实满足；未观察到 failure 不能自动记为 OBSERVED。不同条件下分别需要修复和保留的行为，应拆为条件明确的 O，不能把两者混成一个无条件结论。

状态规则：

- READY：当前规范化义务均有可解释处理且没有 unresolved 项，至少一条可修的受支持 violation。
- PARTIAL_READY：存在可修的受支持 violation，同时有开放义务；开放义务与本轮变更不形成已知冲突。允许后续做渐进修复，但必须保留开放项。
- EVIDENCE_GAP：目前没有足够依据产生任何可执行语义变更，或缺口使已有目标也不可信。
- NO_SUPPORTED_VIOLATION：检查后不能支持关系失效；对给定 bug 任务是未诊断成功，不等于系统行为正常。
- INVALID_OUTPUT：输出结构、引用或身份不合法。

### 7.2 “观察范围”不等于“可推理范围”

保留 `requirement_horizon`（完整需求）、`observed_boundary`（本轮实际已观察部分）、`inferred_causal_path`（源码支撑的解释）、`unverified_outcomes`。R1 可以从渲染失败向上分析 Values/Helper/Template；R2 可以从谓词错误解释入队风险，但不能称已观察到子资源删除；R5 可以通过进程响应分析配置重载路径，而无需宣称真实 kubelet 挂载已运行。

v1 的“结论不得超出观察终点”修订为：**实测声明不能超出观察终点；上游因果推断必须标注证据和不确定性；完整 Requirement 不因 verifier 较浅而被删除。** 后续行为验证仍按实际 oracle 能检查的范围评分。

### 7.3 校验分层，不能把格式合法当成语义正确

确定性校验负责 schema、ID 外键、快照一致性、路径存在/缺失证据、证据 digest、关系端点、重复项和来源权限。输出 `structural_validation=PASS|FAIL`。

语义复核负责需求是否被忠实表达、是否至少存在两个独立语义角色、关系是否必要、证据是否支持因果、是否越界推断。两个角色可以位于同一文件；不能把单一局部运算随意拆成两段来制造跨角色关系。无法支持协调结构时记录 scope_mismatch_suspected，不强行生成关系或自动重分实验类别。它可由同一次结构化自检或独立可配置 reviewer 提供辅助判断；必须记录方式与成本，不能把它称为可靠的自动证明。审稿/模块评价中的 GT 对齐由独立 evaluator 完成，结果不回流到方法端。

有 `structural_validation=PASS` 不代表 diagnosis 正确；模型附带引用也不自动表示引用支持主张。记录 `semantic_review=ACCEPT|REVISE|UNCERTAIN` 与理由，保留人工抽查样本。

## 8. Semantic ChangeSet：把关系恢复落实到目标

### 8.1 输入与计划范围

输入是同一 snapshot 的 RequirementSpec、DiagnosisPacket、已读取证据和相关源码。选择本轮支持且具有目标依据的 Violation，形成 `planned_violation_ids`。不重复全仓库定位；若需要新角色或新因果判断，返回 Diagnosis 的 gap。

计划不要求改变所有 relevant units，也不要求多文件修改。一个模板修改可以恢复多个输出关系；有多个被破坏关系也不意味着要做多个文件编辑。

### 8.2 ChangeTarget 的两种合法形式

- EXISTING：`path、locator、unit_refs、snapshot_id、content_digest`，允许 MODIFY 或 REMOVE；REMOVE 也需要需求与失效证据支持。
- NEW：`parent_path、proposed_path、expected_role、anchor_unit_refs、justification_refs`，使用 CREATE。必须验证父目录或计划中的父目标、名称策略、路径不冲突以及新增理由；不要求一个根本不存在的函数/文件已经有 source locator。

NEW 指新增文件；在已有文件中添加缺失字段/函数使用 EXISTING + MODIFY，locator 锚定其已有父结构，并在 semantic_action 中声明新增角色，不能要求缺失子字段已有行号。首版优先在已有父目录下新增文件；若确需新目录，显式建父目标并校验依赖顺序。若新增父角色尚未定位，返回 gap。新增路径只是方案的一部分，不能硬编码历史补丁采用的文件名为唯一正确答案。

### 8.3 SemanticChangeSet 契约

必需字段：公共 envelope、`diagnosis_digest、goal、planned_violation_ids[]、changes[]、postconditions[]、preserve_constraints[]、open_obligations[]、outcome_coverage[]、status`。

每项 change 含 `id、target、operation=MODIFY|CREATE|REMOVE、semantic_action、intent、violation_refs[]、postcondition_refs[]、depends_on[]`。postcondition 含 `id、statement、requirement_refs[]、relation_refs[]、planned_check、check_scope`。具体标识符和字段名可以使用，完整 diff/新函数实现留给 TopoMen。

覆盖规则：每项 change 至少链接一条已支持且被选入本轮的 violation；每条 planned violation 至少对应一项 change；每个 O 要么被本轮修复义务覆盖，要么进入有证据的保留约束，要么明确列为开放义务。开放项不自动触发盲目修改，也不允许在结果中声称整个需求已满足。

`status=READY` 或 `READY_PARTIAL` 仅表示计划可交给执行层；`NEEDS_EVIDENCE` 表示需回 Diagnosis；`INVALID_PLAN` 表示结构/目标矛盾。检查义务是模型依据公开需求提出的候选检查，不能替代隐藏 evaluator，也不能修改隐藏判定来使补丁通过。

### 8.4 轻量校验与修订

硬检查：引用、snapshots/digests、EXISTING/NEW 目标约束、范围依据、同路径 create 冲突、依赖无环、planned violation 覆盖、O 覆盖、私有来源阻断。

语义检查：变更是否可能恢复关系、是否破坏保留行为、是否只是把测试输入硬编码进实现、后置状态之间是否冲突。后者需要模型/人工判断，不能用字符串规则宣布已排除全部语义冲突。首版最多一次定向 ChangeSet 修订；失败即输出结构化失败信息。

R1 `RW-CAND-067` 的 Broken 归档中没有 serviceaccount 模板文件，但已有 values、Helper、Deployment 和 templates 目录。有效计划可以新增渲染对象或修改已有模板；v1 只要求“目标在 Broken 中存在”的规则会错误拒绝这一类修复，已由 NEW 目标协议解决。

## 9. 有限状态与迭代接口

主流程：`PREPARED → REQUIREMENT_READY → RETRIEVED → DIAGNOSED → PLANNED`。状态代表流水线进度，不代表程序已修好。业务输出状态使用第 7、8 节枚举，结构校验单独记录，避免把所有失败混成一个 PASS/FAIL。

每个前半段 round 最多 2 次 Diagnosis 语义调用（首次及一次 evidence-gap 扩展），最多 2 次 ChangeSet 语义调用（首次及一次修订）。格式重试、传输重试单独记账且设上限，不能暗中无限增加语义尝试。默认值与总 token/时间预算写入 run 配置，耗尽返回 BUDGET_EXHAUSTED。

ChangeSet 返回 NEEDS_EVIDENCE 时，若本 round 尚有 Diagnosis 扩展额度，可用这一次额度重诊断并重新规划；否则停止交给调度器，不通过“新建一个 round”绕过预算。系统级 round 只有在后续执行产生新工作树或新公开执行证据时推进。

未来 `ExecutionFeedback` 需含 `from_snapshot、to_snapshot、patch_digest、build_state、semantic_outcome、public_observations、pending_obligations、failure_kind、route_confidence`。固定 Requirement 的原始身份；当前源码、候选、关系证据和 ChangeSet 则随快照更新。旧证据保留历史引用，不能不经验证沿用旧行号。已恢复关系转为保留约束，新暴露 failure 再触发下一轮。

`failure_kind` 允许 ENVIRONMENT、EXECUTION、PLAN、DIAGNOSIS、UNKNOWN。路由为有证据的判断；UNKNOWN 需保留不确定性，不能因为测试失败就认定 Diagnosis 错。环境失败不统计为语义失败，wrapper exit code 不能代替语义 outcome。

## 10. 一个可检查的接口示例

下面是用于检查 ID 引用与推理边界的简化示例，并非省略字段也可通过完整 schema 的契约样本。它只演示 R1 ServiceAccount 需求中的“创建所选资源”这一项，源于真实需求的一个子义务。它不覆盖该 case 的全部绑定/automount 义务，也不是模型在本次运行生成的正确诊断或实验结果。示例 ID 与 snapshot 为文档标记，不能作为冻结评测输入或提示示例投喂同一家族的测试 case。

```json
{
  "snapshot_id": "demo-s0",
  "outcomes": [
    {"id": "O1", "statement": "serviceAccount.create 启用时应渲染所选名称的 ServiceAccount"}
  ],
  "evidence": [
    {"id": "EV1", "kind": "REQUIREMENT_TEXT", "snapshot_id": "demo-s0", "statement": "公开需求要求 serviceAccount 声明生成 ServiceAccount"},
    {"id": "EV2", "kind": "SOURCE_SPAN", "snapshot_id": "demo-s0", "path": "helm/schautrack/values.yaml", "statement": "serviceAccount 下存在 create、annotations、name"},
    {"id": "EV3", "kind": "PUBLIC_OBSERVATION", "snapshot_id": "demo-s0", "statement": "公开 Broken 摘录：ServiceAccount contractedge-sa was not rendered"},
    {"id": "EV4", "kind": "SOURCE_INVENTORY", "snapshot_id": "demo-s0", "path": "helm/schautrack/templates", "statement": "归档目录清单中没有 serviceaccount 模板文件；这不单独证明其他模板不能生成该对象"}
  ],
  "units": [
    {"id": "U1", "role": "ServiceAccount 声明输入", "existence": "PRESENT", "evidence_refs": ["EV2"]},
    {"id": "U2", "role": "该 Chart 的模板生成入口", "existence": "PRESENT", "evidence_refs": ["EV4"]}
  ],
  "expected_relations": [
    {"id": "E1", "source_unit_ids": ["U1"], "target_unit_ids": ["U2"], "requirement_refs": ["O1"], "expected_contract": "启用创建时，所选名称在生成对象中被实现", "support_refs": ["EV1", "EV2"]}
  ],
  "violations": [
    {"id": "V1", "relation_id": "E1", "broken_behavior": "目标对象未出现在公开渲染观察中", "evidence_refs": ["EV3"], "causal_status": "TENTATIVE"}
  ],
  "planning_decision": "NEEDS_EVIDENCE",
  "evidence_gaps": [
    "检查 Chart 的模板内容及现有 Helper，区分缺少生成逻辑、条件抑制或名称绑定错误，再决定修改已有模板还是新增文件"
  ]
}
```

该例刻意保留 NEEDS_EVIDENCE：症状加目录缺文件不能自动推出唯一修复。完成直接源码检查后，若支持新增目标，则以 `NEW + 已有 templates 父目录 + 需求/源码证据` 规划；如果已有实现只是条件错误，则选 EXISTING。两种可能都通过行为验证判断，不以历史补丁路径裁决。

## 11. GT 适配、评价和 baseline

### 11.1 显式化质量需要测什么

第一层是工程有效性：字段合法、引用可解析、快照一致、没有私有答案、重试有限。第二层是语义质量：是否忠实保留需求、找全必要角色、关系约束是否准确、violation 的证据与因果是否成立、ChangeSet 能否恢复并保留相关行为。第三层才是后续端到端修复结果。

模块指标至少记录：候选角色覆盖、Relevant Unit Precision/Recall、Expected/Violated Relation 语义匹配、证据支持率、O→E→V→C→P 可追溯覆盖、计划目标/意图/义务完整性、过度修改、未决义务、失败和弃答比例、成本。这里“引用覆盖率”与“引用确实支持主张的比例”必须分开；大量引用不代表诊断正确。

GT 适配器在 evaluator 端归一化 R1/R2 overlay 和 R5 的顶层/嵌套关系，保留原文、oracle_limit、完整性标记和来源。当前 R1 有 55 条显式 full_recall_eligible，另两条 `RW-CAND-070/134` 使用不同结构、未提供此字段；缺失不能视为 true。R2 的 FULL_ORACLE 也不能覆盖其 oracle_limit 所明确排除的下游行为。

角色/关系评分按语义端点、条件与约束做匹配，不按 U1/E1 ID、文件字符串或逐字自然语言匹配。模糊多路径 GT 先人工裁定别名与可接受粒度；标注不完整的 case 可报告可核实 precision，不能当作全关系 recall 的分母。判定不确定时列为待裁定并同时报告数量。

除 eligible case 的指标，还要对冻结运行清单报告端到端“产出可用诊断/计划”的覆盖率。EVIDENCE_GAP、无可用计划和预算耗尽不能从分母悄悄剔除；基础设施故障单列。开发集可由人工评价并调参，测试集评价结果不能反馈给方法。

### 11.2 避免实验目的被输入提示替代

当前输入已提供人工 artifact_scope 和细化 observable，因此应称为“给定范围和公开失败信息下的协调诊断”。这些提示可能降低定位难度；不能把该设置的结果直接宣传为无范围辅助的全仓库发现能力。若增加全仓库设置，应单独运行并报告。

已用于说明书示例、prompt 调整或反复查看 GT 的案例/修复家族划入开发资料，不能再称为未接触测试样本。后续划分先按家族去重；根据实验目标增加仓库留出。Top-N=8/12/16 的选择和其他配置只在开发 pilot 冻结。

外部 Agentless/SWE-agent/OpenHands 等沿其自身搜索、规划和执行机制运行，统一公开题目、运行环境、隐藏评价和预算口径；不强制把所有外部方法换成 TopoMen。内部消融则固定检索输入、模型、执行器和预算，尽量只改变目标模块。

- Full：输出显式 E/V，ChangeSet 链接 violation。
- 无显式关系：保留同等公开输入/候选信息，输出定位摘要并用独立的 direct-plan 适配接口生成变更，不再要求 E/V 外键；否则相当于把被删除的关系偷偷加回。
- 无 ChangeSet：Diagnosis 直接交给执行器，不在适配器里重建一份等价施工单。
- 无执行反馈：前半段保留，仅禁止执行反馈驱动后续轮次；最终隐藏评价仍照常运行。

报告语义调用/token/时间/尝试次数。不能通过给 Full 更大的执行预算而把全部提升归于显式关系；无法完全相等时使用预注册预算限制并报告成本与成功率。

## 12. 实施模块与验收

建议目录是设计目标；当前工作区未找到可核实的 contractedge-system 代码，服务器现有骨架需实际核对后复用。

```text
contractedge/
  adapters/       可信导出、源码根目录识别、公开证据投影
  contracts/      输入、O/U/E/V/C/P、快照、状态与错误定义
  requirement/    保守规范化、来源映射
  retrieval/      scope 解析、语法单元、一跳、排序、预算
  diagnosis/      结构化 prompt、解析、引用检查、语义复核
  changeset/      现有/新增目标、计划生成、覆盖和冲突检查
  orchestration/  前半段状态机、调用预算、快照与 trace
  evaluation/     单独运行和权限目录，方法进程不可导入 GT
```

实现按以下顺序推进；每一步须通过具体验收，后一步才依赖它。

1. 导出契约：R1 的字符串/text、R2 的 statement/缺 broken、R5 的待导出记录都能形成明确结果；四条 R2 修复 provenance 不出现在公共输出；原包校验清单保持不变。导出失败返回原因，不静默跳过。
2. 源码/范围：三种归档根目录映射正确；path:selector、symbol、concept 可解析或报告 gap；Helper 一跳可追踪；穿越/软链接不能访问私有目录。
3. Requirement：保留条件、否定和所有已声明 outcome；未知事实不被补写；测试观察浅于需求时仍保留完整需求及观察限制。
4. Diagnosis：至少通过“仅有症状，暂不知原因”“缺失角色”“两个角色在同一文件”“多断点”“部分证据”“引用不支持主张”六类协议测试。假引用能被硬检查拒绝；语义错误样例由人工复核确认不会被当作确定性验证通过。
5. ChangeSet：能表示新增模板；已有目标快照必须匹配；未受影响 O 进入 preserve；未知 O 进入 open；不能把未支持原因直接转成硬编码修复。
6. 迭代接口：旧 snapshot 的引用被识别；一次扩展和一次计划修订不会形成无界回环；SEMANTIC_FAIL + wrapper exit 0 仍为语义失败；环境失败不归因给方法诊断。
7. 开发 pilot：约 20 条覆盖场景、机制、已有/新增目标与局部/运行观察；R5 先完成公开导出再进入计分。检查三阶段语义链和失效类型，冻结 prompt/config/schema，保存调用记录。
8. 进入后续执行：至少每个拟覆盖场景有独立人工确认可交接的代表性计划；若某类仍频繁 scope 失败或计划无法落地，先修适配/方法，再扩大规模。没有实际 pilot 结果前不填写虚构的成功率或“已达标”。

本规格解决的是职责与协议是否自洽、当前数据差异能否接入、实验是否能检验原假设。计划正确性和修复增益仍以代码运行、独立语义评审与最终行为验证为准。

## 13. 依据与审查范围

资料：ContractEdge827_v3.pptx 第 3、5–9 页；ContractEdge9.3号.pptx 第 3–6、8、11 页；2026-09-12 云原生研究定位说明；R1/R2/R5 当前 release manifest 与 method_view/GT；R3/R4 历史导出；抽查的 Broken 源码归档和证据。

此次对 R1/R2 全部 87 条正式 method_view 做字段形态检查，对 R5 的 30 个 case 做关系存放位置检查，并查看代表性源码/证据；未声称对 117 条重新复放，也未声称完成全部 GT 语义重标注。详细问题与修订依据见同目录 `ContractEdge_前三阶段_深度审查记录_20261004.md`。
