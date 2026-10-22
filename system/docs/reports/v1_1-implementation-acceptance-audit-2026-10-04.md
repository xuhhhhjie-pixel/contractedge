# ContractEdge v1.1 前三阶段实现验收审查

日期：2026-10-04  
规格：`docs/specs/ContractEdge_First_Three_Stages_v1.1.md`  
范围：Requirement → Diagnosis → Semantic ChangeSet；不把 TopoMen/执行器实现计入前三阶段完成度。

## 当前结论

前三阶段已有可运行的实现骨架、数据导出与隔离、确定性校验、有限状态编排和评测适配器；目前处于**实现验证阶段，尚未通过完整验收**。主要未完成项是：R5 公共任务文本的来源审查、源码解析器的语法一致性覆盖、开发 pilot 的完成与独立语义复核，以及真实 TopoMen 消费端兼容验证。当前本地 pilot 标记为 `development_only`，只包含 R1/R2，不能作为正式实验结果。

## 验收项逐条对照

| 规格项 | 当前证据 | 判定 | 仍需完成 |
|---|---|---|---|
| 1. 导出契约与方法/评价隔离 | `src/contractedge/dataset/{export,releases,audit}.py`；`outputs/pilot-v1_1-r1-r2-v2/` 包含 87 个 R1/R2 公共输入及独立 evaluator 视图；R5 30 项因来源未审定而拒绝进入 MethodInput；`outputs/r5-v28-adapter-review/public-task-review-v2.md` 有 11 条公开 issue 来源草案（12 个来源），来源摘要哈希本轮逐条复核通过 | 部分通过 | 11 条仍待人工忠实度/泄露审查，另 19 条需要新写公开任务文本；R5 的 25 条 pairwise family 标签差异已逐 pair 列出，仍待 rationale-level adjudication；发布哈希只证明文件完整，不证明任务有效 |
| 2. 源码与 scope | `src/contractedge/source/{snapshot,scopes,parsers,units,absence}.py`；支持路径/符号/概念 hint、有界文本 fallback、一跳直接引用及一次有界 evidence-gap 搜索 | 部分通过 | Go/TS/JS 当前是词法声明提取，不是完整 AST；需补真实语法边界/异常输入/Helm 模板解析一致性测试，并记录不支持语法的降级比例 |
| 3. Requirement 忠实度 | `src/contractedge/requirement/normalize.py` 保留原始文本与原文区间，粗粒度 O 不主动拆分推断 | 结构实现通过，语义验收未通过 | 对开发 pilot 人工检查条件、否定、时序及所有 outcome 的保留情况；观测边界浅时仍保持完整需求范围 |
| 4. Diagnosis | `src/contractedge/diagnosis/`、契约 schema 与 validator 支持证据引用、关系/violations、观察边界、因果状态、部分诊断及缺失证据约束 | 结构机制已实现，六类验收未整体签收 | 汇总并逐项验收六类协议样例：仅症状、缺失角色、同文件多角色、多断点、部分证据、引用不支持主张；结构 PASS 不代表语义正确 |
| 5. Semantic ChangeSet | `src/contractedge/changeset/adapter.py`、`contracts/validation.py` 支持 EXISTING/NEW、快照/digest、planned-V 覆盖、postconditions、preserve/open obligations 和依赖图检查 | 结构机制已实现，语义验收未通过 | 在独立语义审查中确认新增模板与已有文件新增字段两类目标；检查不过度修改、保留约束和未决义务 |
| 6. 有限迭代与快照 | `src/contractedge/orchestration/runner.py` 记录调用、预算、snapshot、一次 evidence extension 和一次受预算约束的格式修正；返回明确失败状态 | 部分通过 | 用跨快照旧引用、语义失败但 wrapper exit 0、环境失败、扩展耗尽等执行记录验收路由；完整工作流尚未做最终审阅 |
| 7. 开发 pilot | `outputs/pilot-v1_1-initial-20-v2/` 冻结 20 个 R1/R2 输入、模型 digest、tokenizer revision、Top-N 配置与实现 digest；R5 明确 hold | 运行完成；语义验收未通过 | 20/20 项均有运行记录与 evaluator 分数：6 READY、5 EVIDENCE_GAP、4 BUDGET_EXHAUSTED、5 INVALID_OUTPUT；14 项含 Diagnosis。自动关系匹配为 0，且 scorer 有端点别名/粒度风险、无独立语义裁定，不能解释为最终准确率。现需裁定输出与 GT，并修复重复 outcome 覆盖错误、跨文件目标歧义和预算问题，再开冻结的新 pilot |
| 8. 进入实际执行层 | `src/contractedge/changeset/topomen.py` 定义拟议转换形状，`consumer_acceptance=UNVERIFIED` | 未通过（外部依赖） | 需要真实 TopoMen consumer 源码/接口或可执行程序；适配器契约测试不能代替消费者接收验证 |

