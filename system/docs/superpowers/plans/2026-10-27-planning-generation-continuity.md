# Planning generation continuity implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. User authorized inline continuation.

**Goal:** Prevent returning to an observed reasoning-starved mode in later planning stages of the same front invocation.
**Architecture:** Optional scope on the existing recovered-generation map. Original policy counters and all budgets unchanged.
**Tech Stack:** Python, existing front pipeline/pytest/public replay.
**Spec:** docs/reports/2026-10-27-planning-generation-continuity.md

## Global Constraints

- `planning_generation_recovery_scope`: `prompt_version` default or `front_half`; invalid values rejected before dispatch.
- Trigger only the existing observed recovery; do not add recovery or global-call allowance.
- Preserve public requirement/evidence/schema/snapshot/messages and strict validation.
- New front invocation resets; effect remains unproven until real validation; formal_ready=false.

## Review Focus

- Failed recovery or ambiguous usage must not change later policy.
- Defaults and fresh invocations must not inherit a past case's modes.
- Every changeset revision and evidence re-diagnosis uses the common policy function.
- Token/time/call checks and journal scopes cannot be bypassed by the mode.
- Actual serialized provider input, not helper output alone, proves delivery.

### Task 1: Scope recovery mode and replay

**Files:** runner.py, tests/test_planning_generation_scope.py, outputs/planning-generation-continuity-20261027/.
**Interfaces:** consumes existing config and recovery map; emits same ModelRequest except thinking/metadata when scope explicitly enabled and recovery occurred.

- [ ] Write generic positive and negative tests; run RED for unsupported new scope.
- [ ] Implement scope validation and bounded per-invocation propagation; run GREEN + related tests.
- [ ] Replay saved first two public source057 responses to actual next planning request; only transport mode/metadata differ.
- [ ] Independent final runtime review and fix concrete Important findings with RED→GREEN.

### Task 2: Freeze and minimal true repair

**Interfaces:** consumes verified Task1 exact source; produces qualified new version and separately registered results.

- [ ] Freeze source/config/tools/tests, full actual process receipt,8actual isolation/public sandbox/source verification.
- [ ] Register one KPT, gated Cert cross-repository control; retain complete failures. Budget150CNY cumulative.
- [ ] Update reports/IDE continuation/GitHub; preserve original fixed46 and formal150 gates.
