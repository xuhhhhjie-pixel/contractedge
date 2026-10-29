# Helper input contracts implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Continue approved inline root-cause optimization.

**Goal:** Deliver bounded current local helper input types and actually mentioned implementations to execution.
**Architecture:** Extend existing local_go_helper_support with shared-budget dependency lookup; preserve direct/lifecycle priority and no edit grant. Standard dynamic package version removes release drift.
**Tech Stack:** Python, existing Go lexical parser, pytest, setuptools.
**Spec:** docs/reports/2026-10-27-helper-input-contracts.md

## Global Constraints

- Original max8 records, support byte cap,128file/4MiB total scan; no new model/retry allowance.
- Exact current snapshot source only; ambiguous/nonlocal/unsupported types are not resolved.
- Type/method evidence is lexical context, never causality or patch permission.
- No hidden grader/native failure detail into method; source058 failure retained; formal_ready=false.

## Review Focus

- Additional package reads must share original counters, no duplicate reads beyond budget.
- Parameter type lookup cannot bind return types or similarly named variables as facts.
- Duplicate receiver/type symbols, test files and symlinks must remain conservative.
- Large or truncated dependencies cannot silently replace existing lifecycle/direct support.
- Final context trimming and package metadata install must be independently verified.

### Task 1: Input contract context

**Files:** execution/helper_context.py; tests/test_helper_input_contracts.py; public saved replay in outputs/helper-input-contracts-20261027/.
**Interfaces:** existing local_go_helper_support returns same record/audit shape; new basis values distinguish input type/method/direct helper dependency.

- [ ] Generic renamed type/method tests RED, shared budget/stale/ambiguity counterexamples.
- [ ] Implement minimal shared scanner reuse and input-contract dependencies; GREEN with existing helper/lifecycle tests.
- [ ] Actual public saved execution request replay, same target/plan/current source, required new contracts remain after trimming; API0.
- [ ] Independent final review; Important fixes RED→GREEN.

### Task 2: Reproducible qualification and effect

**Files:** pyproject.toml, source version, frozen config/binding and reports.
**Interfaces:** exact new source/config identities consumed by existing isolated driver.

- [ ] Dynamic metadata version from module constant, actual clean install matches source.
- [ ] Freeze all changed runtime/config/tests/tools, full exact process receipt, actual8 and public sandbox.
- [ ] Minimal once-only KPT and gated Cert under cumulative150CNY, no retry of prior attempts; preserve every result.
- [ ] Publish code and final truthfully separated implementation/effect status; original46 remains final admission gate.