## 本轮新增的 pilot 证据

`RW-CAND-109` trace 位于 `outputs/pilot-v1_1-initial-20-v2/traces/RW-CAND-109.trace.json`，由冻结模型调用完成 Requirement → Diagnosis（包含一次 extension 和一次格式修正）→ Semantic ChangeSet：

- 流程状态 `READY`，结构校验 `PASS`，生成 1 项变更。
- 语义审查是 `UNCERTAIN / NOT_CONFIGURED`，没有独立 reviewer。
- 独立 evaluator 结果写入 `outputs/pilot-v1_1-initial-20-v2/evaluator/RW-CAND-109.score.json`：预计关系 2 条、参考关系 3 条，语义匹配 0；violation 预测 1 条、参考 1 条，匹配 0。此结果是该 case 的一次诊断失败/不匹配证据，不能据此推断整批总体效果。
- R1 GT 的关系链显式包括 Values → Template → Generated Kubernetes Resource → Runtime Effective State；模型提出前两段，且其目标角色和关系语义未通过当前 evaluator 对齐。需要人工判断属于模型漏链、GT/别名粒度问题还是适配评分问题，不能自动改 GT 来抬高分数。
- 对 `RW-CAND-114` 的零匹配进一步定位到评分风险：预测单元的 `kind` 是 `Helm Values` / `Helm Template`，但当前 `_unit_roles` 仅取模型的 `role` 字段与 GT 的规范化 `role` 字段计算 token-Jaccard，未利用这些直接工件类型；模型还把 violation 绑定到 Values→Template，而 GT 将 violation 记在 generated→runtime 关系。前一种差异可能造成误判，后一种则是实际边界/关系粒度分歧。故该自动零分需要人工裁决后才能解释为纯模型错误。原始 scorer 已冻结于 `outputs/pilot-v1_1-initial-20-v2/evaluator/scoring_metadata.v1.json`；若修评分器，必须另存版本化重评分结果，不能覆盖原始分数。
- `RW-HELM-KEDA-EMPTY-ANNOTATIONS-001` 的模型诊断结构通过，但 ChangeSet 与一次修订都选择了跨多个源码文件的 SemanticUnit，runner 因现有目标协议要求单次 EXISTING change 对应一个文件而返回 `MODEL_EXISTING_TARGET_PATH_AMBIGUOUS` / `INVALID_OUTPUT`。这是已观察到的计划落地接口失败；pilot 后应检查提示的目标选择限制和多文件拆分规则，不在冻结批次中途改动。
- 已生成 evaluator 私有[完整批次语义对齐审查包](../../outputs/pilot-v1_1-initial-20-v2/evaluator/semantic-adjudication-current-v2.md)，并保存[20 例自动分数汇总](../../outputs/pilot-v1_1-initial-20-v2/evaluator/pilot-score-summary.v1.json)。审查包对照全部预测与参考 GT，独立审查栏仍为 pending；自动结果不得当作独立人工结论。

冻结批次配置为 `outputs/pilot-v1_1-initial-20-v2/config-topn-{8,12,16}.json`；20/20 条均完成，状态为 6 READY、4 BUDGET_EXHAUSTED、5 EVIDENCE_GAP、5 INVALID_OUTPUT。20 条 trace 均由独立 evaluator 评分，14 条含 Diagnosis，16 条具有可计数的关系指标（预算耗尽项没有可评分预测）。自动器累计 ExpectedRelation 为 0/22 匹配、Violation 为 0/11 匹配；参考侧对应 44 条 Expected、24 条 Violation。该结果未经过独立语义裁定，且 scorer 有端点别名与粒度限制，不能把 0 匹配当最终准确率。失效分母保留全部 20 条；`RW-CAND-067` 是被触碰的规格示例，需从未触碰准确率中排除。此次 pilot 只覆盖 R1/R2，不能称为 R1/R2/R5 正式 pilot。

