# Preservation identity feedback implementation plan

> Use superpowers:executing-plans inline. Continuation of the user-authorized complete correction feedback requirement; no additional module or model quota.

**Goal:** Make the already enforced distinction between outcome IDs and plan preservation IDs explicit in model input and correction feedback, especially when the allowed preservation set is empty.
**Architecture:** Existing changeset prompt schema constrains an empty allowed preservation set to an empty impact array; existing validator reports exact expected/missing/unexpected/duplicate identities. No generated semantic bindings and no removed model data.
**Spec:** ../specs/2026-10-26-preservation-identity-feedback-design.md
**Stack:** Python/jsonschema, existing RunContext/ValidationIssue/ModelRequest.

## Global constraints

Top12, existing context/output limits,12 calls,2 rounds, one extension and original PASS/UNKNOWN rules unchanged. New freeze and full/actual isolation qualification required before any new paid calls. Source052 failure and controlNOT_RUN retained.

## Review focus

Empty expected set with invented outcome ID; missing or duplicated real preserveID; mixed outcome repaired/preserved atoms; legacy nontraceability plans; exact prior draft and correction delivery.

## Task1: Existing prompt/validator identity contract

Files: changeset/prompt.py, changeset/validate.py; new tests/test_preservation_identity_feedback.py.
Interfaces: existing build_changeset_request and validate_semantic_changeset, unchanged signatures/schema identity. Existing runner consumes ValidationIssue.message unchanged.

- [x] Reproduce source052 empty-preserve/nonempty-impact mismatch and generic renamed cases; assert exact identity differences are actionable.
- [x] Test generated schema rejects a nonempty impact array only when preserve_constraints cannot contain entries; nonempty allowed-set plan remains possible.
- [x] Run new tests RED; implement smallest schema/feedback fix; run GREEN and existing changeset/preservation/draft-continuity/lineage suites.
- [x] Replay exact six source052 responses at zero new calls; next correction must contain expected=[],unexpected=[O1], with unchanged source/previous draft.
- [x] Freeze source053; full suite actual exit0, actual8, drypublic, postverification. Only then prepare one exact target/control diagnostic if justified; never claim a schema fix proves lifecycle success.

Actual outcome: engineering and isolationPASS; target twelvecalls/selected782bytes/publicFAIL/independentSEMANTIC_FAIL; KPTNOT_RUN. Source053 identity error absent; lifecycle effect gate failed and remains open, see latest audit.
