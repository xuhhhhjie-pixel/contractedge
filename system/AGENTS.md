> **2026-10-26 候选0.6.55：** 修复无保持ID时公开探测错误仍晋级，以及独立EXECUTION_FAIL状态漏接；已完成真实旧轨迹零调用回放和76项相关回归，正在新冻结完整资格验证。旧候选仍为失败，formal_ready=false，当前状态以CURRENT_STATUS.json为准。

> **2026-10-26 当前候选0.6.54：** 以0.6.53实际失败为依据，补齐读者共享引用的精确源码窗口、跨阶段必需来源，以及确定性负结果收束；117相关回归和公开旧轨迹零调用回放通过。尚待本版完整工程、隔离与目标效果验收，CURRENT_STATUS.json为当前入口，formal_ready=false。

> **2026-10-26 当前候选0.6.53：** 在已验证字段取证上补齐保持条件ID域、空集合模式与具体纠错反馈，105项相关回归及真实旧输出零调用回放通过；尚待本版完整工程、隔离及唯一目标效果验证。当前身份以CURRENT_STATUS.json为准；formal_ready=false。

> **2026-10-26 当前候选0.6.52：** 有界构造器实参、字段与具体接收者源码取证已完成79项针对性回归；实际Alert公开Broken初诊Top12已送达完整订阅实现，仍待本版完整工程、隔离资格及唯一目标验证。CURRENT_STATUS.json为当前身份入口，下列旧版本记载仅作历史。formal_ready=false。

> **0.6.51 v2准备（2026-10-26）** v1全回归2项旧input-only兼容性失败，已阻断付费；现79项相关回归通过。新完整资格必须绑定实际进程退出码和冻结/日志摘要，6个正反例通过。当前正式准入仍false，状态以CURRENT_STATUS.json为准。

> **2026-10-26当前候选0.6.51：** 分支目标缺失绑定反馈、原有重新诊断路由和补证上下文修复已完成58项针对性回归及公开旧轨迹零调用闭环。本版尚待完整工程、隔离资格和真实效果验收；当前入口CURRENT_STATUS.json及outputs/branch-target-binding-20261026/COMPLETION_AUDIT.md。0.6.50 Cert通过/KPT控制失败保留，formal_ready=false。

> **2026-10-26当前候选0.6.50：** 初始范围正文评分修复已通过72项针对性回归及公开旧轨迹零调用回放；本版完整工程、隔离资格和真实效果待验收。当前入口为CURRENT_STATUS.json及outputs/initial-scope-ranking-20261026/COMPLETION_AUDIT.md；下列旧版说明按历史范围解释。formal_ready=false。

> 2026-10-26当前开发候选0.6.49，仍未正式准入。当前冻结由CURRENT_STATUS.json选择。0.6.48完整工程与实际隔离通过，但两条诊断均独立失败；0.6.49修复查询解释及候选池提前截断，正进入新资格。原0.6.45 A组14项已审计关闭，后32项未开放，不能继承历史效果。

> 当前协议修复进度见[0.6.45审计](outputs/public-check-protocol-20261025/COMPLETION_AUDIT.md)；0.6.44历史成绩保留原口径。

> 前版已验证候选源码0.6.45，本工作树0.6.46仅离线开发，运行目录仍保留0.6.40。当前冻结由CURRENT_STATUS.json选择；原46槽及G0—G6尚未完成，不继承旧版成绩。详见outputs/source-build-recovery-20261025/COMPLETION_AUDIT.md。

# ContractEdge system handoff

Read README.md and docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md first.
This worktree source is a development-only 0.6.49 candidate; formal_ready=false. The active fixed46 panel remains frozen at 0.6.45 in /home/xhj/contractedge-latest-integration-20261024. Do not deploy this candidate, inherit old effectiveness results, or start paid comparison before the active panel is audited and a new exact registration is prepared. The separately preserved runtime /home/xhj/contractedge-system remains 0.6.40 until qualification. Historical reports contain superseded status; CURRENT_STATUS.json and the handoff plan identify the current failed gate.
Preserve benchmark/ and tools/ in the repository root. Do not put credentials, hidden graders, GT/Fixed, runtime workspaces or caches into model context or Git.
Use saved public failure replay before behavior changes; verify actual final serialized requests across diagnosis, plan, execution and re-diagnosis. Re-read the current snapshot after edits.
Run python scripts/test_portable.py for the self-contained development subset; report explicitly that six historical/native modules are excluded. Full tests require the documented external fixtures. Never call subset results a full 662-test reproduction.
Source changes require a new method freeze, configuration digest and registered same-version development panel. Do not pool historical best scores, overwrite failed attempts, or lower PASS/UNKNOWN gates. Publishing this development snapshot does not authorize formal150 runs.

Current development execution uses scripts/run_isolated_method.py and parent-only scripts/score_isolated_method.py. The original 46-slot driver is migrated through scripts/prepare_fixed_panel.py and scripts/run_fixed_panel.py; its earlier A-group is closed; later groups remain gated. See outputs/fixed46-isolated-panel-20261025/COMPLETION_AUDIT.md. Do not count qualification singles or historical scores into that panel, change frozen execution files during a live cohort, or open later groups without verified prerequisites.
