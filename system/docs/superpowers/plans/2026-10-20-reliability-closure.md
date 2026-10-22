# ContractEdge Reliability Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐已批准最终方案的义务谱系，并通过操作事实校验、缺口送达、前提检查和候选回归保护完成真实恢复验收。

**Architecture:** 扩展现有契约与runner，保留三个核心和0—10框架。确定性门负责事实、谱系、范围、版本和回归约束，现有模型调用负责语义解释和代码修复。

**Tech Stack:** Python、JSON Schema、原生TopoMen Agent、已有公开Go/Helm运行器。

**Spec:** `docs/reports/2026-10-20-reliability-closure-design.md`

**Review revision:** 2；跨案例可实施性补充见 `docs/reports/2026-10-20-reliability-feasibility-review.md`。新增Task 0是实施前置项，不得跳过后仍称已完成方案。

## Global Constraints

- 基线冻结0.5.5；新工作使用新方法与必要的新探测版本，不覆盖历史成绩。
- Top12；12模型调用/案例；2语义轮；一次证据扩展；共享80元账本。公开输入、独立评分和数据分组隔离沿用。
- 不启动正式150/消融；不自动提交、重置或清理用户现有工作。
- 从保存的当前工作区基线比较差异，不能用git HEAD漏掉既有未提交实现。
- 本文件全部任务尚未实施；本文的预期输出不是通过记录。
- 所有新记录先定义版本化schema、序列化、读适配和恢复路径；旧结果不自动升级为新能力。统一pytest项目环境在Task 0固定，RED/GREEN命令进入执行记录。

## Review Focus

- 预期异常、无关对象异常和部分成功结果不能混为一种状态。
- 换引文、拆分、合并不能擦除原义务或继承旧快照PASS。
- 最终prompt裁剪不能丢掉已定位的缺口端点。
- 回滚必须同步源码、导出补丁、反馈与候选身份，保留被拒试验。
- 辅助前提未证明、环境抖动及公开能力不足不能变成假PASS或无限重试。

## Task 0: 需求覆盖、接口与跨案例预登记

**Files:** 新增本轮输出目录下requirements-map.json、experiment-registration.json、budget-preflight.json；完善公开能力清单与版本化schema设计。

**Interfaces:** 预登记文件固定A/B名单、仓库来源与曝光标签、两个恢复起点摘要、配对顺序、方法版本、检查比较键和全部门槛；不得把类别前缀当成manifest主类别。

- [ ] 将原最终方案每条规范要求映射到实现任务、正反例、实际运行证据；未映射项在实现前补全。
- [ ] 确认项目Python环境与所有既有测试路径；固定各任务的实际pytest命令。保存工作区基线。
- [ ] 完成新schema、向后读兼容和中断恢复接口；谱系投影显式接收当前snapshot与检查上下文，操作记录进入prompt/review的全链路保真。
- [ ] 对150公开元数据审计新机制适用性；从development固定A=原7、B=每R类2条共10条，并核验仓库与曝光；先锁名单后实现，不按效果换样本。
- [ ] 固定KPT及另一个仓库机制的公开失败恢复起点；公开校准确认失败，非独立评分选种子。
- [ ] 固定34次主要配对运行、4次关键重复运行、8次恢复运行及其完整记账方式；检查费用/磁盘/时间可行性，完成后方可新增付费调度。

## Task 1: 显式义务谱系与覆盖残余

**Files:** Create `src/contractedge/requirement/lineage.py`; modify `requirement/normalize.py`, diagnosis/changeset/feedback schema与绑定；extend `tests/test_obligation_lineage.py`, `tests/test_check_coverage.py`.

**Interfaces:** `validate_lineage(events: list[dict], prior: dict, current: dict) -> dict`输出只追加谱系账本；`project_obligation_coverage(ledger: dict, checks: list[dict], context: dict) -> dict`输出当前谓词、残余和派生状态，context必须含当前snapshot、场景、输入和能力身份。

- [ ] RED：CONTINUE换措辞不丢原定义；REANCHOR不改语义；SPLIT保留父/子/残余；MERGE保留独立状态；循环、跨需求、遗漏来源、过期PASS均拒绝。
- [ ] 实现CONTINUE/REANCHOR/SPLIT/MERGE及原文身份兼容；显式区分出处身份与不变义务身份。
- [ ] 接入计划、执行输入与跨轮反馈；覆盖无法确定保留残余UNKNOWN，禁止仅凭事件存在推导充分性。
- [ ] 用不同来源跨度、误解的初始派生项、非必要拆分、跨仓库需求等反例验证：不能自动证明自然语言等价，也不能把错误初始解释锁为永久硬约束。
- [ ] GREEN：目标反例和现有义务/检查覆盖回归通过，输出原方案逐项映射。

## Task 2: 操作事实与满足判断一致性

**Files:** Create `src/contractedge/diagnosis/operation_consistency.py`; modify `execution/observations.py`, `diagnosis/validate.py`, `execution/support.py`, `orchestration/runner.py`及所需schema；create `tests/test_operation_consistency.py`.

**Interfaces:** `validate_operation_claims(payload: dict, operations: list[dict], obligations: dict) -> list[dict]`产生可合并的结构化错误；操作记录由注册适配器产生，不由模型自行声明成功。

- [ ] RED：KOPF实际公开异常被忽略时拒绝满足；预期异常、有字段但操作失败、不同场景异常、未知操作结果逐项验证。
- [ ] 实现操作成功/失败/未知、结果完整性、义务关联和异常依据校验；诊断与验收共用。
- [ ] 除KOPF外回放至少一种其他注册生产者/传输记录，另测无异常字段、预期拒绝、部分成功和未知生产者；不能因没有结构化操作能力而阻断全部其他案例。
- [ ] 接入既有纠错额度，合并独立错误；无生产依据时回补证，不能强造违反项。
- [ ] GREEN：当前真实公开输出零模型回放通过；正常异常和证据身份回归通过。

