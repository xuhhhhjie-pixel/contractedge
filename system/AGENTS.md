> CURRENT: frozen0.6.35 / 68e5275224d95958f054acda90108c5f0a84272fa1569b6a90ae3e1aaa74b9dd. Full841+22, portable808+17. .34 CLOSED build failure/rollback/NO_CANDIDATE8calls;1/46scored45NOT_RUN. .35 original-Broken ACK candidate/0 running once;45held. Check state/ps and never repeat or mutate freeze. .32PASS historical only. Formal150/B closed. Read CURRENT_STATUS.json and docs/reports/2026-10-24-build-failure-recovery.md. Unused draft group dispatcher is NOT qualified for paid dispatch.

# ContractEdge system handoff

Read README.md and docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md first.
Runtime source is 0.6.35; formal_ready=false. Historical reports contain superseded status; CURRENT_STATUS.json and the handoff plan identify the current failed gate.
Preserve benchmark/ and tools/ in the repository root. Do not put credentials, hidden graders, GT/Fixed, runtime workspaces or caches into model context or Git.
Use saved public failure replay before behavior changes; verify actual final serialized requests across diagnosis, plan, execution and re-diagnosis. Re-read the current snapshot after edits.
Run python scripts/test_portable.py for the self-contained development subset; report explicitly that six historical/native modules are excluded. Full tests require the documented external fixtures. Never call subset results a full 662-test reproduction.
Source changes require a new method freeze, configuration digest and registered same-version development panel. Do not pool historical best scores, overwrite failed attempts, or lower PASS/UNKNOWN gates. Publishing this development snapshot does not authorize formal150 runs.