## 验收边界

本次代码级复核中，`PYTHONPATH=/tmp/contractedge-pilot-deps:src python3 -m unittest discover -s tests -v` 通过 **107 项**；`python3 -m compileall -q src tests` 与 `git diff --check` 通过。`PYTHONPATH=src python3 -m contractedge.cli audit-release --release-root outputs/pilot-v1_1-r1-r2-v2 --expected-members 87` 返回 PASS：87/87 公共与 evaluator 成员、231/88 哈希条目。以上是单元/契约与打包完整性证据，不是完整 pilot、独立语义评审或行为复放。

1. `structural_validation=PASS`、合法 JSON、完整证据 ID 或 `READY` 仅证明工程协议走通，不证明语义正确。
2. 当前 GT 匹配是自动候选评分。模糊端点、关系粒度和 GT 多边拆分仍要人工裁定；未裁定的指标应标为不可用或待审，不能用它作为系统通过证据。
3. R5 发布清单正式成员、MethodInput 可用性、关系指标资格是三个不同判断。当前 R5 30 条仍未进入 v1.1 公共输入。
4. 系统前三阶段可以在真实 TopoMen 未提供时继续独立开发和协议验证，但不能宣布“已通过实际消费者验收”或完整闭环完成。

## 最终验收前必须关闭

- 完成冻结探索批次并保留所有失败分母，排除 prompt-touch/spec-example 样本的未触碰准确率汇总。
- 完成开发样本 Requirement、Diagnosis、ChangeSet 的独立语义评审，逐案对齐 GT；若修改提示/解析/匹配器，使用新 pilot ID 重跑，不混算旧结果。
- 扩充并验证 Go/TS/JS/Helm/YAML/JSON 的解析器 conformance fixtures，报告词法 fallback 和未解析 scope。
- 完成 R5 公开任务来源与文本审查、R5 GT 资格裁定后，才允许把 R5 纳入方法端 pilot。
- 提供真实 TopoMen 消费端并验证代表性 Existing、New、Partial 计划；否则保留消费者验收未验证状态。
- 在冻结发布前重跑完整测试、release/hash/isolation audit、cohort accounting 与最终 pilot 报告，并保存命令、版本、模型/tokenizer digest 和输出。

## 后续修正与最新验证

随后针对 `RW-HELM-KEDA-EMPTY-ANNOTATIONS-001` 的实际接口失败完成了 ChangeSet 目标约束：模型必须从同一源码路径对应的 SemanticUnit 中选择一个文件作为 EXISTING 目标；确有多个目标文件时应拆成多条 change。runner 现在把路径歧义等 adapter 错误码原样用于一次受限修订，而不再一律改写成 `INVALID_JSON`。这修复了已观察到的接口缺陷，但没有重跑旧冻结批次，也没有把旧分数移植到新实现。

最新验证为 `PYTHONPATH=/tmp/contractedge-pilot-deps:src python3 -m unittest discover -s tests -v` **113 项通过**；`python3 -m compileall -q src tests`、`git diff --check` 均通过；R1/R2 发布审计仍为 87/87 成员、231 个 public 与 88 个 evaluator 哈希条目通过。

另用修正后的实现，在未向方法进程提供 GT 的公开 R1 `RW-CAND-124` 上运行一个单例开发冒烟：Requirement → Diagnosis → Semantic ChangeSet 到达 `READY`，结构校验 `PASS`，1 个 outcome、2 个 semantic units、1 条 ExpectedRelation、1 条 Violation 和 1 个 EXISTING 修改计划；Diagnosis 使用一次格式重试。模型审查仍是 `UNCERTAIN / NOT_CONFIGURED`。冻结配置、实现 digest、原始 trace 和结果说明保存在 `outputs/pilot-v1_1-postfix-smoke/`。这是协议贯通证据，不是语义正确性、行为修复或 20 例 pilot 的证据。

因此，上一版 20 例冻结 pilot 的失败分母和分数保持不变；它仍未通过语义验收。当前完成了该批暴露的一项 ChangeSet 目标接口修正和一次新实现单例冒烟，仍需完整新 pilot、逐案独立语义评审、R5 可接受公开任务、解析器 conformance 审核，以及真实 TopoMen consumer 的接收验证。

