> CURRENT frozen0.6.35 / 68e5275224d95958f054acda90108c5f0a84272fa1569b6a90ae3e1aaa74b9dd. Full841+22, portable808+17 including masked freshvenv local clone13ee9ac. .35 CLOSED originalBroken BUDGET_EXHAUSTED/SEMANTIC_FAIL6calls;1/46scored45NOT_RUN. Supplemental .34-saved-response→.35 continuationPASS (8checksPASS,3newcalls) is NOT fresh/panel. No active paid job. Formal150/B closed. Latest report docs/reports/2026-10-24-build-failure-recovery.md. New followup-context failure reproduced; fix/test offline before more API.

# ContractEdge system handoff

Read README.md and docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md first.
Runtime source is 0.6.35; formal_ready=false. Historical reports contain superseded status; CURRENT_STATUS.json and the handoff plan identify the current failed gate.
Preserve benchmark/ and tools/ in the repository root. Do not put credentials, hidden graders, GT/Fixed, runtime workspaces or caches into model context or Git.
Use saved public failure replay before behavior changes; verify actual final serialized requests across diagnosis, plan, execution and re-diagnosis. Re-read the current snapshot after edits.
Run python scripts/test_portable.py for the self-contained development subset; report explicitly that six historical/native modules are excluded. Full tests require the documented external fixtures. Never call subset results a full 662-test reproduction.
Source changes require a new method freeze, configuration digest and registered same-version development panel. Do not pool historical best scores, overwrite failed attempts, or lower PASS/UNKNOWN gates. Publishing this development snapshot does not authorize formal150 runs.
