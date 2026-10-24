# Public inventory scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Already authorized public-observation adaptation; no new permission loop. Steps use checkbox syntax.

**Goal:** 让完整可信集合清单进入对应资源范围验收，同时保留错误/部分证据的拒绝条件。
**Architecture:** 在既有producer标记完整kind清单，在既有support_error分清单对象身份与集合范围；保持语义审查和操作事实校验。
**Tech Stack:** existing Python/pytest/json/schema.
**Spec:** docs/reports/2026-10-24-public-inventory-scope-design.md

## Global Constraints
- 原150、分区、评分器、Top12、12调用、2轮和150元共享账本保持。
- .38原冻结/运行/结果保留；本工作树不得修改现行运行代码；新源码独立冻结后才可评价。
- 不自动升级模型UNKNOWN、不接受未登记/截断清单，不追加模型调用。

## Review Focus
- 集合匹配不会把不同kind/场景混用。
- 日志JSON自称complete不能取得producer能力。
- 字段相同但截断或来源不一致不能通过。
- 集合范围下相关异常仍阻止无依据PASS，无关异常不阻断。
- 准入只说明可使用证据，不证明完整自然语言需求。

### Task 1: Producer and scope validator
**Files:** modify src/contractedge/execution/public.py and support.py; create tests/test_public_inventory_scope.py.
**Interfaces:** support_error(obligation,claim,observations,artifacts=())->str|None unchanged; registered inventory metadata uses existing schema fields.
- [ ] Write generic producer/consumer positive and adversarial tests, observe current RESOURCE_MISMATCH RED.
- [ ] Add minimal producer registration and scope matching; keep original direct-object checks.
- [ ] Replay saved public rejection through fresh trusted production, no HTTP; run related/full/portable suites.
- [ ] Review concrete scope/operation boundaries; checkpoint only verified source, leaving new-version effect acceptance explicit.
