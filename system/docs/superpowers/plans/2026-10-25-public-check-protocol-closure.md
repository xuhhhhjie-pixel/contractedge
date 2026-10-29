# Public check binding and scenario protocol closure

> Execute inline using superpowers:executing-plans. User explicitly authorized this priority repair; no additional design approval is needed. Existing isolated integration worktree is retained. This is the first step only; the 46-slot driver is the subsequent task.

**Goal:** A registered public predicate can travel from its trusted capability declaration through the actual planning request, validated ChangeSet, frozen observation and feedback without losing check or scenario identity. Wrong bindings and insufficient evidence remain rejected/UNKNOWN.

**Architecture:** Reuse existing public observers, planner, semantic validator and support rules. Normalize the two existing check declaration sources into a small trusted catalog; no new agent. Registered process observers declare their existing finite suite scenario explicitly, with member scenarios derived from frozen public assertions (never guessed from check IDs). All checks in a suite share its execution scenario; per-check coverage is limited by the explicit member list and check identity.

**Tech Stack:** Python, JSON Schema, pytest, existing isolated RuntimeSession.

**Spec:** User's approved priority fix; docs/development/2026-10-24-architecture-wrapup.md P0 items; 2026-10-22 formal-experiment plan remains authoritative.

## Constraints and choices
- Preserve method-owned probes, Top12, 12 calls/2 edits for benchmark, public/hidden separation, old freezes and results. Zero model HTTP for this task.
- Do not remove scenario checking, auto-fill model bindings, or claim finite checks cover an entire natural-language requirement.
- Alternative of patching only prompt IDs leaves invalid bindings at READY/resume; patching only observer fields leaves output schema closed. Repair both with shared catalog validation.
- Registered process scenario `selected-and-preservation`; six-case panel scenario `public-requirement-panel`, matching their existing public execution declarations. Member IDs are separate, explicit coverage metadata.
- New method identity 0.6.45, retained .44 snapshot. Runtime stays .40; no formal150 or paid panel started.

## Tasks / acceptance
- [x] Write failing tests for registered-only planning schema, all three obligation kinds, mixed probe catalogs, unknown IDs, duplicate declarations, missing/mismatched scenarios and coverage.
- [x] Implement catalog use in changeset/prompt.py and validate.py. Fresh/correction/revision requests retain declarations; READY preflight uses same validator. Legacy probe behavior remains.
- [x] Write failing observer-to-review tests using actual frozen checker with runtime boundary double; require matching scenario, preserve finite aggregate members; malformed/stale/wrong-scenario/insufficient coverage rejected. Include process and six-case observers.
- [x] Add explicit scenario/members to trusted capability, observation excerpt/record and profile validation; update synthetic providers to explicitly author the same protocol, never alter real responses.
- [x] Run focused tests, real isolated three-case/READY regression, full tests and portable suite. Separate deterministic runtime proof from model-effect claims.
- [x] Review: independent reviewer returned two Important findings, both reproduced and fixed; its final conclusion was unavailable due service usage limit. Root completed supplementary source/contract review; no complete independent clean-review claim. Retain red/green records. Freeze exact source/tests/tooling/config; report next panel integration work and remaining formal gates.

## Review focus
Unknown/malformed/duplicate registry; two declaration sources; finite aggregate vs single scenario; legacy/no-registry path; profile tampering with recomputed digest. Each belongs to tests above. Existing frozen public failures remain unchanged.

## Completion record

0.6.45 source is unchanged after full regression. Full1727+91, portable1694+86, noneditable75+28, actual8(14 sessions/28 containers), actual24(48 sessions/96 runs) PASS. No model HTTP. Actual six-stage request/observation/feedback alignment recorded. Review service limitation and all early failures are retained. Next work is the original46-slot protocol migration described in docs/reports/2026-10-25-public-check-protocol-closure.md; not implemented by this priority repair.
