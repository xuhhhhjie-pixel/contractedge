Current frozen source0.6.32 / afd0732da530687bbd49e44c545ba6448402fd38354b446fa87a28c56d7c2075; full799+22, portable766+17 (six modules excluded). Original-Broken ACK single gate RUNNING, .31 INVALID_OUTPUT/NO_CANDIDATE retained. Read CURRENT_STATUS.json and docs/reports/2026-10-24-execution-contract-closure.md. formal_ready=false; do not rerun registered slots or mutate source freezes.

# ContractEdge system handoff

Read README.md and docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md first.
Runtime source is 0.6.32; formal_ready=false. Historical reports contain superseded status; CURRENT_STATUS.json and the handoff plan identify the current failed gate.
Preserve benchmark/ and tools/ in the repository root. Do not put credentials, hidden graders, GT/Fixed, runtime workspaces or caches into model context or Git.
Use saved public failure replay before behavior changes; verify actual final serialized requests across diagnosis, plan, execution and re-diagnosis. Re-read the current snapshot after edits.
Run python scripts/test_portable.py for the self-contained development subset; report explicitly that six historical/native modules are excluded. Full tests require the documented external fixtures. Never call subset results a full 662-test reproduction.
Source changes require a new method freeze, configuration digest and registered same-version development panel. Do not pool historical best scores, overwrite failed attempts, or lower PASS/UNKNOWN gates. Publishing this development snapshot does not authorize formal150 runs.