该单例的 evaluator-only 自动匹配记录为 ExpectedRelation 0/1、Violation 0/1，参考侧为 3 条 Expected 与 1 条 Violation；预测把失效关系挂在“配置→Deployment pod spec”，GT 则挂在“生成 Deployment→运行时身份绑定”。这提示必须裁定关系分解和观察/故障边界，不能将零匹配直接解释为纯模型错误或 GT 错误。`outputs/pilot-v1_1-postfix-smoke/evaluator/semantic-adjudication.md` 已保留预测与 GT 对照，所有独立人工裁定仍为 pending。

## 2026-10-05 解析和有限重试验收补充

发现 `.yaml/.yml` 中含 Helm `{{ ... }}` 动作时，旧实现仍可能把未渲染模板作为普通 YAML 节点树返回。现在这类文件使用显式 `HELM_YAML_TEMPLATE_FALLBACK` bounded-text provenance；增加的回归先复现旧误分类，再验证新标记。R1 `RW-HELM-GRAFANA-MCP-INGRESS-001` 的公开输入 smoke 选中单元同时包含 `HELM_YAML_TEMPLATE_FALLBACK` 与普通 `YAML_NODE_TREE`，确认实际候选提取路径采用了该边界。

同一 smoke 暴露诊断校验失败后的 retry 数量未遵从配置上限：模型第一次校正仍有语义合同错误时 runner 立即退出，未用完 `max_format_retries=2`。runner 现会在全局预算内继续使用剩余格式重试，并把每次校验错误及反馈写入 trace。合成回归覆盖“格式错误 → 第一轮修正仍缺支持 Violation → 第二轮合法”的完整路径。真实模型新 smoke 在一次格式修正后完成 `Diagnosis=EVIDENCE_GAP` 和一次有界检索扩展，最终也保留为 `EVIDENCE_GAP`（结构 PASS、2 units、1 Expected、1 Violation、1 open gap），没有产生 ChangeSet；这说明合同错误被拒绝并转成可审计的证据缺口，不证明该案例的语义预测正确。配置、实现 digest、trace 和摘要在 `outputs/pilot-v1_1-helm-bounded-retry-smoke/`。

该次修改后的全量回归为 **115 项通过**；`compileall`、`git diff --check` 通过；R1/R2 release audit 仍为 87/87 成员、231/88 哈希通过。两条早期 Helm smoke 的 `INVALID_OUTPUT` trace 保持独立且未覆盖。独立语义评审、R5 输入资格和真实 TopoMen consumer 验收仍未关闭。

## 2026-10-05 YAML 大文件解析性能修正

为核查解析器在真实发布源码上的负载，对 R1/R2 archive 启动过全量 YAML/代码解析盘点。首个案例完成后，第二个案例的 8,840 文件归档在解析 YAML 时持续占用 CPU。中断前的栈明确落在 YAML `line_offset()`：每个节点都重新扫描全文构建换行偏移表。针对 80 个 YAML 节点的回归先观察到 162 次全文换行扫描，再修正为每文件一次。第二次盘点又定位到 `_unit()` 按节点重复调用 `text.count()` 计算行号；节点的 `start_mark/end_mark` 已提供同一信息，改为直接传递 parser 行号。该回归再观察到 `_unit()` 为每个节点重复调用 `splitlines()` 82 次；移除未使用的行数组参数后，每份 YAML 只由有界处理构建一次行数组。三个重复工作均有回归覆盖，parser 套件 **12/12** 通过。

修正后的验证：全量 `unittest` **117/117** 通过，`compileall` 和 `git diff --check` 通过，R1/R2 release audit 为 **87/87**、public/evaluator 哈希 **231/88**。cohort accounting 仍为 117 个冻结成员、87 个已准备和 30 个 R5 暂缓。对 `RW-CAND-068` 归档中最大 YAML 文件（188,334 字节）的单文件核对产出 5 个 `YAML_NODE_TREE` 单元，耗时 0.608 秒，单元行数受 80 行上限约束。全量 archive 盘点在第二例完成（79.4 秒）后停止，因为逐案解析源码中所有 YAML 文件过宽且不代表按 seed scope 运行；因此这里没有宣称完成 R1/R2 全源语法覆盖。R5 的公开任务/GT 人工裁定、独立语义 pilot 和实际 TopoMen consumer 接收仍未通过。

