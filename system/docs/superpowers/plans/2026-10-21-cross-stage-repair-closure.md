# Cross-stage Repair Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复公开证据和必要前提在诊断、计划、执行与重新诊断之间的最早失效边界，并按固定开发面板验收交付。

**Architecture:** 复用现有有界检索、请求组装、ChangeSet验证和Feedback路由。跨轮传递有来源的定位信息，在当前快照重读证据；不新增独立Agent、全仓调用图或无限重试。先保存轨迹回放，再作最小产品改动。

**Tech Stack:** Python、pytest、现有JSON契约、TopoMen源码执行器、冻结公开探测。

**Spec:** `docs/reports/2026-10-21-optimization-reset-and-delivery.md`；保留 `docs/reports/2026-10-20-final-optimization-design.md` 与 `docs/reports/2026-10-20-reliability-closure-design.md` 原验收义务。

## Global Constraints

- 正式150、评分器与数据划分不变；只使用公开信息实现和调试。
- Top12、一次模型补证、12次调用、2轮语义编辑、既有扫描/请求预算不变。
- 共享150元总账有效（用户后续明确上调，原80元历史限额不再用于新增运行）；任何新付费运行必须先过回放门和实时费用准入。
- 不改正在评分的冻结副本、运行脚本、历史结果；B组在方法冻结前关闭。
- 保留脏工作区；不bulk stage、reset、clean或自动提交历史改动。使用执行时核验的隔离副本开展产品修改。
- 每个任务只改其失败复现确实要求的位置；找不到依据先补调查，不按本文件列出的可能文件批量修改。

## Review Focus

- 源码已变、文件删除或重命名：旧定位只能作为提示，旧正文/旧PASS不能冒充当前证据（Task 2）。
- 同名符号、动态分派或预算裁剪：必须显式缺口，不能虚假送达或扩大编辑权限（Task 2）。
- 路由结构与自然语言冲突：当前请求只能有一致的执行方向，历史解释仍可追溯（Task 3）。
- 非异步、配置和非Go任务：不能被强加无关就绪/初始化要求（Task 4）。
- 格式合法但必要条件缺失、有限检查通过但完整义务未证实：不误判充分或PASS（Task 4）。

### Task 0: 冻结现状

- [x] 暂停旧调度父进程派发，保留在途worker与独立评分；收据 `outputs/reliability-closure-20261020/queue-dispatch-hold-20261021.json`。
- [x] 保存结果截点与解码请求存在性核查，不声称完整回放已完成。
- [x] 写统一交付说明并明确历史版本不可混分。

### Task 1: 形成最早边界的可复现证据

**Files:** Create `outputs/reliability-closure-20261020/cross-stage-failure-alignment.json`、`outputs/reliability-closure-20261020/replay_cross_stage.py`；Read 0.6.10 Alertmanager `method_input.json`、`model_calls.json`、`rounds/*/front.trace.json`、`plan.json`、`feedback.json` 和公开观察。

**Interfaces:** 输入保存请求/响应与公开当前快照；输出事实来源、消费者阶段、实际正文摘要、缺失原因及断言依据。回放使用假provider，禁止网络；不得从独立评分器中获取修法。

- [x] 对齐需求、Broken、源码、诊断、计划、实际编辑、公开结果、反馈和下一轮；检查全正文而非只检查函数名。
- [x] 复现初诊/计划缺少初始化实现、重新诊断缺证及反馈相反指令；区分PARTIAL_READY校验终止与生产行为失败。
- [x] 判断最早因果断言是否确实需要该证据，记录其他可能原因；不预设“补代码一定修好”。
- [x] 让回放在旧冻结副本稳定报告相同故障；完成G1后才进入Task 2。

### Task 2: 必要证据跨阶段连续送达

**Files:** Modify only as required `src/contractedge/orchestration/runner.py`、`repair.py`、`src/contractedge/execution/helper_context.py`；复用现有source/retrieval定位入口与最终请求核验。Create `tests/test_cross_stage_evidence.py`；extend `tests/test_gap_delivery.py` only if needed.

**Interfaces:** 复用 `run_front_half(..., execution_feedback=..., public_observations=...)`；输入公开路径/符号/角色和来源快照，重读当前Snapshot；输出带当前ID/摘要的既有CandidateSet单元及缺口审计。只有Task 1证明既有数据无法表达时才版本化最小契约字段，禁止临时塞未校验字段。