## Task 3: 语义候选晋级与回滚

**Files:** Create `src/contractedge/orchestration/candidate_guard.py`; modify `orchestration/repair.py`, `feedback.py`及导出记录；create `tests/test_candidate_regression_guard.py`.

**Interfaces:** `compare_candidate_checks(incumbent: dict, trial: dict, preservation_ids: list[str]) -> dict`输出PROMOTE/REJECT_REGRESSION/UNCOMPARABLE及逐项差异，结果含两侧snapshot、谓词、场景、程序摘要、输入和环境身份；`restore_candidate(source: str, checkpoint: dict) -> dict`只恢复本案例受控文件并返回验证摘要。

- [ ] RED：11/3变7/7拒绝晋级；关键保持失败不能被其他PASS抵消；不同场景、环境异常不可误判回归；原子联合修改允许。
- [ ] 实现当前候选/试验候选记录、受控快照与恢复；完整保留失败试验，不执行工作区级git reset。
- [ ] 确保最终patch、当前源码、feedback、scoring identity一致；下一轮失败观察仍绑定失败试验原快照。
- [ ] 保留公开UNKNOWN候选输出能力，但不宣称已修复；无新进展时停止。
- [ ] 增加非KPT的对象/请求结果回退夹具；固定非确定性检查的两侧复测规则，验证原子联合编辑、中断恢复、检查程序变化和回滚后的反馈一致性。
- [ ] GREEN：回滚失败、文件创建、快照错绑、末轮失败和首候选未充分可观察等反例通过。

## Task 4: 缺口角色覆盖与最终请求送达

**Files:** Modify `source/entrypoints.py`, `source/units.py`, `retrieval/candidates.py`, `orchestration/runner.py`; extend `tests/test_production_path_frontier.py`，新增送达反例。

**Interfaces:** `locate_gap_roles(snapshot, gaps: list[dict], limits: dict) -> tuple[list[dict], dict]`输出有来源的片段与扫描审计；`audit_gap_delivery(gaps: list[dict], selected: list[dict], serialized_request: str) -> dict`记录找到/选入/实际送达。

- [ ] RED：Alertmanager精确缺口端点被低价值重复片段挤掉时失败；最终裁剪丢片段时拒绝声称补证完成。
- [ ] 实现最多2层、4新增文件的定向定位，沿现有扫描预算运行；Top12内最多6片段用于缺口角色，保留必需原证据。
- [ ] 根据公开任务机制选择配置/控制/变换/请求/状态路径模板，避免所有案例套用状态读写角色；最终送达通过解码结构化消息核对片段身份、正文和摘要。
- [ ] 记录词法提示、直接引用与未确认连接，禁止把候选路径当作因果或编辑授权。
- [ ] GREEN：真实Alertmanager前半程请求零HTTP捕获可见必要角色；普通配置与Helm检索回归通过。

## Task 5: 辅助调用前提与行为验证

**Files:** Modify `execution/helper_context.py`, changeset schema/validator、执行请求、`execution/probe_specs.py`, `probes.py`；新增调用前提与资源生命周期反例。

**Interfaces:** `validate_helper_preconditions(plan: dict, changed_source: dict, helper_evidence: list[dict]) -> dict`输出可证/冲突/未证前提，无法静态判定者引用冻结公开检查，禁止以模型自报替代。

- [ ] RED：KPT无selectors但configPath进入MergeWithInput时缺资源ID前提可被具体公开检查揭示；标注未清理、重复/丢失资源及新增未登记调用均覆盖。
- [ ] 提取并绑定实际调用点、初始化/清理、证据和验证方式；编辑后重新核对调用，现有模型请求生成有来源的修订策略。
- [ ] 限定机械检查器支持的语法与关系；测试不同分支、别名未知、动态调用和另一类初始化/清理示例，防止文本位置检查冒充控制流证明。
- [ ] 若现有14项不足，冻结新探测版本并保留旧版；校准Broken与保存失败补丁，记录适用范围。
- [ ] GREEN：初始化/合并/清理联合修订、mutator/validator和无configPath控制均核验；环境错误不产生语义PASS。

## Task 6: 冻结、真实复测与交付判定

**Files:** 新版配置、冻结收据、开发运行记录、原方案逐条需求映射、验收与能力矩阵；保留0.5.5原输出。

- [ ] 核对Task 0全部预登记与费用准入；先跑跨机制正反例，再跑完整工程回归。
- [ ] 全部前置回归通过后冻结方法与探测；执行A+B共17条旧/新版34次交错配对，B不回流本轮调参。
- [ ] 执行KOPF/Alertmanager额外4次旧/新版重复运行，以及两个真实失败起点的8次配对续修；完整记录，不重试到成功。
- [ ] 候选冻结后单独独立评分；评分不回流同案例修复，不使用历史最好成绩补齐。
- [ ] 要求原5通过不退化、KOPF/Alertmanager真实独立通过、跨仓库真实续修通过、谱系及回归门全部通过；B组新版通过数不低于基线，逐条审查仅旧版成功的回退，不能用平均值抵消。
- [ ] 输出实现完整性与修复效果两份验收，记录所有UNKNOWN和失败；小样本重复成功不声称显著泛化提升。

测试执行使用项目现有Python环境；实施前确认解释器与依赖路径，记录每个目标测试的RED/GREEN命令及输出，再执行完整测试。不得把本计划的预期断言当作已经执行的验证。
