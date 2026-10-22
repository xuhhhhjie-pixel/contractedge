# 目标逐项验收

依据：用户目标附件goal-objective.md及docs/superpowers/plans/2026-10-20-system-optimization-implementation.md。目标为保留架构，实施Diagnosis、Semantic ChangeSet、执行反馈三批改动并做有限真实开发验证；目标明确不要求原失败案例全部成功，修复率提升须由结果确认，正式150条和消融暂缓。

本验收证明本轮范围内的实现、约束及有限验证，**不证明所有诊断正确、所有补丁忠实于语义或全150条均可修复**。最终源码为0.4.3，摘要5c2dc5800aec314cae60571dfc90e379ea78a72f6f1a8b26f8eed99d01f32a5c。

| 要求/步骤 | 实现与权威证据 | 结论及支持范围 |
|---|---|---|
| 冻结150，保留身份与公开/私有隔离（0–1） | dataset/benchmark.py；capability-audit150.json；release-integrity-final.json；各run的MethodInput/projection/源码清单 | 150条能力来自固定公开task/environment；MethodInput摘要不增字段；所有冻结校验条目通过。方法侧不载入私有评分材料，独立评分在候选固定后执行 |
| 复用义务原子，保留原文/触发/保持（2） | diagnosis/prompt.py；contracts/validation.py；tests/test_contracts.py；test_translation_preserves_trigger_dependency_and_open_atoms | 没有增加需求理解调用或用正则重写自然语言。提示和引用/覆盖校验增强；自然语言语义完整性仍需观察，不能以字段存在证明正确 |
| 降低重复窗口，避免固定种类配额（3） | source/units.py；retrieval/candidates.py；test_repeated_windows_cannot_crowd_out_a_related_distinct_file；test_unit_pool_reaches_next_file_before_exhausting_first_document；5条replay-summary.json | 使用交错有界源码池和软惩罚；不会全面排除文档/测试/生成代码，也不禁止必要的同文件第二片段。多样性不作为因果相关率或修复成功率 |
| 按缺口只读取证（3） | extract_gap_extension_units；test_exact_gap_reads_require_same_gap_query_and_preserve_source_identity；replay/ACK轨迹 | 精确公开路径及同一缺口的符号/查询可取得正文，记录EVIDENCE_GAP_EXACT_PATH；拒绝越界、跨缺口拼接、假符号；不扩大编辑权限，不把关联冒充调用边 |
| 校验最终请求并记录裁剪（3） | orchestration/runner.py fit_diagnosis；trim_context_bundle；test_context_trimming_preserves_reference_pair_and_records_removed_bundle；replay的实际request/context_audit | 需求、Schema、观察在完整请求预算内计算；参考边组件与窗口一起裁剪；最终回放绑定0.4.3，不能只凭初始Top-N声称证据存活 |
| 断点表达、静态支持与不确定性（4） | diagnosis/prompt.py；validate_diagnosis；tests/test_diagnosis.py | 预期契约/当前证据/违反/义务链保留；静态机制充分时可修复，缺少未来行为观察不一概阻断；仍拒绝无生产消费路径等非法诊断 |
| 一次反馈安全可独立检查的错误（4） | diagnosis_preflight_issues及graph_issues；validation-replay.json；graph-validation-replay.json；test_independent_relation_error_is_visible_with_a_schema_type_error；test_independent_graph_errors_are_reported_together_without_relaxing_paths | 类型与关系形态及结构合法后的独立图错误分阶段汇总，保留首错误代码和严格后续校验；存在依赖的检查不对坏结构强行推理，不自动改成READY，不提高纠错次数 |
| 计划回答为什么/哪里/动作/条件/判断/保持（5–6） | changeset/prompt.py；changeset/topomen.py；existing target/depends_on/violation/postcondition/coverage validators；plan-quality-audit.json | 复用原计划体系增强内容，条件/依赖/义务进入执行上下文；人工公开量表区分具体性与覆盖完整性，没有新增脆弱关键词“正确性评分” |
| 不遗漏已支持义务，区分执行与可验证（5–6） | changeset schema的verification_kind；tests/test_changeset.py；test_unobservable_behavior_is_unknown_without_a_paid_review | 保留现有引用、冲突、依赖、覆盖、身份校验；不可观察不等于合法计划不可执行，也不等于义务通过 |
| 真实TopoMen与必要只读支持（7） | execution/topomen_agent.py；execution/topomen.py；test_topomen_agent.py；test_execution_reads_bounded_support_without_granting_edits_or_reusing_stale_quotes；单条真实运行的backend记录 | 使用已审计的实际DefaultAgent与受限源码环境；只读接口片段验证snapshot、摘要、原文且受总上下文预算约束；未接旧部署调度器 |
| 核对操作覆盖、权限和原子性（7） | apply_edits operation_coverage；test_scope_and_symlink_are_checked_before_model_edits；test_invalid_second_edit_keeps_entire_source_batch_unchanged；test_plan_applies_literal_edit_and_rejects_stale_second_application | 拒绝范围/操作越界、陈旧目标及无效锚点；批量应用原子回滚，补丁基线仍为Broken。确定性操作覆盖不证明所有源码语义或无关行都正确 |
| 按公开能力检查（8） | public_execution_capabilities；public.py；tests/test_optimization_contract_flow.py；single-v4的原始公开输出与public-review | 渲染检查实际字段/开关场景；已有行为设施复用；构建型任务保留行为UNKNOWN，不生成任意探测、不读取隐藏期望值 |
| 确定性公开输入/输出比较（8） | execution/comparison.py；test_structural_comparison_uses_actual_inputs_and_rejects_missing_equal_missing；test_review_cannot_claim_pass_with_contradictory_deterministic_comparison | 值来自公开输入与实际JSON输出；缺失不等于缺失，类型差异不被相等掩盖；相等仍不证明所选字段语义覆盖完整 |
| 观察关联回O/A/E/V/C/P（9） | make_feedback coordination_links；test_feedback_links_failure_to_relation_change_and_actual_observation；test_feedback_context.py | 引用关联实际to_snapshot与观察ID；旧快照不能重标为当前，不将关联自动视为因果归因证明 |
| 跨轮保持和未完成义务（9） | review_plan_with_retained；test_retained_feedback.py；test_unresolved_obligation_survives_rediagnosis_without_forged_current_atom_refs；test_unobservable_boundary_survives_rediagnosis_and_cannot_be_cleared_by_build_quote | 历史义务保留但不伪造新原子引用；明确不可观察标记在P/OPEN/PRESERVE及同义务新ID间保留，编译不能解除 |
| 当前证据和有依据的简单路由（9） | make_feedback/validate_feedback；test_pass_and_fail_need_fresh_exact_observation_quotes；test_environment_failure_never_routes_to_diagnosis；test_repair_execution.py | PASS和FAIL均须当前观察的精确原文；环境/未知停止，具体编辑失败返回EXECUTE，已支持的计划/诊断归因才改相应阶段；无新增自主观察状态机 |
| 稳定有界纠错 | test_planning_truncation_recovery.py；test_optimization_integrity.py；test_review_integrity_retry.py；review-live-regression；真实R1复核两次记录 | 原单次纠错在同轮处理已返回且未改源的执行格式错误；reasoning主导截断可显式局部关闭thinking。公共复核错误汇总及比较细节继续严格拒绝。一次真实纠错验证传输生效但最终比较仍被拒绝，不记成功 |
| 真实中断不盲目重放 | ../deepseek-optimized-review-regression-v1-20261020/interruption-recovery.json、model_calls.json、batch.json及MoneyBudget账本 | 进程句柄缺失且批次锁可取后，max_new_cases=0恢复为INTERRUPTED；未返回响应全额预留，不发送替代HTTP请求，不计通过 |
| 三批改动的通用回归 | tests-final-audit.log及tests.json；test_optimization_evidence.py、test_optimization_contract_flow.py、test_optimization_integrity.py、test_review_integrity_retry.py及既有执行/反馈测试 | 262项测试及9项子测试通过，包括配置字段传递、条件开关、只读跨文件引用、历史状态保持、编译/行为/环境/观察不足/旧快照处理；测试范围不替代全数据集效果 |
| 固定实现后有限真实验证（10） | 0.4.2 fixed5、new-development2的源码冻结/候选固定/独立cohort；0.4.3 single-v4及review-regression-v1；final-validation.json | 既有成功路径、原失败瓶颈、新仓库开发案例均有真实记录。最终小改动另有单条完整PASS、定向R1完整PASS；额外RW086回归中断未验证。版本、失败及中断分开报告，不混算或选择最好补丁 |
| 保留模型/预算/两轮与复杂度边界 | 最终与原budget80开发配置的稳定字段比较；实际trace预算；final-validation.json费用；final-code-review.json | 模型、真实执行后端、Top-N=12、一次扩展、12调用/两轮/原输出token上限保留；共享80元含历史与未结预留。无新增运行时Agent、图数据库、集群平台、测试生成或候选竞赛 |
| 评分器冻结、正式与消融暂缓 | release-integrity-final.json；独立评分的候选SHA核验；CURRENT_STATUS.json | 所有冻结校验通过；仅固定候选开发评分，结果未回送修复模型。当前方法未启动正式150或消融，旧就绪凭据仅历史参考 |

## 结论边界

实施及有限验证范围已具备可检查的交付证据；不能把“测试通过”“证据更多”“构建通过”升级为“修复效果优秀”。原5条本轮1/5独立通过，历史为2/5；新增仓库2/2通过不构成统计上的明显提升证明。KPT语义失败、其他案例证据/诊断停止及中断记录继续保留。这个效果判断与目标中“由真实结果确认，不要求所有失败变成功”的规定一致。

验收没有将中断的RW086记为完成或通过；当前最终复核分支的证据来自独立5项回归、记录截断后的真实纠错传输、RW130和RW067完整真实运行。RW086的那一次中断试验自身仍未验证，不支持对其作结果推断。

## 独立审查与工程决定

初次独立源码审查发现不可观察标记跨轮丢失，已以失败先行的回归修复。新增0.4.3公开复核逻辑另有独立审查及5/5独立测试，无新正确性或安全发现。文档滞后已修正。

保留既有输出账本、基线和隔离副本用于审计；不为技能流程另建或清理一套历史工作树。该决定的代价仅是保留额外本地审计文件，不改变方法运行。没有待合并、发布或推送的外部操作。
