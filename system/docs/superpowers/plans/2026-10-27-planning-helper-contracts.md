# Planning Helper Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Approved inline continuation; reuse existing isolated worktree.

**Goal:** Deliver current helper contracts before planning, with independently revalidated prerequisite review.
**Architecture:** Reuse bounded helper lookup and existing support/review fields; carry declared opt-in across READY. No new module or model call.
**Tech Stack:** Python, existing Go lexical lookup, pytest.
**Spec:** docs/reports/2026-10-27-planning-helper-contracts.md

## Global Constraints

- 4 seed files,512KiB/file,2MiB total; existing128-file/4MiB helper scan,8 records/24KB; original final request cap.
- Exact current snapshot, no extra editable path or causal assertion; unsupported lookup stays explicit.
- Closed059 failures immutable; historical READY without the opt-in retains prior requirements; formal_ready=false.

## Review Focus

- Seeds must bind actual fresh full files and precise excerpts; no symlink or stale-body acceptance.
- Additional support must survive final planning, correction, READY and execution; no metadata-only presence.
- New prerequisite refs cannot authorize edits or invent sufficient behavioral coverage.
- Legacy traces must not acquire new obligations; current traces cannot lose registered helper context.
- Budgets and ambiguity rules must reuse the existing scanner; no unbounded repository graph.

### Task 1: Current helper support before planning

**Files:** execution/helper_context.py; tests/test_planning_helper_contracts.py.
**Interfaces:** planning_helper_source_support(snapshot,selected,*,max_bytes=24000) returns existing SOURCE_SPAN records and audit.

- [ ] Generic renamed call/type/identity-helper fixture RED; stale/changed/symlink/budget counterexamples.
- [ ] Minimal wrapper around existing local_go_helper_support, GREEN.

### Task 2: Planning, prerequisite review and READY continuity

**Files:** orchestration/runner.py, orchestration/repair.py; same tests and saved public replay.
**Interfaces:** required_planning_source_support and required_source_prerequisites accept explicit helper_support; current producer and READY independently derive same support.

- [ ] RED actual request delivery/review and tampered READY; preserve legacy behavior.
- [ ] Integrate only existing support/prerequisite mechanisms; focused GREEN.
- [ ] Replay saved real KPT planner against current S0; source/code budgets and edit targets unchanged, API0.
- [ ] One fresh whole-branch review, Important findings RED→GREEN; no repeated review.
- [ ] New exact version/config/freeze and complete engineering qualification before any real model call.
