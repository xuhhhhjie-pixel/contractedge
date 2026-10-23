> CURRENT frozen0.6.37 / d0d8c56b057c18ee29511d85190c3cb1300ac5b4069878bae732b8169a653d93. Full859+22, portable826+17. Same-context rejected/current plan continuity verified offline; originalBroken ACK single gate RUNNING once,45held. Formal150/B closed. No GitHub push. Read CURRENT_STATUS.json and docs/reports/2026-10-24-changeset-draft-continuity.md.

# ContractEdge system handoff

Read README.md and docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md first.
Runtime source is 0.6.37; formal_ready=false. Historical reports contain superseded status; CURRENT_STATUS.json and the handoff plan identify the current failed gate.
Preserve benchmark/ and tools/ in the repository root. Do not put credentials, hidden graders, GT/Fixed, runtime workspaces or caches into model context or Git.
Use saved public failure replay before behavior changes; verify actual final serialized requests across diagnosis, plan, execution and re-diagnosis. Re-read the current snapshot after edits.
Run python scripts/test_portable.py for the self-contained development subset; report explicitly that six historical/native modules are excluded. Full tests require the documented external fixtures. Never call subset results a full 662-test reproduction.
Source changes require a new method freeze, configuration digest and registered same-version development panel. Do not pool historical best scores, overwrite failed attempts, or lower PASS/UNKNOWN gates. Publishing this development snapshot does not authorize formal150 runs.