## 2026-10-05 有界 scope 解析

对 87 个已准备 MethodInput 重新统计 scope hints，发现 59 条只有明确 path hints，28 条含 concept hints。旧 `resolve_scope()` 在处理 path-only case 时也会先读取整个仓库内容。现在 path hints 仅按已哈希文件目录做匹配；符号/字段/concept 内容搜索采用可配置上限（默认 20,000 文件、64 MiB 总内容、单文件 2 MiB），并在 `ScopeResolution.search_audit` 记录查询、命中、扫描量及是否完整。达到限制或遇到无法读取/过大的文件时，hint 保持 unresolved 并带 `search_complete=false`，不能被下游误认为源码中不存在。所有预算值进入 RunContext 的 retrieval config。

回归先因路径解析读取文件内容而失败，预算回归先因 `resolve_scope` 不接受搜索限制而失败；实现后 scope/orchestration 定向测试 **5/5** 通过。当前全量 `unittest` **119/119** 通过，`compileall`、`git diff --check` 和 R1/R2 release audit 通过（87 成员，231/88 哈希）。这实现了有限且可审计的范围解析；28 条 concept 输入在默认预算内是否完整命中仍需通过独立 source-scope conformance/pilot 逐案评估。

## Scope-resolution and ordering follow-up (2026-10-05)

The reproducible [R1/R2 scope audit](source-scope-conformance-r1-r2-2026-10-05.md) validates all 87 public inputs against their Broken archive digests and initial snapshots. Under frozen retrieval defaults, 57/57 R1 cases fully resolve path hints; R2 has 2 fully resolved cases, 25 with incomplete scope coverage, and 3 with at least one unmatched hint. The audit found 31 searches hitting file/byte budgets, 66 per-hint result lists capped at 64, and 9 zero-match hints. R2 scope coverage is therefore a reported limitation, not a basis for absence claims.

Two silent-truncation risks were fixed with regressions: match-capped lists remain explicitly incomplete in `ScopeResolution`, and source-unit extraction preserves deterministic relevance order instead of sorting seed paths alphabetically before exhausting the unit budget. Diagnosis prompt v1.1.19 receives bounded scope/cap/unresolved metadata and states that incomplete retrieval is not absence evidence. Path-token resolution reduced zero-match hints from 10 to 8; ambiguous suffixes remain unresolved. Final verification after the path-root regression: 126 unit tests pass; `compileall` and `git diff --check` pass; R1/R2 release audit PASS (87 members, 231 public / 88 evaluator hashes); cohort accounting PASS_WITH_HELD_MEMBERS (117 cases, 87 prepared, 30 R5 held). The exact per-case audit and script are `outputs/source-scope-conformance-r1-r2-v2.json` and `scripts/audit_source_scope.py`.

This improves retrieval honesty and prioritization; it does not establish concept-hint semantic precision, finish parser conformance, accept a new pilot, independently adjudicate semantics, qualify R5, or verify the actual TopoMen consumer. The full objective remains active.

## Absence search and evidence-gap follow-up (2026-10-05)

The new R2 smoke exposed a bounded-search defect: an exact path inventory lookup was rejected whenever the repository exceeded `max_absence_search_files`, even though the complete snapshot path inventory is already available. Exact path checks now use direct snapshot membership and remain complete without reading file contents; only content-based symbol scans consume that limit.

The same trace showed that a requested symbol could live in an already resolved seed file but remain outside the first `max_units` units. Evidence-gap expansion previously excluded every seed path, so it could not retrieve that source span. Expansion now considers unselected units inside seed files, prioritizes exact requested paths and ranked seed paths, marks same-seed additions separately, and prefers exact symbol/search-term matches over broad path-only matches. Regressions cover inventory lookup at a zero content-scan budget, retrieval of an unselected same-seed unit, symbol-specific filtering, preservation of the one-hop boundary, and incomplete symbol-search evidence.