- [x] 写旧实现失败的初诊、计划和重新诊断请求送达测试，包括真实旧输入投影及通用小仓库。
- [x] 运行 `PYTHONPATH=src:.topomen-deps python3 -m pytest -q tests/test_cross_stage_evidence.py`，确认失败原因正确。
- [x] 在原预算内把必要定位与当前正文接入现有请求路径；不把词法关联升级为因果证明。
- [x] 加删除/重命名、同名符号、旧摘要、裁剪、容量不足、只读权限及非Go对照。
- [x] 重跑定向测试及Task 1回放；对比旧/新请求、当前快照和总预算。

### Task 3: 路由与指令一致

**Files:** Modify `src/contractedge/orchestration/feedback.py`；Test `tests/test_predicate_progress_routing.py`、`tests/test_feedback_context.py`。

**Interfaces:** 现有 `reassess_failed_target_predicates(feedback, before, after, plan)`、`feedback_prompt_view(...)`；保留原公开事实，当前route与当前指令一致，历史解释单独可追溯。

- [x] 用保存feedback写失败测试：DIAGNOSIS不得同时要求“不要重新诊断”；EXECUTE/CHANGESET路径不被误改。
- [x] 运行测试确认红灯，最小修复生成/投影边界，不改失败事实或归因置信度。
- [x] 检查最终序列化的下一轮请求及历史事实保留；不只测试中间字典。

### Task 4: 计划必要条件与检查闭合

**Files:** Inspect/modify as justified `src/contractedge/changeset/prompt.py`、`validate.py`、`src/contractedge/execution/support.py`、现有前提检查实现；Test `tests/test_helper_preconditions.py` and Create `tests/test_plan_precondition_coverage.py`。

**Interfaces:** 使用现有前提、后置、保持、开放义务及检查覆盖记录；来源指向当前公开证据。计划未证实的条件保留开放，不依靠文字承诺晋级PASS。

- [x] 对Task 1的实际计划列出必要条件、已有依据、验证方式与未覆盖项，审查哪些能机械验证。
- [x] 写缺前提、冲突前提、无关前提及有限检查非充分的失败测试；加入KPT资源身份和配置任务对照。
- [x] 最小完善计划提示与校验；不能机械证明的时序条件进入公开探测/UNKNOWN，不用固定语句模式强行证明就绪。
- [x] Task 2—4组合回放初诊、重诊、执行及反馈；修复状态格式报错时不得放松语义契约或自动伪造缺口。
- [x] 运行相关测试后运行一次 `PYTHONPATH=src:.topomen-deps python3 -m pytest -q`；记录失败、修复和最终结果，不重复无理由全测。

### Task 5: 冻结真实验收与交付

**Files:** 新版freeze/registration、固定结果目录与交付报告；现有 `scripts/run_reliability_delivery.py` 等必须在旧worker/评分收尾后核验再使用，不随手覆盖冻结哈希。

- [x] 确认G1—G3工程门与原方案实现映射、实时费用和磁盘后冻结代码/提示/探测/配置；0.6.19收据已生成，效果验收不随之通过。
- [ ] G4真实单条与登记重复；公开目标、保持项和独立评分逐项验收。失败回到具体边界，不立即扩批。
- [ ] G5同版A/重复/双仓库恢复，然后冻结状态打开B；使用原登记与统一统计，不拼旧成功。
- [ ] 依统一说明G5判定配对改善、原5回归和迁移；未跑、环境失败、UNKNOWN独立报告。
- [ ] G6全部门通过后写交付包、能力边界与运行说明，才允许formal_ready=true；本任务不启动正式150或消融。

执行时不需要重新询问已经授权的普通修复；费用上限或验收范围若需要改变，必须明确说明，不能自行放宽。


2026-10-21执行更新：用户已批准累计API上限150元，原80元是历史截点；共享账本在旧worker收尾后持锁更新，历史调用与预留全部保留。当前执行记录见 `.superpowers/sdd/2026-10-21-cross-stage-repair-closure/progress.md`。

2026-10-22执行更新：Task1—4勾选表示已列工程回放和确定性反例完成，不表示模型修复可靠性达标。根工作区592tests+22subtests通过，0.6.19固定快照/配置/v8harness登记；G4进行中、G5/G6未完成。独立审查与纠错计数补充见 outputs/reliability-closure-20261020/CODE_REVIEW_v0618_v0619.md；所有新失败及各版结果保留。

2026-10-22续：0.6.19 G4新鲜单条Alert已独立PASS（8calls，3163bytes，build/4public checks PASS，semantic UNKNOWN保留）；登记重复仍未完成，因此上方G4完整勾选保留未完成。G5同版A已开始，现成Alert候选不重跑；B未打开。见G4_ACCEPTANCE_v0619.json。
