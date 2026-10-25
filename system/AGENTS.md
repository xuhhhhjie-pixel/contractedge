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