A source-backed R2 offline replay then found `controller/state.go:CompareAppState` after a diagnosis gap request. A fresh pinned-model smoke consumed the expansion and emitted 2 ExpectedRelations plus 1 `TENTATIVE` Violation, but still retained 3 evidence gaps and stopped at `EVIDENCE_GAP`; no ChangeSet was produced. The evaluator-only automatic score after the method run matched 0/2 predicted ExpectedRelations and 0/1 predicted Violations against the eligible R2 GT. This is a negative development result and an automatic score, not independent semantic adjudication. Trace, score, and metadata are in `outputs/pilot-v1_1-gap-extension-smoke-r2-v3/`.

The latest full suite after these code changes passes 129 tests; `compileall`, whitespace checks, and the 87-member R1/R2 release audit pass. These fixes make exact evidence retrieval and one bounded extension behave as specified; the R2 run still demonstrates that structural validity and better retrieval do not guarantee a supported diagnosis or a ChangeSet. R5 qualification, semantic GT adjudication, a new stratified pilot, and actual TopoMen-consumer acceptance remain open.

## 2026-10-05 v3 matched-cohort rerun after prompt corrections

A new, non-overwriting pilot is frozen at `outputs/pilot-v1_1-postfix-r1-r2-20-v3-2026-10-05/`. It uses the same 20 R1/R2 MethodInputs and model/tokenizer/budget configuration as v2; only the versioned Diagnosis and ChangeSet prompts and implementation digest changed. R1 status counts are 3 READY, 4 EVIDENCE_GAP, 3 INVALID_OUTPUT; R2 counts are 1 READY, 8 EVIDENCE_GAP, 1 INVALID_OUTPUT. All 20 traces are accounted for. The evaluator cohort report includes all 117 release members, with R1/R2 unattempted members and all 30 R5 held members retained as NOT_RUN.

The status distribution changed from v2's 5 READY / 12 EVIDENCE_GAP / 2 INVALID_OUTPUT / 1 BUDGET_EXHAUSTED to v3's 4 READY / 12 EVIDENCE_GAP / 4 INVALID_OUTPUT. There is no demonstrated semantic improvement. The automatic relation scorer reports 0 matches, but that figure is not final accuracy because endpoint aliases and relation granularity remain unadjudicated.

All four v3 READY plans were compared with evaluator-only GT and receive REVISE; none is accepted. The detailed case reviews are in `outputs/pilot-v1_1-postfix-r1-r2-20-v3-2026-10-05/evaluator/semantic-adjudication/README.md` and its four JSON records. The matched v2/v3 comparison is `evaluator/v2-v3-prompt-comparison.json`; summary and hash metadata are `evaluator/pilot-summary.json` and `pilot_metadata.json`. Reviews are assistant post-run judgments, not independent human approval or behavior replay.

This pilot identifies two further implementation requirements: independently broken obligations need explicit decomposition and traceability so they are not merged under one coarse outcome; and a proposed configuration edit must have evidence for the production consumer path through to its generated/runtime contract. A values key with no evidenced consumer must not be treated as an accepted plan. These changes must be implemented as versioned contract/reviewer gates with new tests and a new pilot; no current score is retroactively changed.

Fresh code/release verification: 129 unit tests passed, `compileall` and `git diff --check` passed, and the prepared R1/R2 release audit passed 87/87 members (231 public, 88 evaluator hashes). Remaining gates are unchanged for R5 source/GT adjudication, non-READY case review, broader source parser conformance, and actual TopoMen consumer acceptance.

## Continuation checkpoint: retry-state regression

On 2026-10-05 a matched v5 R1/R2 pilot was still running against implementation digest `e297f438cac66b824a40dc7c20b66881b43ace7d8e5523994a697bfb2a0a96ef`; at 7/20, the batch was processing `RW-HELM-GRAFANA-HTTPS-ROUTE-001`. A structural `READY` in the partial cohort is not semantic acceptance. The v5 traces confirmed diagnosis format retries could lose the previous draft and earlier validation feedback, allowing one correction to regress while another is applied.

A regression test was confirmed failing before the fix and passing afterward in isolated worktree `/home/xhj/contractedge-system-retry-fix`. The fix appends each rejected assistant draft and each validation message to the bounded retry conversation for both initial diagnosis retries and the single extension retry. In that worktree, all 136 unit tests passed, as did `compileall` and `git diff --check`. The active v5 run still uses the pre-fix implementation; after it completes, the fix must be transferred to the main working tree and validated with a new frozen pilot. These checks do not provide independent semantic review or actual TopoMen consumer acceptance.
