> 当前开发源码0.6.40，冻结摘要2d117c40ac2c45eb25e440ec33b347da6e25733d5ac300bb4f760d8045246742。全量1069+22、可移植1036+17通过；同版预登记真实结果见 outputs/declared-comparison-closure-20261024/COMPLETION_AUDIT.md。formal_ready=false，原46槽及全部准入门未完成，不继承旧版成绩。

# ContractEdge system handoff

Read README.md and docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md first.
Runtime source is 0.6.40; formal_ready=false. Historical reports contain superseded status; CURRENT_STATUS.json and the handoff plan identify the current failed gate.
Preserve benchmark/ and tools/ in the repository root. Do not put credentials, hidden graders, GT/Fixed, runtime workspaces or caches into model context or Git.
Use saved public failure replay before behavior changes; verify actual final serialized requests across diagnosis, plan, execution and re-diagnosis. Re-read the current snapshot after edits.
Run python scripts/test_portable.py for the self-contained development subset; report explicitly that six historical/native modules are excluded. Full tests require the documented external fixtures. Never call subset results a full 662-test reproduction.
Source changes require a new method freeze, configuration digest and registered same-version development panel. Do not pool historical best scores, overwrite failed attempts, or lower PASS/UNKNOWN gates. Publishing this development snapshot does not authorize formal150 runs.

Current development execution uses scripts/run_isolated_method.py and parent-only scripts/score_isolated_method.py. The original 46-slot driver is not yet migrated to this protocol. Do not count qualification singles or historical scores into that panel.
