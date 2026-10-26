# Grounded graph correction implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Continue authorized inline optimization.

**Goal:** Make existing path correction identify rejected graph endpoints and supplied current source consumers.
**Architecture:** One bounded helper in runner.py, used only by existing diagnosis retry. No new semantic authority.
**Tech Stack:** Python, pytest, existing model request/contract pipeline.
**Spec:** docs/reports/2026-10-26-grounded-graph-correction.md

## Global Constraints

- Inventory <=4096 UTF-8 bytes, <=8 complete records per category; expose omitted counts.
- Source must be in latest user envelope, same snapshot, exact SHA256 excerpt; no source reads or inferred edges.
- Keep rejected draft, strict validators, existing retry and total-call budgets unchanged.
- formal_ready=false; no old-attempt restart or pooled score.

## Review Focus

- Invalid/duplicate catalog or unit identities must not silently choose a winner.
- Model-supplied evidence or fake SRC IDs must not become canonical source evidence.
- Additional correction turns must retain the latest actual request snapshot.
- Large Unicode records must not truncate into misleading partial evidence.
- Inventory cannot be framed as proof of roles or production causality.

### Task 1: Grounded correction and replay

**Files:** runner.py; tests/test_grounded_graph_correction.py; outputs/grounded-graph-correction-20261026/.
**Interfaces:** consumes existing messages/rejected JSON/feedback; produces same retry tuple plus bounded inventory only for PRODUCTION_PATH_INCOMPLETE.

- [ ] Add generic failing tests covering inventory, stale/digest/observation/duplicate/malformed/budget cases and actual front-loop correction.
- [ ] Run tests; expected new behavior FAIL before implementation.
- [ ] Implement `_configuration_graph_feedback(messages, response_content) -> str`; append at existing correction boundary only.
- [ ] Run new + related tests; expected all PASS.
- [ ] Replay source056 first rejected public draft; expected existing U2/U3 and bypass endpoints present; original output/errors unchanged, zero provider/grader calls.
- [ ] Commit implementation and replay; run full engineering suite and independent final review; repair Important findings with RED→GREEN.

### Task 2: Qualification and truthful handoff

**Interfaces:** consumes Task1 verified code/results; produces new freeze and explicit engineering/effect status.

- [ ] New source version/freeze, external public fixture preflight, exact full regression receipt, actual isolation/sandbox before paid validation.
- [ ] Preserve failed056 and update current/IDE handoff. Register any new minimal diagnostic separately; no success assertion before actual result.
- [ ] Commit/push development branch and draft PR; retain original46/G0–G6 gates.
