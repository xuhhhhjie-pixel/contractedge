# ContractEdge v1.1 continued acceptance checkpoint — 2026-10-05

This checkpoint records current evidence against the supplied v1.1 implementation specification. It does not mark the first three stages accepted.

## Live matched pilot

- Frozen R1/R2 pilot: `outputs/pilot-v1_1-postfix-r1-r2-20-v5-2026-10-05/`.
- The runner was confirmed live at PID `3241567`. At this checkpoint its authoritative `run_state.json` is `RUNNING`, 8/20, with `RW-HELM-KEDA-HTTP-LOGGING-001` current. The trace and attempt manifests contain 8 completed attempts; `pilot_metadata.json` has been synchronized to those manifest hashes and the 8/20 checkpoint.
- The first eight traces include `INVALID_OUTPUT`, `EVIDENCE_GAP`, `BUDGET_EXHAUSTED`, and one structural `READY`; the `READY` trace has `semantic_review=NOT_CONFIGURED` and is not an accepted plan. The current cohort has not completed, so no final pilot summary is claimed.
- Do not change the source tree used by this frozen run. Its implementation digest remains `e297f438cac66b824a40dc7c20b66881b43ace7d8e5523994a697bfb2a0a96ef`.
- An isolated retry fix in `/home/xhj/contractedge-system-retry-fix` appends each rejected Diagnosis draft and prior validator feedback to the next bounded retry. The existing configured retry limit and budget guards remain in effect. The full isolated suite passed 136/136 tests; `compileall` and `git diff --check` passed. This fix has not been merged into the live pilot source and has not yet been tested by a matched model pilot.

## R5 release evidence and unresolved gates

- The R5 v28 fresh-replay report exists outside the release tree at `/home/xhj/contractedge/benchmark-ready-export/reports/R5_FRESH_FULL_COHORT_REPLAY_V28_2026-10-03.json`. Its SHA-256 for the referenced `official_release_v28/replay_ledger.jsonl` matches the current ledger. The ledger has 30 unique case IDs; all 30 report `PASS_FRESH_REPLAY`, three Broken trigger failures, three Fixed trigger passes, and passing case-specific controls. The report limits its claim to each package's exact-source verifier and does not claim that every case runs a live Kubernetes cluster.
- This replay evidence does not qualify the public task text or relation GT. The current `audit_summary.json` says 30/30 R5 requirements await publication review, 0 approved public requirement origins, and 30/30 MethodInputs are held. The GT qualification audit marks R5 precision/recall eligibility unadjudicated for 30/30. The `pairwise-family-reconciliation-v28-v1.json` has 25 historical pair decisions pending reconciliation; 10 case-level repair-family values are missing even though the root manifest has values for all 30.
- The public-origin audit has 14/30 cases with public issue evidence and 14/30 with a public fixture/declaration; four have neither. There are 16 reviewer-only drafts, all `DRAFT_PENDING_HUMAN_REVIEW` and `method_input_eligible=false`.
- Source follow-up found that Tempo #6451 describes a block-builder behavior and is resolved through #6532; it does not establish the user task for the separate Tempo #6509 live-store case. Tempo #6435 refers to #6098, which is a PR introducing the feature, not a public user issue. Kubernetes client-go tombstone documentation provides API context for kube-state-metrics #2955 but does not establish a case-specific user requirement. Dapr #9612's public PR narrative includes root-cause and implementation details, so it remains unsuitable as a clean task source by itself. These four cases stay held pending a separate public declaration or user report that matches the actual case verifier.
- A fresh content scan of private R5 case docs found stale candidate-stage prose in 7 formal case packages despite the v28 top-of-file status banners: `RW-ALERTMANAGER-4705`, `RW-ALERTMANAGER-5040`, `RW-CONTAINERD-14207`, `RW-COREDNS-7562`, `RW-DAPR-9612`, `RW-OTEL-OPERATOR-2171`, and `RW-PROMETHEUS-16340`. Examples say a case “remains a candidate” or “is not a formal member” after the banner says it is a formal v28 member. The linked status audit only checks 90 structured boundary fields and does not detect this prose contradiction. This is a release-document consistency defect; the frozen v28 package was not edited, because doing so would invalidate its hashes. A corrected export must synchronize the text and regenerate hashes.
- Public-tree recheck distinguishes package integrity from method eligibility: all 30 manifest members have one Broken archive, all 87 public SHA256SUMS entries pass, but 19 `input.yaml` files contain a `task` field and 18 still carry candidate-stage status text (12 have no such status field). These are candidate public inputs, not the approved v1.1 MethodInput export; all 30 remain held by the trusted exporter until task-origin/fidelity approval.

## Acceptance consequence

The existing implementation is a substantial v1.1 prototype with contracts, exporter, retrieval, Requirement, Diagnosis, ChangeSet, bounded orchestration, and evaluator components. Current evidence still does not prove full-stage acceptance: the matched pilot is incomplete and has no configured independent semantic reviewer; R5 cannot yet be admitted to the method view; relation GT and family adjudications remain open; parser conformance is partial; and actual TopoMen consumer acceptance has not been performed.

## Evidence locations

- R5 source gate: `outputs/r5-v28-adapter-review/audit_summary.json`.
- R5 public-origin counts: `outputs/r5-v28-adapter-review/public-origin-audit/public-requirement-origin-summary.json`.
- R5 family reconciliation: `outputs/r5-v28-adapter-review/pairwise-family-reconciliation-v28-v1.json`.
- R5 fresh replay: `/home/xhj/contractedge/benchmark-ready-export/reports/R5_FRESH_FULL_COHORT_REPLAY_V28_2026-10-03.json` and `/home/xhj/contractedge/benchmark-ready-export/benchmark/R5/official_release_v28/replay_ledger.jsonl`.
- Live pilot: `outputs/pilot-v1_1-postfix-r1-r2-20-v5-2026-10-05/`.
- Retry fix: `/home/xhj/contractedge-system-retry-fix/src/contractedge/orchestration/runner.py` and `/home/xhj/contractedge-system-retry-fix/tests/test_orchestration.py`.
