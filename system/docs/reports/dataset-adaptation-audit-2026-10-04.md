# Dataset adaptation gate for the first three ContractEdge stages

This report audits local release manifests and case packages at `/home/xhj/contractedge/benchmark-ready-export`. It does not modify authoritative benchmark releases or claim new behavioral replays.

## Findings

| Release | Manifest cases | v1.1 adaptation status | Evidence |
|---|---:|---|---|
| R1 | 57 | Public and evaluator projection materialized from the manifest. | The release archive stores 171/171 Broken/Fixed results; not rerun here. Two extra input/GT files are outside the formal manifest and must not be enumerated. |
| R2 | 30 | Public and evaluator projection materialized from the manifest. | The release archive stores 90/90 Broken/Fixed results; not rerun here. Some oracles observe only a local controller decision. |
| R5 v28 | 30 | Source mapping and archive preflight pass; v1.1 projection holds all 30 pending public-task provenance and metadata reconciliation. | All 30 archive/input hashes and source-root mappings verify. The formal manifest/release report say v28, while `public/README.md` calls itself candidate method view v17; 18/30 inputs retain non-formal candidate status and only 19/30 contain a task field. |

R1/R2 inputs are loaded from the top-level class manifest, which points into `official_release/`. The case-ID-specific Broken archive is used for the public view; Fixed source, GT, and verifier remain evaluator-side. R1/R2 archived replay records are historical evidence, not a run made by this adapter audit.

R5 v28's public manifest supplies Broken archives and declared deployment/configuration artifacts. Nineteen public `input.yaml` files contain a `task` field, but the release does not establish those texts' approved public-source provenance; eleven inputs have no task field. In addition, the public README identifies the view as candidate v17, and 18 inputs still say formal admission is pending even though the v28 manifest marks all 30 formal. The first v1.1 review sidecar copied 30 statements from private GT; those drafts were invalidated and removed. All 30 cases remain held until task provenance and case/package status are reconciled, and each method-facing requirement is based on admissible public issue/declaration evidence and receives human review. Approval cannot legitimize private-origin text.

## R5 source mapping and release metadata

The sidecar under `outputs/r5-v28-adapter-review/source_roots.json` was checked against all 30 public manifest members. Each mapping's archive SHA-256, explicit `source_root`, file count, and `snapshot_id` matches a fresh archive inspection. This verifies packaging identity, not case semantics.

The v28 root manifest and release report record 30 distinct repair families and 30 unique upstream PRs. Ten private `case.yaml` files omit the case-level `repair_family` field; 20 populated values are distinct. Treat the root manifest as the formal membership source, and reconcile missing per-case metadata and public candidate-status fields before standalone case-GT or method-view consumption.

## Scope of the result

The R5 v28 release report is the source for its formal status, 30-case exact-source replay, trigger/control outcomes, pairwise review, and public/private isolation claim. This review verified sidecar/archive correspondence and prepared the adapter gate; it did not rerun all R5 verifier suites. The new reproducible accounting report assigns all **117** R1/R2/R5 members exactly once: 87 v1.1-prepared and 30 held. It also records 30/30 R5 input-hash matches and the manifest/README/task-field inconsistency. The v1.1 adapter rejects an R5 override when the same requirement is absent from public input. No private GT or Fixed source is to be copied into the method view.

For R1/R2, this review materialized the current-schema public/evaluator projection. Final release readiness requires checking the output hash manifests and confirming the method tree contains no evaluator files. A successful projection is not a full system pilot or a replacement for fresh behavioral replay.

## GT and observation metadata

The evaluator-only GT qualification audit covers 117 unique formal members. Relation precision/recall eligibility is explicit for 55/57 R1 and 30/30 R2 cases. `RW-CAND-070` and `RW-CAND-134` lack a case-level GT ID and are joined by manifest ID; their relation metrics remain unadjudicated. R5 has 0/30 adjudicated relation-metric eligibility. Its private v28 manifest carries class and oracle-boundary metadata, which the evaluation adapter now preserves in normalized GT and score records without exposing it in method inputs. The audit therefore records R5 oracle limits for 30/30 cases, while keeping relation metrics unscored pending semantic GT review. See [GT qualification audit](gt-qualification-v1_1-2026-10-04.json).

The new cohort evaluator uses the frozen manifests as the score denominator, refuses out-of-cohort traces or path escapes, and leaves unrun and unadjudicated cases explicit. Its current 117-case baseline contains no accepted benchmark traces (`NOT_RUN` 117/117); prior local model smokes are development-only and not included. See [cohort score baseline](../outputs/cohort-evaluation-baseline-2026-10-04.json).

## Public-source provenance follow-up

`scripts/audit_r5_public_task_sources.py` creates a public-only source index for human task review. It reads the R5 v28 **public** manifest/input tree and the collected public issue catalog; it omits existing task text, PR bodies, Broken archives, private GT, verifier, and repair content. The current index finds 8 cases with 10 candidate public issue sources, 14 cases with separate public fixture files, and 10 cases with neither a linked issue source nor a separate fixture file (the groups overlap). These counts identify review material, not approved task coverage; a fixture may be an observation/control rather than a declaration of user intent.

The R5 preparation gate now requires every `APPROVED` task review to bind one or more digest-verified public sources and nonempty evidence references. Local declaration sources must resolve inside the public package and match their SHA-256; issue sources must use HTTPS issue URLs, include the captured issue body, and match its digest. Pull-request URLs are rejected as issue sources. The source URI, digest, and references are stored in the evaluator-side manifest only, not MethodInput. All 30 current R5 review records remain `HELD_PRIVATE_ORIGIN`; the new audit does not approve or reintroduce any task text.
