# Current development iteration (2026-10-23)

The replay package checker now validates every supplied role, required contract/version, effective limits and ledger/identity bindings before declaring COMPLETE. Missing legacy request parameters remain INCOMPLETE. A legitimate INVALID_PLAN still exports and replays; no old ACK root cause has been inferred.

Provider adapters prepare the final HTTP bytes once. The shared durable ledger records body, SHA256, adapter and effective output/thinking settings before HTTP; dispatch reuses those bytes. Replacing an already prepared request fails closed. Failed/unknown calls keep their accounting reservation, and credential-bearing endpoint errors keep only a generic diagnostic.

The public CLI uses the existing repair loop. Three synthetic cases create real files and patches, check selected and preservation behavior in child processes, and retain feedback. config-binding uses vendored TopoMen DefaultAgent; two-consumers requires two repair rounds. Deliberate READY continuation preserves the original exact ledger and inherited budget, uses an atomic one-shot claim, and rejects repeated/unknown attempts. This is not arbitrary crash recovery.

Public examples use trusted synthetic edits on a host Python observer. They provide no network/filesystem sandbox. Non-synthetic providers can only save a front READY/failed plan; executing or continuing their code is refused until an isolated observer is available. API budget configuration requires a persistent non-null MoneyBudget and rejects credential-bearing URLs before config persistence.

README, CURRENT_STATUS and the new development snapshot identify this version; the original 0.6.27 manifest and historical freezes retain their old meaning. The snapshot binds all runtime source, the portable fixed test panel plus separate native module, tools/configuration/examples and the GitHub workflow. Run scripts/verify_development_snapshot.py from either repository root (system/scripts/...) or system directory.

See next-iteration-20261023.validation.json for actual offline results. The portable subset retains six explicit historical/native exclusions, with native separately reported; this is not the original full historical suite. Real 2CPU/2GiB enforcement was checked in a task-owned daemon with separate data/socket/config; the pre-existing daemon and global cgroups were not changed.

GitHub Actions was not run at freeze time; inspect the created PR checks for actual results. Official DeepSeek front validation is authorized up to CNY30 including limited diagnosed retries, but currently NOT_RUN pending a safely configured replacement credential. Real model HTTP and cost are zero. Do not use an exposed chat key or reset the shared money ledger. formal_ready=false and research effectiveness remains unevaluated.
