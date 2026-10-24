> 当前交付候选源码0.6.44，运行目录仍保留0.6.40。当前冻结由CURRENT_STATUS.json选择；原46槽及G0—G6尚未完成，不继承旧版成绩。详见outputs/source-build-recovery-20261025/COMPLETION_AUDIT.md。

# ContractEdge system handoff

Read README.md and docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md first.
This isolated delivery worktree source is 0.6.44; formal_ready=false. The separately preserved runtime /home/xhj/contractedge-system remains 0.6.40 until qualification. Historical reports contain superseded status; CURRENT_STATUS.json and the handoff plan identify the current failed gate.
Preserve benchmark/ and tools/ in the repository root. Do not put credentials, hidden graders, GT/Fixed, runtime workspaces or caches into model context or Git.
Use saved public failure replay before behavior changes; verify actual final serialized requests across diagnosis, plan, execution and re-diagnosis. Re-read the current snapshot after edits.
Run python scripts/test_portable.py for the self-contained development subset; report explicitly that six historical/native modules are excluded. Full tests require the documented external fixtures. Never call subset results a full 662-test reproduction.
Source changes require a new method freeze, configuration digest and registered same-version development panel. Do not pool historical best scores, overwrite failed attempts, or lower PASS/UNKNOWN gates. Publishing this development snapshot does not authorize formal150 runs.

Current development execution uses scripts/run_isolated_method.py and parent-only scripts/score_isolated_method.py. The original 46-slot driver is not yet migrated to this protocol. Do not count qualification singles or historical scores into that panel.
