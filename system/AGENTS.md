Current source freeze396ef3744848f44257b9eaf40a97d456fb7b2e696cc4f6fce053fcefcc2b94d4; full792+22, portable759+17 (six modules excluded). Read CURRENT_STATUS.json and docs/reports/2026-10-23-v0631-production-tag-probe.md. Formal readiness remains false.

# ContractEdge system handoff

Read README.md and docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md first.
Runtime source is 0.6.31; formal_ready=false. Historical reports contain superseded status; CURRENT_STATUS.json and the handoff plan identify the current failed gate.
Preserve benchmark/ and tools/ in the repository root. Do not put credentials, hidden graders, GT/Fixed, runtime workspaces or caches into model context or Git.
Use saved public failure replay before behavior changes; verify actual final serialized requests across diagnosis, plan, execution and re-diagnosis. Re-read the current snapshot after edits.
Run python scripts/test_portable.py for the self-contained development subset; report explicitly that six historical/native modules are excluded. Full tests require the documented external fixtures. Never call subset results a full 662-test reproduction.
Source changes require a new method freeze, configuration digest and registered same-version development panel. Do not pool historical best scores, overwrite failed attempts, or lower PASS/UNKNOWN gates. Publishing this development snapshot does not authorize formal150 runs.
