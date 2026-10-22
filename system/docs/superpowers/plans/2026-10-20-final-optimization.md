# ContractEdge Final Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implement all five approved work packages and deliver evidence-based experimental readiness.
**Architecture:** Preserve the 0–10 pipeline. Registered public output adapters produce complete, provenance-bound observations; source-grounded obligations constrain plans and survive rounds; deterministic checks support bounded feedback and real recovery.
**Tech Stack:** Python 3.10, existing JSON Schema/PyYAML/pytest, native TopoMen Agent, frozen offline benchmark runners.
**Spec:** docs/reports/2026-10-20-final-optimization-design.md

## Global Constraints
- Freeze benchmark150 and scorers; development_only; no formal150 or ablation dispatch.
- Existing dirty codex development branch is the authorized workspace; preserve pre-existing edits, no reset/clean/bulk commit. Baseline copied to outputs/final-optimization-20261020/baseline.
- Retain current snapshot/evidence/path permission guards, total model-call and money ledgers; 80 CNY total unless user explicitly changes it.
- Public producer identity and schema establish observation capability; emitted PASS/type metadata never grants it.
- All published results refer to exact method/config hashes. Preserve failures, not best-of mixtures.

## Review Focus
- Valid producer output exceeding a text window remains parseable; truncated enumeration cannot prove absence.
- A failed necessary check can support FAIL, related-only checks cannot manufacture PASS.
- Paraphrases, two atoms in one clause, and split/merge lineage never erase prior constraints.
- Preservation impact links guide review without being mistaken for causal blame or skipping checks.
- Runtime reload observations distinguish setup/transport failure from semantic failure and retain time-bound scope.

### Task 1: Registered public structured observations
**Files:** execution/observations.py (new), execution/public.py; tests/test_structured_observation_adapters.py (new).
**Interfaces:** consumes frozen task/environment and raw execution results; produces adapter profiles frozen at prepare plus derived evidence path metadata, parse diagnostics, source pointers.
- [x] RED: real KOPF/provider-format and wrapper replay; valid large objects, wrong producer, spoof, malformed/truncated JSON, scenario/resource mismatch.
- [x] Implement bounded complete-object parsing and identity/schema registration; integrate without weakening Helm behavior.
- [x] GREEN: focused adapter and wrapper/gate tests; zero-call real public-output replay saved.

### Task 2: Sufficient/necessary check semantics and actionable feedback
**Files:** execution/support.py, review.py, comparison.py, orchestration/feedback.py, schemas and changeset prompt; tests/test_check_coverage.py (new).
**Interfaces:** consumes preregistered checks and predeclared scope; produces conservative per-obligation PASS/FAIL/UNKNOWN plus reason.
- [x] RED: necessary subset failure; incomplete PASS; related-only PASS; absent/truncated/mismatched records; deterministic aggregation.
- [x] Implement explicit finite all-of coverage contract and trusted observation/check validation; preserve legacy trace interpretation.
- [x] GREEN: historical KPT feedback replay and public review/retention tests; reason categories propagated.

### Task 3: Clause sources, stable obligation lineage, preservation impact
**Files:** requirement/normalize.py, requirement/obligations.py (new), diagnosis/parse.py and prompt.py, changeset validation/prompt, orchestration/feedback.py/runner.py, schemas.
**Interfaces:** consumes immutable raw requirement; produces precise source refs, clause treatment, stable obligation keys and explicit lineage, conservative preservation-to-change links.
- [x] RED: Markdown trigger, clause exact offsets, renamed statements, same-clause distinct atoms, dropped/rewritten constraints, invalid lineage/impact refs.
- [x] Implement without an extra decomposition model call; old traces retain old semantics.
- [x] GREEN: full original contracts/orchestration tests plus new cross-round intervention cases.

### Task 4: Public entrypoints and helper preconditions
**Files:** source/entrypoints.py, orchestration/runner.py, execution/helper_context.py, changeset prompt; relevant tests.
**Interfaces:** consumes registered public producer/API metadata and bounded public source; produces read-only entry hints/helper context, never permissions or verified causal edges.
- [x] RED: probe-independent Python/Go entrypoint and configuration-to-consumer evidence cases.
- [x] Implement bounded general mechanisms; retain Top-N and scan/context limits; preservation influence enters execution and feedback.
- [x] GREEN: ACK/public-source retrieval replay and successful path context regression.

### Task 5: Fine-grained KPT and runtime-state probes
**Files:** execution/probe_specs.py/probes.py plus small runtime profile module, registered schemas/prompts; tests and real probe reports.
**Interfaces:** consumes public requirements/APIs and Broken-frozen profile; produces versioned complete check set and scope-limited observations.
- [x] RED: KPT config/workload/transformation separation; runtime setup error versus behavioral failure; frozen selection and check-set integrity.
- [x] Implement finite public-source-derived probes, no private fixtures/oracles.
- [x] GREEN: same probes on Broken and preserved candidates; real Alertmanager public calibration with explicit temporal boundary.

### Task 6: Fixed development panel, recovery and release audit
**Files:** configs/deepseek-development-final-*.json, scripts/replay_final_optimization.py (new), outputs/final-optimization-20261020/*, README.md/CURRENT_STATUS.json/spec appendix.
**Interfaces:** consumes final source/config/probe hashes and frozen development partition; produces full panel outcomes, independent scores, recovery report, capability matrix and requirement-by-requirement audit.
- [x] Preselect 6–8 development cases and retain exposure history before new calls.
- [x] Run full engineering tests, public replay, 150 public capability/entry audits; freeze source/config.
- [x] Real bounded development runs and one explicitly labelled recovery validation; independent evaluation after candidate freeze.
- [x] Fresh whole-change review; fix Important findings with counterexamples; verify final tree and reports.
- [x] Declare readiness only to the extent proved; keep unmet objective items active.

Final acceptance: implementation and all reporting delivered for0.5.5; fixed development5/7independentPASS, seeded recoverySEMANTIC_FAIL. Checked tasks mean execution/assessment complete, not all repair outcomes successful. formal_ready=false; see outputs/final-optimization-20261020/COMPLETION_AUDIT.md.
